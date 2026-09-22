"""
Cell-level denoise filters for the column-compressed voxel grid.
@ingroup t2_algos


Two filters. In the shipped pipeline they run BEFORE resolve():
``postprocess_cli``'s fixed order is min-points -> morph -> absorb -> resolve
-> group, so the denoisers thin the intervals that absorb and resolve then
reason about. Both are column-local and order-independent of resolve as
library calls; the order above is the one every recorded run used.

1. **Min-points filter** - removes intervals whose LiDAR point count
   is at or below a threshold. These are typically single-return noise
   or sensor artefacts.

2. **Morphological filter** - for each interval, counts how many of
   its 8 neighbours (Moore neighbourhood) contain any interval of the
   *same class* overlapping the original interval's z-range.  Intervals
   with too few such neighbours are removed (they are spatially
   unsupported - likely noise or a misclassification).

Both may return the INPUT store object unchanged when nothing was
filtered; when anything is removed, a new store is returned and the
source is not modified.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import numpy as np

from .data_structures import ColumnStore, Column

# 8-neighbourhood (Moore) - captures diagonal adjacency, critical for
# vegetation and rotated structures where 4-neighbour (von Neumann)
# misses valid support.  (A denser 26-neighbour variant was
# considered but never built.)
NEIGHBOUR_OFFSETS: list[tuple[int, int]] = [
    (1, 0), (-1, 0), (0, 1), (0, -1),
    (1, 1), (1, -1), (-1, 1), (-1, -1),
]


# ---------------------------------------------------------------------------
# Min-points filter
# ---------------------------------------------------------------------------

def min_points_filter(store: ColumnStore, min_points: int = 4) -> ColumnStore:
    """
    Remove intervals whose point count is <= *min_points*.  Returns a
    NEW store when anything is removed, and the INPUT store object
    unchanged when nothing is.  Columns that become empty after removal
    are dropped.
    Pass 0 to disable filtering (returns the store unchanged).
    """
    if min_points < 1:
        return store

    mask = store._ct > min_points
    if mask.all():
        return store  # no change

    return _filter_by_mask(store, mask)


def _filter_by_mask(store: ColumnStore, keep: np.ndarray) -> ColumnStore:
    """Build a new store keeping only intervals where *keep* is True."""
    if not keep.any():
        return ColumnStore(store.x_min, store.y_min, store.z_min,
                           store.cell_xy, store.cell_z)

    kept = np.flatnonzero(keep)
    zs = store._zs[kept]
    ze = store._ze[kept]
    cl = store._cl[kept]
    ct = store._ct[kept]

    # Map each kept interval back to its column via the offset array.
    # For each kept interval, find which column it belongs to.
    off = store._off
    col_idx = np.searchsorted(off, kept, side="right") - 1
    ix, iy = _unpack_keys_subset(store._keys, col_idx)

    return ColumnStore.from_intervals(
        store.x_min, store.y_min, store.z_min,
        store.cell_xy, store.cell_z,
        ix, iy, zs, ze, cl, ct,
        assume_canonical=False,
    )


def _unpack_keys_subset(keys: np.ndarray, idx: np.ndarray
                         ) -> tuple[np.ndarray, np.ndarray]:
    """Unpack a subset of uint64 keys by index array -> (ix, iy)."""
    from .data_structures import _KOFF, _LO32, _SH32
    k = keys[idx]
    ix = ((k >> _SH32).astype(np.int64) - _KOFF).astype(np.int32)
    iy = ((k & _LO32).astype(np.int64) - _KOFF).astype(np.int32)
    return ix, iy


# ---------------------------------------------------------------------------
# Morphological filter
# ---------------------------------------------------------------------------

def morphological_filter(
    store: ColumnStore,
    min_neighbours_same: int = 2,
    *,
    check_classes: Union[Sequence[int], None] = None,
) -> ColumnStore:
    """
    Remove morphologically unsupported intervals.  Returns a NEW store
    when anything is removed, and the INPUT store object unchanged when
    nothing is.

    For each interval, examines the 8 Moore neighbours at the same
    z-range.  If fewer than *min_neighbours_same* of those neighbours
    contain any interval of the same class that overlaps the original
    interval's z-range, the interval is removed.

    8-neighbourhood catches diagonal adjacency - important for vegetation
    and rotated structures that 4-neighbour (von Neumann) would miss.

    @param store              Source store, typically resolved, one class per voxel.
    @param min_neighbours_same Minimum number of neighbours that must share the class at
                              this z-range for the interval to be kept (default 2: an
                              interval should have at least 2 supporting neighbours to be
                              trustworthy). Values below 1 return the store unchanged.
    @param check_classes      Keyword-only. Only filter intervals whose class is in this set;
                              when None (the default), all classes are checked. Unlisted
                              classes pass through untouched.
    @return A new store when anything is removed, otherwise the INPUT store object
            unchanged; empty columns are dropped by the rebuild.
    """
    if min_neighbours_same < 1:
        return store

    n = int(store._zs.shape[0])
    if n == 0:
        return store

    zs = store._zs
    ze = store._ze
    cl = store._cl
    ct = store._ct
    off = store._off
    keys = store._keys

    keep = np.ones(n, dtype=bool)
    allowed = set(check_classes) if check_classes is not None else None

    # One vectorized pass maps every interval to its OWN column and unpacks
    # all keys at once, so the n-interval loop below never searchsorts or
    # unpacks a key to answer "which column is interval i in". Looking up
    # the up-to-8 Moore NEIGHBOURS still costs one ``store.columns.get`` -
    # hence one ``ColumnStore._find`` searchsorted - per neighbour tested.
    from .data_structures import _unpack_keys
    col_of = np.repeat(np.arange(int(keys.shape[0]), dtype=np.int64),
                       np.diff(off))
    ix_all, iy_all = _unpack_keys(keys)

    for i in range(n):
        cls = int(cl[i])
        if allowed is not None and cls not in allowed:
            continue

        z_lo = int(zs[i])
        z_hi = int(ze[i])

        ci = int(col_of[i])
        ix, iy = int(ix_all[ci]), int(iy_all[ci])

        same_count = 0
        for dx, dy in NEIGHBOUR_OFFSETS:
            nk = (ix + dx, iy + dy)
            n_col = store.columns.get(nk)
            if n_col is None:
                continue
            if _interval_overlaps_class(n_col, z_lo, z_hi, cls):
                same_count += 1
                if same_count >= min_neighbours_same:
                    break

        if same_count < min_neighbours_same:
            keep[i] = False

    if keep.all():
        return store

    return _filter_by_mask(store, keep)


def _interval_overlaps_class(col: Column, z_lo: int, z_hi: int, cls: int) -> bool:
    """
    True if *col* has an interval of class *cls* that overlaps
    [z_lo, z_hi).

    Implementation note: intervals may OVERLAP across classes, so z_end
    is NOT monotonic in canonical (z_start, cls) order - the previous
    ``searchsorted`` on z_end was invalid and could skip a covering
    interval entirely (false negative). z_start IS sorted, so we bound
    the candidates on it and vector-check their ends.

    Keep in sync with ``absorb._interval_overlaps_class`` (same
    algorithm; duplicated so this module keeps ``data_structures`` as
    its only internal import rather than pulling in ``absorb`` for one
    helper - the documented layering itself would permit that import).
    """
    if z_lo >= z_hi:
        return False  # empty range overlaps nothing
    zs = col.z_start
    ze = col.z_end
    cc = col.cls
    i_hi = int(np.searchsorted(zs, z_hi, side="left"))  # zs[j] < z_hi for j < i_hi
    if i_hi == 0:
        return False
    return bool(np.any((ze[:i_hi] > z_lo) & (cc[:i_hi] == cls)))
