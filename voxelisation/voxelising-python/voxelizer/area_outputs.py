"""
Shared output-writing layer for the area pipelines.
@ingroup t3_orchestr


Everything here used to live in ``area_cli.py``, which created a circular
dependency: ``sharding`` needed these writers while ``area_cli`` needed
``sharding.run_area_sharded``, held together only by function-level deferred
imports. This module sits BELOW both consumers - it imports neither - so the
layering documented in ``__init__`` is real again:

    area_outputs.py   (this file: store persistence, per-stage isolated
                       execution, 2-D/3-D output writers)
        ^                ^
    area_cli.py      sharding.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from .data_structures import ColumnStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 3-D HTML render of the *area store* (mirrors viz3d_cli._run_single, but on
# the already-merged store so the area is never re-voxelized).
# ---------------------------------------------------------------------------
def _import_viz3d():
    """Return the visualizer3d module."""
    from . import visualizer3d as _v
    return _v


def _import_render_store_3d():
    """Resolve visualizer3d.render_store_3d() through the lazy import, so the 3-D
    renderer is only loaded when an area actually asks for a 3-D output."""
    return _import_viz3d().render_store_3d


def render_area_3d(store: ColumnStore, out_dir: Path, *, max_boxes=5_000_000,
                   roi_size=200.0, roi_cx=None, roi_cy=None,
                   do_full=True, do_roi=True, label="area") -> None:
    """Render full/ROI 3-D HTML for an area store, honouring every viz3d option.

    Thin wrapper over visualizer3d.render_store_3d() (the one shared 3-D
    renderer, also used by the single-tile CLI) so the area is never
    re-voxelized: it renders straight from the already-merged store.
    """
    try:
        render_store_3d = _import_render_store_3d()
    except Exception as exc:  # noqa: BLE001
        logger.warning("3-D visualiser unavailable (%s) -- skipping.", exc)
        return
    if max_boxes and max_boxes > 5_000_000:
        logger.warning(
            "3-D: max_boxes=%s is above the 5M default; the HTML "
            "may be too large for a browser to open.", f"{max_boxes:,}")
    render_store_3d(store, out_dir, file_label=label, max_boxes=max_boxes,
                    roi_size=roi_size, roi_cx=roi_cx, roi_cy=roi_cy,
                    do_full=do_full, do_roi=do_roi)


# ---------------------------------------------------------------------------
# Output writer - matches process_single_tile's deliverables for an area store
# ---------------------------------------------------------------------------
def _write_area_store(store, out_dir, bbox, cell_xy, cell_z, height_mode,
                      provenance=None):
    """
    Persist the voxelized area as ``area.npz`` (lossless flat-array dump via
    ColumnStore.save()) plus an ``area_manifest.json`` sidecar.

    This materializes the voxelization (stage B) as an on-disk artifact so the
    downstream stages - 2-D maps + stats and the 3-D viewer - can be re-run
    later straight from the grid (``ColumnStore.load``) without re-reading any
    LAZ. ``viz3d_cli from-store area.npz`` is the 3-D consumer; the manifest
    records the grid geometry and counts a consumer needs before loading.

    ``provenance`` carries the run settings that are NOT derivable from the
    store itself - above all ``group_gap``. ``area.npz`` is normally the
    GROUPED store, and grouping is not invertible, so without the recorded gap
    the deliverable cannot be reproduced from the raw per-tile shards. See
    voxelizer.archive_cli.

    Memory: the .npz itself is written through numpy's own 16 MiB nditer loop,
    so a memory-mapped store never has to be resident to be persisted. The
    manifest's counts go through store_streaming.stats_for(), which takes
    the out-of-core reduction for a mapped store (``stats()`` materialises one
    number per column several times over - 100+ GB at the 0.5 m metropolis
    grid). The write is staged through a ``.partial.npz`` so an interrupted run
    cannot leave a truncated file under the final name.
    """
    import json
    from .store_streaming import save_store_npz_atomic, stats_for
    out_dir = Path(out_dir)
    npz = save_store_npz_atomic(store, out_dir / "area.npz")
    st = stats_for(store)
    manifest = {
        "schema": "voxelizer.area/2",
        "store_file": npz.name,
        "bbox": [float(v) for v in bbox],
        "cell_xy": float(cell_xy),
        "cell_z": float(cell_z),
        "origin": [float(store.x_min), float(store.y_min), float(store.z_min)],
        "height_mode": height_mode,
        "n_columns": int(st["n_columns"]),
        "n_intervals": int(st["n_intervals"]),
        "n_points": int(st["n_points"]),
        **(provenance or {}),
    }
    (out_dir / "area_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    logger.info("wrote %s (+ area_manifest.json)", npz)


# ---------------------------------------------------------------------------
# Per-stage process isolation
#
# Native faults - access violations (0xC0000005) inside matplotlib or numpy
# C code, stack-buffer-overrun fast-fails (0xC0000409) - kill the interpreter
# before Python can catch anything. The OS process is the only containment
# boundary such a fault cannot cross, so each output stage runs in an
# expendable child (voxelizer.stage_runner) attached to the store via a raw
# mmap-able on-disk copy (ColumnStore.save_dir). The parent survives any
# stage's death, decodes it, retries it once with thread-cap mitigations
# (such faults are nondeterministic, so one retry is worth its cost), records
# a manifest, and continues. Three further effects: the parent frees its
# multi-GB heap store during rendering (swap_to_dir_mmap), every stage gets
# its own faulthandler log, and any failed stage is reproducible by hand from
# the logged command line against store_raw/.
# ---------------------------------------------------------------------------
_NTSTATUS = {
    0xC0000005: "ACCESS_VIOLATION",
    0xC0000409: "STACK_BUFFER_OVERRUN (fast-fail)",
    0xC00000FD: "STACK_OVERFLOW",
    0xC0000017: "NO_MEMORY (commit failure)",
    0xC000013A: "CTRL_C_EXIT",
}

# No stage timeout by default (see _run_stage). Fixed per-stage wall-clock
# limits do not work here, because a limit chosen for one area size reads
# "too slow for its size" as "hung" on the next one up. A stage
# that is really stuck is visible in its progress log and its faultlog; a stage
# that is merely large is not, so the operator opts in with --stage-timeout.
_STAGE_TIMEOUT_DISABLED = 0

_RETRY_ENV = {  # attempt-2 mitigations: cap native pools
    "OPENBLAS_NUM_THREADS": "4", "OMP_NUM_THREADS": "4",
    "MKL_NUM_THREADS": "4", "NUMEXPR_NUM_THREADS": "4",
    "VECLIB_MAXIMUM_THREADS": "4",
}


def _decode_exit(rc: int) -> str:
    """Human-readable exit: 0, POSIX -signal, or Windows NTSTATUS."""
    if rc == 0:
        return "OK"
    if rc < 0:
        import signal as _sig
        try:
            return f"signal {-rc} ({_sig.Signals(-rc).name})"
        except ValueError:
            return f"signal {-rc}"
    u = rc & 0xFFFFFFFF
    if u in _NTSTATUS:
        return f"0x{u:08X} {_NTSTATUS[u]}"
    return f"exit {rc}"


def _is_native_fault(rc: int) -> bool:
    """True for deaths worth an automatic retry (segfault-class, not exit 1)."""
    if rc < 0:
        return True  # killed by a signal (POSIX)
    u = rc & 0xFFFFFFFF
    return u >= 0xC0000000  # NTSTATUS severity=error (Windows)


def _run_stage(stage: str, *, store_dir: Path, params_file: Path,
               out_dir: Path, stages_dir: Path,
               stage_timeout: float = _STAGE_TIMEOUT_DISABLED) -> dict:
    """Launch one stage child; wait, decode, retry once on native fault.

    ``stage_timeout`` is a wall-clock limit in seconds for the child, applied
    to each attempt; 0 (the default) waits as long as the stage takes. A stage
    killed by the timeout is retried exactly like a native fault, so setting
    one keeps the recovery-block behaviour it has always had.
    """
    import os
    import subprocess
    import time as _time

    fault_log = stages_dir / f"{stage}.faultlog"
    cmd = [sys.executable, "-u", "-m", "voxelizer.stage_runner",
           "--store-dir", str(store_dir), "--stage", stage,
           "--params", str(params_file), "--out-dir", str(out_dir),
           "--fault-log", str(fault_log)]
    timeout = float(stage_timeout) if stage_timeout else None
    # Make the package importable in the child regardless of cwd, and force
    # the non-interactive backend everywhere.
    base_env = dict(os.environ)
    base_env["PYTHONPATH"] = (str(Path(__file__).resolve().parent.parent)
                              + os.pathsep + base_env.get("PYTHONPATH", ""))
    base_env.setdefault("MPLBACKEND", "Agg")

    rec = {"stage": stage, "cmd": " ".join(cmd), "attempts": [],
           "fault_log": fault_log.name}
    for attempt in (1, 2):
        env = dict(base_env)
        if attempt == 2:
            env.update(_RETRY_ENV)
            logger.warning("[stage %s] retrying with thread-cap mitigations "
                           "(%s)", stage, ", ".join(_RETRY_ENV))
        t0 = _time.monotonic()
        try:
            proc = subprocess.run(cmd, env=env, timeout=timeout)
            rc, status = proc.returncode, _decode_exit(proc.returncode)
        except subprocess.TimeoutExpired:
            rc, status = None, f"TIMEOUT after {timeout:.0f}s (killed)"
        wall = round(_time.monotonic() - t0, 1)
        rec["attempts"].append({"attempt": attempt, "returncode": rc,
                                "status": status, "wall_s": wall})
        logger.info("[stage %s] attempt %d: %s (%.1fs)",
                    stage, attempt, status, wall)
        if rc == 0:
            break
        if rc is not None and not _is_native_fault(rc):
            break  # ordinary Python error: no retry
    rec["final_status"] = rec["attempts"][-1]["status"]
    rec["ok"] = rec["attempts"][-1]["returncode"] == 0
    return rec


def _write_outputs_isolated(store, out_dir, bbox, cell_xy, cell_z,
                            height_mode="default", *, columns_mode="diag",
                            columns_top_n=50, columns_all_max=None, viz3d=False, max_boxes=5_000_000,
                            roi_size=200.0, roi_cx=None, roi_cy=None,
                            do_full=True, do_roi=True, save_store=True,
                            extra_header=None, raw_store_dir=None,
                            provenance=None,
                            only_stages=None,
                            viz3d_stream=False, max_instances=4_000_000, tile_m=64.0,
                            inline_threshold_mb=64,
                            include_maps=True,
                            stage_timeout=_STAGE_TIMEOUT_DISABLED) -> list[dict]:
    """Isolated twin of ``_write_outputs()``: same deliverable SET, with
    every stage in a child process (see the block comment above). The four
    ``area_*.png`` maps are not pixel-identical twins, though: this path's
    maps stage saves dpi-120 matplotlib figures (about 960 px), while
    ``_write_outputs()`` writes the raster arrays natively via PIL.
    Returns the stage manifest. ``store`` may be ``None`` when resuming from an existing
    ``raw_store_dir`` (``--resume-from-store``). ``stage_timeout`` is the
    per-stage wall-clock limit in seconds (0 = none; see _run_stage())."""
    import json

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stages_dir = out_dir / "stages"
    stages_dir.mkdir(exist_ok=True)

    raw = Path(raw_store_dir) if raw_store_dir else out_dir / "store_raw"
    if store is not None:
        store.save_dir(raw)
        logger.info("persisted raw store -> %s", raw)
        # Parent's dirty heap pages become clean file-backed pages; the heap
        # copy (about 6 GB at metropolis scale) is freed
        # while the (idle) parent waits on stage children.
        store.swap_to_dir_mmap(raw)
    elif not (raw / "meta.json").exists():
        raise SystemExit(f"--resume-from-store: {raw} is not a complete raw "
                         "store (missing meta.json).")

    params = {
        "bbox": [float(v) for v in bbox], "cell_xy": float(cell_xy),
        "cell_z": float(cell_z), "height_mode": height_mode,
        "extra_header": extra_header, "columns_mode": columns_mode,
        "columns_top_n": int(columns_top_n),
        "columns_all_max": columns_all_max, "max_boxes": int(max_boxes),
        "roi_size": float(roi_size), "roi_cx": roi_cx, "roi_cy": roi_cy,
        "save_store": bool(save_store),
        "viz3d_stream": bool(viz3d_stream),
        "tile_m": float(tile_m),
        "max_instances": int(max_instances),
        "inline_threshold_mb": int(inline_threshold_mb),
        # Travels into the stage child (and into run_params.json), so a
        # --resume-from-store rerun writes the same manifest as the original.
        "provenance": provenance,
    }
    params_file = stages_dir / "params.json"
    params_file.write_text(json.dumps(params, indent=2), encoding="utf-8")
    # run_params.json travels WITH the store so --resume-from-store needs no
    # re-supplied CLI geometry.
    (raw / "run_params.json").write_text(json.dumps(params, indent=2),
                                         encoding="utf-8")

    n_columns = json.loads((raw / "meta.json").read_text())["n_columns"]

    stage_list = ["stats"]
    if n_columns == 0:
        logger.warning("Empty area store -- no maps/columns/3-D to draw.")
    else:
        if save_store:
            stage_list.append("persist_npz")
        if include_maps:
            # Shard mode passes include_maps=False: its bounded mosaic maps
            # are already written by the per-tile fold, and a native-res
            # re-render of a metropolis-scale merged store is exactly the
            # raster blow-up the mosaic exists to avoid.
            stage_list.append("maps")
        if columns_mode != "skip":
            stage_list.append("col_diag")
        if viz3d_stream:
            stage_list.append("viz3d_stream")
        else:
            if viz3d and do_roi:
                stage_list.append("viz3d_roi")
            if viz3d and do_full:
                stage_list.append("viz3d_full")
    if only_stages:
        enabled = set(stage_list)
        stage_list = [s for s in only_stages
                      if s in enabled | {"_crash_test"}]
        if not stage_list:
            # Refuse to "succeed" while doing nothing: a stage must ALSO be
            # enabled by its own flag (viz3d_* needs --viz3d/--viz3d-stream,
            # col_diag needs --columns-mode != skip, persist_npz needs
            # store-saving on). Silently running zero stages previously
            # exited 0 and - via all([]) == True on the empty stages.json -
            # was scored a full success that deleted the recovery data.
            raise SystemExit(
                f"--only-stage {list(only_stages)}: none of the requested "
                f"stage(s) are enabled for this run. Enabled stages here: "
                f"{sorted(enabled)}. Add the enabling flag (--viz3d / "
                f"--viz3d-stream / --columns-mode / store saving) or drop "
                f"--only-stage.")

    manifest = []
    for stage in stage_list:
        manifest.append(_run_stage(stage, store_dir=raw,
                                   params_file=params_file,
                                   out_dir=out_dir, stages_dir=stages_dir,
                                   stage_timeout=stage_timeout))
    (out_dir / "stages.json").write_text(json.dumps(manifest, indent=2),
                                         encoding="utf-8")

    logger.info("STAGE SUMMARY")
    for rec in manifest:
        logger.info("  %-12s %s%s", rec["stage"], rec["final_status"],
                    "" if rec["ok"] else
                    f"  [fault log: stages/{rec['fault_log']}; reproduce: "
                    f"{rec['cmd']}]")
    failed = [r["stage"] for r in manifest if not r["ok"]]
    if failed:
        logger.error("%d stage(s) failed: %s - every other output was "
                     "written; the raw store is preserved at %s for "
                     "re-runs (--resume-from-store).",
                     len(failed), ", ".join(failed), raw)
    return manifest


def _write_outputs(store, out_dir, bbox, cell_xy, cell_z, height_mode="default",
                   *, columns_mode="diag", columns_top_n=50, columns_all_max=None,
                   viz3d=False, max_boxes=5_000_000, roi_size=200.0,
                   roi_cx=None, roi_cy=None, do_full=True, do_roi=True,
                   save_store=True, extra_header=None, provenance=None,
                   viz3d_stream=False, max_instances=4_000_000, tile_m=64.0,
                   inline_threshold_mb=64) -> None:
    """Write the in-process area outputs into *out_dir*: always ``stats.txt`` (via ``stats_for``, so an mmap-backed store is reduced out of core); then, unless the store is empty, the persisted store when *save_store* is set, the 2-D maps through ``write_area_maps``, the ``columns/`` diagnostics unless *columns_mode* is ``"skip"``, and one 3-D viewer, the tiled ``area_stream.html`` plus its launcher when *viz3d_stream* is set, else the box viewer(s) via ``render_area_3d`` when *viz3d* is set. *extra_header* is prepended to the stats header lines."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # stats.txt - identical formatter/fields as the single-tile run.
    from .pipeline import _format_stats, MODES
    header = [
        f"Area bbox: x [{bbox[0]}, {bbox[2]}], y [{bbox[1]}, {bbox[3]}]",
        f"cell_xy: {cell_xy} m, cell_z: {cell_z} m",
        f"height mode: {height_mode}",
        f"shared origin: ({store.x_min}, {store.y_min}, {store.z_min})",
    ]
    if extra_header:
        header.insert(0, extra_header)
    from .store_streaming import stats_for
    (out_dir / "stats.txt").write_text(_format_stats(stats_for(store), header),
                                       encoding="utf-8")
    logger.info("wrote %s", out_dir / "stats.txt")

    if not store.columns:
        logger.warning("Empty area store -- no maps/columns/3-D to draw.")
        return

    # Persist the voxelization (stage B) before drawing anything, so the grid
    # is on disk even if a later rendering stage fails or is interrupted.
    if save_store:
        _write_area_store(store, out_dir, bbox, cell_xy, cell_z, height_mode,
                          provenance)

    # Four 2-D maps, through the SAME renderer the isolated maps stage uses
    # (visualization.write_area_maps), so the in-process and isolated paths
    # write byte-identical PNGs. This path used to save native-resolution
    # render_tile_map rasters through PIL while the stage drew matplotlib
    # figures, which left the two routes disagreeing on the same store.
    # Rasters are scattered ONCE for all four modes (the expensive reduction);
    # only compute the dominant class if a mode that reads it is rendered, and
    # keep the DEFAULT_MAX_PIXELS budget preflight's peak estimate assumes.
    from .visualization import rasters_from_store, write_area_maps
    rasters, _frame = rasters_from_store(
        store, need_dom=("max_points_class" in MODES))
    write_area_maps(store, out_dir, height_mode=height_mode, rasters=rasters)

    # columns/ diagnostics - the same "complete calculation of columns" the
    # single run produces (samples, top-complex, interval histogram, and the
    # per-column PNGs for 'top'/'all').
    if columns_mode != "skip":
        from .column_diagnostics import write_column_diagnostics
        write_column_diagnostics(store, out_dir / "columns",
                                 all_cap=columns_all_max,
                                 mode=columns_mode, top_n=columns_top_n,
                                 tile_label="area")
        logger.info("wrote %s", out_dir / "columns")

    # 3-D HTML viewer(s) straight from the area store.
    if viz3d_stream:
        # Tiled, view-dependent, FULL DETAIL: every interval is written; the
        # camera - not a stride - decides what is on the GPU. Never builds the
        # whole geometry dict: one tile-row band at a time.
        from .tiled_exporter import export_tiled_from_store
        out_path = out_dir / "area_stream.html"
        export_tiled_from_store(store, out_path,
                                title="Area - stream",
                                max_instances=max_instances,
                                tile_m=tile_m,
                                inline_threshold=inline_threshold_mb * 1024 * 1024)
        # Same double-click launcher the isolated stage writes, so both paths
        # leave an output directory that opens itself.
        from .launchers import write_stream_launcher
        write_stream_launcher(out_dir, out_path.name)
    elif viz3d:
        render_area_3d(store, out_dir, max_boxes=max_boxes, roi_size=roi_size,
                       roi_cx=roi_cx, roi_cy=roi_cy, do_full=do_full, do_roi=do_roi)



def _cleanup_remaining_laz(laz_dir: str | Path) -> None:
    """Delete any remaining .laz/.las files in *laz_dir*.

    Used by the sharded pipeline as a final sweep so that ``--delete-laz``
    guarantees all source files are removed even when some tiles were
    skipped, had empty results, or the loop aborted early.
    """
    laz_dir = Path(laz_dir)
    if not laz_dir.is_dir():
        return
    for p in list(laz_dir.iterdir()):
        if p.suffix.lower() in (".laz", ".las") and p.is_file():
            p.unlink(missing_ok=True)
            logger.info("  deleted %s", p.name)


