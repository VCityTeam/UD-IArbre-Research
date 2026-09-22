"""
Per-stage child process for the area pipeline's output stages.
@ingroup t3_orchestr


    python -m voxelizer.stage_runner --store-dir <raw> --stage <id>
           --params <params.json> --out-dir <dir> --fault-log <file>

Rationale: the failures this guards against are native faults (access
violations and fast-fail aborts inside matplotlib/numpy C code), which kill
the interpreter before Python can raise anything catchable. The OS process boundary is the
only containment wall such a fault cannot cross, so each output stage runs
here, in an expendable child: the parent orchestrator
(area_outputs._write_outputs_isolated) observes a death as an exit code
instead of dying with it, retries once with mitigations, and continues to the
next stage.

The child attaches to the store via ``ColumnStore.load_dir(mmap=True)``:
zero-copy, lazy page-in, clean file-backed pages the OS can discard under
memory pressure. Each stage therefore starts from a fresh, unfragmented
address space holding only its own working set. The store is READ-ONLY here.

Out-of-core stages
------------------
Attaching lazily is not the same as reducing lazily: the whole-store reductions
behind stats, the npz manifest, the column diagnostics and the four 2-D maps
each build several arrays as long as the store (100+ GB at the 0.5 m
metropolis grid), which no amount of memory-mapping helps with. Those
reductions now have streaming counterparts in
voxelizer.store_streaming, and the switch between them is the STORE,
not a flag: a memory-mapped store takes the out-of-core path, anything else
keeps the in-RAM one. Since this child always maps, every reduction stage
launched here - stats, persist_npz, col_diag, maps - runs bounded-RAM, and no
caller's command line changes. The answers are identical either way - same
tie rules, same first-match rules, same last-write raster representatives,
and exact (not approximated) quantiles - which is what the unit tests assert.
The two legacy box exports (viz3d roi/full) are the exception: their column
selection still unpacks per-column index arrays (~9 B of transients per
column) before the box cap applies - full detail at scale is
``viz3d_stream``'s job.

``viz3d_stream`` needed nothing: it already walks the store one tile-row band
at a time and finds each band with ``searchsorted``, which is zero-copy on a
mapped array. Its measured multi-GB "peak" is file-backed page cache, not
anonymous memory. Both it and ``persist_npz`` now stage their large outputs and
rename them into place, so an interrupted stage cannot leave a truncated
artifact under the final name.

Every stage writes ``<out>/stages/<stage>.result.json`` on success and gets a
dedicated faulthandler log (``--fault-log``), so a native death leaves its
Python-level traceback in a per-stage file - reproducible by re-running the
exact command line the orchestrator logged.
"""

from __future__ import annotations

import argparse
import faulthandler
import json
import logging
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# stage implementations - each mirrors one block of the in-process
# area_outputs._write_outputs. The maps stage and that writer now share one
# renderer (visualization.write_area_maps), so their four map PNGs are
# byte-identical; they used to differ, this stage drawing matplotlib figures
# at dpi 120 while _write_outputs saved native-resolution render_tile_map
# rasters through PIL. One difference remains: the stream stage's page title
# differs in case ("area - stream" vs "Area - stream").
# ---------------------------------------------------------------------------
def _stage_stats(store, out_dir: Path, p: dict) -> dict:
    """Write ``<out>/stats.txt``: the streaming whole-store statistics under
    a header giving the area bbox, cell sizes, height mode and shared origin
    (plus ``p["extra_header"]`` first, when present). Returns the output
    name list."""
    from .pipeline import _format_stats
    from .store_streaming import stats_for
    header = [
        f"Area bbox: x [{p['bbox'][0]}, {p['bbox'][2]}], "
        f"y [{p['bbox'][1]}, {p['bbox'][3]}]",
        f"cell_xy: {p['cell_xy']} m, cell_z: {p['cell_z']} m",
        f"height mode: {p['height_mode']}",
        f"shared origin: ({store.x_min}, {store.y_min}, {store.z_min})",
    ]
    if p.get("extra_header"):
        header.insert(0, p["extra_header"])
    out = out_dir / "stats.txt"
    out.write_text(_format_stats(stats_for(store), header), encoding="utf-8")
    logger.info("wrote %s", out)
    return {"outputs": [out.name]}


def _stage_persist_npz(store, out_dir: Path, p: dict) -> dict:
    """Persist the store as ``area.npz`` plus ``area_manifest.json`` through
    ``area_outputs._write_area_store``, passing the bbox, cell sizes, height
    mode and optional provenance from the params dict."""
    from .area_outputs import _write_area_store
    _write_area_store(store, out_dir, tuple(p["bbox"]),
                      p["cell_xy"], p["cell_z"], p["height_mode"],
                      p.get("provenance"))
    return {"outputs": ["area.npz", "area_manifest.json"]}


def _stage_maps(store, out_dir: Path, p: dict) -> dict:
    """Render the four 2-D area maps: the rasters are reduced once from the
    store (batch-wise on a memory-mapped store) and handed to
    ``visualization.write_area_maps``, whose list of written file names is
    returned as the outputs."""
    from .pipeline import MODES
    from .visualization import rasters_from_store, write_area_maps
    # rasters computed ONCE for all four maps (the expensive reduction); on
    # this child's memory-mapped store they accumulate one whole-column batch
    # at a time (store_streaming.summaries_batches), so the peak is one batch
    # plus the pixel-guarded rasters - never a per-column array. The dict is
    # born and dies inside this child - nothing outlives the stage.
    rasters, _frame = rasters_from_store(
        store, need_dom=("max_points_class" in MODES))
    # write_area_maps renders through Figure + FigureCanvasAgg, so this stage
    # no longer has to switch the global backend, and the in-process writer
    # calls the same function to get the same bytes.
    written = write_area_maps(store, out_dir, height_mode=p["height_mode"],
                              rasters=rasters)
    return {"outputs": written}


def _stage_col_diag(store, out_dir: Path, p: dict) -> dict:
    """Write the per-column diagnostics into ``<out>/columns/`` with the
    mode, top-N and optional all-columns cap taken from the params dict,
    labelled "area"."""
    from .column_diagnostics import write_column_diagnostics
    write_column_diagnostics(store, out_dir / "columns",
                             mode=p["columns_mode"], top_n=p["columns_top_n"],
                             all_cap=p.get("columns_all_max"),
                             tile_label="area")
    logger.info("wrote %s", out_dir / "columns")
    return {"outputs": ["columns/"]}


def _stage_viz3d_roi(store, out_dir: Path, p: dict) -> dict:
    """Write only the legacy box-capped 3-D viewer of the region of interest
    (``max_boxes``, ``roi_size``, ``roi_cx``, ``roi_cy`` from the params
    dict) via ``area_outputs.render_area_3d``."""
    from .area_outputs import render_area_3d
    render_area_3d(store, out_dir, max_boxes=p["max_boxes"],
                   roi_size=p["roi_size"], roi_cx=p["roi_cx"],
                   roi_cy=p["roi_cy"], do_full=False, do_roi=True)
    return {"outputs": ["3-D ROI viewer"]}


def _stage_viz3d_full(store, out_dir: Path, p: dict) -> dict:
    """Write only the legacy box-capped 3-D viewer of the full area (same
    params as the ROI stage, ``do_full=True``) via
    ``area_outputs.render_area_3d``."""
    from .area_outputs import render_area_3d
    render_area_3d(store, out_dir, max_boxes=p["max_boxes"],
                   roi_size=p["roi_size"], roi_cx=p["roi_cx"],
                   roi_cy=p["roi_cy"], do_full=True, do_roi=False)
    return {"outputs": ["3-D full viewer"]}


def _stage_viz3d_stream(store, out_dir: Path, p: dict) -> dict:
    """Tiled, view-dependent, FULL-DETAIL streaming viewer.

    Writes EVERY interval in the store (no stride, no cap) as a spatially
    tile-contiguous record stream plus an index, and a page whose GPU budget
    selects which TILES are resident rather than which boxes survive. Runs one
    tile-row band at a time, so the single whole-geometry allocation that
    killed a 10.16 GB export at metropolis scale never happens here.
    """
    import os
    import shutil
    from .tiled_exporter import export_tiled_from_store

    file_label = p.get("file_label", "area")
    title = p.get("title", f"{file_label} - stream")
    max_instances = int(p.get("max_instances", 4_000_000))
    inline_mb = int(p.get("inline_threshold_mb", 64))
    tile_m = float(p.get("tile_m", 64.0))

    out_path = out_dir / f"{file_label}_stream.html"
    # The .bin is by far the largest artifact any stage writes (46.5 GB at 1 m,
    # and it grows with the grid), so the export is staged in a sibling
    # directory and moved into place only once all three files are complete: a
    # death mid-write can no longer leave a truncated payload sitting under the
    # name the page and the HTTP server expect. The FINAL names are used inside
    # the staging directory because the page embeds the payload's file name -
    # and the move preserves the mtime the cache-busting query string carries.
    staging = out_dir / f"{file_label}_stream.partial"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        export_tiled_from_store(
            store, staging / out_path.name,
            title=title,
            max_instances=max_instances,
            tile_m=tile_m,
            inline_threshold=inline_mb * 1024 * 1024,
        )
        written = []
        # MARKER LAST. The .html is the name the launcher, the page and the
        # HTTP server all look for, so it must not appear until the payload
        # and index it references are already under their final names. Plain
        # alphabetical order put it in the middle (.bin, .html, .idx.json),
        # so a death inside this loop could leave a page fetching byte ranges
        # of an index that had not arrived. Moving it last makes an
        # interrupted commit leave an incomplete set nothing points at.
        staged = sorted(staging.iterdir())
        marker = [s for s in staged if s.name == out_path.name]
        for src in [s for s in staged if s.name != out_path.name] + marker:
            os.replace(src, out_dir / src.name)
            written.append(src.name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    # A small enough payload is base64-inlined and its sidecar dropped; drop
    # any same-named leftover of a previous export too, or the page would sit
    # next to a stale .bin it no longer references.
    for name in (out_path.name, out_path.with_suffix(".bin").name,
                 out_path.with_suffix(".idx.json").name):
        if name not in written:
            (out_dir / name).unlink(missing_ok=True)
    # A double-click that serves this directory and opens the page. Whether
    # the page works over file:// depends on which way the export went: a
    # payload under --inline-threshold-mb (64 MB by default) is base64-embedded
    # and the page is then self-contained, while anything above it keeps a
    # sidecar .bin and fetches byte ranges of it, which file:// cannot serve.
    # The launcher is written either way, because which side of the threshold
    # a given area lands on is not something the person opening it should have
    # to work out.
    from .launchers import write_stream_launcher
    written.append(write_stream_launcher(out_dir, out_path.name).name)
    return {"outputs": written}


def _stage_crash_test(store, out_dir: Path, p: dict) -> dict:
    """Deliberate genuine native death, for verifying containment end-to-end
    (``--only-stage _crash_test``): the parent must survive this child,
    decode the exit (SIGSEGV / 0xC0000005-class), retry, record, continue."""
    import os
    import signal
    if os.name == "nt":
        # Windows has no SIGSEGV to raise: signal.raise_signal(SIGSEGV) goes
        # through the CRT and leaves via abort(), so the parent saw exit 3 and
        # decoded an ordinary failure. The faults this containment exists for
        # are genuine access violations from native code, so produce one -
        # writing a byte to address 0 through a ctypes memory view exits
        # 0xC0000005 (3221225477), which is what the parent's decoder reads.
        # Note the route matters: a null write made through a ctypes FOREIGN
        # CALL (memset, or kernel32.RaiseException) is caught by ctypes' own
        # exception wrapper and surfaces as a catchable Python OSError, which
        # would test nothing.
        import ctypes
        logger.info("crash test: forcing a real access violation "
                    "(0xC0000005) in pid %d now", os.getpid())
        ctypes.c_char.from_address(0).value = b"\x01"
    else:
        logger.info("crash test: raising a real SIGSEGV in pid %d now",
                    os.getpid())
        signal.raise_signal(signal.SIGSEGV)
    return {"outputs": []}  # unreachable


STAGES = {
    "stats": _stage_stats,
    "persist_npz": _stage_persist_npz,
    "maps": _stage_maps,
    "col_diag": _stage_col_diag,
    "viz3d_roi": _stage_viz3d_roi,
    "viz3d_full": _stage_viz3d_full,
    "viz3d_stream": _stage_viz3d_stream,
    "_crash_test": _stage_crash_test,
}


def main(argv=None) -> int:
    """Child entry point: parse ``--store-dir``, ``--stage``, ``--params``,
    ``--out-dir`` and ``--fault-log`` (all required), enable faulthandler on
    the fault log, memory-map the store read-only and run the one requested
    ``STAGES`` function. On success writes ``<out>/stages/<stage>.result.json``
    (status, wall time, the stage's outputs and, when psutil is available, an
    end-of-stage RSS sample under ``peak_rss_mb``) and returns 0; any
    exception propagates and a native fault ends the process with the OS
    exit code."""
    ap = argparse.ArgumentParser(prog="python -m voxelizer.stage_runner")
    ap.add_argument("--store-dir", type=Path, required=True,
                    help="ColumnStore.save_dir raw directory to mmap.")
    ap.add_argument("--stage", choices=sorted(STAGES), required=True)
    ap.add_argument("--params", type=Path, required=True,
                    help="JSON file of run parameters (shared by all stages).")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--fault-log", type=Path, required=True,
                    help="Per-stage faulthandler destination; on a native "
                         "fault the Python-level traceback lands here.")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Keep the file object alive for the process lifetime - faulthandler
    # writes to the raw fd at fault time.
    fault_fh = open(args.fault_log, "w", encoding="utf-8")
    faulthandler.enable(fault_fh)

    t0 = time.monotonic()
    params = json.loads(args.params.read_text(encoding="utf-8"))
    args.out_dir.mkdir(parents=True, exist_ok=True)

    from .data_structures import ColumnStore
    store = ColumnStore.load_dir(args.store_dir, mmap=True)

    logger.info("[stage %s] pid=%d store=%s (%d columns, mmap)",
                args.stage, __import__("os").getpid(), args.store_dir,
                len(store.columns))
    result = STAGES[args.stage](store, args.out_dir, params)

    wall = time.monotonic() - t0
    res = {"stage": args.stage, "status": "ok", "wall_s": round(wall, 2)}
    res.update(result or {})
    try:
        import psutil
        # The key is named peak_rss_mb but this is a single sample taken at
        # the END of the stage, not a high-water mark: nothing polls RSS
        # while the stage runs. A stage that peaked and freed reports the
        # figure after the free. (preflight.current_rss_mb is the same
        # measurement, named for what it is.) The key name is kept because
        # stages/*.result.json is a published artifact.
        res["peak_rss_mb"] = round(
            psutil.Process().memory_info().rss / 1e6, 1)
    except Exception:  # noqa: BLE001 - psutil optional
        pass
    stages_dir = args.out_dir / "stages"
    stages_dir.mkdir(exist_ok=True)
    (stages_dir / f"{args.stage}.result.json").write_text(
        json.dumps(res, indent=2), encoding="utf-8")
    logger.info("[stage %s] done in %.1fs", args.stage, wall)
    return 0


if __name__ == "__main__":
    sys.exit(main())
