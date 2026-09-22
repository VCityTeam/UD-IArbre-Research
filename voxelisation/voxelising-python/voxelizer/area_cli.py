"""
CLI for the coordinate-driven, whole-area voxelizer.
@ingroup t4_entrees


One subcommand, ``area``. The full declared flag set, grouped by what it
controls:

    python -m voxelizer.area_cli area --xmin 1831000 --ymin 5175000 \
        --xmax 1832000 --ymax 5176000 --laz-dir inputs/laz --output-dir OUT

    grid + reading
        [--cell-xy 0.5] [--cell-z 0.5] [--chunk-size N] [--no-chunks]
        [--no-clip] [--keep-classes 2,3,4,5,6] [--height-mode MODE]
    tile source
        [--download --json INVENTORY.json [--tile-pitch 500] [--workers N]
         [--limit N]]
        [--stream --json INVENTORY.json]
    column diagnostics
        [--columns-mode diag|top|all|skip] [--columns-top-n 50]
        [--columns-all-max N]
    interval grouping
        [--no-group-intervals] [--group-gap METERS]
    3-D viewers
        [--viz3d [--max-boxes N] [--roi-size M] [--roi-cx M] [--roi-cy M]
                 [--no-full] [--no-roi]]
        [--viz3d-stream [--tile-m 64] [--max-instances N]
                        [--inline-threshold-mb 64]]
    memory strategy
        [--preflight-only] [--max-rss-mb MB]
        [--shard [--resume-shards] [--isolate-tiles [--retry-lazrs]]
                 [--merge-shards [--merge-band-intervals N]]]
    stage isolation
        [--no-isolate-stages] [--stage-timeout SECONDS] [--only-stage STAGE]
        [--resume-from-store DIR]
    what survives the run
        [--no-save-store] [--keep-raw-store] [--keep-area-raw]
        [--delete-laz] [--delete-shards] [--intermediates auto|keep|delete]

Streams every tile intersecting the box (``chunk_iterator``) onto one shared
grid with a single vertical datum, clips to the exact rectangle, and writes
the *same* deliverables as the single-tile run - stats.txt, the four 2-D
maps, and the ``columns/`` diagnostics - plus, when ``--viz3d`` is given, the
3-D HTML viewer(s) rendered straight from the merged area store (so the area
is voxelized only once).

SHARD MODE
----------
``--shard`` is a different pipeline, not a tuning flag. Each tile is
voxelized, written to ``shards/<tile>.npz`` and freed, so peak memory is one
tile rather than one merged store, and the deliverables are computed by
folding over the shards: stats.txt, the four mosaicked maps, ``columns/``
(via ``shard_diagnostics``) and both the full and ROI 3-D views are all
produced with no merged store in existence. What that leaves out is anything
that IS the merged store - ``area.npz``, ``area_raw.npz``, ``store_raw/`` and
the streaming viewer - and asking for one of those without ``--merge-shards``
is refused before the first tile is read rather than reconciled silently.
``--merge-shards`` adds the merge, assembled on disk one key band at a time
(``merge_streaming``), after which the output stages attach to it by memory
map. See voxelizer.sharding.

The single-file drag/drop workflow deliberately stays on the whole-file
``laspy.read`` path in ``pipeline.process_single_tile``; this module is the
coordinates -> chunked-area path.
"""

from __future__ import annotations

import argparse
import logging

from .cli_common import add_cell_args, add_viz3d_geom_args, positive_int
import sys
from pathlib import Path

try:
    from .area import process_area, process_area_streamed
    from .data_structures import ColumnStore
    from .io_laz import DEFAULT_EPSG
    from .merge_streaming import DEFAULT_BAND_INTERVALS
    from .run_utils import make_provenance
    from .voxelize import DEFAULT_CHUNK_SIZE
except ImportError:  # allow running the file directly
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from voxelizer.area import process_area, process_area_streamed  # noqa: E402
    from voxelizer.data_structures import ColumnStore  # noqa: E402
    from voxelizer.io_laz import DEFAULT_EPSG  # noqa: E402
    from voxelizer.merge_streaming import DEFAULT_BAND_INTERVALS  # noqa: E402
    from voxelizer.run_utils import make_provenance  # noqa: E402
    from voxelizer.voxelize import DEFAULT_CHUNK_SIZE  # noqa: E402

logger = logging.getLogger(__name__)

# Output writers + per-stage isolation live in area_outputs (shared with
# sharding without a circular dependency). Of the five names pulled in here,
# two are USED below - _write_outputs and _write_outputs_isolated, the pair
# run_area picks between - and three are re-exports for callers that still
# reach for them at this address: render_area_3d, which the package __init__
# advertises as voxelizer.render_area_3d, plus _write_area_store and
# _cleanup_remaining_laz. The package __init__ still binds render_area_3d
# from HERE (`from .area_cli import render_area_3d`), so that re-export is
# load-bearing rather than compatibility; the other two are what keeps an
# older `from voxelizer.area_cli import ...` working.
from .area_outputs import (  # noqa: E402
    render_area_3d,
    _write_area_store,
    _write_outputs,
    _write_outputs_isolated,
    _cleanup_remaining_laz,
)


def run_area(bbox, laz_dir, out_dir, *, cell_xy=0.5, cell_z=0.5,
             keep_classes=None, use_chunks=True, chunk_size=DEFAULT_CHUNK_SIZE,
             clip=True, height_mode="default",
             columns_mode="diag", columns_top_n=50, columns_all_max=None,
             viz3d=False, max_boxes=5_000_000, roi_size=200.0,
             roi_cx=None, roi_cy=None, do_full=True, do_roi=True,
             delete_laz=False, stream=False, json_file=None,
             tile_pitch=500, limit=0,
             max_rss_mb=None, save_store=True,
             group_intervals=True, group_gap=None,
             isolate_stages=False, only_stages=None, stage_timeout=0,
             viz3d_stream=False, max_instances=4_000_000, tile_m=64.0,
             inline_threshold_mb=64) -> ColumnStore:
    """Programmatic entry point (used by the GUI). Returns the area store.

    ``tile_pitch`` and ``limit`` only affect streaming mode (they control
    the inventory tile selection radius and the tile-count cap in
    process_area_streamed()). In non-stream mode, tile selection is
    extent-based and these are ignored; the CLI's ``--download`` pre-fetch
    consumes them separately via ``_maybe_download``.

    ``max_rss_mb`` - RAM watchdog: abort the tile loop
    cleanly when this process's RSS crosses the budget; the partial store is
    still written out, with a PARTIAL banner in stats.txt.

    @param bbox ``(xmin, ymin, xmax, ymax)`` in metres (RGF93/CC46 EPSG:3946).
    @param laz_dir Directory with local ``.laz``/``.las`` files
        (``stream=False``); in stream mode its parent receives the
        ``templaz/`` download directory.
    @param out_dir Output directory for the persisted store and deliverables.
    @param cell_xy Horizontal voxel size in metres (default 0.5).
    @param cell_z Vertical voxel size in metres (default 0.5).
    @param keep_classes When set, keep only points carrying these ASPRS
        codes; ``None`` (the default) keeps every class.
    @param use_chunks Stream each tile with ``chunk_iterator`` (True, the
        default) or read it whole (False).
    @param chunk_size Points per chunk when ``use_chunks`` is on (default
        ``DEFAULT_CHUNK_SIZE``).
    @param clip Drop points outside ``bbox`` so the result is exactly the
        rectangle (default True).
    @param height_mode 'default' / 'relative' / 'absolute' - the vertical
        reference of the max_height map (default 'default'), also recorded in
        the stats.txt header.
    @param columns_mode Column diagnostics mode (default 'diag'): 'diag',
        'top', 'all' or 'skip'.
    @param columns_top_n Number of top columns to write when
        ``columns_mode='top'`` (default 50).
    @param columns_all_max Max columns to write when ``columns_mode='all'``;
        ``None`` (the default) applies the module cap.
    @param viz3d When True, build the 3-D HTML view(s) (default False).
    @param max_boxes Box budget for the full-area 3-D view (default
        5,000,000).
    @param roi_size Side length in metres of the region-of-interest 3-D view
        (default 200.0).
    @param roi_cx ROI centre x coordinate (RGF93/CC46); ``None`` uses the
        area centre.
    @param roi_cy ROI centre y coordinate (RGF93/CC46); ``None`` uses the
        area centre.
    @param do_full When True (the default) and ``viz3d=True``, render the
        full-area 3-D view.
    @param do_roi When True (the default) and ``viz3d=True``, render the ROI
        3-D view.
    @param delete_laz DESTRUCTIVE (default False). Unlink each source tile
        once it has been voxelized.
    @param stream When True, download each tile from a Grand Lyon inventory
        JSON (requires ``json_file``); when False, read local files from
        ``laz_dir`` (default False).
    @param json_file Path to the Grand Lyon inventory JSON, required when
        ``stream=True``.
    @param tile_pitch Tile size in metres of the acquisition grid (default
        500); streaming mode only.
    @param limit Max tiles to process (default 0 = no limit); streaming mode
        only.
    @param max_rss_mb Hard RSS limit in MB (``None``, the default, disables
        it). If exceeded during the tile loop the run aborts cleanly and the
        partial store is written with a PARTIAL banner in stats.txt.
    @param save_store Write the persisted store grid (default True).
    @param group_intervals When True (the default), coalesce adjacent
        same-class intervals before writing outputs; the raw store is saved
        first as ``area_raw.npz`` when ``save_store`` is on.
    @param group_gap Gap tolerance in METRES for that grouping (``None`` =
        no gap limit).
    @param isolate_stages When True, run the output stages in isolated child
        processes (default False).
    @param only_stages If set, restricts the isolated stages to these stage
        names.
    @param stage_timeout Wall-clock limit in seconds for each isolated output
        stage (default 0 = disabled).
    @param viz3d_stream When True, render the streaming HTML viewer instead
        of the legacy singleton one (default False).
    @param max_instances Hard GPU instance cap for that viewer (default
        4,000,000).
    @param tile_m Streaming viewer payload tile size in metres (default
        64.0).
    @param inline_threshold_mb If the ``.bin`` is smaller than this many MB
        (default 64), it is base64-embedded in the HTML and the sidecar is
        deleted.
    @return The area ``ColumnStore`` that drove the outputs (the grouped store
        when ``group_intervals`` is on, the raw store otherwise).
    @throws ValueError when ``stream=True`` and ``json_file`` is None.
    """
    from .preflight import RamBudgetExceeded

    partial_banner = None
    try:
        if stream:
            if json_file is None:
                raise ValueError("stream=True requires a json_file path.")
            templaz_dir = Path(laz_dir).parent / "templaz"
            store = process_area_streamed(
                tuple(bbox), json_file, cell_xy=cell_xy, cell_z=cell_z,
                keep_classes=keep_classes, use_chunks=use_chunks,
                chunk_size=chunk_size, clip=clip,
                templaz_dir=templaz_dir,
                tile_pitch=tile_pitch, limit=limit,
                max_rss_mb=max_rss_mb,
            )
        else:
            store = process_area(
                tuple(bbox), laz_dir, cell_xy=cell_xy, cell_z=cell_z,
                keep_classes=keep_classes, use_chunks=use_chunks,
                chunk_size=chunk_size, clip=clip, delete_laz=delete_laz,
                max_rss_mb=max_rss_mb,
            )
    except RamBudgetExceeded as exc:
        logger.error("Aborted at RAM budget: %s. Writing PARTIAL outputs "
                     "from the %d completed tile(s).", exc, exc.tiles_done)
        store = exc.partial_store
        if store is None:
            from .data_structures import ColumnStore as _CS
            import math as _m
            store = _CS(_m.floor(bbox[0] / cell_xy) * cell_xy,
                        _m.floor(bbox[1] / cell_xy) * cell_xy,
                        0.0, cell_xy, cell_z)
        partial_banner = (
            f"PARTIAL RESULT - aborted after {exc.tiles_done}/"
            f"{exc.tiles_total} tiles at RSS {exc.rss_mb:.0f} MB "
            f"(budget {exc.budget_mb:.0f} MB)")
    # Grouping / unison step: merge consecutive same-class
    # intervals so sparse-return fragmentation (one tree = dozens of
    # 1-point slivers at fine cell_z) collapses into solid bands. The RAW
    # store is preserved first (area_raw.npz, only when store-saving is
    # on) so both sets exist; the
    # grouped store then drives persist/maps/diagnostics/viz3d. Default
    # ON; --no-group-intervals renders raw.
    if group_intervals and len(store.columns):
        if save_store:
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            raw_npz = Path(out_dir) / "area_raw.npz"
            # Staged through .partial.npz like every other large artifact:
            # an interrupted write must not leave a truncated file under the
            # final name, where np.load only reports it as a corrupt zip much
            # later.
            from .store_streaming import save_store_npz_atomic
            save_store_npz_atomic(store, raw_npz)
            logger.info("saved ungrouped store -> %s", raw_npz)
        gap_cells = (None if group_gap is None
                     else max(0, int(round(group_gap / cell_z))))
        iv0 = int(store._zs.shape[0])
        store = store.grouped(max_gap_cells=gap_cells)
        logger.info("grouping: %s -> %s intervals (%.1fx fewer; gap=%s)",
                    f"{iv0:,}", f"{store._zs.shape[0]:,}",
                    iv0 / max(1, int(store._zs.shape[0])),
                    "unlimited" if gap_cells is None else f"{gap_cells} cells")

    writer = _write_outputs_isolated if isolate_stages else _write_outputs
    kwargs = dict(columns_mode=columns_mode, columns_top_n=columns_top_n,
                  columns_all_max=columns_all_max,
                  viz3d=viz3d, max_boxes=max_boxes, roi_size=roi_size,
                  roi_cx=roi_cx, roi_cy=roi_cy, do_full=do_full, do_roi=do_roi,
                  save_store=save_store, extra_header=partial_banner,
                  viz3d_stream=viz3d_stream, max_instances=max_instances,
                  tile_m=tile_m,
                  inline_threshold_mb=inline_threshold_mb,
                  # Recorded so area.npz stays reproducible from the raw
                  # shards: grouping is not invertible (see run_utils).
                  provenance=make_provenance(
                      group_intervals=group_intervals, group_gap=group_gap,
                      keep_classes=keep_classes, epsg=DEFAULT_EPSG))
    if isolate_stages:
        kwargs["only_stages"] = only_stages
        kwargs["stage_timeout"] = stage_timeout
    writer(store, Path(out_dir), tuple(bbox), cell_xy, cell_z, height_mode,
           **kwargs)
    return store


def _maybe_download(bbox, laz_dir, json_file, tile_pitch, workers, limit):
    """Fetch tiles intersecting the box first (best-effort; needs network)."""
    from .download_laz import download_laz
    from ._download_common import origin_window
    dl_bbox = origin_window(bbox, tile_pitch)
    logger.info("Downloading tiles for origin window %s ...", dl_bbox)
    download_laz(dl_bbox, Path(laz_dir), Path(json_file),
                 workers=workers, limit=limit, dry_run=False)


def _parse_classes(s):
    """``--keep-classes`` text (comma or space separated codes) -> set of
    int, or None for an empty/absent value (keep every class)."""
    if not s:
        return None
    return {int(v) for v in s.replace(",", " ").split()}


def _build_parser() -> argparse.ArgumentParser:
    """Build the parser with the single ``area`` sub-command and the full
    flag set listed in the module docstring: bbox and tile source, grid and
    reading, column diagnostics, interval grouping, the 3-D viewers, the
    memory strategy (``--shard`` family, ``--max-rss-mb``), stage isolation
    and the end-of-run retention flags."""
    p = argparse.ArgumentParser(prog="python -m voxelizer.area_cli",
                                description="Coordinate-driven whole-area voxelizer.")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("area", help="Voxelize the area inside a bounding box.")
    # bbox is required unless --resume-from-store supplies the geometry via
    # the raw store's run_params.json (validated in main()).
    a.add_argument("--xmin", type=float, default=None)
    a.add_argument("--ymin", type=float, default=None)
    a.add_argument("--xmax", type=float, default=None)
    a.add_argument("--ymax", type=float, default=None)
    a.add_argument("--laz-dir", type=Path, default=None,
                   help="Directory of .laz/.las tiles to draw from. Required "
                        "unless --resume-from-store is used.")
    a.add_argument("--output-dir", "-o", type=Path, default=None,
                   help="Output directory. Defaults to outputs/RunN/area_output/ "
                        "with auto-incrementing Run number.")
    a.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    a.add_argument("--no-chunks", action="store_true",
                   help="Read each tile whole (laspy.read) instead of streaming.")
    a.add_argument("--no-clip", action="store_true",
                   help="Keep whole intersecting tiles instead of clipping to bbox.")
    a.add_argument("--keep-classes", type=str, default=None,
                   help="Comma/space list of ASPRS classes to keep.")
    add_cell_args(a)
    add_viz3d_geom_args(a, max_boxes=5_000_000, roi_size=200.0)
    a.add_argument("--height-mode", choices=("default", "relative", "absolute"),
                   default="default")
    # columns diagnostics (parity with the single-tile run)
    a.add_argument("--columns-mode", choices=("diag", "top", "all", "skip"),
                   default="diag",
                   help="Column diagnostics: diag=4 figures, top=+top-N per-column "
                        "PNGs, all=+every column (slow), skip=none.")
    a.add_argument("--columns-top-n", type=int, default=50)
    a.add_argument("--group-intervals", action=argparse.BooleanOptionalAction,
                   default=True,
                   help="Merge consecutive same-class intervals per column "
                        "before any output is drawn (raw store saved as "
                        "area_raw.npz first when saving is on). Collapses "
                        "sparse-return fragmentation: smaller store, fewer "
                        "3-D boxes, and complexity diagnostics that count "
                        "structures rather than return sparsity. "
                        "--no-group-intervals renders the raw store.")
    a.add_argument("--group-gap", type=float, default=None, metavar="METERS",
                   help="Only merge across vertical gaps up to this many "
                        "metres (default: any gap).")
    a.add_argument("--columns-all-max", type=int, default=None,
                   help="Column cap for --columns-mode all (default "
                        "500000, from column_diagnostics."
                        "_MODE_ALL_MAX_COLUMNS). 0 disables the cap - the "
                        "refusal message quotes the estimated hours and "
                        "GB so the choice is informed. Fan-out into "
                        "blk_XXXX_YYYY/ subdirectories is automatic on "
                        "large runs.")
    # 3-D visualiser (all viz3d options)
    a.add_argument("--viz3d", action="store_true",
                   help="Also render 3-D HTML viewer(s) from the area store.")
    a.add_argument("--viz3d-stream", action="store_true",
                   help="Render streaming HTML viewer (handles 200M+ boxes) "
                        "instead of the legacy singleton viewer.")
    a.add_argument("--delete-laz", action="store_true",
                   help="Delete each source .laz file after it is voxelized.")
    a.add_argument("--tile-m", type=float, default=64.0,
                   help="Streaming viewer tile size in metres (default 64). "
                        "Only used with --viz3d-stream.")
    a.add_argument("--max-instances", type=int, default=4_000_000,
                   help="Hard GPU cap for the streaming viewer (default 4M). "
                        "Only used with --viz3d-stream.")
    a.add_argument("--inline-threshold-mb", type=int, default=64,
                   help="If the .bin is below this size, base64-embed it "
                        "in the HTML and delete the sidecar. Default 64 MB. "
                        "Only used with --viz3d-stream.")
    a.add_argument("--no-save-store", action="store_true",
                   help="Do not write area.npz + area_manifest.json. By default "
                        "the merged voxel grid is persisted so the 2-D/3-D "
                        "stages can be re-run from it (see "
                        "'viz3d_cli from-store') without re-voxelizing.")
    a.add_argument("--delete-shards", action="store_true",
                   help="Shard mode only: delete the shards/ .npz cache at the "
                        "end of the run, once everything that reads it has "
                        "run - the maps, the 3-D views, the from-shards "
                        "column diagnostics and statistics sweep, and the "
                        "merge if one was asked for. Reclaims disk but "
                        "disables re-rendering, resuming and archiving from "
                        "shards. No effect on non-sharded runs.")
    a.add_argument("--stream", action="store_true",
                   help="Streaming mode: download each tile on the fly, voxelize, "
                        "discard. Replaces the 2-phase download+voxelize flow. "
                        "Requires --json.")
    # optional pre-download
    a.add_argument("--download", action="store_true",
                   help="Fetch tiles for the box first (needs inventory + network).")
    a.add_argument("--json", type=Path, default=None, help="Inventory JSON for --download.")
    a.add_argument("--tile-pitch", type=int, default=500)
    a.add_argument("--workers", type=int, default=None,
                   help="Parallel download threads (default: min(32, cpu+4)).")
    a.add_argument("--limit", type=int, default=0)
    # RAM guarding: see voxelizer/preflight.py
    a.add_argument("--preflight-only", action="store_true",
                   help="Print the tile/RAM estimate for this run and exit "
                        "without processing anything.")
    a.add_argument("--max-rss-mb", type=float, default=None,
                   help="RAM watchdog: abort the tile loop cleanly (writing "
                        "partial outputs) if this process's RSS crosses the "
                        "budget, instead of grinding into the pagefile.")
    a.add_argument("--shard", action="store_true",
                   help="Shard beyond RAM: per-tile .npz shards + combined "
                        "stats + bounded mosaic maps + the column "
                        "diagnostics, all computed from the shards "
                        "themselves (see voxelizer/shard_diagnostics.py). "
                        "What still needs --merge-shards is the merged store "
                        "and what is exported from it: area.npz, "
                        "area_raw.npz, store_raw/ and the streaming viewer.")
    a.add_argument("--resume-shards", action="store_true",
                   help="With --shard: reuse every matching shard already in "
                        "<output-dir>/shards/ instead of re-voxelizing its "
                        "tile (lattice metadata validated per shard; "
                        "run_config.json guards against parameter drift). "
                        "Continues an interrupted metropolis run without "
                        "redoing finished tiles.")
    a.add_argument("--isolate-tiles", action="store_true",
                   help="With --shard: voxelize each tile in a child process "
                        "so a native LAZ-decoder crash (access violation) "
                        "costs one tile, recorded in shards/"
                        "failed_tiles.json, instead of the whole run.")
    a.add_argument("--retry-lazrs", action="store_true",
                   help="With --isolate-tiles: after a child crash, retry "
                        "that tile once with the single-thread lazrs backend "
                        "before recording it as failed. (LazrsParallel is "
                        "never used; see io_laz.py.)")
    a.add_argument("--merge-shards", action="store_true",
                   help="Shard mode only: after the per-tile loop, merge every "
                        ".npz shard into <out>/store_raw/ and write area.npz, "
                        "per-column diagnostics and any other requested stages "
                        "from it. The merge is out of core - store_raw/ is "
                        "assembled one key band at a time and the stages "
                        "attach to it by memory map - so peak RAM does not "
                        "scale with the merged store, which is what makes a "
                        "whole-metropolis merge at 0.5 m possible on 32 GB. "
                        "Not required for the column diagnostics: those are "
                        "computed from the shards when this is off.")
    a.add_argument("--merge-band-intervals", type=positive_int,
                   default=DEFAULT_BAND_INTERVALS, metavar="N",
                   help=f"RAM/speed dial for that merge: intervals per band "
                        f"(default {DEFAULT_BAND_INTERVALS:,}, about 1.4 GB "
                        f"peak). Halve it on a small machine - each band is "
                        f"assembled whole in RAM; raise it to re-read the "
                        f"compressed shards fewer times (a shard is read once "
                        f"per band its column range touches).")
    # Per-stage process isolation: see _write_outputs_isolated.
    a.add_argument("--isolate-stages", action=argparse.BooleanOptionalAction,
                   default=True,
                   help="Run each output stage (stats, area.npz, maps, column "
                        "diagnostics, 3-D viewers) in its own child process "
                        "attached to a memory-mapped on-disk store. A native "
                        "crash (access violation) in one stage then costs "
                        "that stage only - the run survives, retries it once, "
                        "and every other output is written. Also frees the "
                        "parent's in-heap store during rendering. Default: "
                        "on. --no-isolate-stages restores the historical "
                        "in-process path. NON-SHARD RUNS ONLY: a --shard "
                        "run always isolates its merged phase, whichever way "
                        "this is set.")
    a.add_argument("--stage-timeout", type=float, default=0, metavar="SECONDS",
                   help="Wall-clock limit per isolated stage; the child is "
                        "killed and retried once when it expires. Default 0: "
                        "no limit. A fixed limit cannot tell a big stage from "
                        "a stuck one, because the value that separates them "
                        "scales with the store, so the stage logs and "
                        "faultlogs are the hang evidence and this is "
                        "opt-in.")
    a.add_argument("--keep-raw-store", action="store_true",
                   help="Keep <out>/store_raw/ (the mmap-able store the stages "
                        "read, and in shard mode the merge output itself) "
                        "after the run instead of deleting it. Without this "
                        "flag store_raw/ is removed once the deliverables are "
                        "written - unless a stage failed, which keeps it for "
                        "the resume. Required if you plan to use "
                        "--resume-from-store later.")
    a.add_argument("--keep-area-raw", action="store_true",
                   help="Keep <out>/area_raw.npz (the pre-grouping store, "
                        "written when --group-intervals is on) after the run "
                        "instead of deleting it. Useful to re-run grouping "
                        "later with a different --group-gap without "
                        "re-voxelizing. Overrides the --intermediates "
                        "umbrella for this one artefact. In --shard mode this "
                        "additionally triggers a second out-of-core merge pass "
                        "so area_raw.npz is produced alongside the grouped "
                        "area.npz (peak RAM stays one band either way). "
                        "CAVEAT: the ungrouped store is the largest artefact a "
                        "run produces and this asks for it twice on disk - as "
                        "the scratch store directory the pass assembles, then "
                        "as the .npz - plus a second full pass over the "
                        "shards. The raw data is never lost by declining it: "
                        "it lives in the per-tile shards, and an ungrouped "
                        "store can be rebuilt later over a smaller sub-area "
                        "with --resume-shards --no-group-intervals.")
    a.add_argument("--intermediates", choices=("keep", "delete", "auto"),
                   default="auto",
                   help="Policy for derived caches (store_raw/, area_raw.npz, "
                        "templaz/) at end of run. auto (default): delete on "
                        "full success, keep everything and print the exact "
                        "--resume-from-store command if any stage failed. "
                        "Explicit per-artifact flags (--keep-raw-store, "
                        "--keep-area-raw, --delete-laz, --delete-shards) "
                        "always override; deliverables and stages/ "
                        "diagnostics are never touched.")
    a.add_argument("--resume-from-store", type=Path, default=None,
                   help="Skip voxelization: run output stages directly from "
                        "an existing store_raw/ directory (written by a "
                        "previous --isolate-stages run with "
                        "--keep-raw-store). Geometry and options are read "
                        "from its run_params.json; bbox flags are not needed.")
    a.add_argument("--only-stage", action="append", default=None,
                   metavar="STAGE",
                   help="Restrict the isolated run to the named stage(s); "
                         "repeatable. Stages: stats, persist_npz, maps, "
                         "col_diag, viz3d_roi, viz3d_full, viz3d_stream. "
                         "('_crash_test' deliberately segfaults a child so "
                         "you can verify crash containment on your machine.)")
    return p


def _cleanup_intermediates(args, store) -> None:
    """Apply the --intermediates policy (keep | delete | auto).

    'auto' is success-conditional, driven by the signal the isolation
    machinery already produces: stages.json all-OK -> reclaim the derived
    caches; any failure -> keep the recovery data and print the exact resume
    command. Windows ordering respected: mmap views are released before the
    backing directory is removed. Sources (LAZ) stay under --delete-laz;
    deliverables and stages/ diagnostics are never touched.
    """
    import gc
    import json
    import shutil

    out = Path(args.output_dir)
    policy = args.intermediates
    if policy == "keep":
        logger.info("intermediates: keep - nothing removed.")
        return
    ok = True
    man = out / "stages.json"
    if man.exists():
        try:
            ok = all(r.get("ok") for r in json.loads(man.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 - unreadable manifest = not a success
            ok = False
    if policy == "auto" and not ok:
        logger.warning(
            "intermediates: auto - kept everything (a stage failed). Resume "
            "with: python -m voxelizer.area_cli area --resume-from-store %s "
            "--output-dir %s", out / "store_raw", out)
        return

    reclaimed = 0

    def _size(p):
        """Bytes on disk of a file, or of every file below a directory."""
        return (sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                if p.is_dir() else p.stat().st_size)

    def _rm(p):
        """Remove a file or directory tree if it exists, adding its size to
        the enclosing ``reclaimed`` total when it is really gone and warning
        (a mapped file may still be open) when it is not."""
        nonlocal reclaimed
        if not p.exists():
            return
        sz = _size(p)
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
        if not p.exists():
            reclaimed += sz
        else:
            logger.warning("intermediates: could not fully remove %s "
                           "(mapped file still open?) - safe to delete by "
                           "hand.", p)

    if not args.keep_raw_store:  # explicit keep beats the umbrella
        if store is not None:
            store.release_arrays()
        gc.collect()
        _rm(out / "store_raw")
    if not getattr(args, "keep_area_raw", False):  # explicit keep beats the umbrella
        _rm(out / "area_raw.npz")
    _rm(out / "templaz")
    if getattr(args, "laz_dir", None) is not None:
        _rm(Path(args.laz_dir).parent / "templaz")
    logger.info("intermediates: %s - reclaimed %.2f GB.",
                policy, reclaimed / 1e9)


def main(argv=None) -> None:
    """Parse the ``area`` verb and dispatch on its flags. Refuses up front,
    via ``parser.error``, a ``--shard`` run without ``--merge-shards`` that
    asks for merged-store outputs, and stage-selection flags under
    ``--no-isolate-stages``. ``--resume-from-store`` reruns the output stages
    from a kept ``store_raw/`` and exits 1 if any stage failed;
    ``--preflight-only`` prints the RAM estimate and returns; otherwise the
    optional ``--download`` runs, a stale ``stages.json`` is removed, and the
    area goes through ``sharding.run_area_sharded`` (``--shard``) or
    ``run_area``. Both paths print a summary, apply the ``--intermediates``
    policy and ``sys.exit(1)`` on a RAM-budget abort, lost tiles, a failed
    stage or a PARTIAL ``stats.txt``; a clean run returns None. Missing
    bbox or ``--laz-dir`` flags, an inverted bbox, or ``--stream`` /
    ``--download`` without ``--json`` raise ``SystemExit``."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = _build_parser()
    args = parser.parse_args(argv)

    # A shard-only run writes the column diagnostics and the exact statistics
    # from the shards themselves (voxelizer.shard_diagnostics), so the merge is
    # no longer implied by --columns-mode. What still needs the merged store is
    # the merged store: area.npz and its ungrouped sibling, store_raw/ itself,
    # the streaming viewer that is exported from one store, and the output
    # stages --only-stage selects (there are none without a merge). Asking for
    # those without --merge-shards used to be reconciled silently in favour of
    # merging, which turned a deliberately bounded-memory run into a full merge
    # nobody had asked for. Say so instead, before any work starts.
    #
    # Every one of those four is refused here, --viz3d-stream included. It used
    # to reach sharding.run_area_sharded, run the whole area, and only then log
    # a warning that no <label>_stream.html would exist - a refusal delivered
    # after the compute rather than before it. That warning is still in
    # sharding.py because run_area_sharded is also called as a library
    # function, where no parser is in reach to refuse anything.
    if args.shard and not args.merge_shards:
        needs_merge = []
        if args.keep_raw_store:
            needs_merge.append("--keep-raw-store (store_raw/ IS the merge)")
        if getattr(args, "keep_area_raw", False):
            needs_merge.append("--keep-area-raw (area_raw.npz)")
        if args.only_stage:
            needs_merge.append(f"--only-stage {list(args.only_stage)} "
                               "(the output stages run on the merged store)")
        if args.stage_timeout:
            needs_merge.append(f"--stage-timeout {args.stage_timeout:g} "
                               "(per-stage limits apply to the merged "
                               "phase's isolated stages; a shard-only run "
                               "has none)")
        if args.viz3d_stream:
            needs_merge.append("--viz3d-stream (the streaming viewer is "
                               "exported from the merged store: "
                               "tiled_exporter walks ONE store's key order, "
                               "which a set of shards does not have)")
        if needs_merge:
            parser.error(
                "--shard without --merge-shards cannot produce: "
                + "; ".join(needs_merge)
                + ". A shard-only run writes no area.npz, no store_raw/ and "
                  "no streaming viewer either - those all come off the merged "
                  "store. Add --merge-shards to build it, or drop the flag: "
                  "stats.txt, the mosaic maps, the full and ROI 3-D views and "
                  "columns/ are written from the shards themselves.")

    # The historical in-process path has no stage machinery at all, so the
    # stage-selection flags would be dropped on the floor there. Refuse the
    # combination before any work starts (same policy as above). --shard and
    # --resume-from-store are exempt: both run their stages isolated
    # whichever way --isolate-stages is set.
    if (not args.isolate_stages and not args.shard
            and args.resume_from_store is None):
        dropped = []
        if args.only_stage:
            dropped.append(f"--only-stage {list(args.only_stage)}")
        if args.stage_timeout:
            dropped.append(f"--stage-timeout {args.stage_timeout:g}")
        if dropped:
            parser.error(
                "--no-isolate-stages runs every output stage in-process, "
                "where " + " and ".join(dropped) + " cannot apply and would "
                "be silently ignored. Drop --no-isolate-stages to select "
                "stages or set a per-stage timeout.")

    # -- resume: output stages only, straight from a preserved raw store -----
    if args.resume_from_store is not None:
        import json
        raw = Path(args.resume_from_store)
        rp_file = raw / "run_params.json"
        if not rp_file.exists():
            raise SystemExit(f"--resume-from-store: {rp_file} not found "
                             "(was the original run made with "
                             "--isolate-stages and --keep-raw-store?).")
        rp = json.loads(rp_file.read_text(encoding="utf-8"))
        out_dir = args.output_dir
        if out_dir is None:
            from .run_utils import next_run_output_dir
            out_dir = next_run_output_dir(Path("outputs"), "area_output")
            logger.info("Auto-created run directory: %s", out_dir)
        manifest = _write_outputs_isolated(
            None, out_dir, tuple(rp["bbox"]), rp["cell_xy"], rp["cell_z"],
            rp["height_mode"], columns_mode=rp["columns_mode"],
            columns_top_n=rp["columns_top_n"],
            columns_all_max=rp.get("columns_all_max"),
            viz3d=args.viz3d or any(s.startswith("viz3d")
                                    for s in (args.only_stage or [])),
            max_boxes=rp["max_boxes"], roi_size=rp["roi_size"],
            roi_cx=rp["roi_cx"], roi_cy=rp["roi_cy"],
            save_store=rp["save_store"], extra_header=rp.get("extra_header"),
            raw_store_dir=raw, only_stages=args.only_stage,
            stage_timeout=args.stage_timeout,
            viz3d_stream=rp.get("viz3d_stream", False),
            tile_m=rp.get("tile_m", 64.0),
            max_instances=rp.get("max_instances", 4_000_000),
            inline_threshold_mb=rp.get("inline_threshold_mb", 64))
        n_fail = sum(not r["ok"] for r in manifest)
        print(f"\nResume done: {len(manifest) - n_fail}/{len(manifest)} "
              f"stage(s) OK -> {out_dir}")
        sys.exit(1 if n_fail else 0)

    if any(v is None for v in (args.xmin, args.ymin, args.xmax, args.ymax)):
        raise SystemExit("--xmin/--ymin/--xmax/--ymax are required "
                         "(unless using --resume-from-store).")
    if args.laz_dir is None:
        raise SystemExit("--laz-dir is required "
                         "(unless using --resume-from-store).")
    if args.xmin > args.xmax or args.ymin > args.ymax:
        raise SystemExit("Invalid bbox: min must be <= max.")
    bbox = (args.xmin, args.ymin, args.xmax, args.ymax)

    if args.output_dir is None:
        from .run_utils import next_run_output_dir
        args.output_dir = next_run_output_dir(Path("outputs"), "area_output")
        logger.info("Auto-created run directory: %s", args.output_dir)

    if args.stream:
        if args.json is None:
            raise SystemExit("--stream requires --json <inventory>.")

    if args.preflight_only:
        from .preflight import estimate_area_run, format_estimate
        est = estimate_area_run(
            bbox, cell_xy=args.cell_xy, cell_z=args.cell_z,
            stream=args.stream, json_file=args.json, laz_dir=args.laz_dir,
            clip=not args.no_clip, tile_pitch=args.tile_pitch,
            limit=args.limit, group_intervals=args.group_intervals)
        print(format_estimate(est))
        return

    if not args.stream:
        if args.download:
            if args.json is None:
                raise SystemExit("--download requires --json <inventory>.")
            _maybe_download(bbox, args.laz_dir, args.json,
                            args.tile_pitch, args.workers, args.limit)

    # A previous run's stage manifest must not judge this one. Both the exit
    # code below and the 'auto' intermediates policy read stages.json, and
    # only an isolated-stage run writes it, so a plain re-run into the same
    # output directory used to inherit the earlier run's failures: a clean
    # run exited 1 and kept its intermediates on the strength of a stale
    # file. A run that does produce one writes it back before either reader
    # looks.
    _stale_stages = Path(args.output_dir) / "stages.json"
    if _stale_stages.exists():
        logger.info("removing stage manifest from a previous run: %s",
                    _stale_stages)
        _stale_stages.unlink(missing_ok=True)

    if args.shard:
        from .sharding import run_area_sharded
        summary = run_area_sharded(
            bbox, args.output_dir,
            stream=args.stream, json_file=args.json, laz_dir=args.laz_dir,
            cell_xy=args.cell_xy, cell_z=args.cell_z,
            keep_classes=_parse_classes(args.keep_classes),
            use_chunks=not args.no_chunks, chunk_size=args.chunk_size,
            clip=not args.no_clip, tile_pitch=args.tile_pitch,
            limit=args.limit, delete_laz=args.delete_laz,
            height_mode=args.height_mode,
            viz3d=args.viz3d, max_boxes=args.max_boxes,
            roi_size=args.roi_size, roi_cx=args.roi_cx, roi_cy=args.roi_cy,
            do_full=not args.no_full, do_roi=not args.no_roi,
            max_rss_mb=args.max_rss_mb,
            delete_shards=args.delete_shards,
            merge_shards=args.merge_shards,
            merge_band_intervals=args.merge_band_intervals,
            stage_timeout=args.stage_timeout,
            resume_shards=args.resume_shards,
            isolate_tiles=args.isolate_tiles,
            retry_lazrs=args.retry_lazrs,
            columns_mode=args.columns_mode,
            columns_top_n=args.columns_top_n,
            columns_all_max=args.columns_all_max,
            group_intervals=args.group_intervals,
            group_gap=args.group_gap,
            save_store=not args.no_save_store,
            keep_area_raw=args.keep_area_raw,
            only_stages=args.only_stage,
            viz3d_stream=args.viz3d_stream,
            max_instances=args.max_instances,
            tile_m=args.tile_m,
            inline_threshold_mb=args.inline_threshold_mb)
        print(f"\nSharded area done: {summary['tiles_done']}/"
              f"{summary['n_tiles']} tiles, {summary['n_columns']} columns "
              f"-> {args.output_dir}"
              + ("  [ABORTED at RAM budget - PARTIAL]" if summary["aborted"]
                 else ""))
        if summary.get("resumed_tiles"):
            print(f"  resumed {summary['resumed_tiles']} tile(s) from "
                  f"existing shards")
        if summary.get("failed_tiles"):
            print(f"  WARNING: {len(summary['failed_tiles'])} tile(s) "
                  f"lost to download, header or decode failure - see "
                  f"shards/failed_tiles.json")
        if summary.get("failed_stages"):
            print(f"  WARNING: merged-phase stage(s) failed: "
                  f"{summary['failed_stages']} - see stages.json / stages/")
        # The intermediates policy now applies in shard mode too: the
        # isolated merge phase writes store_raw/ and stages.json, and
        # stream mode leaves a templaz/ directory.
        _cleanup_intermediates(args, None)
        # Exit code: a shard run that aborted at the RAM budget, lost
        # tiles, or had a merged-phase stage fail exits 1, not 0.
        if (summary["aborted"] or summary.get("failed_tiles")
                or summary.get("failed_stages")):
            sys.exit(1)
        return

    store = run_area(
        bbox, args.laz_dir, args.output_dir,
        cell_xy=args.cell_xy, cell_z=args.cell_z,
        keep_classes=_parse_classes(args.keep_classes),
        use_chunks=not args.no_chunks, chunk_size=args.chunk_size,
        clip=not args.no_clip, height_mode=args.height_mode,
        columns_mode=args.columns_mode, columns_top_n=args.columns_top_n,
        columns_all_max=args.columns_all_max,
        group_intervals=args.group_intervals, group_gap=args.group_gap,
        # viz3d inferred from --only-stage viz3d_* exactly as the
        # --resume-from-store path already does, so the same flag means the
        # same thing on both entry points.
        viz3d=args.viz3d or any(s.startswith("viz3d")
                                for s in (args.only_stage or [])),
        max_boxes=args.max_boxes, roi_size=args.roi_size,
        roi_cx=args.roi_cx, roi_cy=args.roi_cy,
        do_full=not args.no_full, do_roi=not args.no_roi,
        delete_laz=args.delete_laz,
        stream=args.stream,
        json_file=args.json if args.stream else None,
        tile_pitch=args.tile_pitch, limit=args.limit,
        max_rss_mb=args.max_rss_mb,
        save_store=not args.no_save_store,
        isolate_stages=args.isolate_stages, only_stages=args.only_stage,
        stage_timeout=args.stage_timeout,
        viz3d_stream=args.viz3d_stream,
        tile_m=args.tile_m,
        max_instances=args.max_instances,
        inline_threshold_mb=args.inline_threshold_mb,
    )
    # After the merge the store can be mmap-backed, where stats() would page
    # the whole thing back into RAM (~5 GB on a dense area) to print three
    # numbers the stats stage has already computed. stats_for folds them out
    # of core in that case and calls stats() otherwise, so the printed
    # figures are the same either way.
    from .store_streaming import stats_for
    s = stats_for(store)
    print(f"\nArea done: {s['n_columns']} columns, {s['n_intervals']} intervals, "
          f"{s['n_points']} points -> {args.output_dir}")

    _cleanup_intermediates(args, store)

    # Exit code: non-zero when a stage failed (stages.json) or the run
    # aborted PARTIAL at the RAM budget (the banner run_area wrote into
    # stats.txt), so wrappers and CI can tell a clean run from a partial
    # one. Deliverables are still written either way - this only changes
    # the exit status.
    import json as _json
    rc = 0
    man = Path(args.output_dir) / "stages.json"
    if man.exists():
        try:
            ok = all(r.get("ok") for r in
                     _json.loads(man.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 - unreadable manifest = failure
            ok = False
        if not ok:
            rc = 1
    if rc == 0:
        st_file = Path(args.output_dir) / "stats.txt"
        try:
            if (st_file.exists()
                    and "PARTIAL RESULT" in
                    st_file.read_text(encoding="utf-8")[:400]):
                rc = 1
        except Exception:  # noqa: BLE001
            pass
    if rc:
        sys.exit(rc)


if __name__ == "__main__":
    main()