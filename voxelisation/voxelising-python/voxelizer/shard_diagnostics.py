"""
@ingroup t3_orchestr


The column diagnostics computed from a SHARD SET, without merging it.

    python -m voxelizer.shard_diagnostics --shards-dir DIR --out-dir DIR
        [--columns-mode diag|top|all|skip] [--columns-top-n N]
        [--columns-all-max N] [--stats | --no-stats]
        [--group-intervals | --no-group-intervals] [--group-gap METRES]
        [--batch-intervals N] [--tile-label LABEL]

Everything under ``columns/`` - the four figures, the top-N per-column pages,
and the stats the run is read off - would otherwise require the merged store,
paying a full out-of-core merge and the disk its arrays weigh, for a store read
exactly once. It does not have to be that way: a column lives in exactly ONE
shard (tiles partition the grid), so every reduction behind those artifacts is
a fold over disjoint parts, and the parts are already on disk.

This module is that fold. One sequential pass over the shards feeds the
accumulators in voxelizer.store_streaming (written as folds precisely so
they can take more than one store), the order-sensitive selections are
resolved globally on the packed key, and each selected column is rendered by
loading only the shard that owns it. Peak private memory is one shard plus the
accumulators, whatever the size of the corpus.

What must be reproduced, and how
--------------------------------
The answers have to equal the merged path's, not approximate it. Two properties
of the merged store make that possible and both are checked here rather than
assumed:

  * Disjointness. Shards hold disjoint columns, so sums, minima, maxima and
    counting histograms add up. The manifest's per-shard ``ix``/``iy`` extents
    are tested for pairwise overlap; an overlap means a column straddles two
    shards, at which point neither this sweep nor the merge's per-shard
    grouping is equivalent to the merged whole, and the run stops.
  * Grouping. ``sharding._merge_all_shards`` groups each shard BEFORE merging
    (grouping is per column, so per-shard grouping equals grouping the merged
    whole). The sweep applies the same ``grouped(max_gap_cells=...)`` to each
    shard, with the settings recorded in the manifest's provenance block, so it
    reduces the same intervals the merged store holds.

Global order is KEY order, never shard order. A tile partitions both axes, so
two shards covering the same ``ix`` range at different ``iy`` interleave in the
merged store's ordering: no visiting order over shards is key order. Every
selection below is therefore resolved by collecting per-shard candidates and
comparing them on the packed uint64 key.

    pick                        comparator (merged path)             reproduction
    ------------------------    ---------------------------------    ---------------------------
    shortest column             min height, first in key order       per-shard (height, key)
    (stats)                     (`argmin` + strict running best)     -> global min of the pair
    tallest column              max height, first in key order       per-shard (-height, key)
    (stats)                     (`argmax` + strict running best)     -> global min of the pair
    top-N by intervals          sort by (-n_intervals, key),         per-shard top-N under the
    (figure + pages)            take N (`lexsort` on -nivs, pos)     same order -> global top-N
    category sample             first column in key order            per-shard first match
    (6 predicates)              matching the predicate (`argmax`)    -> global min key
    complex / fragmented        max intervals inside the band,       per-shard (n, key)
    band sample                 first in key order on a tie          -> max n, min key on a tie
    class representative        first column in key order            per-shard first match
    (per class code)            containing the code (`argmax`)       -> global min key
    interval histogram          bincount of per-column counts        per-shard histograms summed
    height median               order statistic of the height        per-shard height histograms
                                counting histogram                   summed, then the same statistic

The de-duplication of the category picks (a column drawn under one label must
not reappear under another) runs on the resolved picks. Doing it per shard
would drop the candidate that wins globally, which is why
``store_streaming.CategoryFold`` separates "first match per predicate" from
"de-duplicate" instead of reusing ``category_picks`` per shard.

Stats
-----
The sweep also produces the full ``ColumnStore.stats()`` dict, including the
exact column-height median, which the per-tile aggregate in
voxelizer.sharding cannot compute because it never sees two tiles at
once. Note what changes with it: that aggregate folds each tile's raw stats
(the shard as saved, before grouping), whereas the merged path reports the
grouped merged store. The sweep reports the grouped numbers, so a shard-only
run's stats.txt describes the same thing a merged run's does.
"""

from __future__ import annotations

import argparse
import gc
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .data_structures import ColumnStore, _pack_keys
from .merge_streaming import _base_grid, _check_grid, _read_manifest
from . import store_streaming

logger = logging.getLogger(__name__)

#: Shards between progress lines during a sweep.
_LOG_EVERY = 25


# ---------------------------------------------------------------------------
# the corpus
# ---------------------------------------------------------------------------
@dataclass
class ShardSet:
    """A shard directory read as one addressable store.

    ``records`` is the manifest's shard list filtered to the files actually on
    disk, in manifest order - the same input set, in the same order, that
    voxelizer.merge_streaming.merge_shards_streaming() would merge.
    ``grid`` is ``(x_min, y_min, z_min, cell_xy, cell_z)``, taken from the
    first non-empty shard exactly as the merge takes it.
    """

    shards_dir: Path
    records: list
    grid: tuple
    manifest: dict
    group_intervals: bool = True
    gap_cells: int | None = None

    @property
    def n_columns(self) -> int:
        """Upper bound on the merged column count (exact when keys are
        disjoint, which assert_disjoint() establishes)."""
        return sum(int(r["n_columns"]) for r in self.records)

    @property
    def n_intervals(self) -> int:
        """Raw (ungrouped) interval count over the corpus."""
        return sum(int(r["n_intervals"]) for r in self.records)

    def load(self, rec) -> ColumnStore:
        """One shard, on the run's grid, grouped the way the merge groups it."""
        store = ColumnStore.load(self.shards_dir / rec["file"])
        _check_grid(store, self.grid, rec["file"])
        if self.group_intervals and store._zs.shape[0]:
            store = store.grouped(max_gap_cells=self.gap_cells)
        return store

    def owner_of(self, keys) -> dict[int, list]:
        """Group packed keys by the index of the shard whose extent holds them.

        Extents are disjoint rectangles (see assert_disjoint()), so at
        most one shard can own a key. A key owned by none is a caller error -
        it did not come out of this corpus - and says so rather than silently
        dropping a page.
        """
        out: dict[int, list] = {}
        for key in keys:
            ix, iy = store_streaming.unpack_key(key)
            for k, r in enumerate(self.records):
                if (int(r["ix_min"]) <= ix <= int(r["ix_max"])
                        and int(r["iy_min"]) <= iy <= int(r["iy_max"])):
                    out.setdefault(k, []).append(key)
                    break
            else:
                raise KeyError(
                    f"column (ix={ix}, iy={iy}) is outside every shard extent "
                    f"of {self.shards_dir}")
        return out


def open_shard_set(shards_dir, *, group_intervals: bool | None = None,
                   group_gap: float | None = None,
                   cell_z: float | None = None) -> ShardSet | None:
    """Read ``shards_dir`` as a ShardSet, or None when there is
    nothing there (no manifest, no shard listed, none on disk) - the same
    "nothing happened" signal the merge gives.

    The grouping settings default to the manifest's provenance block, which is
    what the run itself applied (or would apply) at merge time; passing them
    explicitly overrides that, for a caller reproducing a different merge.
    """
    shards_dir = Path(shards_dir)
    records = _read_manifest(shards_dir)
    if records is None:
        return None
    manifest = json.loads(
        (shards_dir / "manifest.json").read_text(encoding="utf-8"))
    if group_intervals is None:
        # A manifest written before the provenance block existed records
        # nothing; the pipeline's own default is grouping on.
        group_intervals = manifest.get("group_intervals")
        group_intervals = True if group_intervals is None else bool(group_intervals)
    if group_gap is None:
        group_gap = manifest.get("group_gap_m")
    grid = _base_grid(shards_dir, records)
    cz = grid[4] if cell_z is None else float(cell_z)
    gap_cells = (None if group_gap is None
                 else max(0, int(round(float(group_gap) / cz))))
    return ShardSet(shards_dir=shards_dir, records=records, grid=grid,
                    manifest=manifest, group_intervals=bool(group_intervals),
                    gap_cells=gap_cells)


def assert_disjoint(shard_set: ShardSet, *, block: int = 2048) -> None:
    """Check that no two shard extents overlap, so that no column is shared between shards.

    Every equality this module claims rests on it: disjoint columns are what
    make the folds additive, and they are also what makes the merge's
    per-shard grouping equal to grouping the merged whole (a column split
    across two shards would be grouped in halves). The rectangles are compared
    in blocks so the test costs a bounded amount of memory on a corpus of any
    size.
    """
    n = len(shard_set.records)
    if n < 2:
        return
    x0 = np.array([int(r["ix_min"]) for r in shard_set.records])
    x1 = np.array([int(r["ix_max"]) for r in shard_set.records])
    y0 = np.array([int(r["iy_min"]) for r in shard_set.records])
    y1 = np.array([int(r["iy_max"]) for r in shard_set.records])
    cols = np.arange(n)[None, :]
    for a in range(0, n, block):
        b = min(n, a + block)
        hit = ((x0[a:b, None] <= x1[None, :]) & (x1[a:b, None] >= x0[None, :])
               & (y0[a:b, None] <= y1[None, :]) & (y1[a:b, None] >= y0[None, :]))
        # Each unordered pair is examined once (and no shard against itself).
        hit &= cols > np.arange(a, b)[:, None]
        where = np.argwhere(hit)
        if where.size:
            i, j = int(where[0][0]) + a, int(where[0][1])
            ri, rj = shard_set.records[i], shard_set.records[j]
            raise ValueError(
                f"shard extents overlap: {ri['file']} "
                f"(ix {ri['ix_min']}-{ri['ix_max']}, iy {ri['iy_min']}-"
                f"{ri['iy_max']}) and {rj['file']} (ix {rj['ix_min']}-"
                f"{rj['ix_max']}, iy {rj['iy_min']}-{rj['iy_max']}). Columns "
                "are then shared between shards, and neither these "
                "diagnostics nor the merge's per-shard grouping equals the "
                "merged whole. This happens when cell_xy does not divide the "
                "tile pitch; merge the shards (--merge-shards) and read the "
                "diagnostics off the merged store instead.")


# ---------------------------------------------------------------------------
# the sweep
# ---------------------------------------------------------------------------
@dataclass
class SweepResult:
    """Everything the four figures, the pages and stats.txt need."""

    grid: tuple
    n_shards: int
    n_columns: int = 0
    stats: dict | None = None
    count_hist: np.ndarray | None = None
    top_keys: list = field(default_factory=list)
    picks: dict = field(default_factory=dict)
    class_entries: list = field(default_factory=list)
    #: The ranking depth this sweep was taken at. Recorded because the ranking
    #: cannot be deepened after the fact without another pass.
    top_n: int = 0
    has_figures: bool = False
    seconds: float = 0.0


def sweep_shards(shard_set: ShardSet, *, top_n: int = 50,
                 with_stats: bool = True, with_figures: bool = True,
                 class_codes=None,
                 batch_intervals: int = store_streaming.DEFAULT_BATCH_INTERVALS
                 ) -> SweepResult:
    """One sequential pass over the corpus, feeding every accumulator at once.

    Reading a shard is the expensive part (it is a compressed .npz), so all
    five folds see it while it is in memory and it is dropped before the next
    one is opened. ``with_stats`` / ``with_figures`` only decide which folds
    run, never how they answer.

    The returned ``SweepResult`` is populated to match: ``stats`` is a dict
    only when ``with_stats``, and stays None otherwise, exactly as
    ``count_hist``, ``picks``, ``top_keys`` and ``class_entries`` are only
    filled when ``with_figures``. A caller that asked for one half must not
    read the other.
    """
    from .column_diagnostics import (
        _COMPLEX_LABEL, _COMPLEX_MIN, _FRAGMENTED_LABEL, _FRAGMENTED_MIN,
        _figure_class_codes,
    )

    assert_disjoint(shard_set)
    if class_codes is None:
        class_codes = _figure_class_codes()

    stats_fold = store_streaming.StatsFold() if with_stats else None
    hist_fold = cat_fold = cls_fold = top_fold = None
    if with_figures:
        hist_fold = store_streaming.IntervalHistogramFold()
        top_fold = store_streaming.TopNFold(top_n)
        cat_fold = store_streaming.CategoryFold(
            complex_min=_COMPLEX_MIN, fragmented_min=_FRAGMENTED_MIN,
            complex_label=_COMPLEX_LABEL, fragmented_label=_FRAGMENTED_LABEL)
        cls_fold = store_streaming.ClassFirstFold(class_codes)

    t0 = time.time()
    n_columns = 0
    n = len(shard_set.records)
    logger.info("shard diagnostics: sweeping %d shard(s) in %s%s", n,
                shard_set.shards_dir,
                "" if not shard_set.group_intervals else
                (f", grouped (gap="
                 f"{'unlimited' if shard_set.gap_cells is None else f'{shard_set.gap_cells} cells'})"))
    for i, rec in enumerate(shard_set.records, 1):
        store = shard_set.load(rec)
        n_columns += int(store._keys.shape[0])
        for fold in (stats_fold, hist_fold, top_fold, cat_fold, cls_fold):
            if fold is not None:
                fold.add(store, batch_intervals=batch_intervals)
        del store
        if i % 16 == 0:
            gc.collect()
        if i % _LOG_EVERY == 0 or i == n:
            logger.info("  swept %d/%d shard(s), %s columns (%.0fs)",
                        i, n, f"{n_columns:,}", time.time() - t0)

    out = SweepResult(grid=shard_set.grid, n_shards=n, n_columns=n_columns,
                      top_n=int(top_n), has_figures=bool(with_figures),
                      seconds=time.time() - t0)
    if stats_fold is not None:
        out.stats = stats_fold.result()
    if with_figures:
        out.count_hist = hist_fold.result()
        out.top_keys = top_fold.result()
        out.picks = cat_fold.result()
        out.class_entries = cls_fold.result()
    return out


def stats_from_shards(shard_set: ShardSet, *,
                      batch_intervals: int =
                      store_streaming.DEFAULT_BATCH_INTERVALS) -> dict:
    """``ColumnStore.stats()`` of the merged store, without merging it."""
    return sweep_shards(shard_set, with_stats=True, with_figures=False,
                        batch_intervals=batch_intervals).stats


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------
def subset_store(shard_set: ShardSet, keys) -> ColumnStore:
    """A small in-memory store holding exactly ``keys``, in key order.

    The figure writers draw from a store: they read the grid metadata and index
    ``store.columns[(ix, iy)]``. A store carrying only the few dozen selected
    columns, on the corpus's own grid, draws every panel identically to the
    merged store - and costs a handful of kilobytes instead of 111 GB.
    """
    packed = sorted({int(_pack_keys(np.int64(ix), np.int64(iy))[()])
                     for ix, iy in keys})
    x0, y0, z0, cxy, cz = shard_set.grid
    out = ColumnStore(x0, y0, z0, cxy, cz)
    if not packed:
        return out
    keys_out, zs, ze, cl, ct = [], [], [], [], []
    off = [0]
    by_shard = shard_set.owner_of(packed)
    picked: dict[int, tuple] = {}
    for k in sorted(by_shard):
        store = shard_set.load(shard_set.records[k])
        want = np.asarray(by_shard[k], dtype=np.uint64)
        pos = np.searchsorted(store._keys, want)
        for key, p in zip(want.tolist(), pos.tolist()):
            if p >= store._keys.shape[0] or int(store._keys[p]) != int(key):
                ix, iy = store_streaming.unpack_key(key)
                raise KeyError(f"column (ix={ix}, iy={iy}) is not in shard "
                               f"{shard_set.records[k]['file']}")
            a, b = int(store._off[p]), int(store._off[p + 1])
            picked[int(key)] = (np.asarray(store._zs[a:b]).copy(),
                                np.asarray(store._ze[a:b]).copy(),
                                np.asarray(store._cl[a:b]).copy(),
                                np.asarray(store._ct[a:b]).copy())
        del store
    for key in packed:                      # ascending key order, as merged
        z_s, z_e, c_l, c_t = picked[key]
        keys_out.append(key)
        zs.append(z_s); ze.append(z_e); cl.append(c_l); ct.append(c_t)
        off.append(off[-1] + int(z_s.shape[0]))
    out._keys = np.asarray(keys_out, dtype=np.uint64)
    out._off = np.asarray(off, dtype=np.int64)
    out._zs = np.concatenate(zs)
    out._ze = np.concatenate(ze)
    out._cl = np.concatenate(cl)
    out._ct = np.concatenate(ct)
    return out


def _write_pages_for_keys(shard_set: ShardSet, pc_dir: Path, keys,
                          *, fan_out_block: int | None = None) -> int:
    """Per-column PNGs for ``keys``, loading each owning shard once."""
    from .column_diagnostics import write_columns_in_bulk

    packed = [int(_pack_keys(np.int64(ix), np.int64(iy))[()])
              for ix, iy in keys]
    by_shard = shard_set.owner_of(packed)
    order = {key: i for i, key in enumerate(packed)}
    written = 0
    for k in sorted(by_shard):
        store = shard_set.load(shard_set.records[k])
        mine = sorted(by_shard[k], key=lambda key: order[key])
        written += write_columns_in_bulk(
            store, pc_dir, store_streaming.unpack_keys(mine),
            fan_out_block=fan_out_block)
        del store
        gc.collect()
    return written


def write_column_diagnostics_from_shards(
    shard_set: ShardSet,
    columns_dir,
    *,
    mode: str = "diag",
    top_n: int = 50,
    tile_label: str = "",
    all_cap: int | None = None,
    sweep: SweepResult | None = None,
    batch_intervals: int = store_streaming.DEFAULT_BATCH_INTERVALS,
) -> SweepResult | None:
    """``column_diagnostics.write_column_diagnostics`` against a shard set.

    Same layout under ``columns_dir`` (``diagnostics/`` + ``per_column/``) and
    the same content: the figure writers are the ones in
    voxelizer.column_diagnostics, fed the precomputed inputs they
    already accept, drawing from a store that holds only the selected columns.

    One difference, and it is only in the default: ``mode`` defaults to
    ``"diag"`` here and to ``"top"`` in ``write_column_diagnostics``. Every
    caller in the package passes ``mode`` explicitly, so the two paths agree
    in practice; a direct caller of either function does not get the same
    thing from an omitted argument.

    Returns the sweep it used (so a caller can also write stats from it), or
    None for ``mode='skip'``.
    """
    from .column_diagnostics import (
        _MODE_ALL_MAX_COLUMNS, _TOP_COMPLEX_MAX_PANELS, write_columns_in_bulk,
        write_class_representatives_figure, write_intervals_histogram,
        write_samples_figure, write_top_complex_figure,
    )

    if mode == "skip":
        return None
    if mode not in ("all", "top", "diag"):
        raise ValueError(
            f"unknown columns mode {mode!r} - pick 'all', 'top', 'diag', or "
            "'skip'")

    # Cheap, and the rendering below reads it too: owner_of resolves a column
    # to its shard by extent, which only has one answer while the extents are
    # disjoint. Repeating the check costs a few milliseconds and covers the
    # path where the caller brings its own sweep.
    assert_disjoint(shard_set)

    columns_dir = Path(columns_dir)
    diag_dir = columns_dir / "diagnostics"
    diag_dir.mkdir(parents=True, exist_ok=True)

    if sweep is None:
        sweep = sweep_shards(shard_set, top_n=top_n, with_stats=False,
                             batch_intervals=batch_intervals)
    elif not sweep.has_figures:
        raise ValueError(
            "the sweep passed in was taken with with_figures=False, so it "
            "carries no selections to draw; sweep again with figures on.")
    elif sweep.top_n != int(top_n):
        # The ranking is a fold, not a slice: it cannot be re-cut afterwards.
        logger.warning(
            "    top-N mismatch: the sweep ranked %d columns, this call asks "
            "for %d - drawing the %d the sweep holds.",
            sweep.top_n, int(top_n), sweep.top_n)
    if sweep.n_columns == 0:
        logger.warning("Shard set holds no columns - nothing to draw.")
        return sweep

    # One tiny store for every column any figure draws: the category picks,
    # the class representatives, and the top-N ranking (the overview clamps
    # itself to _TOP_COMPLEX_MAX_PANELS panels but is handed the full ranking,
    # which is what its "showing N of M" title counts).
    fig_keys = list(sweep.picks.values())
    fig_keys += [(ix, iy) for _c, ix, iy in sweep.class_entries]
    fig_keys += list(sweep.top_keys[:_TOP_COMPLEX_MAX_PANELS])
    fig_store = subset_store(shard_set, fig_keys)

    p = write_samples_figure(fig_store, diag_dir / "columns_samples.png",
                             tile_label=tile_label, picks=sweep.picks)
    if p: logger.info("    wrote %s", p)
    p = write_top_complex_figure(fig_store, diag_dir / "columns_top_complex.png",
                                 n=top_n, tile_label=tile_label,
                                 keys=sweep.top_keys)
    if p: logger.info("    wrote %s", p)
    p = write_intervals_histogram(fig_store, diag_dir / "histogram_intervals.png",
                                  tile_label=tile_label,
                                  count_hist=sweep.count_hist)
    if p: logger.info("    wrote %s", p)
    p = write_class_representatives_figure(
        fig_store, diag_dir / "columns_by_class.png", tile_label=tile_label,
        entries=sweep.class_entries)
    if p: logger.info("    wrote %s", p)
    del fig_store

    if mode == "diag":
        return sweep

    pc_dir = columns_dir / "per_column"
    if mode == "all":
        n_cols = sweep.n_columns
        cap = _MODE_ALL_MAX_COLUMNS if all_cap is None else int(all_cap)
        if cap > 0 and n_cols > cap:
            # Same refusal as the merged path, for the same reason: mode=all
            # was built for single tiles, and on a corpus it means MILLIONS of
            # files. The figures above were still written.
            logger.error(
                "    mode=all refused: %s columns > cap of %s (would be "
                "~%.0f h and ~%.0f GB of PNGs at measured rates). Use "
                "--columns-mode top, or make the informed choice explicit "
                "with --columns-all-max (0 = no cap).",
                f"{n_cols:,}", f"{cap:,}",
                n_cols * 0.075 / 3600, n_cols * 27_000 / 1e9)
            return sweep
        logger.info("    writing %d per-column PNGs (mode=all) - this is slow ...",
                    n_cols)
        fan = 100 if n_cols > 10_000 else None
        written = 0
        for rec in shard_set.records:
            store = shard_set.load(rec)
            written += write_columns_in_bulk(store, pc_dir, store.columns,
                                             fan_out_block=fan)
            del store
            gc.collect()
        logger.info("    wrote %d per-column PNGs in %s", written, pc_dir)
    else:
        n = _write_pages_for_keys(shard_set, pc_dir, sweep.top_keys)
        logger.info("    wrote %d per-column PNGs (top by intervals) in %s",
                    n, pc_dir)
    return sweep


# ---------------------------------------------------------------------------
# stats.txt
# ---------------------------------------------------------------------------
def shard_run_banner(*, n_shards: int, tiles_done, tiles_total, aborted: bool,
                     resumed, n_failed) -> str:
    """The one-line provenance banner of a sharded run.

    Spelled once, here, because two callers need the identical string: the run
    itself (voxelizer.sharding, which also hands it to the merged
    stages) and any later pass over the shards, which rebuilds it from
    manifest.json. stats.txt is the artifact figures are read off, so a
    partial run must not be able to produce a file textually identical to a
    complete one.
    """
    return (
        f"SHARDED RUN: {n_shards} shard(s) in shards/, "
        f"{tiles_done}/{tiles_total} tiles processed"
        + (" - ABORTED at RAM budget, outputs are PARTIAL" if aborted else "")
        + (f" - RESUME: {resumed} shard(s) reused from a previous run"
           if resumed else "")
        + (f" - WARNING: {n_failed} tile(s) failed isolated decode, "
           f"see shards/failed_tiles.json" if n_failed else "")
    )


def stats_header(shard_set: ShardSet, *, banner: str | None = None) -> list[str]:
    """The stats.txt header of a sharded run, in the merged path's shape.

    ``area_outputs._write_outputs`` / ``stage_runner._stage_stats`` build four
    lines from the run geometry and insert the provenance banner at the top;
    the same four lines are recoverable from manifest.json (schema 2 carries
    the bbox, both cell sizes, the height mode and the shared origin), so a
    stats.txt written from a shard sweep is the same text the merged store
    would have produced for the same data, banner included.
    """
    mf = shard_set.manifest
    bbox = mf.get("bbox") or [0, 0, 0, 0]
    origin = mf.get("origin") or list(shard_set.grid[:3])
    if banner is None:
        banner = shard_run_banner(
            n_shards=len(shard_set.records),
            tiles_done=mf.get("tiles_done"), tiles_total=mf.get("tiles_total"),
            aborted=bool(mf.get("aborted")), resumed=mf.get("resumed_tiles"),
            n_failed=mf.get("n_failed_tiles"))
    return [
        banner,
        f"Area bbox: x [{bbox[0]}, {bbox[2]}], y [{bbox[1]}, {bbox[3]}]",
        f"cell_xy: {mf.get('cell_xy')} m, cell_z: {mf.get('cell_z')} m",
        f"height mode: {mf.get('height_mode')}",
        f"shared origin: ({origin[0]}, {origin[1]}, {origin[2]})",
    ]


def write_stats_txt(stats: dict, out_dir, header: list[str]) -> Path:
    """``stats.txt``, through the one formatter every run mode shares."""
    from .pipeline import _format_stats

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "stats.txt"
    path.write_text(_format_stats(stats, header), encoding="utf-8")
    logger.info("wrote %s", path)
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    """Build the parser: ``--shards-dir`` and ``--out-dir`` (required), the
    ``--columns-mode`` / ``--columns-top-n`` / ``--columns-all-max`` figure
    options, ``--stats`` and ``--group-intervals`` as boolean-optional flags,
    ``--group-gap``, ``--batch-intervals`` and ``--tile-label``."""
    p = argparse.ArgumentParser(
        prog="python -m voxelizer.shard_diagnostics",
        description="Column diagnostics and exact statistics computed "
                    "directly from a shards/ directory, with no merged store.")
    p.add_argument("--shards-dir", required=True,
                   help="Directory holding the .npz shards and manifest.json.")
    p.add_argument("--out-dir", required=True,
                   help="Where columns/ and stats.txt are written.")
    p.add_argument("--columns-mode", default="diag",
                   choices=["all", "top", "diag", "skip"],
                   help="diag = the four figures; top = figures + the top-N "
                        "per-column pages; all = one page per column "
                        "(capped); skip = no columns/ at all.")
    p.add_argument("--columns-top-n", type=int, default=50, metavar="N",
                   help="Columns in the top-N ranking (default 50).")
    p.add_argument("--columns-all-max", type=int, default=None, metavar="N",
                   help="Cap for --columns-mode all (0 = no cap).")
    p.add_argument("--stats", action=argparse.BooleanOptionalAction,
                   default=True,
                   help="Write stats.txt from the same sweep (default on).")
    p.add_argument("--group-intervals", action=argparse.BooleanOptionalAction,
                   default=None,
                   help="Group each shard before reducing it, as the merge "
                        "does (default: whatever the manifest's provenance "
                        "block recorded for the run).")
    p.add_argument("--group-gap", type=float, default=None, metavar="METRES",
                   help="With grouping, only merge across vertical gaps up to "
                        "this many metres (default: the manifest's value).")
    p.add_argument("--batch-intervals", type=int,
                   default=store_streaming.DEFAULT_BATCH_INTERVALS,
                   metavar="N", help="Intervals per reduction batch (RAM knob).")
    p.add_argument("--tile-label", default="area",
                   help="Label printed in the figure titles (default: area).")
    return p


def main(argv=None) -> int:
    """Parse the flags above, open the shard set, run one sweep_shards()
    pass and write ``columns/`` (unless ``--columns-mode skip``) and
    ``stats.txt`` (unless ``--no-stats``) under ``--out-dir``. Returns 0
    after printing the sweep summary, 1 when no shard could be read or when
    nothing at all was requested."""
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    shard_set = open_shard_set(args.shards_dir,
                               group_intervals=args.group_intervals,
                               group_gap=args.group_gap)
    if shard_set is None:
        print("nothing to read (no manifest, or no shard on disk).")
        return 1
    out_dir = Path(args.out_dir)
    want_figures = args.columns_mode != "skip"
    if not (want_figures or args.stats):
        print("nothing requested: --columns-mode skip with --no-stats.")
        return 1
    sweep = sweep_shards(shard_set, top_n=args.columns_top_n,
                         with_stats=args.stats, with_figures=want_figures,
                         batch_intervals=args.batch_intervals)
    if want_figures:
        write_column_diagnostics_from_shards(
            shard_set, out_dir / "columns", mode=args.columns_mode,
            top_n=args.columns_top_n, tile_label=args.tile_label,
            all_cap=args.columns_all_max, sweep=sweep)
    if args.stats:
        write_stats_txt(sweep.stats, out_dir, stats_header(shard_set))
    print(f"swept {sweep.n_shards} shard(s), {sweep.n_columns:,} columns, "
          f"{sweep.seconds:.0f}s -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
