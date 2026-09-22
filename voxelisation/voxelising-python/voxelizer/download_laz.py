"""
Download Grand Lyon 2023 LiDAR (.laz) tiles for an RGF93/CC46 (EPSG:3946) bounding box.
@ingroup t0_socle


Reads the inventory JSON and streams every tile whose origin falls inside
the requested rectangle into an output directory (default ``inputs/laz/``).

Why this is its own module rather than a switch on one downloader: what
``_download_common`` shares with ``download_orthos`` is the per-file fetch
(``download_one``: one URL to one path, with its timeout and retries), the
inventory resolver, the inventory reader, the bounding-box validation, the
tile selection and the default worker count. The multi-tile orchestration
below is NOT shared: dry-run listing, job list, thread pool, skip and fail
accounting, progress and summary printing are a name-swapped twin of
``download_orthos`` (57 lines each, 53 of them byte-identical, longest
identical run 35 lines). Only the per-dataset surface differs - the Grand
Lyon 2023 LiDAR inventory, the ``inputs/laz/`` destination, the ``[LAZ]``
log tag and this module's own ``-m`` entry point - so each command stays
usable on its own. The twin is kept in pairs: change one and check the
other.

Examples
--------
    # Preview which tiles the default box selects
    python -m voxelizer.download_laz --dry-run

    # Download a 1 km area
    python -m voxelizer.download_laz \
        --xmin-start 1831000 --xmin-end 1832000 \
        --ymin-start 5175000 --ymin-end 5176000

    # Limit to 5 tiles (safety valve)
    python -m voxelizer.download_laz --limit 5 ...

    # NOTE: must be run with ``-m`` - the module uses package-relative
    # imports, so ``python voxelizer/download_laz.py`` raises ImportError
"""

from __future__ import annotations
import argparse
import concurrent.futures
import sys
import time
from pathlib import Path

from ._download_common import (
    default_workers,
    download_one,
    load_values,
    resolve_inventory,
    select_tiles,
    validate_bbox,
)

# ---------------------------------------------------------------------------
# Defaults - resolve relative to the *package* so ``python -m voxelizer``
# works from any working directory.  Callers can always override with --json /
# --laz-dir on the CLI.
# ---------------------------------------------------------------------------
_PKG_DIR = Path(__file__).resolve().parent          # .../voxelizer
_INPUTS_DIR = _PKG_DIR.parent / "inputs"            # .../inputs

# The inventory JSON sits under ``inputs/quickhelpers/`` in the development
# tree and at the repository root in the delivered tree; the resolver picks
# whichever exists.
DEFAULT_JSON = resolve_inventory(
    "nuage-de-points-lidar-2023-de-la-metropole-de-lyon.json")
DEFAULT_LAZ_DIR = _INPUTS_DIR / "laz"

# A small ~1 km box so a no-argument run stays tiny.
DEFAULT_XMIN_START = 1831000
DEFAULT_XMIN_END = 1832000
DEFAULT_YMIN_START = 5175000
DEFAULT_YMIN_END = 5176000


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def download_laz(
    bbox: tuple[int, int, int, int],
    out_dir: Path,
    json_file: Path,
    *,
    workers: int | None = None,
    limit: int = 0,
    dry_run: bool = False,
) -> int:
    """Download LAZ tiles inside *bbox*. Returns the number of failures."""
    if workers is None:
        workers = default_workers()
    validate_bbox(*bbox)
    values = load_values(json_file)
    selected = select_tiles(values, *bbox)
    if limit > 0:
        selected = selected[:limit]

    print(f"[LAZ] {len(selected)} tile(s) selected"
          + (f" (capped at {limit})" if limit > 0 else "") + ".")
    if not selected:
        return 0

    if dry_run:
        for t in selected:
            print(f"  would fetch {t.get('nom', '?')}  <-  {t['url'].strip()}")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = [
        (t["url"].strip(), out_dir / Path(t["url"].strip()).name)
        for t in selected
    ]

    failures: list[tuple[str, str | None]] = []
    skipped = downloaded = 0
    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(download_one, url, dest) for url, dest in jobs]
        for i, fut in enumerate(concurrent.futures.as_completed(futures), start=1):
            name, status, err = fut.result()
            if status == "ok":
                downloaded += 1
            elif status == "skip":
                skipped += 1
            else:
                failures.append((name, err))
                print(f"  [{i}/{len(jobs)}] FAILED {name}: {err}")
            if status != "FAILED" and (i % 10 == 0 or i == len(jobs)):
                elapsed = time.time() - start
                rate = i / elapsed if elapsed > 0 else 0.0
                print(f"  [{i}/{len(jobs)}] {downloaded} new, {skipped} existing "
                      f"({rate:.1f} tiles/s)")

    print(f"[LAZ] done: {downloaded} downloaded, {skipped} already present, "
          f"{len(failures)} failed -> {out_dir}")
    return len(failures)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Build the parser (bounding-box ints, ``--json``, ``--laz-dir``, ``--workers``, ``--limit``, ``--dry-run``) and parse *argv*."""
    p = argparse.ArgumentParser(
        prog="download_laz",
        description="Download Grand Lyon 2023 LiDAR tiles for a "
                    "RGF93/CC46 (EPSG:3946) bounding box.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--xmin-start", type=int, default=DEFAULT_XMIN_START)
    p.add_argument("--xmin-end", type=int, default=DEFAULT_XMIN_END)
    p.add_argument("--ymin-start", type=int, default=DEFAULT_YMIN_START)
    p.add_argument("--ymin-end", type=int, default=DEFAULT_YMIN_END)
    p.add_argument("--json", type=Path, default=DEFAULT_JSON,
                   help="LiDAR inventory JSON.")
    p.add_argument("--laz-dir", type=Path, default=DEFAULT_LAZ_DIR,
                   help="Destination for .laz tiles.")
    # ArgumentDefaultsHelpFormatter appends "(default: %(default)s)" to any
    # help that does not already mention %(default)s - which here would
    # print a second, contradictory default ("None") after the real one.
    # Naming %(default)s inside the text suppresses that and states what
    # the None sentinel means.
    p.add_argument("--workers", type=int, default=None,
                   help=f"Parallel download threads. Left unset "
                        f"(%(default)s) it is min(32, cpu+4), which is "
                        f"{default_workers()} on this machine.")
    p.add_argument("--limit", type=int, default=0,
                   help="Cap the number of tiles (0 = no cap).")
    p.add_argument("--dry-run", action="store_true",
                   help="List the selected tiles and exit without downloading.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point: parse the bounding-box and download flags, print the box and run download_laz().

    Returns None on success and raises ``SystemExit(1)`` (after a message on
    stderr) when any tile failed to download.
    """
    args = _parse_args(argv)
    bbox = (args.xmin_start, args.xmin_end, args.ymin_start, args.ymin_end)

    print(f"Bounding box (RGF93/CC46 EPSG:3946): "
          f"x [{args.xmin_start}, {args.xmin_end}], "
          f"y [{args.ymin_start}, {args.ymin_end}]")

    failures = download_laz(
        bbox, args.laz_dir, args.json,
        workers=args.workers, limit=args.limit, dry_run=args.dry_run,
    )
    if failures:
        print(f"\n{failures} tile(s) failed to download.", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
