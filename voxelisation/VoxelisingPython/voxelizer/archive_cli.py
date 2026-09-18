"""
Archive a sharded run's per-tile stores as exact LAS/LAZ, and restore them.
@ingroup t4_entrees


    python -m voxelizer.archive_cli pack <run_dir>
        [--workers N] [--no-verify] [--replace] [--epsg CODE]

    python -m voxelizer.archive_cli unpack <archive_dir>
        [--out DIR] [--workers N]

``--workers`` defaults to 1 in both. ``pack`` verifies each round trip unless
``--no-verify`` is given, and only ``--replace`` deletes the ``.npz`` it came
from - and only for a shard that verified, because ``main`` refuses
``--replace --no-verify``. That refusal is the CLI's, not the library's: a
direct ``pack(verify=False, replace=True)`` call would delete unverified
files. ``unpack`` writes beside the archive unless ``--out`` says otherwise.

Why this exists
---------------
``shards/*.npz`` is a numpy zip of six flat arrays whose meaning lives only in
this repository. ``shards_laz/*.laz`` is the same information in a format any
GIS tool opens, and it converts back **bit-exactly** - verified per shard before
anything is deleted.

Size depends on how fine the grid is: a ``.npz`` costs ~4 B per INTERVAL and
interval count grows with grid fineness, while the LAZ writes a point count
fixed by the source tile (per-point cost varies with resolution, measured
0.13-1.1 B/point): measured 1.65x the ``.npz`` at 1 m, 1.17x at
0.5 m, and 0.65x (i.e. smaller) at 0.25 m xy / 0.1 m z on a 61M-point tile - down
to 0.29x on a sparse one. Treat it as an interoperability / longevity move that
happens to pay for itself at fine LODs, not as a compression scheme. See
``ReadMEs/laz_roundtrip_design.md`` for the full rationale.

Why SHARDS and never area.npz
-----------------------------
``shard_worker`` saves each tile straight out of ``voxelize`` with no grouping
(grouping happens in memory at merge time), so every shard is a RAW store -
the canonical run-length encoding of its own cell set, and therefore a fixed
point of the expand/re-voxelize round trip.

``area.npz`` is the GROUPED store, and grouping is not reliably invertible.
Most merged intervals end up with ``count < height`` (gap voxels add height,
not points) and the exact export refuses them; but a merge whose summed count
still covers its height, with no foreign class inside the span, round-trips
perfectly well. Nothing on the store says which kind a given column is.

So ``pack`` does not try to decide per store: it refuses anything that is not
``shards/``, which ``shard_worker`` guarantees is raw. That costs nothing -
the merge from shards is deterministic, so ``area.npz`` is regenerated rather
than archived, which is exactly why the grouping settings are now recorded in
``shards/manifest.json`` (see ``run_utils.make_provenance``).

What is NOT archived
--------------------
The original IGN tiles. An exact-LAZ is a faithful container for the STORE at
the grid it was built on: it can be re-voxelized to any integer-multiple
COARSER grid, but never to a finer one, because every point in it sits at a
voxel centre. Coarser is POSSIBLE, not faithful: ``mode="exact"`` parks each
interval's ``count - (z_end - z_start)`` padding points in the interval's
FIRST voxel, so a coarse re-voxelization reproduces the occupied-voxel
geometry but not the per-coarse-voxel point counts that voxelizing the source
points at that grid would give. Keeping this instead of the source tiles is a
bet that the grid geometry stays fixed.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

from .data_structures import ColumnStore
from .io_laz import DEFAULT_EPSG
from contextlib import contextmanager

from .reconstruct import store_from_laz, store_to_las, stores_equal

logger = logging.getLogger(__name__)

SHARDS_DIRNAME = "shards"
ARCHIVE_DIRNAME = "shards_laz"
ARCHIVE_MANIFEST = "archive_manifest.json"
ARCHIVE_SCHEMA = "voxelizer.shard_archive/1"


# ---------------------------------------------------------------------------
# per-shard workers (module-level so ProcessPoolExecutor can pickle them)
# ---------------------------------------------------------------------------
def _pack_one(args) -> dict:
    """npz -> laz for one shard, verifying the round trip before reporting ok.

    Verification happens only when ``verify`` is set - this function does
    exactly what its argument says and never verifies on its own account.
    (The ``pack`` subcommand passes ``not --no-verify``, so a plain ``pack``
    does verify; a direct caller of this function decides for itself.)

    It reads the file back and rebuilds the store, roughly tripling the
    per-shard cost (~2.7 s export vs ~7.0 s rebuild on a median IGN tile),
    and it is the only thing standing between a silent grid mismatch and a
    deleted ``.npz``.
    """
    npz_path, laz_path, epsg, verify, replace = args
    npz_path, laz_path = Path(npz_path), Path(laz_path)
    try:
        store = ColumnStore.load(npz_path)
        if not store.columns:
            return {"file": npz_path.name, "ok": True, "skipped": "empty"}
        # Atomic: a killed process must never leave a truncated .laz that a
        # later unpack would trust (same discipline as _atomic_shard_save).
        tmp = laz_path.with_name("_tmp_" + laz_path.name)
        store_to_las(store, tmp, mode="exact", epsg=epsg)
        os.replace(tmp, laz_path)

        rec = {"file": npz_path.name, "laz": laz_path.name,
               "n_columns": len(store.columns),
               "n_intervals": int(store.n_intervals),
               "npz_bytes": npz_path.stat().st_size,
               "laz_bytes": laz_path.stat().st_size, "ok": True}
        if verify:
            rebuilt = store_from_laz(
                laz_path, origin=(store.x_min, store.y_min, store.z_min),
                cell_xy=store.cell_xy, cell_z=store.cell_z)
            ok, diffs = stores_equal(store, rebuilt)
            rec["ok"] = ok
            if not ok:
                rec["diffs"] = diffs
                laz_path.unlink(missing_ok=True)
                return rec
        if replace and rec["ok"]:
            npz_path.unlink()
            rec["npz_deleted"] = True
        return rec
    except Exception as exc:  # noqa: BLE001 - one bad shard must not kill the run
        return {"file": npz_path.name, "ok": False,
                "error": f"{type(exc).__name__}: {exc}"}


def _unpack_one(args) -> dict:
    """laz -> npz for one shard."""
    laz_path, npz_path = Path(args[0]), Path(args[1])
    try:
        store = store_from_laz(laz_path)
        tmp = npz_path.with_name("_tmp_" + npz_path.name)
        store.save(tmp)
        os.replace(tmp, npz_path)
        return {"file": laz_path.name, "npz": npz_path.name, "ok": True,
                "n_columns": len(store.columns),
                "n_intervals": int(store.n_intervals)}
    except Exception as exc:  # noqa: BLE001
        return {"file": laz_path.name, "ok": False,
                "error": f"{type(exc).__name__}: {exc}"}


@contextmanager
def _forced_run_order(order: str):
    """Re-voxelize under the order that built the archived data.

    ``store_from_laz`` re-voxelizes, and the run-forming order shapes the
    interval structure it produces, so pack's verify and unpack must run
    under the order recorded in the source manifest - an archive written
    under one order is unreadable-as-identical under the other. The
    variable is set process-wide so parallel workers inherit it, and
    restored afterwards.

    ``reconstruct._forced_run_order`` is the same context manager with one
    extra branch: it accepts ``None`` and then leaves the environment
    untouched, for callers that predate the parameter. This copy takes only a
    real order, because pack and unpack always have one recorded in the source
    manifest, and it keeps the archive commands' environment handling in the
    module that owns them. Nothing forces the copy: this module already
    imports from ``reconstruct``, so the other one could be called with a
    non-None order for the same effect. The bodies are otherwise identical;
    change one and check the other.
    """
    prev = os.environ.get("VOXELIZER_RUN_ORDER")
    os.environ["VOXELIZER_RUN_ORDER"] = order
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("VOXELIZER_RUN_ORDER", None)
        else:
            os.environ["VOXELIZER_RUN_ORDER"] = prev


def _recorded_order(manifest: dict) -> str:
    """The run order a manifest records; absent means pre-toggle height_first."""
    return str(manifest.get("run_order") or "height_first")


def _run_parallel(fn, jobs, workers: int) -> list[dict]:
    """Map ``fn`` over ``jobs``, in-process when workers <= 1.

    Shards are independent, so this is embarrassingly parallel - the same
    shape as the sharded runner's per-tile child processes.
    """
    if workers <= 1 or len(jobs) <= 1:
        return [fn(j) for j in jobs]
    out: list[dict] = []
    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(fn, j) for j in jobs]
            for fut in as_completed(futures):
                out.append(fut.result())
    except BrokenProcessPool as exc:
        # Windows (and macOS) spawn workers by RE-IMPORTING the caller's
        # __main__ module. A caller script without an `if __name__ ==
        # "__main__":` guard therefore re-runs itself in every worker, which
        # surfaces here as an opaque BrokenProcessPool. Name the cause instead.
        raise RuntimeError(
            f"parallel workers died ({exc}). On spawn-based platforms every "
            f"worker re-imports the calling module, so a script that calls "
            f"pack()/unpack() at import time must guard its entry point with "
            f"`if __name__ == \"__main__\":`. Re-run with workers=1 to "
            f"confirm, or add the guard.") from exc
    return out


# ---------------------------------------------------------------------------
# pack / unpack
# ---------------------------------------------------------------------------
def _resolve_shards_dir(run_dir: Path) -> Path:
    """Accept either the run directory or the ``shards/`` directory itself."""
    run_dir = Path(run_dir)
    if (run_dir / "manifest.json").is_file() and run_dir.name == SHARDS_DIRNAME:
        return run_dir
    cand = run_dir / SHARDS_DIRNAME
    if (cand / "manifest.json").is_file():
        return cand
    raise SystemExit(
        f"{run_dir} is not a sharded run: expected {cand}/manifest.json. "
        f"Only the RAW per-tile shards can be archived exactly - a grouped "
        f"area.npz cannot (see the module docstring).")


def pack(run_dir: Path | str, *, workers: int = 1, verify: bool = True,
         replace: bool = False, epsg: int | None = None) -> dict:
    """Convert every ``shards/*.npz`` to an exact ``shards_laz/*.laz``.

    Returns the archive manifest (also written to
    ``shards_laz/archive_manifest.json``).

    ``replace`` requires ``verify``: the module docstring's guarantee that a
    ``.npz`` is deleted only for a shard that verified must hold for library
    callers (``voxelizer.pack_shards``) exactly as it does for the CLI, so
    the refusal lives here and not only behind the flag parser.
    """
    if replace and not verify:
        raise ValueError(
            "pack(replace=True, verify=False) would delete the only exact "
            "copy of the data on the strength of an unchecked conversion; "
            "keep verify=True when replace is set.")
    shards_dir = _resolve_shards_dir(run_dir)
    out_dir = shards_dir.parent / ARCHIVE_DIRNAME
    out_dir.mkdir(parents=True, exist_ok=True)

    src = json.loads((shards_dir / "manifest.json").read_text(encoding="utf-8"))
    if epsg is None:
        # Prefer the run's own recorded CRS; fall back to the project default
        # for manifests written before provenance existed.
        epsg = int(src.get("epsg") or DEFAULT_EPSG)

    listed = [s["file"] for s in src.get("shards", [])]
    present = [p.name for p in sorted(shards_dir.glob("*.npz"))
               if not p.name.startswith("_tmp_")]
    files = [f for f in listed if f in set(present)] or present
    missing = sorted(set(listed) - set(present))
    if missing:
        logger.warning("%d shard(s) listed in the manifest are missing on "
                       "disk and will not be archived: %s", len(missing),
                       ", ".join(missing[:5]) + (" ..." if len(missing) > 5 else ""))
    if not files:
        raise SystemExit(f"no .npz shards found in {shards_dir}")

    logger.info("packing %d shard(s) from %s -> %s (verify=%s, workers=%d)",
                len(files), shards_dir, out_dir, verify, workers)
    jobs = [(shards_dir / f, out_dir / (Path(f).stem + ".laz"), epsg, verify,
             replace) for f in files]
    with _forced_run_order(_recorded_order(src)):
        results = _run_parallel(_pack_one, jobs, workers)
    results.sort(key=lambda r: r["file"])

    bad = [r for r in results if not r.get("ok")]
    npz_b = sum(r.get("npz_bytes", 0) for r in results)
    laz_b = sum(r.get("laz_bytes", 0) for r in results)
    manifest = {
        "schema": ARCHIVE_SCHEMA,
        "source_manifest": str(shards_dir / "manifest.json"),
        "verified": bool(verify),
        "epsg": int(epsg),
        "export_mode": "exact",
        "n_shards": len(results),
        "n_failed": len(bad),
        "npz_bytes_total": npz_b,
        "laz_bytes_total": laz_b,
        "size_ratio": (laz_b / npz_b) if npz_b else None,
        # Everything a rebuild needs that is not in the shard files: the
        # lattice is in each file's IARBRE VLR, the grouping settings are not.
        "grid": {k: src.get(k) for k in ("bbox", "clip", "cell_xy", "cell_z",
                                         "origin", "height_mode")},
        "provenance": {k: src.get(k) for k in
                       ("provenance_schema", "group_intervals", "group_gap_m",
                        "keep_classes", "epsg", "run_order")},
        "shards": results,
    }
    (out_dir / ARCHIVE_MANIFEST).write_text(json.dumps(manifest, indent=2),
                                            encoding="utf-8")
    # Keep the source manifest beside the archive: `unpack` restores the
    # shards, and the run's own consumers (merge, resume) need it verbatim.
    (out_dir / "manifest.json").write_text(json.dumps(src, indent=2),
                                           encoding="utf-8")

    if bad:
        for r in bad:
            logger.error("  %s: %s", r["file"],
                         r.get("error") or "; ".join(r.get("diffs", [])))
        logger.error("packed with %d FAILURE(S) - see %s",
                     len(bad), out_dir / ARCHIVE_MANIFEST)
    else:
        logger.info("packed %d shard(s): %.1f MB npz -> %.1f MB laz (%.2fx)%s",
                    len(results), npz_b / 1e6, laz_b / 1e6,
                    (laz_b / npz_b) if npz_b else 0.0,
                    ", verified exact" if verify else " (NOT verified)")
    return manifest


def unpack(archive_dir: Path | str, *, out: Path | str | None = None,
           workers: int = 1) -> dict:
    """Rebuild ``shards/*.npz`` from an archive produced by pack()."""
    archive_dir = Path(archive_dir)
    if archive_dir.name != ARCHIVE_DIRNAME and \
            (archive_dir / ARCHIVE_DIRNAME).is_dir():
        archive_dir = archive_dir / ARCHIVE_DIRNAME
    if not (archive_dir / ARCHIVE_MANIFEST).is_file():
        raise SystemExit(f"{archive_dir} has no {ARCHIVE_MANIFEST} - not a "
                         f"shard archive.")
    out_dir = Path(out) if out else archive_dir.parent / SHARDS_DIRNAME
    out_dir.mkdir(parents=True, exist_ok=True)

    files = [p for p in sorted(archive_dir.glob("*.laz"))
             if not p.name.startswith("_tmp_")]
    if not files:
        raise SystemExit(f"no .laz files in {archive_dir}")
    logger.info("unpacking %d file(s) from %s -> %s (workers=%d)",
                len(files), archive_dir, out_dir, workers)
    jobs = [(p, out_dir / (p.stem + ".npz")) for p in files]
    try:
        src = json.loads((archive_dir / "manifest.json")
                         .read_text(encoding="utf-8"))
    except OSError:
        src = {}
    with _forced_run_order(_recorded_order(src)):
        results = _run_parallel(_unpack_one, jobs, workers)
    results.sort(key=lambda r: r["file"])

    src_mf = archive_dir / "manifest.json"
    if src_mf.is_file():
        (out_dir / "manifest.json").write_text(
            src_mf.read_text(encoding="utf-8"), encoding="utf-8")
        logger.info("restored shards/manifest.json - the run's merge and "
                    "--resume-shards paths work again")
    else:
        logger.warning("archive has no manifest.json; the restored shards/ "
                       "will need one before --resume-shards can use it.")

    bad = [r for r in results if not r.get("ok")]
    for r in bad:
        logger.error("  %s: %s", r["file"], r.get("error"))
    logger.info("unpacked %d/%d shard(s)%s", len(results) - len(bad),
                len(results), " with FAILURES" if bad else "")
    return {"schema": ARCHIVE_SCHEMA, "n_shards": len(results),
            "n_failed": len(bad), "out_dir": str(out_dir), "shards": results}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    """Build the parser with the ``pack`` (``run_dir``, ``--workers``,
    ``--no-verify``, ``--replace``, ``--epsg``) and ``unpack``
    (``archive_dir``, ``--out``, ``--workers``) sub-commands."""
    p = argparse.ArgumentParser(
        prog="python -m voxelizer.archive_cli",
        description="Archive a sharded run's raw per-tile stores as exact "
                    "LAS/LAZ, and restore them.")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("pack", help="shards/*.npz -> shards_laz/*.laz")
    a.add_argument("run_dir", type=Path,
                   help="Run directory (or its shards/ directory)")
    a.add_argument("--workers", type=int, default=1,
                   help="Parallel shard workers (default: 1)")
    a.add_argument("--no-verify", action="store_true",
                   help="Skip the per-shard rebuild-and-compare check. Faster "
                        "(~3x) and strictly worse: verification is what makes "
                        "--replace safe.")
    a.add_argument("--replace", action="store_true",
                   help="Delete each source .npz once its .laz has verified. "
                        "Refused together with --no-verify.")
    a.add_argument("--epsg", type=int, default=None,
                   help="Override the CRS written into the archive files "
                        "(default: the run's recorded EPSG, else "
                        f"{DEFAULT_EPSG})")

    b = sub.add_parser("unpack", help="shards_laz/*.laz -> shards/*.npz")
    b.add_argument("archive_dir", type=Path,
                   help="Archive directory (or the run directory above it)")
    b.add_argument("--out", type=Path, default=None,
                   help="Destination for the rebuilt shards "
                        "(default: <run>/shards)")
    b.add_argument("--workers", type=int, default=1)
    return p


def main(argv: list[str] | None = None) -> int:
    """Parse the ``pack`` or ``unpack`` verb and run pack() or
    unpack() with its flags. ``pack --replace --no-verify`` is refused
    with ``SystemExit``. Returns 1 when any shard failed, else 0."""
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.cmd == "pack":
        if args.replace and args.no_verify:
            raise SystemExit(
                "--replace with --no-verify would delete the only exact copy "
                "of the data on the strength of an unchecked conversion. "
                "Drop one of the two.")
        mf = pack(args.run_dir, workers=args.workers,
                  verify=not args.no_verify, replace=args.replace,
                  epsg=args.epsg)
        return 1 if mf["n_failed"] else 0

    res = unpack(args.archive_dir, out=args.out, workers=args.workers)
    return 1 if res["n_failed"] else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
