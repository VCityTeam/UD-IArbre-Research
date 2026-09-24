"""
"Shard beyond RAM": the area pipeline for jobs that cannot (or should not)
hold one merged ColumnStore in memory.
@ingroup t3_orchestr


Instead of accumulating tiles into a single store, each tile is voxelized,
written to ``<out>/shards/<tile>.npz`` (compressed struct-of-arrays,
``ColumnStore.save``), folded into streaming aggregates, and freed. Peak
memory is therefore O(one tile) + O(one bounded map mosaic), regardless of
how many tiles the bbox selects. Deliverables:

    stats.txt        combined statistics (exact sums/mins/maxes/mean folded
                     from the per-tile stats; with the column diagnostics on,
                     the whole dict comes from the shard sweep instead and
                     includes the exact column-height median)
    area_<mode>.png  the four 2-D maps, mosaicked at a bounded pixel budget
    columns/         the four diagnostic figures and the per-column pages,
                     computed straight from the shards - no merged store
    shards/*.npz     one reloadable store per tile
    shards/manifest.json   origin, cell sizes, per-shard extents

3-D: both views are produced without ever materializing the merged store.
The ROI view reloads only the shards intersecting the ROI window and merges
those (a disjoint, cheap union). The full view is assembled by
``render_full_from_shards``: each shard is streamed, decimated at one global
column stride (chosen so the whole-area box count respects ``max_boxes``), and
its survivors concatenated - which, because the stride runs on the shared
global grid index, equals the merged store thinned at that stride, at a peak
cost of O(one shard) + O(<= max_boxes boxes).

Column diagnostics follow the same rule. Without a merge they are computed by
a sequential sweep over the shards (voxelizer.shard_diagnostics): a
column lives in exactly one shard, so the reductions behind the four figures,
the top-N pages and the statistics fold over disjoint parts, and each selected
column is drawn by loading only the shard that owns it. That sweep also
produces the statistics the run is read off, median included.

The optional merge phase keeps the same discipline. With ``merge_shards`` the
shards are assembled into ``<out>/store_raw/`` one key band at a time, straight
to disk (voxelizer.merge_streaming), and the output stages attach to
that store by memory map - so area.npz, the per-column diagnostics and the
viewers cost one band of RAM, not one merged store. There is no in-memory
variant of that merge.

The design follows the external-memory pattern (process blocks against
bounded state, keep on-disk artifacts reloadable): Cignoni et al., IEEE TVCG
9(4), 2003; Lindstrom & Pascucci, IEEE TVCG 8(3), 2002.
"""

from __future__ import annotations

import gc
import json
import logging
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path
import numpy as np

from ._download_common import download_one, load_values, select_tiles
from .data_structures import ColumnStore
from .io_laz import DEFAULT_EPSG, read_laz, read_laz_header
from .run_utils import make_provenance
from .preflight import check_rss_budget
from .voxelize import (DEFAULT_CHUNK_SIZE, _run_order, voxelize,
                       voxelize_laz_chunked)
from . import visualization as viz
from .classes_config import CLASS_NAMES

logger = logging.getLogger(__name__)

# Mosaic pixel budget - its own constant (rather than reusing
# visualization.DEFAULT_MAX_PIXELS) because in shard mode the five scalar
# rasters stay ALIVE for the whole run, not just one render call. Cost is
# 19 B/px: 32 MP ~ 0.6 GB | 64 MP ~ 1.2 GB | 128 MP ~ 2.4 GB | 256 MP ~ 4.9 GB.
# 64-128 MP is the suggested resident budget here; it currently ships equal
# to the single-store default (256 MP) for detail, and is a per-run knob via
# run_area_sharded(map_max_pixels=...).
DEFAULT_MOSAIC_MAX_PIXELS = 256_000_000


class _Aggregates:
    """Streaming, exact-where-possible combination of per-shard stats."""

    def __init__(self):
        """Start with zeroed per-class counters and undefined extremes; every field is filled by add()."""
        self.points_by_code = np.zeros(256, dtype=np.int64)
        self.intervals_by_code = np.zeros(256, dtype=np.int64)
        self.n_columns = 0
        self.n_single = 0
        self.niv_min = None
        self.niv_max = 0
        self.h_min = None       # (height_m, {ix, iy})
        self.h_max = None
        self.h_sum = 0.0
        self.z_low = None
        self.z_high = None
        self.n_ov_iv = 0        # shared-voxel counters (exact sums)
        self.n_ov_col = 0

    def add(self, stats: dict) -> None:
        """Fold one shard's ``ColumnStore.stats()`` dict in: per-class counts (names mapped back to codes) and the overlap counters are summed, column count, single-interval count and height sum accumulated, and the z range, interval-count range and shortest/tallest column widened; a strictly better height replaces an extreme, so the first shard seen keeps a tie. An empty shard is ignored."""
        if stats["n_columns"] == 0:
            return
        for name, v in stats["points_per_class"].items():
            code = _code_of(name)
            self.points_by_code[code] += v
        for name, v in stats["intervals_per_class"].items():
            code = _code_of(name)
            self.intervals_by_code[code] += v
        self.n_columns += stats["n_columns"]
        self.n_single += stats["n_single_interval_columns"]
        nmin, nmax = (stats["intervals_per_column_min"],
                      stats["intervals_per_column_max"])
        self.niv_min = nmin if self.niv_min is None else min(self.niv_min, nmin)
        self.niv_max = max(self.niv_max, nmax)
        sc, tc = stats["shortest_column"], stats["tallest_column"]
        if self.h_min is None or sc["height_m"] < self.h_min[0]:
            self.h_min = (sc["height_m"], sc)
        if self.h_max is None or tc["height_m"] > self.h_max[0]:
            self.h_max = (tc["height_m"], tc)
        self.h_sum += stats["column_height_sum_m"]
        self.n_ov_iv += int(stats.get("n_overlap_intervals", 0))
        self.n_ov_col += int(stats.get("n_overlap_columns", 0))
        z0, z1 = stats["z_min_m"], stats["z_max_m"]
        self.z_low = z0 if self.z_low is None else min(self.z_low, z0)
        self.z_high = z1 if self.z_high is None else max(self.z_high, z1)

    def to_stats(self) -> dict:
        """A stats dict shaped like ColumnStore.stats() (median omitted)."""
        n_intervals = int(self.intervals_by_code.sum())
        n_points = int(self.points_by_code.sum())
        seen = np.where(self.intervals_by_code > 0)[0]
        ppc = {CLASS_NAMES.get(int(c), f"class_{c}"): int(self.points_by_code[c])
               for c in seen}
        ipc = {CLASS_NAMES.get(int(c), f"class_{c}"):
               int(self.intervals_by_code[c]) for c in seen}
        ppc = dict(sorted(ppc.items(), key=lambda kv: -kv[1]))
        ipc = dict(sorted(ipc.items(), key=lambda kv: -kv[1]))
        n_cols = max(self.n_columns, 1)
        return {
            "n_columns": self.n_columns,
            "n_intervals": n_intervals,
            "n_points": n_points,
            "avg_intervals_per_column": round(n_intervals / n_cols, 2),
            "points_per_class": ppc,
            "intervals_per_class": ipc,
            "z_min_m": self.z_low,
            "z_max_m": self.z_high,
            "column_height_min_m": self.h_min[0] if self.h_min else None,
            "column_height_max_m": self.h_max[0] if self.h_max else None,
            "column_height_mean_m": (round(self.h_sum / n_cols, 3)
                                     if self.n_columns else None),
            # Exact global median needs every column height at once - the
            # single thing shard mode gives up. _format_stats skips None.
            "column_height_median_m": None,
            "column_height_sum_m": self.h_sum,
            "shortest_column": self.h_min[1] if self.h_min else None,
            "tallest_column": self.h_max[1] if self.h_max else None,
            "intervals_per_column_min": self.niv_min or 0,
            "intervals_per_column_max": self.niv_max,
            "n_single_interval_columns": self.n_single,
            # Exact across shards whenever tile boundaries fall on lattice
            # lines (cell_xy divides the 500 m pitch, true of every shipped
            # resolution): columns are then disjoint across shards and the
            # per-shard shared-voxel counts sum without double counting.
            "n_overlap_intervals": self.n_ov_iv,
            "n_overlap_columns": self.n_ov_col,
        }


_NAME_TO_CODE = None


def _code_of(class_name: str) -> int:
    """ASPRS code for a class name as printed in a stats dict: the ``CLASS_NAMES`` inverse (built once and cached in ``_NAME_TO_CODE``), else the number after a ``class_`` prefix, else 255."""
    global _NAME_TO_CODE
    if _NAME_TO_CODE is None:
        _NAME_TO_CODE = {v: k for k, v in CLASS_NAMES.items()}
    if class_name in _NAME_TO_CODE:
        return _NAME_TO_CODE[class_name]
    if class_name.startswith("class_"):
        try:
            return int(class_name[6:])
        except ValueError:
            pass
    return 255


def _tile_iter_stream(json_file: Path, bbox, tile_pitch: int, limit: int):
    """The inventory tile records of *json_file* that fall in the origin window of *bbox* at *tile_pitch*, as ``select_tiles`` orders them, truncated to the first *limit* when *limit* is positive."""
    values = load_values(Path(json_file))
    from ._download_common import origin_window
    selected = select_tiles(values, *origin_window(bbox, tile_pitch))
    if limit > 0:
        selected = selected[:limit]
    return selected


def _merge_all_shards(shards_dir, cell_z, *, store_dir,
                      group_intervals=True, group_gap=None,
                      band_intervals=None):
    """Merge every .npz shard of ``shards_dir`` into the raw store
    ``store_dir``, out of core.

    Returns a memory-mapped view of the merged store, or None if there was
    nothing to merge (no manifest, no shard listed, none on disk).

    The merge is assembled band by band directly on disk by
    voxelizer.merge_streaming.merge_shards_streaming(), which calls
    ``ColumnStore.merge_many`` once per key band on inputs small enough to fit
    and concatenates the band results into memory-mapped final arrays. This is
    the ONLY sharded merge: one ``merge_many`` over every loaded shard would
    need the sources plus a full copy of the result in RAM, and its int32
    offsets cannot address more than 2^31 intervals. The band decomposition
    returns the same arrays byte for byte - see the ``merge_streaming`` module
    docstring for why, and tests/test_merge_streaming.py for the proof against
    ``merge_many``, which remains the primitive and the test oracle.

    ``band_intervals`` is the RAM/speed dial (None = merge_streaming's
    default): bigger bands hold more of the store at once and re-read the
    compressed shards less often.

    When ``group_intervals`` is set, grouping happens inside
    ``merge_shards_streaming`` and its unit is the BAND SLICE of a shard, not
    the shard: each shard is loaded, cut down to the key band being assembled,
    and that slice is grouped - BEFORE the merge_many for the band, not once
    on the assembled store. Grouping is a pure per-column operation (a column
    boundary always breaks a run) and a band boundary IS a column boundary, so
    slicing first changes nothing. Likewise, when tile boundaries fall on
    lattice lines (cell_xy divides the 500 m tile pitch, true of every shipped
    resolution), every column lives entirely within one shard; grouping per
    shard slice is then byte-identical to grouping the merged whole. (A
    cell_xy that does not divide the pitch can let one boundary column
    straddle two shards; the equivalence breaks there.) It also keeps each
    band small: the band's buffers are sized to the
    GROUPED interval count, and the full raw area store is never materialized
    (a final ``.grouped()`` would otherwise have to hold the raw store alive
    alongside its grouped copy - see ``ColumnStore.grouped``).
    """
    from .merge_streaming import DEFAULT_BAND_INTERVALS, merge_shards_streaming
    summary = merge_shards_streaming(
        Path(shards_dir), store_dir, group_intervals=group_intervals,
        group_gap=group_gap, cell_z=cell_z, overwrite=True,
        band_intervals=(DEFAULT_BAND_INTERVALS if band_intervals is None
                        else int(band_intervals)))
    if summary is None:
        return None
    return ColumnStore.load_dir(store_dir, mmap=True)

def _atomic_shard_save(store, shard_path: Path) -> None:
    """Write ``shard_path`` atomically (tmp file + ``os.replace``).

    A process killed mid-write (RAM watchdog, power loss, native crash in a
    later tile) must never leave a truncated .npz that a ``resume_shards``
    rerun would trust. The tmp name keeps the ``.npz`` suffix so numpy does
    not append another one, and carries a ``_tmp_`` prefix so the startup
    sweep in ``run_area_sharded`` can recognise and delete leftovers.
    """
    tmp = shard_path.with_name("_tmp_" + shard_path.name)
    store.save(tmp)
    os.replace(tmp, shard_path)


def save_single_tile_shard(store, out_dir, tile_stem, *,
                           stats: dict | None = None,
                           height_mode: str = "default",
                           keep_classes=None,
                           group_intervals: bool = True,
                           group_gap: float | None = None,
                           epsg: int = DEFAULT_EPSG) -> Path | None:
    """Write one already-voxelized tile as a shard set (``single --shards``).

    The single-file front end of the shard format. ``python -m voxelizer
    single`` already holds the tile's ``ColumnStore`` in memory; this is what
    turns that one store into the same on-disk shape an area run leaves in
    ``shards/`` - one ``.npz`` (``ColumnStore.save``) plus the
    ``manifest.json`` the shard tooling reads (schema ``voxelizer.shards/2``,
    per-shard extents, provenance). Nothing here is new to the format: the
    record is built exactly as ``run_area_sharded``'s per-tile loop builds it,
    so ``merge_streaming``, ``shard_diagnostics`` and ``archive_cli`` consume
    a single-tile folder and an area folder identically.

    The store keeps the grid origin ``process_single_tile`` gave it (the
    tile's own data minimum; see ``voxelize.voxelize`` with ``origin=None``).
    A single-tile shard is therefore self-contained and complete, but it sits
    on its own lattice: ``merge_streaming._check_grid`` refuses to merge it
    with a shard built on a different origin, so a corpus assembled from
    several ``single --shards`` runs over different tiles is a set of
    one-shard sets, not one mergeable set. Rebuild an area as one mergeable
    set with ``area_cli area --shard``.

    One run writes one shard, so ``manifest.json`` describes this run alone:
    it is overwritten on every call, and any other ``.npz`` already sitting in
    ``shards/`` is left on disk but unlisted (a warning names them). Give
    each tile its own run directory - the default auto-incrementing
    ``outputs/RunN/single/`` does exactly that - or use ``area_cli area`` for
    many tiles in one folder.

    @param store The voxelized tile (``ColumnStore`` with occupied columns).
    @param out_dir Run directory; shards go into ``<out_dir>/shards/``.
    @param tile_stem File name stem of the source tile, used for the shard name.
    @param stats The store's ``stats()`` dict when the caller already has it
        (``process_single_tile`` returns it); recomputed when None.
    @param height_mode Recorded in the manifest's header block.
    @param keep_classes Recorded in the provenance block (None keeps all).
        ``single`` does not filter by class, so this is None in practice.
    @param group_intervals / @param group_gap Recorded in the provenance block
        as the settings a later merge or shard sweep would apply. The shard
        itself is always raw (ungrouped), exactly as in the area pipeline.
    @param epsg EPSG code recorded in the provenance block.
    @return The shard path, or None when the tile has no occupied column
        (an empty tile leaves no shard, matching the area pipeline).
    """
    out_dir = Path(out_dir)
    shards_dir = out_dir / "shards"
    shards_dir.mkdir(parents=True, exist_ok=True)
    shard_path = shards_dir / f"{tile_stem}.npz"

    kb = store.key_bounds()
    if kb is None or not len(store.columns):
        # Empty tile: no shard, and any stale same-named file goes - a later
        # consumer trusts file existence (archive_cli globs *.npz,
        # --resume-shards adopts by name), so silence here would let another
        # run's data be read as this tile's.
        shard_path.unlink(missing_ok=True)
        logger.warning("tile %s has no occupied column - no shard written.",
                       tile_stem)
        return None

    _atomic_shard_save(store, shard_path)
    if stats is None:
        stats = store.stats()

    x0, y0, z0 = float(store.x_min), float(store.y_min), float(store.z_min)
    cxy, cz = float(store.cell_xy), float(store.cell_z)
    record = {
        "file": shard_path.name,
        "n_columns": len(store.columns),
        "n_intervals": int(stats["n_intervals"]),
        "ix_min": kb[0], "iy_min": kb[1], "ix_max": kb[2], "iy_max": kb[3],
        "x0_m": x0 + kb[0] * cxy, "y0_m": y0 + kb[1] * cxy,
        "x1_m": x0 + (kb[2] + 1) * cxy,
        "y1_m": y0 + (kb[3] + 1) * cxy,
    }
    (shards_dir / "manifest.json").write_text(json.dumps({
        "schema": "voxelizer.shards/2",
        "bbox": [record["x0_m"], record["y0_m"], record["x1_m"], record["y1_m"]],
        "clip": False, "cell_xy": cxy, "cell_z": cz,
        "origin": [x0, y0, z0],
        "height_mode": height_mode,
        "aborted": False, "tiles_done": 1, "tiles_total": 1,
        "resumed_tiles": 0, "n_failed_tiles": 0,
        "shards_are_raw": True,
        **make_provenance(group_intervals=group_intervals,
                          group_gap=group_gap, keep_classes=keep_classes,
                          epsg=epsg, run_order=_run_order()),
        "shards": [record],
    }, indent=2), encoding="utf-8")

    unlisted = [p.name for p in sorted(shards_dir.glob("*.npz"))
                if p.name != shard_path.name]
    if unlisted:
        logger.warning("shards/ already held %d shard(s) this manifest does "
                       "not describe (%s); one run writes one shard, so use a "
                       "fresh --output-dir per tile.", len(unlisted),
                       ", ".join(unlisted))
    logger.info("saved shard %s + %s", shard_path.name,
                shards_dir / "manifest.json")
    return shard_path


def _try_load_shard(shard_path: Path, *, cell_xy, cell_z, x0, y0, z0):
    """Load an existing shard for resume, or return None to re-voxelize.

    A shard is reusable only if its embedded metadata (written by
    ``ColumnStore.save``) matches this run's lattice exactly: same cell
    sizes and same shared origin. ``z0`` may still be None when the first
    tiles of a resumed run are all reused shards; in that case the caller
    adopts the shard's own z reference (all shards of one run share it, and the
    per-shard check below enforces that from the second shard on).
    Corrupt or mismatched files are logged and re-voxelized, never trusted.
    """
    from .data_structures import ColumnStore
    try:
        store = ColumnStore.load(shard_path)
    except Exception as exc:  # noqa: BLE001 - any unreadable shard => redo
        logger.warning("  existing shard %s is unreadable (%s) - "
                       "re-voxelizing.", shard_path.name, exc)
        return None
    ok = (math.isclose(store.cell_xy, cell_xy, abs_tol=1e-9)
          and math.isclose(store.cell_z, cell_z, abs_tol=1e-9)
          and math.isclose(store.x_min, x0, abs_tol=1e-6)
          and math.isclose(store.y_min, y0, abs_tol=1e-6)
          and (z0 is None or math.isclose(store.z_min, z0, abs_tol=1e-6)))
    if not ok:
        logger.warning(
            "  existing shard %s does not match this run "
            "(cells %.3g/%.3g vs %.3g/%.3g, origin (%.2f, %.2f, %s) vs "
            "(%.2f, %.2f, %s)) - re-voxelizing.",
            shard_path.name, store.cell_xy, store.cell_z, cell_xy, cell_z,
            store.x_min, store.y_min, store.z_min, x0, y0, z0)
        return None
    return store


def _check_run_config(shards_dir: Path, config: dict, *, resume: bool) -> None:
    """Guard ``resume_shards`` against mixing shards from different runs.

    The shard .npz carries lattice metadata, but not everything that shapes
    a shard's CONTENT (``keep_classes``, clipping, the bbox that selects the
    tiles). We persist those in ``shards/run_config.json`` on every run and,
    when resuming, refuse to continue over a folder produced with different
    parameters - silently blending two runs would corrupt statistics without
    any error. Without ``resume`` a mismatch warns and then CLEARS the folder.
    Overwriting tile by tile is not enough: every tile this run processes does
    replace its shard, but a tile outside the new selection keeps a shard built
    under the old parameters, and the config on disk is about to say the new
    ones. A later ``--resume-shards`` would then find a matching config and
    adopt those foreign shards, because ``_try_load_shard`` checks only the
    lattice (cell sizes and origin) and cannot see ``keep_classes``, clipping
    or the run order. Deleting is the only way to keep the config a true
    statement about every shard beside it.

    The config is written at run START, not at the end. An aborted run must
    leave its parameters behind, or ``--resume-shards`` - which exists to
    continue exactly such a run - would have nothing to check them against.
    """
    cfg_path = shards_dir / "run_config.json"
    if cfg_path.is_file():
        try:
            old = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            old = None
        if old is not None:
            # Configs written before the run-order toggle existed were all
            # produced by the only order there was.
            old.setdefault("run_order", "height_first")
        if old is not None and old != config:
            diff = {k: (old.get(k), config[k]) for k in config
                    if old.get(k) != config[k]}
            if resume:
                raise ValueError(
                    f"resume_shards: {cfg_path} was written by a run with "
                    f"different parameters {diff}; resuming over it would "
                    f"blend incompatible shards. Use a fresh --output-dir "
                    f"or delete the shards folder.")
            logger.warning("shards folder was produced with different "
                           "parameters %s - deleting the existing shards, "
                           "because a tile outside this run's selection "
                           "would otherwise keep a shard built under the "
                           "old parameters.", diff)
            n_removed = 0
            for stale in sorted(shards_dir.glob("*.npz")):
                stale.unlink(missing_ok=True)
                n_removed += 1
            # The manifest and the failure record describe the shard set
            # that has just been removed.
            (shards_dir / "manifest.json").unlink(missing_ok=True)
            (shards_dir / "failed_tiles.json").unlink(missing_ok=True)
            logger.warning("  removed %d shard file(s) from %s",
                           n_removed, shards_dir)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(config, indent=2), encoding="utf-8")


def _voxelize_tile_in_child(path, shard_path: Path, *, cell_xy, cell_z,
                            origin, clip_bbox, keep_classes, chunk_size,
                            retry_lazrs: bool):
    """Voxelize one tile in a child process (crash isolation).

    Returns ``(store, None)`` on success (``store`` is None for an empty
    tile), or ``(None, failure_record)`` after all attempts failed. The
    child writes the shard atomically; the parent then loads it back for
    the aggregate/mosaic fold, so a native decompressor crash costs one
    tile (plus one process start and one shard reload per tile) instead of
    the run.

    Attempt policy: first try with the pipeline's pinned backend (laszip);
    if the child dies and ``retry_lazrs`` is set, retry ONCE with the
    single-thread lazrs backend as a second opinion (the two backends were
    checked to agree byte for byte).
    """
    from .data_structures import ColumnStore
    # repr() round-trips floats exactly, so the child quantizes on the
    # bit-identical lattice as the parent.
    cmd = [sys.executable, "-m", "voxelizer.shard_worker",
           "--laz", str(path), "--out", str(shard_path),
           "--cell-xy", repr(float(cell_xy)), "--cell-z", repr(float(cell_z)),
           "--origin", repr(float(origin[0])), repr(float(origin[1])),
           repr(float(origin[2])),
           "--chunk-size", str(int(chunk_size))]
    if clip_bbox is not None:
        cmd += ["--clip"] + [repr(float(v)) for v in clip_bbox]
    # `is not None`, NOT truthiness: an EMPTY set means "keep nothing" and
    # must reach the child as --keep-classes "" - dropping the flag made
    # the isolated child keep EVERYTHING while the in-process path kept
    # nothing for the same input.
    if keep_classes is not None:
        cmd += ["--keep-classes", ",".join(str(c) for c in sorted(keep_classes))]

    attempts, codes, tails = 0, [], []
    for attempt in (1, 2):
        env = os.environ.copy()
        if attempt == 2:
            if not retry_lazrs:
                break
            env["VOXELIZER_LAZ_BACKEND"] = "lazrs"
            logger.warning("  retrying %s with the single-thread lazrs "
                           "backend ...", Path(path).name)
        attempts = attempt
        # No timeout on purpose: a big tile on a loaded machine can be slow,
        # and the in-process path has no timeout either. A HUNG (not crashed)
        # decoder would stall the run in both designs.
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
        codes.append(proc.returncode)
        tail = "\n".join(proc.stderr.strip().splitlines()[-3:])
        tails.append(tail)
        if proc.returncode == 0:
            if not shard_path.is_file():
                return None, None          # clean run, empty tile
            store = _try_load_shard(shard_path, cell_xy=cell_xy,
                                    cell_z=cell_z, x0=origin[0], y0=origin[1],
                                    z0=origin[2])
            if store is not None:
                return store, None
            # exit 0 but unreadable/mismatched shard: treat as a failed
            # attempt (fall through to retry / failure record).
            logger.warning("  child exited 0 but shard is unusable.")
        else:
            rc = proc.returncode
            # Decode the crash for the log, per platform: on POSIX,
            # subprocess reports death-by-signal as -signum (-11 = SIGSEGV,
            # -9 = SIGKILL e.g. the docker OOM killer). On Windows the
            # NTSTATUS surfaces as a large POSITIVE unsigned code
            # (3221225477 = 0xC0000005 access violation; measured on
            # CPython 3.13), so rc < 0 does not occur there and the nt
            # hex decoration below is currently unreachable.
            decoration = ""
            if rc < 0:
                if os.name == "nt":
                    decoration = f" (0x{rc & 0xFFFFFFFF:08X})"
                else:
                    import signal as _signal
                    try:
                        decoration = f" ({_signal.Signals(-rc).name})"
                    except ValueError:
                        decoration = f" (signal {-rc})"
            logger.error("  child failed on %s: exit %s%s%s",
                         Path(path).name, rc, decoration,
                         (f"; stderr: {tail}" if tail else ""))
    failure = {"file": Path(path).name, "attempts": attempts,
               "returncodes": codes, "stderr_tails": tails}
    return None, failure


def run_area_sharded(
    bbox,
    out_dir,
    *,
    stream: bool,
    json_file=None,
    laz_dir=None,
    cell_xy: float = 0.5,
    cell_z: float = 0.5,
    keep_classes=None,
    use_chunks: bool = True,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    clip: bool = True,
    tile_pitch: int = 500,
    limit: int = 0,
    delete_laz: bool = False,
    height_mode: str = "default",
    viz3d: bool = False,
    max_boxes: int = 5_000_000,
    roi_size: float = 300.0,
    roi_cx=None,
    roi_cy=None,
    do_full: bool = True,
    do_roi: bool = True,
    max_rss_mb: float | None = None,
    map_max_pixels: int = DEFAULT_MOSAIC_MAX_PIXELS,
    templaz_dir: Path | str | None = None,
    delete_shards: bool = False,
    merge_shards: bool = False,
    merge_band_intervals: int | None = None,
    stage_timeout: float = 0,
    resume_shards: bool = False,
    isolate_tiles: bool = False,
    retry_lazrs: bool = False,
    columns_mode: str = "skip",
    columns_top_n: int = 50,
    columns_all_max: int | None = None,
    group_intervals: bool = True,
    group_gap: float | None = None,
    keep_area_raw: bool = False,
    save_store: bool = True,
    only_stages: list[str] | None = None,
    viz3d_stream: bool = False,
    max_instances: int = 4_000_000,
    tile_m: float = 64.0,
    inline_threshold_mb: int = 64,
) -> dict:
    """
    Sharded area run: bounded-memory alternative to ``area_cli.run_area``.

    Each tile is voxelized independently and saved as a ``.npz`` shard in
    ``<out_dir>/shards/``.  Whenever at least one tile is selected, a
    manifest and stats file are written from shard-level aggregates (no
    merged store needed; a bbox selecting zero tiles returns early with
    only a summary dict); the mosaicked
    tile maps are written whenever any column was folded (skipped for an
    all-empty result).
    Optional 3-D views stream shards one at a time, keeping peak RAM bounded.

    When ``merge_shards=True``, after the per-tile loop every shard is merged
    into ``<out_dir>/store_raw/`` - out of core, one key band at a time - and
    ``area.npz``, column diagnostics and any additional output stages are
    written from that memory-mapped store. The full deliverable set therefore
    costs bounded memory too: peak RAM is one band (see
    _merge_all_shards()), not the merged store.

    @param bbox ``(xmin, ymin, xmax, ymax)`` in metres (RGF93/CC46 EPSG:3946).
    @param out_dir Output directory; shards go into ``<out_dir>/shards/``.
    @param stream When True, download each tile from a Grand Lyon inventory
        JSON (requires ``json_file``).  When False, read local files from
        ``laz_dir``.
    @param json_file Path to the Grand Lyon inventory JSON
        (``{"values": [...]}``), required when ``stream=True``.
    @param laz_dir Directory with local ``.laz``/``.las`` files, required
        when ``stream=False``.
    @param cell_xy Horizontal cell size in metres (default 0.5).
    @param cell_z Vertical cell size in metres (default 0.5).
    @param keep_classes If set, only these ASPRS class codes are kept; all
        others are dropped before voxelization.  ``None`` keeps all classes.
    @param use_chunks When True (the default), stream each tile with
        ``voxelize_laz_chunked`` (bounded memory).  When False, load the
        whole file via ``read_laz``.
    @param chunk_size Points per chunk when ``use_chunks=True`` (default
        ``DEFAULT_CHUNK_SIZE``, 5,000,000).
    @param clip When True (the default), clip points to the area bounding
        box before voxelization.
    @param resume_shards Crash resilience (default False): reuse every shard
        already present in ``<out_dir>/shards/`` instead of re-voxelizing
        its tile (the shard's embedded lattice metadata is validated first;
        mismatching or corrupt shards are re-voxelized).
        ``shards/run_config.json`` guards against resuming over a folder
        produced with different parameters. Empty tiles leave no shard, so
        they are re-examined on resume - cheap and harmless.
    @param isolate_tiles Crash resilience (default False): voxelize each tile
        in a child process (``voxelizer.shard_worker``) so a native
        decompressor crash costs one tile instead of the whole run. Failed
        tiles are recorded in ``shards/failed_tiles.json`` and the run
        continues. Costs roughly a process start plus one shard reload per
        tile.
    @param retry_lazrs With ``isolate_tiles`` (default False): after a child
        crash, retry that tile once with the single-thread lazrs backend as
        a second opinion before recording it as failed (a corrupted tile
        can fault the parallel backend deterministically; the
        single-thread backend rejects it cleanly instead).
    @param tile_pitch Tile size in metres of the acquisition grid (default
        500). Used to compute the exact origin-selection window when
        selecting tiles via the inventory JSON (see
        _download_common.origin_window): a tile is selected iff its
        pitch-sized extent can overlap the query bbox.
    @param limit Maximum number of tiles to process (default 0 = no limit).
        Useful for testing on a subset.
    @param delete_laz DESTRUCTIVE (default False). When True, delete each
        local ``.laz`` file after successful voxelization.
    @param height_mode Height visualisation mode for tile maps (default
        "default"; passed to ``colorize_rasters``).
    @param viz3d When True, build 3-D HTML view(s) from the collected
        shards (default False).
    @param max_boxes Box budget for the full-area 3-D view (default
        5,000,000; auto-thinned by striding).
    @param roi_size Side length in metres of the region-of-interest 3-D view
        (default 300.0).
    @param roi_cx ROI centre x coordinate (RGF93/CC46); ``None`` uses the
        area centre.
    @param roi_cy ROI centre y coordinate (RGF93/CC46); ``None`` uses the
        area centre.
    @param do_full When True (the default) and ``viz3d=True``, render the
        full-area 3-D view.
    @param do_roi When True (the default) and ``viz3d=True``, render the ROI
        3-D view.
    @param max_rss_mb Hard RSS limit in MB.  If exceeded during the per-tile
        loop the run is aborted with partial outputs; ``None`` (the default)
        disables the limit.
    @param map_max_pixels Maximum pixel count for the mosaicked tile maps
        (default 256,000,000); the raster is aggregated if the native grid
        exceeds this.
    @param templaz_dir Temp directory for downloaded tiles (``stream=True``).
        ``None`` uses ``<out_dir>/templaz/``.
    @param delete_shards When True, delete the ``shards/`` directory after
        all consumers (maps, 3-D views, optional merge) have read them
        (default False).
    @param merge_shards When True, merge all shards into
        ``<out_dir>/store_raw/`` and write ``area.npz``, per-column
        diagnostics, and any additional stages from it (default False). The
        merge is always out of core (band by band, straight to disk - see
        _merge_all_shards()), so peak RAM does not scale with the merged
        store.
    @param merge_band_intervals Intervals per merge band, the RAM/speed dial
        of that merge (``None``, the default, means
        ``voxelizer.merge_streaming.DEFAULT_BAND_INTERVALS``). Smaller
        bands cost less RAM and re-read the compressed shards more often.
    @param stage_timeout Wall-clock limit in seconds for each isolated output
        stage (default 0 = disabled; see area_outputs._run_stage()).
    @param columns_mode Column diagnostics mode (default "skip"): ``"skip"``,
        ``"diag"``, ``"top"`` or ``"all"``. Without ``merge_shards`` the
        diagnostics are computed directly from the shards
        (voxelizer.shard_diagnostics) - a second pass over the corpus, whose
        sweep also supplies the exact stats.txt. With ``merge_shards`` they
        come off the merged store, as every other output stage does.
    @param columns_top_n Number of top columns to write when
        ``columns_mode="top"`` (default 50).
    @param columns_all_max Max columns to write when ``columns_mode="all"``.
        ``None`` is NOT "no limit": it means the module default cap,
        ``column_diagnostics._MODE_ALL_MAX_COLUMNS`` (500,000). Only an
        explicit ``0`` removes the cap.
    @param group_intervals When True (the default), coalesce adjacent
        same-class intervals. This governs the from-shards diagnostics sweep
        as well as the optional merge: without ``merge_shards`` each shard is
        grouped as it is read for the sweep, so the figures and statistics
        describe grouped intervals either way.
    @param group_gap Gap tolerance in METRES for interval grouping (``None``
        = no gap limit: ``ColumnStore.grouped`` then merges same-class runs
        across any gap). It is converted to whole voxels internally as
        ``round(group_gap / cell_z)``, so the effective tolerance is
        quantised to the vertical cell size.
    @param keep_area_raw With ``group_intervals`` on (default False), also
        write the ungrouped merged store as ``area_raw.npz`` (the grouped
        store remains ``area.npz``). Produced by a SECOND out-of-core merge
        pass into a scratch store directory, which is compressed into the
        .npz and then removed, so peak RAM stays at one band. No effect when
        grouping is off (``area.npz`` is already the raw store then).
        Caveat: the ungrouped store is the largest artefact a run produces,
        and this asks for it twice over - once as the scratch store
        directory, once as the compressed .npz - plus a second full pass over
        the shard corpus. It is a disk-and-time bill now, not a RAM risk. The
        raw data already lives in the per-tile shards, and an ungrouped store
        can be rebuilt later over a smaller sub-area (``resume_shards`` with
        ``group_intervals=False``) or straight from the shards with
        ``python -m voxelizer.merge_streaming``.
    @param only_stages If set, restricts the isolated merge phase to these
        stage names (``stats``, ``persist_npz``, ``col_diag``, ``viz3d_roi``,
        ``viz3d_full``, ``viz3d_stream``; ``maps`` never runs here - the
        mosaics are already written by the fold). Validated by the shared
        writer: an impossible selection raises instead of silently doing
        nothing.
    @param save_store Write ``area.npz`` + ``area_manifest.json`` (default
        True). False skips the persisted grid, so the 2-D/3-D stages cannot
        later be re-run from it without re-voxelizing.
    @param viz3d_stream Render the streaming HTML viewer instead of the
        legacy singleton one (default False). Needs ``merge_shards``: the
        exporter walks ONE store's key order, which a set of shards does not
        have (the refusal is logged here, and refused up front by
        ``area_cli``).
    @param max_instances Hard GPU instance cap for that viewer (default
        4,000,000).
    @param tile_m Streaming viewer payload tile size in metres (default
        64.0).
    @param inline_threshold_mb If the ``.bin`` is smaller than this many MB
        (default 64), it is base64-embedded in the HTML and the sidecar is
        deleted. ``max_instances``, ``tile_m`` and ``inline_threshold_mb``
        are forwarded to the isolated merge phase and do nothing without
        ``viz3d_stream``.
    @return Summary dict with ten keys: ``n_tiles``, ``tiles_done``,
        ``failed_stages``, ``n_columns``, ``aborted``, ``shards_dir``,
        ``out_dir``, ``resumed_tiles``, ``failed_tiles`` and ``stats``. ONE
        EARLY RETURN IS NARROWER: when no tile intersects the bbox the run
        stops before any work and returns six keys only - ``n_tiles`` and
        ``tiles_done`` at 0, ``n_columns`` 0, ``aborted`` False,
        ``shards_dir`` and ``out_dir`` - with no ``stats`` and none of the
        per-tile or per-stage lists, because nothing ran to report on. A
        caller reading ``stats`` off this must use ``.get``.
    @throws ValueError when ``stream=True`` and ``json_file`` is None.
    @throws ValueError when ``resume_shards=True`` and ``shards_dir``
        already holds a ``run_config.json`` written with different run
        parameters (propagated from _check_run_config).
    """
    bbox = tuple(float(v) for v in bbox)
    out_dir = Path(out_dir)
    shards_dir = out_dir / "shards"
    shards_dir.mkdir(parents=True, exist_ok=True)

    # -- crash-resilience bookkeeping ---------------------------------------
    # Refuse to resume over shards from a differently-parameterised run, and
    # sweep atomic-write leftovers (_tmp_*.npz). The merge, the region loader
    # and resume go through manifest.json or exact shard names, so a leftover
    # is invisible to them; archive_cli does NOT - it globs ``shards/*.npz``
    # and would pack a truncated file as if it were a shard. The sweep is what
    # makes that glob safe, and it reclaims the disk space either way.
    _check_run_config(shards_dir, {
        "bbox": list(bbox), "clip": bool(clip),
        "cell_xy": float(cell_xy), "cell_z": float(cell_z),
        # `is not None`: an empty set records as [] so --resume-shards can
        # tell "keep nothing" apart from "keep everything".
        "keep_classes": (sorted(int(c) for c in keep_classes)
                         if keep_classes is not None else None),
        # The run-forming order shapes every shard's interval structure, so
        # resuming under a different order would mix structures silently.
        "run_order": _run_order(),
    }, resume=resume_shards)
    for leftover in shards_dir.glob("_tmp_*.npz"):
        logger.warning("removing interrupted shard write %s", leftover.name)
        leftover.unlink(missing_ok=True)
    # failed_tiles.json is written at the END of the loop and only when THIS
    # run lost tiles, so a leftover copy from an earlier run would sit next
    # to a fresh manifest saying n_failed_tiles: 0. Cleared on resume too: a
    # failed tile left no shard, so --resume-shards retries it from scratch
    # and the record below is rebuilt from this run's outcomes alone.
    (shards_dir / "failed_tiles.json").unlink(missing_ok=True)

    # ---- tile selection + extents (for the mosaic frame) --------------------
    if stream:
        if json_file is None:
            raise ValueError("stream=True requires json_file")
        selected = _tile_iter_stream(Path(json_file), bbox, tile_pitch, limit)
        rects = [(float(t["x_min"]), float(t["y_min"]),
                  float(t["x_max"]), float(t["y_max"])) for t in selected]
        z0 = None  # first tile header sets it, like process_area_streamed
    else:
        from .area import select_area_tiles
        kept, z_floor = select_area_tiles(laz_dir, bbox)
        # The streaming branch applies `limit` inside _tile_iter_stream. This
        # branch has to apply it here, or the documented flag would silently
        # do nothing whenever the run reads its tiles from a directory. Sliced
        # before the header reads below, so a limited run does not open files
        # it will not process.
        if limit > 0:
            kept = kept[:limit]
        selected = kept
        rects = []
        for p in kept:
            mins, maxs, _ = read_laz_header(p)
            rects.append((float(mins[0]), float(mins[1]),
                          float(maxs[0]), float(maxs[1])))
        z0 = float(z_floor) if z_floor is not None else None

    n_total = len(selected)
    x0 = math.floor(bbox[0] / cell_xy) * cell_xy
    y0 = math.floor(bbox[1] / cell_xy) * cell_xy
    clip_bbox = bbox if clip else None
    logger.info("Sharded area run: %d tile(s), shards -> %s", n_total, shards_dir)

    # The per-tile aggregate sums each shard's counts as if the shards held
    # disjoint columns. That holds only while tile boundaries fall on lattice
    # lines, which needs cell_xy to divide the tile pitch (true of every
    # shipped resolution). Otherwise one physical column straddles a boundary,
    # lands in two shards, and is counted once in each. With
    # columns_mode="skip" no later pass re-derives the columns, so nothing
    # downstream would catch it.
    _pitch_cells = tile_pitch / cell_xy
    if (columns_mode == "skip"
            and abs(_pitch_cells - round(_pitch_cells)) > 1e-9):
        logger.warning(
            "tile pitch %s m is not a whole number of %s m cells "
            "(%.4f cells): columns straddling a tile boundary fall in two "
            "shards and are counted twice, so the column, interval and "
            "shared-voxel totals in stats.txt are over-estimates. With "
            "--columns-mode skip nothing downstream re-checks this.",
            tile_pitch, cell_xy, _pitch_cells)

    if n_total == 0:
        logger.warning("No tiles intersect the requested area %s.", bbox)
        return {"n_tiles": 0, "tiles_done": 0, "n_columns": 0,
                "aborted": False, "shards_dir": str(shards_dir),
                "out_dir": str(out_dir)}

    # ---- mosaic frame over the (clipped) union of tile extents --------------
    hx0 = min(r[0] for r in rects); hy0 = min(r[1] for r in rects)
    hx1 = max(r[2] for r in rects); hy1 = max(r[3] for r in rects)
    if clip:
        hx0, hy0 = max(hx0, bbox[0]), max(hy0, bbox[1])
        hx1, hy1 = min(hx1, bbox[2]), min(hy1, bbox[3])
    # Frame sized with FLOOR semantics, matching voxelize's
    # ix = floor((x - x0) / cell_xy). Header maxima are INCLUSIVE (a real
    # point can sit exactly at hx1), so the previous ceil(..) - 1 put that
    # point one column outside the frame whenever the extent landed on a
    # voxel boundary: IndexError on x, SILENT row-wrap on y (the north
    # edge painted onto the south edge).
    # floor() covers the inclusive maximum; when a clipped bound happens
    # to be exact the frame gains at most one empty edge column/row.
    ix_min = int(math.floor((hx0 - x0) / cell_xy))
    ix_max = int(math.floor((hx1 - x0) / cell_xy))
    iy_min = int(math.floor((hy0 - y0) / cell_xy))
    iy_max = int(math.floor((hy1 - y0) / cell_xy))
    W = max(ix_max - ix_min + 1, 1)
    H = max(iy_max - iy_min + 1, 1)
    agg = viz.aggregation_factor(H * W, map_max_pixels)
    if agg > 1:
        logger.info("Mosaic: native %d x %d px, aggregating %dx%d to stay "
                    "under %.0f MP.", W, H, agg, agg, map_max_pixels / 1e6)
    rasters = viz.make_rasters((H + agg - 1) // agg, (W + agg - 1) // agg)

    # ---- per-tile loop --------------------------------------------------------
    aggregates = _Aggregates()
    manifest: list[dict] = []
    aborted = False
    tiles_done = 0
    templaz = Path(templaz_dir) if templaz_dir else out_dir / "templaz"
    if stream:
        templaz.mkdir(parents=True, exist_ok=True)

    origin = None if z0 is None else (x0, y0, z0)

    # -- crash-resilience counters (see docstring) --------------------------
    resumed = 0                       # shards reused from a previous run
    failed_stages: list[str] = []     # merged-phase stages that failed
                                      # in their isolated child
    failed_tiles: list[dict] = []     # every lost tile: download ("kind":
                                      # "download"), unreadable header
                                      # ("kind": "header"), and isolated-
                                      # child decode failures - all flow
                                      # into failed_tiles.json, the
                                      # manifest, the stats banner and the
                                      # exit code

    for i, entry in enumerate(selected, 1):
        # The shard name derives from the tile name alone, so it is known
        # BEFORE any download: resumed tiles skip the download entirely.
        if stream:
            url = entry["url"].strip()
            name = Path(url).name
        else:
            path = entry
            name = path.name
        shard_path = shards_dir / (Path(name).stem + ".npz")

        # ---- resume: reuse a matching existing shard ----------------------
        tile = None
        if resume_shards and shard_path.is_file():
            tile = _try_load_shard(shard_path, cell_xy=cell_xy,
                                   cell_z=cell_z, x0=x0, y0=y0, z0=z0)
            if tile is not None:
                if origin is None:
                    # Adopt the interrupted run's z reference so every tile
                    # voxelized from here on shares its exact lattice.
                    z0 = float(tile.z_min)
                    origin = (x0, y0, z0)
                    logger.info("  z reference = %.3f (adopted from resumed "
                                "shard %s)", z0, shard_path.name)
                resumed += 1
                logger.info("[%d/%d] %s: resumed from existing shard",
                            i, n_total, name)
            # On validation failure _try_load_shard logged why; fall through
            # and re-voxelize (the fresh shard overwrites the stale one).

        if tile is None:
            # -- obtain the file --------------------------------------------
            # Download and header failures are RECORDED like decode
            # failures, not just logged: a warning that scrolls past in a
            # multi-hour run is invisible in the deliverables, and a run
            # that silently lost tiles to transient HTTP errors would be
            # indistinguishable from a complete one. Recording them puts
            # the loss in failed_tiles.json, the manifest counters, the
            # stats.txt banner, and the exit code.
            if stream:
                path = templaz / name
                logger.info("[%d/%d] downloading %s ...", i, n_total, name)
                _name, _status, err = download_one(url, path)
                if err:
                    logger.warning("  skipped %s: %s", name, err)
                    failed_tiles.append({"file": name, "kind": "download",
                                         "error": str(err)})
                    # A lost tile is still a PROCESSED tile: tiles_done
                    # counts attempts on every exit from this loop body,
                    # n_failed_tiles counts the losses.
                    tiles_done = i
                    continue
            else:
                logger.info("[%d/%d] %s", i, n_total, name)

            try:
                mins, _maxs, _n = read_laz_header(path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("  skipped %s: unreadable header (%s)",
                               name, exc)
                failed_tiles.append({"file": name, "kind": "header",
                                     "error": f"{type(exc).__name__}: {exc}"})
                if stream:
                    path.unlink(missing_ok=True)
                tiles_done = i
                continue
            if origin is None:
                z0 = float(mins[2])
                origin = (x0, y0, z0)
                logger.info("  z reference = %.3f (from first tile)", z0)

            # -- voxelize ---------------------------------------------------
            if isolate_tiles:
                # Child process per tile: a native decompressor crash
                # (0xC0000005 in the LAZ backend) costs this tile only.
                # The child saves the shard atomically; we load it back
                # for the fold below.
                tile, failure = _voxelize_tile_in_child(
                    path, shard_path, cell_xy=cell_xy, cell_z=cell_z,
                    origin=origin, clip_bbox=clip_bbox,
                    keep_classes=keep_classes,
                    chunk_size=(chunk_size if use_chunks else 0),
                    retry_lazrs=retry_lazrs)
                if failure is not None:
                    failed_tiles.append(failure)
                    logger.error("  FAILED %s after %d attempt(s) - "
                                 "recorded in failed_tiles.json; "
                                 "continuing.", name, failure["attempts"])
                    if stream:
                        path.unlink(missing_ok=True)
                    tiles_done = i
                    continue
            else:
                # In-process path, with an atomic shard write so an
                # interrupted run can be resumed without trusting
                # half-written files.
                if use_chunks:
                    tile = voxelize_laz_chunked(
                        path, cell_xy=cell_xy, cell_z=cell_z,
                        keep_classes=keep_classes, origin=origin,
                        chunk_size=chunk_size, clip_bbox=clip_bbox)
                else:
                    x, y, z, cls = read_laz(path)
                    if clip_bbox is not None:
                        xmn, ymn, xmx, ymx = clip_bbox
                        m = ((x >= xmn) & (x < xmx)
                             & (y >= ymn) & (y < ymx))
                        x, y, z, cls = x[m], y[m], z[m], cls[m]
                    tile = voxelize(x, y, z, cls, cell_xy=cell_xy,
                                    cell_z=cell_z,
                                    keep_classes=keep_classes,
                                    origin=origin)
                if tile.columns:
                    _atomic_shard_save(tile, shard_path)
                else:
                    # An empty result writes no shard, so a same-name shard
                    # from an earlier run over this folder would survive on
                    # disk: excluded from this manifest, but archive_cli
                    # globs shards/*.npz and a later --resume-shards would
                    # adopt it as this tile's data. Empty must mean no file,
                    # and a cleanup that fails (a concurrent reader holding
                    # the stale file open, say) must be recorded as a tile
                    # failure - silence here would let the stale data be
                    # adopted later with nothing in the run's own record.
                    try:
                        shard_path.unlink(missing_ok=True)
                    except OSError as exc:
                        logger.error(
                            "  could not remove stale shard %s (%s: %s) - "
                            "recording the tile as failed.",
                            shard_path.name, type(exc).__name__, exc)
                        failed_tiles.append({
                            "file": name, "kind": "stale-shard-cleanup",
                            "error": f"{type(exc).__name__}: {exc}"})

            if stream:
                path.unlink(missing_ok=True)
            elif (delete_laz and tile is not None and tile.columns):
                path.unlink()
                logger.info("  deleted %s", name)

        tiles_done = i
        if tile is None or not tile.columns:
            continue

        # ---- fold into aggregates/mosaic/manifest -------------------------
        # Identical for freshly voxelized, child-produced and resumed
        # shards: by this point the shard file exists and ``tile`` holds it.
        kb = tile.key_bounds()
        st = tile.stats()  # computed once, reused for manifest + aggregates
        manifest.append({
            "file": shard_path.name,
            "n_columns": len(tile.columns),
            # n_intervals lets render_full_from_shards size the global 3-D
            # stride exactly (Sigma intervals / Sigma columns).
            "n_intervals": int(st["n_intervals"]),
            "ix_min": kb[0], "iy_min": kb[1], "ix_max": kb[2],
            "iy_max": kb[3],
            "x0_m": x0 + kb[0] * cell_xy, "y0_m": y0 + kb[1] * cell_xy,
            "x1_m": x0 + (kb[2] + 1) * cell_xy,
            "y1_m": y0 + (kb[3] + 1) * cell_xy,
        })
        aggregates.add(st)
        viz.scatter_summaries(rasters, tile.column_summaries(),
                              ix_min=ix_min, iy_max=iy_max, agg=agg)
        del tile

        exceeded, rss = check_rss_budget(max_rss_mb)
        if exceeded:
            logger.error(
                "RAM budget exceeded after shard %d/%d: RSS %.0f MB > "
                "budget %.0f MB - stopping; partial outputs will be "
                "written.", i, n_total, rss, max_rss_mb)
            aborted = True
            break

    # -- persist the failure record even when empty runs finish clean -------
    if failed_tiles:
        (shards_dir / "failed_tiles.json").write_text(
            json.dumps(failed_tiles, indent=2), encoding="utf-8")
        logger.warning("%d tile(s) lost to download, header or decode "
                       "failure - see %s",
                       len(failed_tiles), shards_dir / "failed_tiles.json")

    if stream:
        shutil.rmtree(templaz, ignore_errors=True)

    # ---- manifest -------------------------------------------------------------
    # The shards are RAW (ungrouped) stores - shard_worker saves straight out
    # of voxelize, and grouping happens only in memory at merge time - so this
    # manifest plus the shard files is a complete, exactly-reproducible record
    # of the run. That only holds if the settings grouping/merging will apply
    # are written down too, hence the provenance block: see run_utils and
    # voxelizer.archive_cli.
    (shards_dir / "manifest.json").write_text(json.dumps({
        "schema": "voxelizer.shards/2",
        "bbox": bbox, "clip": clip, "cell_xy": cell_xy, "cell_z": cell_z,
        "origin": [x0, y0, z0 if z0 is not None else 0.0],
        "height_mode": height_mode,
        "aborted": aborted, "tiles_done": tiles_done, "tiles_total": n_total,
        "resumed_tiles": resumed, "n_failed_tiles": len(failed_tiles),
        "shards_are_raw": True,
        **make_provenance(group_intervals=group_intervals,
                          group_gap=group_gap, keep_classes=keep_classes,
                          epsg=DEFAULT_EPSG, run_order=_run_order()),
        "shards": manifest,
    }, indent=2), encoding="utf-8")

    # ---- combined stats.txt -----------------------------------------------------
    from .pipeline import _format_stats, MODES
    from .shard_diagnostics import shard_run_banner

    # Single-line provenance banner. The MERGED-phase stats stage further down
    # rewrites this same stats.txt through _write_outputs_isolated, which
    # defaults extra_header=None - so without this the SHARDED / ABORTED /
    # RESUME / failed-tile provenance was silently overwritten and a partial
    # run produced a stats.txt textually identical to a complete one. stats.txt
    # is the artifact figures are read off, so that made a truncated run
    # indistinguishable from a full-area result. The shard-sweep path below
    # uses the same banner, which is why it is spelled in one place.
    provenance_banner = shard_run_banner(
        n_shards=len(manifest), tiles_done=tiles_done, tiles_total=n_total,
        aborted=aborted, resumed=resumed, n_failed=len(failed_tiles))

    # The four area_*.png mosaics are scattered from each tile's RAW column
    # summaries: the fold runs inside the per-tile loop, where the raw store
    # is the only one that exists. stats.txt is rewritten further down from a
    # GROUPED source (the shard sweep, or the merged stats stage). With
    # grouping on the two therefore disagree on intervals per column, the maps
    # carrying the larger ungrouped figure. Stated in the headers rather than
    # changed: regrouping inside the fold would move every recorded mosaic.
    map_source_note = (
        "maps: the four area_*.png mosaics are scattered from RAW per-tile "
        "summaries, so their intervals-per-column is the UNGROUPED figure "
        "and is higher than the grouped table below"
        if group_intervals else
        "maps: the four area_*.png mosaics are scattered from per-tile "
        "summaries; grouping is off, so they and the table below agree")

    # The running per-tile aggregate, written now because it costs nothing and
    # is what a run interrupted later still has. It folds each tile's RAW
    # stats and never holds two tiles at once, which is why the median is
    # missing from it. Both the shard sweep below and the merged stats stage
    # rewrite this same file with the exact dict when they get that far.
    stats = aggregates.to_stats()
    header = [
        f"Area bbox: x [{bbox[0]}, {bbox[2]}], y [{bbox[1]}, {bbox[3]}]",
        f"cell_xy: {cell_xy} m, cell_z: {cell_z} m",
        f"height mode: {height_mode}",
        f"shared origin: ({x0}, {y0}, {z0 if z0 is not None else 0.0})",
        f"SHARDED RUN: {len(manifest)} shard(s) in shards/, "
        f"{tiles_done}/{n_total} tiles processed"
        + (" - ABORTED at RAM budget, outputs are PARTIAL" if aborted else ""),
        "(column-height median needs the column diagnostics pass: "
        "--columns-mode diag, or --merge-shards)",
        "figures below: RAW per-tile fold, before interval grouping",
        map_source_note,
    ]
    if resumed:
        header.append(f"RESUME: {resumed} shard(s) reused from a previous run")
    if failed_tiles:
        header.append(f"WARNING: {len(failed_tiles)} tile(s) failed isolated "
                      f"decode - see shards/failed_tiles.json")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "stats.txt").write_text(_format_stats(stats, header),
                                       encoding="utf-8")
    logger.info("wrote %s", out_dir / "stats.txt")

    # ---- the four mosaicked maps -------------------------------------------------
    if aggregates.n_columns > 0:
        from PIL import Image
        for mode in MODES:
            img = viz.colorize_rasters(rasters, mode, height_mode=height_mode)
            Image.fromarray(img).save(out_dir / f"area_{mode}.png")
            logger.info("wrote %s (%dx%d px)", out_dir / f"area_{mode}.png",
                        img.shape[1], img.shape[0])
    else:
        logger.warning("No occupied columns - skipping maps.")

    # ---- columns/ and exact stats straight from the shards ------------------------
    # Without a merge there is no merged store to reduce - and no need for one:
    # the shards hold disjoint columns, so the same reductions fold over them
    # (voxelizer.shard_diagnostics). ONE sweep serves both deliverables: the
    # columns/ figures and pages, and a stats.txt that finally carries the
    # exact column-height median. It runs only when the diagnostics were asked
    # for, being a second pass over the shard corpus, and it runs here - where
    # the merged path runs col_diag, after the maps and before the 3-D views -
    # so an interruption costs neither the maps nor the aggregate stats.txt
    # already on disk.
    if not merge_shards and columns_mode != "skip" and aggregates.n_columns:
        from .shard_diagnostics import (
            open_shard_set, sweep_shards, write_column_diagnostics_from_shards)
        shard_set = open_shard_set(shards_dir, group_intervals=group_intervals,
                                   group_gap=group_gap, cell_z=cell_z)
        if shard_set is not None:
            shard_sweep = sweep_shards(shard_set, top_n=columns_top_n,
                                       with_stats=True, with_figures=True)
            write_column_diagnostics_from_shards(
                shard_set, out_dir / "columns", mode=columns_mode,
                top_n=columns_top_n, all_cap=columns_all_max,
                tile_label="area", sweep=shard_sweep)
            logger.info("wrote %s (from shards, no merged store)",
                        out_dir / "columns")
            # Same fields, same formatter and the same header lines the merged
            # path composes, in the same order: banner, then the map-source
            # note (the merged path passes both as its extra_header), then the
            # four geometry lines - so a shard-only run's stats.txt is the
            # merged run's stats.txt for the same data, byte for byte.
            stats = shard_sweep.stats
            (out_dir / "stats.txt").write_text(_format_stats(stats, [
                provenance_banner,
                map_source_note,
                f"Area bbox: x [{bbox[0]}, {bbox[2]}], "
                f"y [{bbox[1]}, {bbox[3]}]",
                f"cell_xy: {cell_xy} m, cell_z: {cell_z} m",
                f"height mode: {height_mode}",
                f"shared origin: ({x0}, {y0}, "
                f"{z0 if z0 is not None else 0.0})",
            ]), encoding="utf-8")
            logger.info("rewrote %s from the shard sweep (exact median)",
                        out_dir / "stats.txt")

    # ---- 3-D: both full and ROI reconstructed from shards -------------------------
    # Full view: streamed shard-by-shard at one global stride and concatenated,
    # so the merged store is never materialized (see render_full_from_shards).
    # ROI view: reload only the shards intersecting the window and merge those.
    # With a merge requested, the merged-phase viz3d_full/viz3d_roi stages
    # below write the SAME filenames from the merged store, so a from-shards
    # render here would only be paid for and then overwritten.
    # Both renderers below read the shards as written, i.e. raw (ungrouped)
    # intervals, where the merged-phase stages render the grouped store - so
    # those shared filenames can hold different geometry in the two pipelines.
    if viz3d and aggregates.n_columns > 0:
        if merge_shards:
            logger.info("3-D full/ROI views deferred to the merged store's "
                        "viz3d stages.")
        else:
            if do_full:
                render_full_from_shards(shards_dir, out_dir, file_label="area",
                                        max_boxes=max_boxes)
            if do_roi:
                cx = roi_cx if roi_cx is not None else (hx0 + hx1) / 2.0
                cy = roi_cy if roi_cy is not None else (hy0 + hy1) / 2.0
                half = roi_size / 2.0
                region = (cx - half, cy - half, cx + half, cy + half)
                roi_store = load_region_from_shards(shards_dir, region)
                if roi_store is None or not roi_store.columns:
                    logger.warning("3-D ROI: no shard intersects %s.", region)
                else:
                    from .area_outputs import render_area_3d
                    render_area_3d(roi_store, out_dir, max_boxes=max_boxes,
                                   roi_size=roi_size, roi_cx=cx, roi_cy=cy,
                                   do_full=False, do_roi=True, label="area")

    # The streaming viewer is exported from ONE store (tiled_exporter walks a
    # single store's key order), so it is the one 3-D view a shard-only run
    # cannot produce. The warning below says so, rather than letting the flag
    # pass with no artifact written and nothing said.
    if viz3d_stream and not merge_shards:
        logger.warning(
            "--viz3d-stream needs the merged store and this run does not "
            "build one: no <label>_stream.html was written. Add "
            "--merge-shards for it, or use --viz3d for the full and ROI "
            "views, which are assembled from the shards.")

    # ---- optional merge: assemble one store for area.npz + diagnostics ------
    if merge_shards and aggregates.n_columns > 0:
        # Optional raw sibling: when grouping is on and the ungrouped store was
        # explicitly requested, assemble it in a SEPARATE pass into a scratch
        # store directory, compress that into area_raw.npz, and drop the
        # scratch copy before the grouped merge. (With grouping off, area.npz
        # already IS the raw store, so this pass is skipped.)
        #
        # It stays opt-in because the ungrouped store is the largest thing a
        # run produces: this asks for it twice on disk and pays a second full
        # pass over the shard corpus. Declining it loses no data - the raw
        # intervals stay in the per-tile shards and can be re-merged ungrouped
        # over a smaller sub-area later.
        if keep_area_raw and group_intervals:
            logger.info("Merging %d shard(s) raw for area_raw.npz "
                        "(separate out-of-core pass) ...", len(manifest))
            raw_dir = out_dir / "store_raw_ungrouped"
            raw = None
            try:
                raw = _merge_all_shards(shards_dir, cell_z,
                                        store_dir=raw_dir,
                                        group_intervals=False,
                                        band_intervals=merge_band_intervals)
                if raw is not None and len(raw.columns):
                    # np.savez_compressed streams the mapped arrays through the
                    # zip in chunks, so this stays a disk-to-disk copy. Staged
                    # through .partial.npz like every other large artifact: an
                    # interrupted write must not leave a truncated file under
                    # the final name.
                    from .store_streaming import save_store_npz_atomic
                    save_store_npz_atomic(raw, out_dir / "area_raw.npz")
                    logger.info("saved ungrouped merged store -> %s",
                                out_dir / "area_raw.npz")
            finally:
                if raw is not None:
                    raw.release_arrays()   # Windows: unmap before removing
                del raw
                gc.collect()
                shutil.rmtree(raw_dir, ignore_errors=True)
        # store_raw/ IS the merge output: it is written band by band and the
        # stages then attach to it, so nothing ever holds the whole store (see
        # merge_streaming).
        stream_dir = out_dir / "store_raw"
        logger.info("Merging %d shard(s) into %s for area.npz + diagnostics "
                    "(out of core, bounded RAM) ...", len(manifest), stream_dir)
        merged = _merge_all_shards(shards_dir, cell_z,
                                   store_dir=stream_dir,
                                   group_intervals=group_intervals,
                                   group_gap=group_gap,
                                   band_intervals=merge_band_intervals)
        if merged is not None and len(merged.columns):
            # Merged-store output stages run through the same per-stage
            # process isolation as the non-shard path: each stage in an
            # expendable child attached to a memory-mapped store_raw/
            # copy, so a native crash costs one stage, not the whole
            # multi-hour run. This also makes stages.json, the
            # --intermediates policy, --keep-raw-store and
            # --resume-from-store meaningful in shard mode. Maps are
            # excluded: the bounded mosaics were already written by the
            # per-tile fold above.
            from .area_outputs import _write_outputs_isolated
            stage_manifest = _write_outputs_isolated(
                # The merged raw store is already on disk, so the writer
                # attaches to it instead of persisting the (mapped) store back
                # over itself - the same hook --resume-from-store uses.
                None,
                out_dir, tuple(bbox), cell_xy, cell_z, height_mode,
                raw_store_dir=stream_dir, stage_timeout=stage_timeout,
                columns_mode=columns_mode, columns_top_n=columns_top_n,
                columns_all_max=columns_all_max,
                viz3d=viz3d, max_boxes=max_boxes, roi_size=roi_size,
                roi_cx=roi_cx, roi_cy=roi_cy,
                do_full=do_full, do_roi=do_roi,
                save_store=save_store, only_stages=only_stages,
                extra_header=provenance_banner + "\n" + map_source_note,
                viz3d_stream=viz3d_stream, max_instances=max_instances,
                tile_m=tile_m, inline_threshold_mb=inline_threshold_mb,
                provenance=make_provenance(
                    group_intervals=group_intervals, group_gap=group_gap,
                    keep_classes=keep_classes, epsg=DEFAULT_EPSG,
                    run_order=_run_order()),
                include_maps=False)
            failed_stages = [r["stage"] for r in stage_manifest
                             if not r.get("ok")]
            # Rebind the returned stats to the MERGED store. Until here
            # `stats` is the raw per-tile fold: ungrouped interval counts and
            # no column-height median, because the fold never holds two tiles
            # at once. The stats stage above has just written stats.txt from
            # this same merged store, so leaving the raw dict in the summary
            # made the returned numbers contradict the file the run points
            # its reader at. Folded out of core against the mapped store, so
            # this costs one streamed reduction and no residency.
            from .store_streaming import stats_for
            stats = stats_for(merged)
            # Release the parent's mmap views so the --intermediates
            # policy can delete store_raw/ afterwards (Windows refuses
            # to remove a directory with live mappings).
            merged.release_arrays()
        else:
            logger.warning(
                "Merged store is empty - skipping area.npz and per-column "
                "diagnostics%s.",
                " (and the 3-D full/ROI views, whose from-shards render was "
                "deferred to this merged phase)" if viz3d else "")
        del merged

    # ---- optional cleanup: drop the .npz shards once every consumer above
    # (the mosaic maps, the from-shards diagnostics and statistics sweep, the
    # merge when one was asked for, and the 3-D views) has read them.
    # Deleting the folder
    # trades away the from-shards reuse path (viz3d ROI re-render,
    # load_region_from_shards) for disk space; the maps, stats and any HTML
    # already written are untouched.
    if delete_shards:
        shutil.rmtree(shards_dir, ignore_errors=True)
        logger.info("deleted shard cache %s (reuse disabled by request)",
                    shards_dir)

    # In stream mode there is no local tile directory (laz_dir is None;
    # per-tile temp files are already deleted as they are consumed) -
    # calling the sweep with None raised TypeError at the very end of a
    # multi-hour run and discarded the summary.
    if delete_laz and laz_dir is not None:
        from .area_outputs import _cleanup_remaining_laz
        _cleanup_remaining_laz(laz_dir)

    return {"n_tiles": n_total, "tiles_done": tiles_done,
            "failed_stages": failed_stages,
            "n_columns": aggregates.n_columns, "aborted": aborted,
            "shards_dir": str(shards_dir), "out_dir": str(out_dir),
            "resumed_tiles": resumed, "failed_tiles": failed_tiles,
            "stats": stats}


def load_region_from_shards(shards_dir, region) -> ColumnStore | None:
    """
    Merge only the shards whose extent intersects ``region``
    (x0, y0, x1, y1, metres). Disjoint tiles -> cheap concatenation merges.

    @param shards_dir Directory holding the per-tile ``.npz`` shards and
        their ``manifest.json``.
    @param region ``(x0, y0, x1, y1)`` query window in metres (RGF93/CC46),
        compared against each shard's ``x0_m``/``x1_m``/``y0_m``/``y1_m``
        manifest extents.
    @return A single ColumnStore holding the merged intersecting shards, in
        the shards' own raw (ungrouped) intervals, or None when no manifest
        exists or no shard intersects ``region``.
    """
    # Shards are loaded as written, i.e. raw (ungrouped), so a view built from
    # this store shows raw intervals where the merged-phase view of the same
    # name shows the grouped store's.
    shards_dir = Path(shards_dir)
    mf_path = shards_dir / "manifest.json"
    if not mf_path.is_file():
        logger.warning("No manifest at %s.", mf_path)
        return None
    mf = json.loads(mf_path.read_text(encoding="utf-8"))
    x0, y0, x1, y1 = region
    store = None
    for s in mf["shards"]:
        if s["x1_m"] <= x0 or s["x0_m"] >= x1 \
                or s["y1_m"] <= y0 or s["y0_m"] >= y1:
            continue
        shard = ColumnStore.load(shards_dir / s["file"])
        store = shard if store is None else store.merge(shard, inplace=True)
    return store


def render_full_from_shards(shards_dir, out_dir, *, file_label: str = "area",
                            title_label: str | None = None,
                            max_boxes: int = 5_000_000,
                            classes=None) -> list:
    """
    Build the *full* 3-D HTML from per-tile shards without ever holding the
    merged store.

    Rationale. A ColumnStore's full 3-D view is auto-thinned to ``max_boxes``
    by striding columns, and the stride test runs on the *global* grid index
    (``ix % s == 0 and iy % s == 0``). Every shard was voxelized against the
    same origin, so decimating each shard at one shared stride and
    concatenating the survivors yields exactly the survivor set of the merged
    store at that stride - no merge required. We therefore:

    1. read ``manifest.json`` and pick one global stride from the total column
       and interval counts (``estimate_thin_stride``), so the box budget is
       respected across the whole area;
    2. stream each shard: load, ``collect_geometry`` at that fixed stride
       (``max_boxes=None`` - no further per-shard thinning), keep the tiny
       survivor arrays, free the store;
    3. ``merge_geometries`` the survivors and write one ``<label>_full.html``.

    Peak memory is O(one shard) + O(<= max_boxes boxes) - the bounded-memory
    equivalent of ``render_store_3d(merged_store, ..., do_full=True)``, whose
    input is precisely the resident structure shard mode exists to avoid.
    Returns the list of written paths (``[]`` if nothing was rendered).
    """
    shards_dir = Path(shards_dir)
    out_dir = Path(out_dir)
    mf_path = shards_dir / "manifest.json"
    if not mf_path.is_file():
        logger.warning("3-D full: no manifest at %s - skipping.", mf_path)
        return []
    shards = json.loads(mf_path.read_text(encoding="utf-8")).get("shards", [])
    if not shards:
        logger.warning("3-D full: manifest lists no shards - skipping.")
        return []

    from .area_outputs import _import_viz3d
    try:
        viz3d = _import_viz3d()
    except Exception as exc:  # noqa: BLE001
        logger.warning("3-D visualiser unavailable (%s) - skipping full view.", exc)
        return []

    # -- one global stride so per-shard decimation composes to the merged set --
    total_cols = sum(int(s.get("n_columns", 0)) for s in shards)
    ivs = [int(s["n_intervals"]) for s in shards if "n_intervals" in s]
    # Manifests written before n_intervals existed fall back to a fixed
    # 3.0 intervals/column guess - a mild over-estimate of the corpus
    # average (2.72 on the metropolis-scale store: 317.4M intervals over 116.5M
    # columns), so the stride errs toward thinning; new runs are exact.
    avg_iv = (sum(ivs) / total_cols) if (ivs and total_cols) else 3.0
    stride = viz3d.estimate_thin_stride(total_cols, avg_iv, max_boxes)
    if stride > 1:
        logger.info("3-D full (from shards): global stride=%d "
                    "(~%d cols x %.1f iv/col vs budget %s).",
                    stride, total_cols, avg_iv, f"{max_boxes:,}")

    # -- stream shards: collect decimated geometry, then free each store -------
    geoms = []
    for s in shards:
        p = shards_dir / s["file"]
        if not p.is_file():
            logger.warning("  shard missing on disk: %s", p.name)
            continue
        store = ColumnStore.load(p)
        g = viz3d.collect_geometry(store, classes=classes,
                                   stride=stride, max_boxes=None)
        del store
        if g["n_boxes"]:
            geoms.append(g)

    if not geoms:
        logger.warning("3-D full: no occupied geometry after decimation - "
                       "nothing to render.")
        return []

    merged = viz3d.merge_geometries(geoms)
    title = file_label if title_label is None else title_label
    out_full = out_dir / f"{file_label}_full.html"
    logger.info("Writing %s (%d boxes from %d shard(s), no merged store) ...",
                out_full, merged["n_boxes"], len(geoms))
    viz3d.export_html_from_geom(merged, out_full, title=f"{title} \u2014 full")
    return [out_full]
