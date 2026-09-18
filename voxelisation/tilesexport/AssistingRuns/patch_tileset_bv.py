#!/usr/bin/env python3
"""Repair parent/child boundingVolume containment in an existing
``tileset.json``, in place.

The LOD exporter used to mix two conventions: leaves claimed their whole
nominal tile square, interior nodes hugged the coarsened records they
actually held, so a sparse leaf could poke outside its own parent - which
the 3D Tiles spec forbids. ``tileset_exporter.convert_to_3d_tiles_lod``
now unions each node's box with its children's on the way back up; this
script applies the SAME union to tilesets that were already exported, so
they need not be rebuilt (the fix touches nothing but ``tileset.json`` -
the .glb payloads are unaffected).

Only axis-aligned boxes are handled: the off-diagonal half-axis entries
must be zero, otherwise the file is left alone rather than corrupted.

Usage::

    python patch_tileset_bv.py path/to/tileset.json [more.json ...]
    python patch_tileset_bv.py --audit-only path/to/tileset.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Iterator

BACKUP_SUFFIX = ".bak-prebvfix"

# Half-axis entries that must vanish for the box to be axis-aligned:
# [cx, cy, cz, hx, 0, 0, 0, hy, 0, 0, 0, hz].
_OFF_DIAGONAL = (4, 5, 6, 8, 9, 10)
_DIAGONAL = (3, 7, 11)


# ----------------------------------------------------------------------
# Box <-> min/max
# ----------------------------------------------------------------------

def box_to_minmax(box: list[float], where: str) -> tuple[float, ...]:
    """(xmin, ymin, zmin, xmax, ymax, zmax) for an axis-aligned 3D Tiles
    box, refusing anything rotated or malformed."""
    if len(box) != 12:
        raise ValueError(f"{where}: box has {len(box)} entries, expected 12")
    for k in _OFF_DIAGONAL:
        if box[k] != 0:
            raise ValueError(
                f"{where}: box is not axis-aligned (entry {k} = {box[k]!r}); "
                "refusing to patch")
    cx, cy, cz = box[0], box[1], box[2]
    hx, hy, hz = abs(box[3]), abs(box[7]), abs(box[11])
    return (cx - hx, cy - hy, cz - hz, cx + hx, cy + hy, cz + hz)


def minmax_into_box(bv: tuple[float, ...], box: list[float]) -> None:
    """Write *bv* back into *box* in place, touching only the centre and
    the three diagonal half-axes so the zero entries keep their exact
    JSON spelling."""
    box[0] = (bv[0] + bv[3]) / 2
    box[1] = (bv[1] + bv[4]) / 2
    box[2] = (bv[2] + bv[5]) / 2
    box[3] = (bv[3] - bv[0]) / 2
    box[7] = (bv[4] - bv[1]) / 2
    box[11] = (bv[5] - bv[2]) / 2


def union(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, ...]:
    return (min(a[0], b[0]), min(a[1], b[1]), min(a[2], b[2]),
            max(a[3], b[3]), max(a[4], b[4]), max(a[5], b[5]))


def overhang(parent: tuple[float, ...], child: tuple[float, ...]) -> float:
    """How far the child's box escapes the parent's, in metres (0 if
    fully contained)."""
    return max(0.0, max(parent[k] - child[k] for k in (0, 1, 2)),
               max(child[k] - parent[k] for k in (3, 4, 5)))


# ----------------------------------------------------------------------
# Tree walking
# ----------------------------------------------------------------------

def walk(tile: dict[str, Any], depth: int = 0
         ) -> Iterator[tuple[int, dict[str, Any]]]:
    yield depth, tile
    for child in tile.get("children", ()):
        yield from walk(child, depth + 1)


def audit(root: dict[str, Any], tol: float = 1e-6, material: float = 1e-3
          ) -> tuple[int, int, int, float, dict[int, int]]:
    """(pairs, violations, material violations, worst overhang, violations
    by parent depth).

    Two counts because the shipped runs mix two very different failures:
    a handful of genuine metre-scale overhangs from the leaf/interior bv
    mismatch, and a long tail of ~1e-5 m ones that are just float32
    rounding in the record-derived interior boxes. *material* separates
    them; the union below removes both.
    """
    pairs = violations = big = 0
    worst = 0.0
    by_depth: dict[int, int] = {}
    for depth, tile in walk(root):
        pbv = box_to_minmax(tile["boundingVolume"]["box"], f"depth {depth}")
        for child in tile.get("children", ()):
            cbv = box_to_minmax(child["boundingVolume"]["box"],
                                f"depth {depth + 1}")
            pairs += 1
            over = overhang(pbv, cbv)
            if over > tol:
                violations += 1
                by_depth[depth] = by_depth.get(depth, 0) + 1
            if over > material:
                big += 1
            worst = max(worst, over)
    return pairs, violations, big, worst, by_depth


def audit_root(root: dict[str, Any], tol: float = 1e-6,
               material: float = 1e-3) -> tuple[int, int, float]:
    """(descendants escaping the ROOT box, of those the material ones,
    worst escape)."""
    rbv = box_to_minmax(root["boundingVolume"]["box"], "root")
    bad = big = 0
    worst = 0.0
    for depth, tile in walk(root):
        if depth == 0:
            continue
        over = overhang(rbv, box_to_minmax(tile["boundingVolume"]["box"],
                                           f"depth {depth}"))
        if over > tol:
            bad += 1
        if over > material:
            big += 1
        worst = max(worst, over)
    return bad, big, worst


def patch_tree(tile: dict[str, Any], depth: int = 0) -> tuple[float, ...]:
    """Bottom-up union: expand *tile*'s box to cover its own extent plus
    every child's (already-unioned) box. Returns the resulting min/max."""
    box = tile["boundingVolume"]["box"]
    bv = box_to_minmax(box, f"depth {depth}")
    for child in tile.get("children", ()):
        bv = union(bv, patch_tree(child, depth + 1))
    minmax_into_box(bv, box)
    return bv


# ----------------------------------------------------------------------
# Structural comparison - nothing but boxes may change
# ----------------------------------------------------------------------

def diff_outside_boxes(a: Any, b: Any, path: str = "$") -> list[str]:
    """Every difference between two loaded tilesets that is NOT inside a
    boundingVolume box array."""
    if path.endswith('["boundingVolume"]["box"]'):
        return []
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return [f"{path}: keys {sorted(a)} != {sorted(b)}"]
        out: list[str] = []
        for k in a:
            out += diff_outside_boxes(a[k], b[k], f'{path}["{k}"]')
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: length {len(a)} != {len(b)}"]
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += diff_outside_boxes(x, y, f"{path}[{i}]")
        return out
    if type(a) is not type(b) or a != b:
        return [f"{path}: {a!r} != {b!r}"]
    return []


# ----------------------------------------------------------------------

def process(path: Path, audit_only: bool = False) -> bool:
    print(f"\n=== {path} ===")
    if not path.is_file():
        print("  MISSING - skipped")
        return False

    original_text = path.read_text(encoding="utf-8")
    before = json.loads(original_text)
    root = before["root"]

    n_nodes = sum(1 for _ in walk(root))
    uris_before = [t["content"]["uri"] for _, t in walk(root)
                   if "content" in t]
    transform_before = json.dumps(root.get("transform"))

    pairs, viol, big, worst, by_depth = audit(root)
    r_bad, r_big, r_worst = audit_root(root)
    print(f"  before: {n_nodes} nodes, {pairs} parent->child pairs, "
          f"{viol} violations ({big} above 1 mm), "
          f"worst overhang {worst:.2f} m")
    if by_depth:
        print("          violations by parent depth: "
              + ", ".join(f"{d}: {n}" for d, n in sorted(by_depth.items())))
    print(f"          root containment: {r_bad} escaping descendants "
          f"({r_big} above 1 mm), worst {r_worst:.2f} m")
    if audit_only:
        return viol == 0 and r_bad == 0

    after = json.loads(original_text)  # patch a fresh copy
    patch_tree(after["root"])

    backup = path.with_suffix(path.suffix + BACKUP_SUFFIX)
    shutil.copy2(path, backup)
    tmp = path.with_suffix(path.suffix + ".tmp-bvfix")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(after, f, indent=2)
    tmp.replace(path)
    print(f"  backup: {backup.name}")

    # -- re-read from disk and verify -------------------------------
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    root2 = reloaded["root"]
    pairs2, viol2, big2, worst2, _ = audit(root2)
    r_bad2, r_big2, r_worst2 = audit_root(root2)
    n_nodes2 = sum(1 for _ in walk(root2))
    uris_after = [t["content"]["uri"] for _, t in walk(root2)
                  if "content" in t]
    transform_after = json.dumps(root2.get("transform"))
    other = diff_outside_boxes(before, reloaded)

    print(f"  after:  {n_nodes2} nodes, {pairs2} parent->child pairs, "
          f"{viol2} violations ({big2} above 1 mm), "
          f"worst overhang {worst2:.2e} m")
    print(f"          root containment: {r_bad2} escaping descendants "
          f"({r_big2} above 1 mm), worst {r_worst2:.2e} m")

    checks = [
        ("round-trips (loads cleanly)", True),
        ("node count unchanged", n_nodes2 == n_nodes),
        ("pair count unchanged", pairs2 == pairs),
        ("content URIs unchanged", uris_after == uris_before),
        ("root.transform byte-identical",
         transform_after == transform_before),
        ("nothing changed outside boxes", not other),
        ("0 parent->child violations", viol2 == 0),
        ("0 descendants escaping root", r_bad2 == 0),
    ]
    ok = True
    for label, passed in checks:
        print(f"  [{'ok ' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    for line in other[:10]:
        print(f"        unexpected change: {line}")
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tilesets", nargs="+", type=Path)
    ap.add_argument("--audit-only", action="store_true",
                    help="report containment without writing anything")
    args = ap.parse_args(argv)

    ok = True
    for p in args.tilesets:
        try:
            ok &= process(p, audit_only=args.audit_only)
        except ValueError as exc:  # non-axis-aligned / malformed box
            print(f"  REFUSED: {exc}")
            ok = False
    print("\nALL OK" if ok else "\nPROBLEMS FOUND")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
