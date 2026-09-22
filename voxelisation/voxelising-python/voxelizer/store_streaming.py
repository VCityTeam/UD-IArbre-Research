"""
@ingroup t2_algos


Streaming reductions over a memory-mapped ColumnStore, plus
``save_store_npz_atomic``, the writer the persist stage uses (write to a
temporary file, then rename, so an interrupted save never leaves a truncated
``.npz`` in place of a good one).

``ColumnStore.load_dir(mmap=True)`` attaches for free, but most whole-store
reductions in data_structures and column_diagnostics build at
least one array as long as the store, so their peak RAM grows with the store
and a fine grid over a city does not fit. Nothing about the answers needs
that: a column's intervals are contiguous in the flat arrays and the columns
are globally key-sorted, so the store can be walked in batches that split only
on whole columns, and each reduction becomes that walk plus an accumulator
that is O(1) or bounded in the store size.

    stats_streaming            per-class bincounts summed batch by batch;
                               min/max/argmin/argmax carried as running bests;
                               the per-column height MEDIAN from an exact
                               counting histogram (heights are small bounded
                               integers, so no sort is needed and the answer is
                               exact, not approximated)
    interval_count_histogram   the complexity distribution itself
    top_n_by_intervals         per-batch top-n merged into a running top-n
    category_picks             first-match-per-predicate, one pass
    class_first_columns        first column containing each class code
    summaries_batches          ``column_summaries`` one whole-column batch at
                               a time - the 2-D maps' input; the accumulator
                               is the pixel-guarded raster set itself
    key_frame                  the raster frame (ix/iy extents) from the key
                               array alone

Five of those are written as a FOLD (``StatsFold``,
``IntervalHistogramFold``, ``TopNFold``, ``CategoryFold``, ``ClassFirstFold``)
whose ``add`` accepts any number of stores holding disjoint columns; the
functions above are the fold over a single store. ``summaries_batches`` and
``key_frame`` have no fold class: their accumulator is the caller's raster set
and the running ix/iy extents respectively. That is what lets
voxelizer.shard_diagnostics compute the same answers over a shard corpus
without merging it: the shards of a run hold disjoint columns, so the sums,
minima and counting histograms simply add up. The order-sensitive selections
(the extremes, the top-n ranking, the first match per category and per class
code) carry the packed uint64 KEY rather than a position, because a position
only means something inside the store it came from, and the merged store is
ordered by key, not by shard.

The results are the same objects the in-RAM paths produce, value for value,
asserted by ``tests/test_store_streaming.py`` and ``tests/test_maps_streaming.py``
against the in-RAM originals. Batch size is a pure RAM knob: batches split on
whole columns, so no column is seen twice or split, and every accumulator is
exact and order-independent.

Both paths are kept. is_mmap_backed() decides which runs:
``stage_runner`` always attaches with ``mmap=True``, so every reduction stage
launched there - stats, the npz manifest, the column diagnostics and the four
2-D maps - takes the streaming path, which is the normal way an area run's
outputs are written. Every other caller (the GUI, single-tile runs, the tests)
keeps the in-RAM path. The two legacy box exports (viz3d roi/full) still unpack
per-column index arrays before the box cap applies; full detail at scale is
``viz3d_stream``'s job.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np

from .classes_config import (
    CLASS_NAMES, GROUND, BUILDING, VEGETATION_CLASSES,
)
from .data_structures import ColumnStore, _unpack_keys

logger = logging.getLogger(__name__)

# Intervals per batch. Same budget as ColumnStore._DOM_CHUNK_INTERVALS, for the
# same reason: it caps the int64/float64 temporaries at a few hundred MB no
# matter how many hundreds of millions of intervals the store holds.
DEFAULT_BATCH_INTERVALS = 8_000_000

# Refuse to build a counting histogram wider than this many bins (512 MB of
# int64). Per-column heights are bounded by the store's z span in voxels - ~1000
# at the metropolis 0.5 m grid - so this only ever trips on a store whose z
# indices are nonsense, where a silent 100 GB allocation would be far worse.
_MAX_HIST_BINS = 1 << 26


def is_mmap_backed(store: ColumnStore) -> bool:
    """True when the store's flat arrays are memory-mapped (``load_dir``).

    The switch every stage uses to choose between the in-RAM reduction and the
    streaming one. Deliberately a property of the STORE rather than a flag: a
    store that is mmap-backed is exactly the one that may be too large to
    reduce in RAM, and it is the only kind ``stage_runner`` ever builds.
    """
    return isinstance(getattr(store, "_zs", None), np.memmap)


def column_batches(store: ColumnStore,
                   batch_intervals: int = DEFAULT_BATCH_INTERVALS):
    """Yield ``(c0, c1, i0, i1)``: whole-column batches of <= that many intervals.

    ``c0:c1`` are column indices, ``i0:i1`` the interval range they span. A
    column larger than the budget becomes its own oversized batch, so progress
    is always made. ``searchsorted`` over the offset array is zero-copy on a
    memmap (measured), so the walk itself allocates nothing.
    """
    off = store._off
    n = int(store._keys.shape[0])
    budget = max(1, int(batch_intervals))
    c0 = 0
    while c0 < n:
        i0 = int(off[c0])
        c1 = int(np.searchsorted(off, i0 + budget, side="right")) - 1
        if c1 <= c0:
            c1 = c0 + 1
        if c1 > n:
            c1 = n
        yield c0, c1, i0, int(off[c1])
        c0 = c1


def _local_starts(store: ColumnStore, c0: int, c1: int, i0: int) -> np.ndarray:
    """Batch-local interval start of each column in ``c0:c1``."""
    return (np.asarray(store._off[c0:c1]) - i0).astype(np.int64)


def unpack_key(key) -> tuple[int, int]:
    """``(ix, iy)`` of one packed uint64 key.

    The folds below carry KEYS rather than positions, because a position only
    means something inside the store it came from, while a key is global: it is
    what orders the merged store, and it is the only thing the per-shard
    candidates of a corpus sweep can be compared on.
    """
    ix, iy = _unpack_keys(np.asarray([np.uint64(key)], dtype=np.uint64))
    return int(ix[0]), int(iy[0])


def unpack_keys(keys) -> list[tuple[int, int]]:
    """``[(ix, iy), ...]`` for a handful of packed keys, order preserved."""
    k = np.asarray(list(keys), dtype=np.uint64)
    if k.size == 0:
        return []
    ix, iy = _unpack_keys(k)
    return [(int(a), int(b)) for a, b in zip(ix.tolist(), iy.tolist())]


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------
def _grow(hist: np.ndarray, n: int) -> np.ndarray:
    """Right-pad a counting histogram to at least ``n`` bins."""
    if n <= hist.shape[0]:
        return hist
    if n > _MAX_HIST_BINS:
        raise ValueError(
            f"counting histogram would need {n:,} bins (> {_MAX_HIST_BINS:,}); "
            "the store's z indices are far outside the expected range.")
    out = np.zeros(n, dtype=np.int64)
    out[:hist.shape[0]] = hist
    return out


def _order_statistic(hist: np.ndarray, k: int) -> int:
    """Value of the k-th smallest (0-based) sample of a counting histogram."""
    return int(np.searchsorted(np.cumsum(hist), k + 1, side="left"))


def _median_from_hist(hist: np.ndarray, n: int, scale: float) -> float:
    """``np.median`` of the sample multiset, scaled by ``scale``, exactly.

    The samples are small non-negative integers, so their order statistics come
    straight out of a counting histogram - no sort, no materialised array.
    Scaling by a positive ``scale`` is monotone, so the order statistics of the
    scaled values are the scaled order statistics; feeding the one or two middle
    values to ``np.median`` reproduces numpy's own even/odd rule (and its
    ``(a + b) / 2`` rounding) bit for bit.
    """
    if n % 2:
        vals = [_order_statistic(hist, n // 2)]
    else:
        vals = [_order_statistic(hist, n // 2 - 1), _order_statistic(hist, n // 2)]
    return float(np.median(np.asarray(vals, dtype=np.int64).astype(np.float64)
                           * scale))


def _empty_stats() -> dict:
    """``ColumnStore.stats()``'s empty-store answer, reproduced verbatim."""
    return {
        "n_columns": 0, "n_intervals": 0, "n_points": 0,
        "avg_intervals_per_column": 0.0,
        "points_per_class": {}, "intervals_per_class": {},
        "z_min_m": None, "z_max_m": None,
        "column_height_min_m": None, "column_height_max_m": None,
        "column_height_mean_m": None, "column_height_median_m": None,
        "column_height_sum_m": 0.0,
        "shortest_column": None, "tallest_column": None,
        "intervals_per_column_min": 0, "intervals_per_column_max": 0,
        "n_single_interval_columns": 0,
        "n_overlap_intervals": 0, "n_overlap_columns": 0,
    }


class StatsFold:
    """``ColumnStore.stats()`` as a fold over one or more DISJOINT stores.

    add() accumulates one store in bounded RAM, result() finalises.
    stats_streaming() is this fold over a single store; the shard sweep
    (voxelizer.shard_diagnostics) is the same fold over every shard of a
    corpus, which is exact for the reason the single-store batching is: every
    accumulator here is a sum, a min/max or a counting histogram over whole
    columns, and the shards of a run hold disjoint columns.

    The two extremes are the only order-sensitive quantities. The single-store
    code carried them as running bests under a STRICT improvement test which -
    batches being visited in ascending position order - selects the first
    occurrence in canonical column order. That rule is spelled out here as a
    comparison on ``(height, packed key)``: inside one store it picks exactly
    the same column (position order IS key order), and across stores it applies
    to the GLOBAL key order instead of the order the stores were visited in.
    """

    def __init__(self):
        """Start with empty accumulators; the vertical grid (``z_min``, ``cell_z``) is adopted from the first store added."""
        self.points_by_code = np.zeros(256, dtype=np.float64)
        self.intervals_by_code = np.zeros(256, dtype=np.int64)
        self.n_columns = 0
        self.ext_hist = np.zeros(1, dtype=np.int64)
        self.ext_sum = 0
        self.short = None          # (height in voxels, packed key)
        self.tall = None           # (-height in voxels, packed key)
        self.bot_min = None
        self.top_max = None
        self.niv_min = None
        self.niv_max = None
        self.n_single = 0
        self.n_ov_iv = 0
        self.n_ov_col = 0
        self.z_min = None
        self.cell_z = None

    def _adopt_grid(self, store: ColumnStore) -> None:
        """Take the vertical grid from the first store, then hold the rest to
        it: heights and altitudes only compare on one lattice."""
        if self.z_min is None:
            self.z_min = float(store.z_min)
            self.cell_z = float(store.cell_z)
            return
        if (abs(float(store.z_min) - self.z_min) > 1e-6
                or abs(float(store.cell_z) - self.cell_z) > 1e-9):
            raise ValueError(
                f"stats fold: store on a different vertical grid (z_min "
                f"{store.z_min} / cell_z {store.cell_z} against {self.z_min} / "
                f"{self.cell_z}) - every shard of a run shares one origin.")

    def add(self, store: ColumnStore, *,
            batch_intervals: int = DEFAULT_BATCH_INTERVALS) -> None:
        """Accumulate one store batch by batch: per-class bincounts, z extent, column-height sum and histogram, interval-count extremes, the two extreme columns as ``(height, key)`` bests, and cross-class overlap counts. An empty store is skipped; a store on another vertical grid raises ``ValueError``."""
        n_columns = int(store._keys.shape[0])
        if n_columns == 0:
            return
        self._adopt_grid(store)
        self.n_columns += n_columns

        for c0, c1, i0, i1 in column_batches(store, batch_intervals):
            cl = np.asarray(store._cl[i0:i1])
            ct = np.asarray(store._ct[i0:i1])
            self.points_by_code += np.bincount(
                cl, weights=ct.astype(np.float64), minlength=256)
            self.intervals_by_code += np.bincount(cl, minlength=256)
            del ct

            starts = _local_starts(store, c0, c1, i0)
            zs_b = np.asarray(store._zs[i0:i1])
            ze_b = np.asarray(store._ze[i0:i1])
            bots = zs_b[starts].astype(np.int64)
            ends = np.empty_like(starts)
            ends[:-1] = starts[1:]
            ends[-1] = i1 - i0
            # Highest z_end over each column, not the last interval's (an
            # earlier interval can end higher under cross-class overlap) -
            # the same rule stats()/column_summaries()/summaries_batches()
            # apply, kept in lockstep for the fold's value-parity guarantee.
            tops = np.maximum.reduceat(ze_b, starts).astype(np.int64)

            lo, hi = int(bots.min()), int(tops.max())
            self.bot_min = lo if self.bot_min is None else min(self.bot_min, lo)
            self.top_max = hi if self.top_max is None else max(self.top_max, hi)

            ext = tops - bots
            del bots, tops
            self.ext_sum += int(ext.sum())
            j = int(ext.argmin())
            cand = (int(ext[j]), int(store._keys[c0 + j]))
            if self.short is None or cand < self.short:
                self.short = cand
            j = int(ext.argmax())
            cand = (-int(ext[j]), int(store._keys[c0 + j]))
            if self.tall is None or cand < self.tall:
                self.tall = cand
            self.ext_hist = _grow(self.ext_hist, int(ext.max()) + 1)
            b = np.bincount(ext)
            self.ext_hist[:b.shape[0]] += b
            del ext, b

            nivs = ends - starts
            lo, hi = int(nivs.min()), int(nivs.max())
            self.niv_min = lo if self.niv_min is None else min(self.niv_min, lo)
            self.niv_max = hi if self.niv_max is None else max(self.niv_max, hi)
            self.n_single += int((nivs == 1).sum())
            del nivs, ends

            d_iv, d_col = _overlaps_in_batch(zs_b, ze_b, starts)
            self.n_ov_iv += d_iv
            self.n_ov_col += d_col
            del zs_b, ze_b, starts

    def result(self) -> dict:
        """Finalise into the ``ColumnStore.stats()`` dict (per-class dicts sorted by descending count, heights scaled by ``cell_z``, median from the histogram); the empty-store dict when nothing was added."""
        if self.n_columns == 0:
            return _empty_stats()
        cell_z = self.cell_z
        points_by_code = self.points_by_code.astype(np.int64)
        intervals_by_code = self.intervals_by_code
        n_intervals = int(intervals_by_code.sum())
        n_points = int(points_by_code.sum())

        seen = np.where(intervals_by_code > 0)[0]
        per_class_points = {CLASS_NAMES.get(int(c), f"class_{c}"):
                            int(points_by_code[c]) for c in seen}
        per_class_intervals = {CLASS_NAMES.get(int(c), f"class_{c}"):
                               int(intervals_by_code[c]) for c in seen}
        per_class_points = dict(sorted(per_class_points.items(),
                                       key=lambda kv: -kv[1]))
        per_class_intervals = dict(sorted(per_class_intervals.items(),
                                          key=lambda kv: -kv[1]))

        ext_min, ext_max = self.short[0], -self.tall[0]
        ix_s, iy_s = unpack_key(self.short[1])
        ix_t, iy_t = unpack_key(self.tall[1])
        sum_m = float(np.float64(self.ext_sum) * cell_z)

        return {
            "n_columns": self.n_columns,
            "n_intervals": n_intervals,
            "n_points": n_points,
            "avg_intervals_per_column": round(n_intervals / self.n_columns, 2),
            "points_per_class": per_class_points,
            "intervals_per_class": per_class_intervals,
            "z_min_m": float(self.z_min + self.bot_min * cell_z),
            "z_max_m": float(self.z_min + self.top_max * cell_z),
            "column_height_min_m": float(np.float64(ext_min) * cell_z),
            "column_height_max_m": float(np.float64(ext_max) * cell_z),
            "column_height_mean_m": round(sum_m / self.n_columns, 3),
            "column_height_median_m": round(
                _median_from_hist(self.ext_hist, self.n_columns, cell_z), 3),
            "column_height_sum_m": sum_m,
            "shortest_column": {"ix": ix_s, "iy": iy_s,
                                "height_m": float(np.float64(ext_min) * cell_z)},
            "tallest_column": {"ix": ix_t, "iy": iy_t,
                               "height_m": float(np.float64(ext_max) * cell_z)},
            "intervals_per_column_min": self.niv_min,
            "intervals_per_column_max": self.niv_max,
            "n_single_interval_columns": self.n_single,
            "n_overlap_intervals": self.n_ov_iv,
            "n_overlap_columns": self.n_ov_col,
        }


def stats_streaming(store: ColumnStore, *,
                    batch_intervals: int = DEFAULT_BATCH_INTERVALS) -> dict:
    """``ColumnStore.stats()`` in bounded RAM, value for value.

    One sequential pass. Per-class point/interval totals are bincounts summed
    batch by batch (exact: the summands are integers held exactly in float64,
    so the total does not depend on how the sum was split); the extremes are
    running bests with the same first-occurrence tie rule ``argmin``/``argmax``
    have; the per-column height median comes from an exact counting histogram
    instead of ``np.median``'s full sort.

    The one place where floating-point association could in principle show is
    the height mean/sum: numpy pairwise-sums ``heights_in_voxels * cell_z``,
    this sums the integer voxel heights exactly and scales once. Both are the
    same number whenever ``k * cell_z`` is exact, which covers every grid the
    pipeline uses (0.25 / 0.5 / 1 / 2 m), and stats.txt prints the value to two
    decimals in any case.
    """
    fold = StatsFold()
    fold.add(store, batch_intervals=batch_intervals)
    return fold.result()


def _overlaps_in_batch(zs_b, ze_b, starts) -> tuple[int, int]:
    """Cross-class z-overlap counts for one batch.

    The segment-cumulative-max trick of ``ColumnStore.class_overlap_stats``,
    restricted to a batch of whole columns (which is what makes the total
    independent of how the store was cut). Kept here rather than reused from
    the store because that method's own batching begins with a full-length
    ``np.diff(off)`` + ``np.cumsum`` - the 41 GB this module exists to avoid.
    """
    k = int(zs_b.shape[0])
    if k == 0:
        return 0, 0
    zs = zs_b.astype(np.int64)
    ze = ze_b.astype(np.int64)
    seg = np.zeros(k, dtype=np.int64)
    seg[starts[1:]] = 1
    np.cumsum(seg, out=seg)
    K = int(ze.max() - zs.min()) + 2
    seg *= K
    shifted = ze + seg
    cm = np.maximum.accumulate(shifted)
    prev_max = np.empty_like(cm)
    prev_max[0] = np.iinfo(np.int64).min
    prev_max[1:] = cm[:-1]
    overlap = (zs + seg) < prev_max
    overlap[starts] = False
    per_col = np.add.reduceat(overlap.astype(np.int64), starts)
    return int(overlap.sum()), int((per_col > 0).sum())


def stats_for(store: ColumnStore, *,
              batch_intervals: int = DEFAULT_BATCH_INTERVALS) -> dict:
    """``store.stats()``, computed out of core when the store is mmap-backed."""
    if is_mmap_backed(store):
        return stats_streaming(store, batch_intervals=batch_intervals)
    return store.stats()


# ---------------------------------------------------------------------------
# column complexity
# ---------------------------------------------------------------------------
class IntervalHistogramFold:
    """Counting histogram of intervals per column, folded over stores."""

    def __init__(self):
        """Start with a one-bin empty histogram that add() grows as needed."""
        self.hist = np.zeros(1, dtype=np.int64)

    def add(self, store: ColumnStore, *,
            batch_intervals: int = DEFAULT_BATCH_INTERVALS) -> None:
        """Add one store's per-column interval counts (read from the offsets alone) to the histogram, batch by batch."""
        for c0, c1, i0, i1 in column_batches(store, batch_intervals):
            nivs = np.diff(np.asarray(store._off[c0:c1 + 1]))
            b = np.bincount(nivs)
            self.hist = _grow(self.hist, b.shape[0])
            self.hist[:b.shape[0]] += b

    def result(self) -> np.ndarray:
        """The histogram trimmed to ``np.bincount``'s length (highest non-zero bin + 1), or an empty int64 array when no column was seen."""
        nz = np.flatnonzero(self.hist)
        # np.bincount's own length: max value + 1 (length 0 for no samples).
        return (self.hist[:int(nz[-1]) + 1] if nz.size
                else np.zeros(0, dtype=np.int64))


def interval_count_histogram(store: ColumnStore, *,
                             batch_intervals: int = DEFAULT_BATCH_INTERVALS
                             ) -> np.ndarray:
    """Counting histogram of intervals per column: ``hist[k]`` columns have k.

    Identical to ``np.bincount(store.interval_counts())``, but never holding
    one number per column (20.6 GB at 0.5 m, twice over: ``np.diff`` then
    ``bincount``'s intp cast). Everything the histogram figure draws - the
    linear bars, the log-log stem, the mean line, the clipped-tail count - is a
    function of this array alone.
    """
    fold = IntervalHistogramFold()
    fold.add(store, batch_intervals=batch_intervals)
    return fold.result()


class TopNFold:
    """The ``n`` most-complex columns, folded over stores - bounded RAM.

    Ranking (the one ``column_diagnostics._top_n_by_intervals`` defines):
    interval count DESCENDING, ties broken by ASCENDING key, i.e. canonical
    ``(ix, iy)`` order. A column that is not among its own batch's best n
    cannot be among the whole corpus's best n, so a running top-n over
    per-batch top-n selections is exact - including ties, because the
    tie-break is the global key and not the position at which a candidate
    happened to be seen.
    """

    def __init__(self, n: int):
        """Keep the best ``n`` columns (clamped to >= 0) as parallel interval-count and packed-key arrays, initially empty."""
        self.n = max(0, int(n))
        self.best_niv = np.empty(0, dtype=np.int64)
        self.best_key = np.empty(0, dtype=np.uint64)

    def add(self, store: ColumnStore, *,
            batch_intervals: int = DEFAULT_BATCH_INTERVALS) -> None:
        """Merge each batch's own top-n (interval count descending, key ascending) into the running best; a no-op when ``n`` is 0 or the store is empty."""
        if self.n <= 0 or int(store._keys.shape[0]) == 0:
            return
        for c0, c1, i0, i1 in column_batches(store, batch_intervals):
            nivs = np.diff(np.asarray(store._off[c0:c1 + 1])).astype(np.int64)
            keys = np.asarray(store._keys[c0:c1], dtype=np.uint64)
            take = min(self.n, int(nivs.size))
            order = np.lexsort((keys, -nivs))[:take]
            cand_niv = np.concatenate((self.best_niv, nivs[order]))
            cand_key = np.concatenate((self.best_key, keys[order]))
            keep = np.lexsort((cand_key, -cand_niv))[:self.n]
            self.best_niv, self.best_key = cand_niv[keep], cand_key[keep]

    def result(self) -> list[tuple[int, int]]:
        """The winning columns as ``(ix, iy)`` tuples, most intervals first."""
        return unpack_keys(self.best_key)


def top_n_by_intervals(store: ColumnStore, n: int, *,
                       batch_intervals: int = DEFAULT_BATCH_INTERVALS
                       ) -> list[tuple[int, int]]:
    """The ``n`` most-complex columns, most intervals first - bounded RAM.

    Same ranking as ``column_diagnostics._top_n_by_intervals`` (see
    TopNFold), capped at the store's own column count.
    """
    if n <= 0 or int(store._keys.shape[0]) == 0:
        return []
    fold = TopNFold(min(int(n), int(store._keys.shape[0])))
    fold.add(store, batch_intervals=batch_intervals)
    return fold.result()


# ---------------------------------------------------------------------------
# per-column class facts (one pass, first match wins)
# ---------------------------------------------------------------------------
def _batch_lanes(cl: np.ndarray, starts: np.ndarray, lanes=(0, 1, 2, 3), *,
                 want_distinct: bool = True):
    """Per-column class bitmask lanes for one batch, plus the popcount.

    The reduction of ``ColumnStore.column_class_profile`` / ``class_presence``,
    restricted to a batch: lane L of a column has bit b set when class code
    ``64 * L + b`` occurs in it. Returns ``(dict[L] -> uint64 per column,
    n_distinct int32 per column)``. With ``want_distinct`` the popcount covers
    all FOUR lanes, so it is exact for any raw code, exactly as the store's
    version is; without it only the requested lanes are reduced at all.
    """
    code = cl.astype(np.uint16)
    lane_idx = (code >> 6).astype(np.int64)
    bitval = (np.uint64(1) << (code & np.uint16(63)).astype(np.uint64))
    del code
    out = {}
    n_distinct = np.zeros(starts.shape[0], dtype=np.int32)
    for L in (range(4) if want_distinct else lanes):
        contrib = np.where(lane_idx == L, bitval, np.uint64(0))
        col_lane = np.bitwise_or.reduceat(contrib, starts)
        del contrib
        if want_distinct:
            n_distinct += np.bitwise_count(col_lane).astype(np.int32)
        if L in lanes:
            out[L] = col_lane
    return out, n_distinct


#: Sample-figure categories, in the order ``_categorize`` inserts them. The
#: de-duplication that follows keeps the FIRST category to claim a column, so
#: this order is part of the answer, not presentation.
CATEGORY_NAMES = ("ground only", "building only", "ground + building",
                  "ground + tree", "vegetation stack", "multi-class stack")


class CategoryFold:
    """``column_diagnostics._categorize`` as a fold over stores.

    Each of the six predicate categories keeps the FIRST column in canonical
    (key) order that matches it; the two complexity bands keep the column with
    the most intervals inside the band, the first such column in key order
    breaking a tie - which is what ``argmax`` over the masked counts picks.
    Inside one store both rules are settled by a single forward pass; across
    stores they are settled by the same comparison on keys, since a store
    visited later can still hold a smaller key (tiles partition BOTH axes, so
    shards interleave in global key order).

    De-duplication runs on the resolved picks, never per store: dropping a
    category inside one shard because a neighbour category claimed the same
    column there would discard the candidate that wins globally.

    The band thresholds and their two labels are passed in rather than
    redefined here: they are a diagnostics policy, and one copy of it is
    enough (see ``column_diagnostics._COMPLEX_MIN`` / ``_FRAGMENTED_MIN``).
    """

    def __init__(self, *, complex_min: int, fragmented_min: int,
                 complex_label: str, fragmented_label: str):
        """Store the two band thresholds and their labels; every category slot and both band bests start empty (``None``)."""
        self.complex_min = int(complex_min)
        self.fragmented_min = int(fragmented_min)
        self.complex_label = complex_label
        self.fragmented_label = fragmented_label
        self.hits: dict[str, int | None] = {k: None for k in CATEGORY_NAMES}
        self.band_best = None        # (n_intervals, packed key)
        self.frag_best = None

    @staticmethod
    def _beats(cand, cur) -> bool:
        """More intervals wins; an equal count is settled by the smaller key."""
        return cur is None or (-cand[0], cand[1]) < (-cur[0], cur[1])

    def add(self, store: ColumnStore, *,
            batch_intervals: int = DEFAULT_BATCH_INTERVALS) -> None:
        """Scan one store for the first column (in key order) matching each of the six categories and for the most-interval column of each complexity band, then fold those store-local picks into the running ones by key comparison. The class array is only read while some category is still unmatched in this store; empty stores are skipped."""
        if int(store._keys.shape[0]) == 0:
            return
        veg_mask = np.uint64(sum(1 << int(c) for c in set(VEGETATION_CLASSES)))
        g_bit = np.uint64(1 << int(GROUND))
        b_bit = np.uint64(1 << int(BUILDING))

        # First match inside THIS store (key, or None), then folded below.
        local: dict[str, int | None] = {k: None for k in CATEGORY_NAMES}
        band_local = frag_local = None

        for c0, c1, i0, i1 in column_batches(store, batch_intervals):
            starts = _local_starts(store, c0, c1, i0)
            ends = np.empty_like(starts)
            ends[:-1] = starts[1:]
            ends[-1] = i1 - i0
            nivs = (ends - starts).astype(np.int64)
            del ends
            keys = np.asarray(store._keys[c0:c1], dtype=np.uint64)

            # Once every category has its representative IN THIS STORE, only
            # the two complexity bands are still open - and those read the
            # offsets alone, so the rest of the pass never touches the class
            # array at all. (Positions ascend with keys inside a store, so a
            # later batch cannot beat a match already found in this one.)
            if any(v is None for v in local.values()):
                cl = np.asarray(store._cl[i0:i1])
                lanes, n_distinct = _batch_lanes(cl, starts, lanes=(0,))
                lane0 = lanes[0]
                first_cls = cl[starts]
                del cl, lanes

                has_ground = (lane0 & g_bit) != 0
                has_building = (lane0 & b_bit) != 0
                has_veg = (lane0 & veg_mask) != 0
                del lane0

                def _first(mask, name):
                    """Record the key of the first True column of *mask* under *name*, unless this store already has a match for it."""
                    if local[name] is not None or not mask.any():
                        return
                    local[name] = int(keys[int(np.argmax(mask))])

                single = nivs == 1
                two = nivs == 2
                _first(single & (first_cls == GROUND), "ground only")
                _first(single & (first_cls == BUILDING), "building only")
                two_g = two & has_ground & (n_distinct == 2)
                _first(two_g & has_building, "ground + building")
                _first(two_g & has_veg & ~has_building, "ground + tree")
                _first((nivs >= 2) & ~has_building & has_veg,
                       "vegetation stack")
                _first(n_distinct >= 3, "multi-class stack")
                del has_ground, has_building, has_veg, first_cls, n_distinct

            band = (nivs >= self.complex_min) & (nivs < self.fragmented_min)
            if band.any():
                w = np.where(band, nivs, -1)
                j = int(w.argmax())
                cand = (int(w[j]), int(keys[j]))
                if self._beats(cand, band_local):
                    band_local = cand
            frag = nivs >= self.fragmented_min
            if frag.any():
                w = np.where(frag, nivs, -1)
                j = int(w.argmax())
                cand = (int(w[j]), int(keys[j]))
                if self._beats(cand, frag_local):
                    frag_local = cand

        for name, key in local.items():
            if key is not None and (self.hits[name] is None
                                    or key < self.hits[name]):
                self.hits[name] = key
        if band_local is not None and self._beats(band_local, self.band_best):
            self.band_best = band_local
        if frag_local is not None and self._beats(frag_local, self.frag_best):
            self.frag_best = frag_local

    def result(self) -> dict:
        """The picks as ``{label: (ix, iy)}`` in ``CATEGORY_NAMES`` order, a column claimed by an earlier category dropped from later ones, followed by the two band labels when a band matched."""
        picks: dict[str, tuple[int, int]] = {}
        for name, key in self.hits.items():
            if key is not None:
                picks[name] = unpack_key(key)
        seen: set[tuple[int, int]] = set()
        picks = {k: v for k, v in picks.items()
                 if not (v in seen or seen.add(v))}
        if self.band_best is not None:
            picks[self.complex_label] = unpack_key(self.band_best[1])
        if self.frag_best is not None:
            picks[self.fragmented_label] = unpack_key(self.frag_best[1])
        return picks


def category_picks(store: ColumnStore, *,
                   complex_min: int, fragmented_min: int,
                   complex_label: str, fragmented_label: str,
                   batch_intervals: int = DEFAULT_BATCH_INTERVALS) -> dict:
    """``column_diagnostics._categorize`` in bounded RAM (see
    CategoryFold): one pass, first match per category, first column
    reaching each complexity band's maximum, and the same de-duplication and
    insertion order as the original."""
    fold = CategoryFold(complex_min=complex_min, fragmented_min=fragmented_min,
                        complex_label=complex_label,
                        fragmented_label=fragmented_label)
    fold.add(store, batch_intervals=batch_intervals)
    return fold.result()


class ClassFirstFold:
    """First column containing each class code, folded over stores.

    The reduction behind columns_by_class.png, without ``class_presence``'s two
    resident per-column uint64 lanes (41 GB at 0.5 m). Codes must be in
    [0, 128), the same domain the store's version accepts.

    "First" means smallest key, so each store contributes its own first match
    and the smallest of those wins. The "every code found, stop reading" exit
    is therefore per store: a store visited later can hold a smaller key.
    """

    def __init__(self, codes):
        """Fix the class codes to look for, all required in [0, 128) (``ValueError`` otherwise); no match is recorded yet."""
        self.codes = tuple(int(c) for c in codes)
        if not all(0 <= c < 128 for c in self.codes):
            raise ValueError("class_first_columns codes must be in [0, 128)")
        self.found: dict[int, int] = {}      # code -> smallest packed key

    def add(self, store: ColumnStore, *,
            batch_intervals: int = DEFAULT_BATCH_INTERVALS) -> None:
        """Find, per requested code, the first column of this store containing it (stopping the scan once every code has a match here), then keep the smaller of that key and the running one."""
        if int(store._keys.shape[0]) == 0:
            return
        local: dict[int, int] = {}
        for c0, c1, i0, i1 in column_batches(store, batch_intervals):
            pending = [c for c in self.codes if c not in local]
            if not pending:
                break
            starts = _local_starts(store, c0, c1, i0)
            cl = np.asarray(store._cl[i0:i1])
            lanes, _ = _batch_lanes(cl, starts, lanes=(0, 1),
                                    want_distinct=False)
            del cl, starts
            keys = np.asarray(store._keys[c0:c1], dtype=np.uint64)
            for c in pending:
                hit = (lanes[c >> 6] & (np.uint64(1) << np.uint64(c & 63))) != 0
                if hit.any():
                    local[c] = int(keys[int(np.argmax(hit))])
            del lanes
        for c, key in local.items():
            if c not in self.found or key < self.found[c]:
                self.found[c] = key

    def result(self) -> list[tuple[int, int, int]]:
        """``(code, ix, iy)`` for each code that was found, in the order the codes were given; absent codes are omitted."""
        out = []
        for c in self.codes:
            if c in self.found:
                ix, iy = unpack_key(self.found[c])
                out.append((c, ix, iy))
        return out


def class_first_columns(store: ColumnStore, codes, *,
                        batch_intervals: int = DEFAULT_BATCH_INTERVALS
                        ) -> list[tuple[int, int, int]]:
    """``(code, ix, iy)`` of the first column containing each code, in code
    order (see ClassFirstFold)."""
    fold = ClassFirstFold(codes)
    fold.add(store, batch_intervals=batch_intervals)
    return fold.result()


# ---------------------------------------------------------------------------
# maps (the 2-D raster inputs)
# ---------------------------------------------------------------------------
def key_frame(store: ColumnStore, *, batch_keys: int = 16_000_000):
    """``(ix_min, ix_max, iy_min, iy_max)`` over all columns, batch by batch.

    The frame the map renderers size their rasters with. Packed keys are
    bias-shifted, so canonical store order is ascending (ix, iy) and the ix
    extremes are simply the first and last key. iy is not monotone under an
    ix-major sort, so its extremes come from a banded unpack over the key
    array - 8 B per column read once, one bounded batch of int32 temporaries
    at a time. Returns None for an empty store.
    """
    n = int(store._keys.shape[0])
    if n == 0:
        return None
    ix_first, _ = _unpack_keys(np.asarray(store._keys[:1]))
    ix_last, _ = _unpack_keys(np.asarray(store._keys[n - 1:n]))
    iy_min, iy_max = None, None
    step = max(1, int(batch_keys))
    for s in range(0, n, step):
        _, iy = _unpack_keys(np.asarray(store._keys[s:s + step]))
        lo, hi = int(iy.min()), int(iy.max())
        iy_min = lo if iy_min is None else min(iy_min, lo)
        iy_max = hi if iy_max is None else max(iy_max, hi)
    return int(ix_first[0]), int(ix_last[0]), iy_min, iy_max


def summaries_batches(store: ColumnStore, *,
                      need_dom: bool = True,
                      batch_intervals: int = DEFAULT_BATCH_INTERVALS,
                      dom_chunk_intervals: int | None = None):
    """Yield ``ColumnStore.column_summaries()`` dicts one column batch at a time.

    Each yielded dict has the same seven keys and, for its columns, exactly
    the values the in-RAM reduction computes: the five cheap fields are pure
    per-column gathers, and the dominant class goes through
    ``ColumnStore._fill_dominant`` itself - absolute interval offsets against
    batch-local output positions - so its tie rules (ties -> smallest code)
    are inherited rather than reimplemented. Batches split on whole columns,
    so no column is split or seen twice, and concatenating the yields
    reproduces ``column_summaries`` field for field.

    ``need_dom=False`` matches the in-RAM contract: ``dom_cls`` is yielded
    all-zeros and the expensive reduction is skipped entirely.
    """
    for c0, c1, _i0, _i1 in column_batches(store, batch_intervals):
        off = np.asarray(store._off[c0:c1 + 1]).astype(np.int64)
        ix, iy = _unpack_keys(np.asarray(store._keys[c0:c1]))
        nivs = np.diff(off)
        dom = np.zeros(c1 - c0, dtype=np.uint8)
        if need_dom:
            budget = (store._DOM_CHUNK_INTERVALS if dom_chunk_intervals is None
                      else dom_chunk_intervals)
            store._fill_dominant(dom, off, nivs, chunk_intervals=budget)
        # "Top" mirrors column_summaries: highest z_end over the column, not
        # the last interval's (an earlier interval can end higher under
        # cross-class overlap). The reduceat runs on the batch SLICE with
        # batch-local offsets - fed the absolute offsets it would extend its
        # final segment to the end of the whole store, past this batch.
        ze_b = np.asarray(store._ze[off[0]:off[-1]])
        cl_b = np.asarray(store._cl[off[0]:off[-1]])
        loc = off - off[0]
        top = np.maximum.reduceat(ze_b, loc[:-1]).astype(np.int32)
        # Same set-function tie rule as column_summaries: smallest class code
        # among the intervals reaching the top, never a positional pick.
        cl_masked = np.where(ze_b == np.repeat(top, nivs),
                             cl_b, np.uint8(255))
        top_cls = np.minimum.reduceat(cl_masked, loc[:-1]).astype(np.uint8)
        yield dict(ix=ix, iy=iy,
                   n_intervals=nivs.astype(np.int32),
                   top=top,
                   bot=np.asarray(store._zs[off[:-1]]).astype(np.int32),
                   top_cls=top_cls,
                   dom_cls=dom)


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------
def save_store_npz_atomic(store: ColumnStore, path) -> Path:
    """``ColumnStore.save`` via a ``.partial.npz`` that is renamed on success.

    ``np.savez_compressed`` writes the zip members through a 16 MiB nditer
    loop, so the payload never has to be resident even for a memory-mapped
    store (measured: a 400 MB member costs ~16 MB of peak allocation) - the
    only thing missing was crash-safety. area.npz is one of the two large
    single files the stages write (5.56 GB at 1 m; the streaming viewer's
    ``.bin`` payload is the larger at 46.5 GB, staged the same way by
    ``stage_runner``); an interrupted write used to leave a
    truncated one under the final name, which ``np.load`` reports as a corrupt
    zip much later.
    """
    path = Path(path)
    if path.suffix != ".npz":
        path = path.with_name(path.name + ".npz")
    tmp = path.with_name(path.stem + ".partial.npz")
    tmp.unlink(missing_ok=True)
    store.save(tmp)
    os.replace(tmp, path)
    return path
