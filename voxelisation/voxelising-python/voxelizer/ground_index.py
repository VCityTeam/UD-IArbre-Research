"""
Ground-index pass for the column-compressed voxel grid.
@ingroup t2_algos


Each column gets one ``ground_z_idx`` (int32): ONE PAST the top ground
voxel of the highest ground interval (class 2) in that column - the
interval's exclusive ``z_end``, restated below. Columns without a ground
return store ``NODATA``.

(The array was previously int16; source ``z_end`` values are int32, and
the unchecked narrowing cast silently wrapped once
``cell_z < z_span/32767`` - about 0.018 m for the Lyon data - producing a
wrong-but-valid-looking ground index. The NODATA *value* is unchanged, so
stores persisted with the old int16 array load fine and are widened.)

``ground_z_idx`` is the EXCLUSIVE ``z_end`` of the highest ground
interval - i.e. ONE PAST the top ground voxel (the top ground voxel
itself is ``ground_z_idx - 1``).

The ground index anchors the three-way emptiness decoder
(``decoder.classify_voxel``):
  - iz <  ground_z_idx - SUBSURFACE_BAND  -> subsurface (solid earth);
    with the exclusive end this leaves an effective band of ONE
    non-subsurface gap voxel below the top ground voxel
  - penetrated (ground exists) ^ iz in a gap -> measured air, unless
    the nadir neighbourhood check demotes the gap to opaque interior
    (every penetrated 4-neighbour is building at that height)
  - not penetrated (NODATA) ^ iz in a gap -> measured air ABOVE the
    column's top return, opaque interior at or below it

Constants:
  NODATA = np.int32(-32768)
  SUBSURFACE_BAND = 2   # compared against the EXCLUSIVE ground_z_idx, so
                      # the effective non-subsurface band below the top
                      # ground voxel is 1 (see module docstring)
"""

from __future__ import annotations

import logging
import numpy as np

from .classes_config import GROUND
from .data_structures import ColumnStore, _unpack_keys

logger = logging.getLogger(__name__)

NODATA = np.int32(-32768)
SUBSURFACE_BAND = 2


def compute_ground_indices(store: ColumnStore) -> np.ndarray:
    """
    Return one int32 per occupied column (store's canonical order):
    the EXCLUSIVE z_end of the HIGHEST ground interval (one past its
    top voxel).

    Why the *highest* ground interval?
    The LiDAR classification labels the bare-earth surface as class 2.
    If a column carries more than one ground interval, the HIGHEST one is
    taken as the anchor - a heuristic that treats the uppermost class-2
    evidence as the terrain surface. Note the failure direction: a raised
    surface mislabelled as ground (e.g. a bridge deck, class 17) would
    lie ABOVE the true terrain and win the anchor, so "highest" is not
    guaranteed to be the real surface. No current artifact quantifies how
    often multi-ground columns occur or why; a per-column count of
    class-2 intervals over one store would pin that down.

    Algorithm
    ---------
    1. Build a per-interval column index via np.repeat.
    2. Mask to class 2 intervals only.
    3. Per-column maximum.reduceat of z_end (the highest ground surface).
    4. Columns with no ground interval -> NODATA.

    @param store The ``ColumnStore`` whose flat arrays (``_keys``,
        ``_off``, ``_cl``, ``_ze``) are read; it is not modified.
    @return int32 array with one entry per column, in the store's
        canonical column order: the EXCLUSIVE ``z_end`` (a voxel z index,
        one past the top ground voxel) of the highest GROUND (class 2)
        interval, or NODATA (-32768) for a column with no ground interval.
        An empty store gives an empty int32 array.
    """
    n_cols = int(store._keys.shape[0])
    if n_cols == 0:
        return np.empty(0, dtype=np.int32)

    off = store._off
    cls = store._cl
    ze = store._ze

    nivs = np.diff(off)
    col_idx = np.repeat(np.arange(n_cols, dtype=np.int64), nivs)
    ground_mask = cls == GROUND

    ground_cols = col_idx[ground_mask]
    ground_ze = ze[ground_mask]

    if ground_cols.size == 0:
        return np.full(n_cols, NODATA, dtype=np.int32)

    order = np.argsort(ground_cols, kind="stable")
    gc_sorted = ground_cols[order]
    gze_sorted = ground_ze[order]

    block_starts = np.concatenate((
        [0],
        np.where(np.diff(gc_sorted) != 0)[0] + 1
    ))
    max_ze = np.maximum.reduceat(gze_sorted, block_starts)

    # _ze is int32, so this cast is value-preserving for every legal input
    # (the old .astype(np.int16) wrapped silently at fine cell_z).
    out = np.full(n_cols, NODATA, dtype=np.int32)
    out[gc_sorted[block_starts]] = max_ze.astype(np.int32)
    return out


def fill_ground_holes(
    ground_idx: np.ndarray,
    ix: np.ndarray,
    iy: np.ndarray,
    *,
    max_radius: int = 10,
) -> np.ndarray:
    """
    Fill NODATA entries by interpolating from nearby penetrated columns.

    Operates on the store's canonical column order. The (ix, iy) arrays
    MUST be in the same order as *ground_idx* (i.e. from _unpack_keys).

    Strategy (multi-resolution flood-fill):
      1. 4-neighbourhood: for each NODATA column, average valid
         orthogonal neighbours.
      2. 8-neighbourhood: same, including diagonals.
      3. Radial expansion: up to max_radius steps outward.

    LIMITATION: This is an interpolation heuristic. The filled value is
    UNVERIFIED - no beam ever penetrated that column. Use it only for
    visualisation and approximate statistics. The authoritative three-way
    decoder never interpolates.

    ORDER DEPENDENCE: every pass writes into the output array as it goes, so
    a column filled earlier in a sweep is already a valid neighbour for a
    column visited later in the SAME sweep. The sweep follows the store's
    canonical column order (ascending packed (ix, iy)), which means a hole's
    filled value depends on that order: reaching the same hole from a
    different scan order can average a different set of neighbours. The values
    are an unverified heuristic either way, so the sweep is left as it is
    rather than made order-independent.

    The function returns a copy; the input is not modified.

    @param ground_idx int32 array of per-column ground indices (voxel z
        indices, the exclusive z_end of the highest ground interval, or
        NODATA for a hole), in the store's canonical column order.
    @param ix Integer array of column x indices (grid coordinates, not
        metres), in the same order as ``ground_idx``.
    @param iy Integer array of column y indices (grid coordinates, not
        metres), in the same order as ``ground_idx``.
    @param max_radius Largest Chebyshev radius, in columns, of the radial
        expansion stage; radii 2 to ``max_radius`` inclusive are scanned
        after the 4- and 8-neighbourhood passes. Default 10.
    @return Copy of ``ground_idx`` in which every NODATA entry a pass
        could reach is replaced by the rounded mean of the valid neighbour
        values found in that pass (``np.int32(round(np.mean(vals)))``);
        entries no pass could fill stay NODATA, and their count is logged
        at info level. An empty input returns an empty copy.
    """
    n_cols = len(ground_idx)
    if n_cols == 0:
        return ground_idx.copy()

    out = ground_idx.copy()
    nodata_mask = out == NODATA
    if not nodata_mask.any():
        return out

    # Packed-key binary search instead of a dict of (ix, iy) tuples over
    # every column (the per-column Python layout the store redesign
    # removed). _pack_keys gives one uint64 per column; argsort makes the
    # lookup robust to any input order.
    from .data_structures import _pack_keys
    _pk = _pack_keys(np.asarray(ix, np.int64), np.asarray(iy, np.int64))
    _order = np.argsort(_pk, kind="stable")
    _pk_sorted = _pk[_order]

    def _lookup(cx: int, cy: int):
        """Position of column ``(cx, cy)`` in the input ``ix``/``iy`` order (a binary search on the sorted packed keys), or None when the column is not in the store."""
        k = _pack_keys(np.array([cx], np.int64), np.array([cy], np.int64))[0]
        j = int(np.searchsorted(_pk_sorted, k))
        if j < len(_pk_sorted) and _pk_sorted[j] == k:
            return int(_order[j])
        return None

    neighbours_4 = [(1, 0), (-1, 0), (0, 1), (0, -1)]

    for _ in range(10):  # up to 10 relaxation passes (early-exits below)
        changed = False
        for col_i in np.where(nodata_mask)[0]:
            cx, cy = int(ix[col_i]), int(iy[col_i])
            vals = []
            for dx, dy in neighbours_4:
                ni = _lookup(cx + dx, cy + dy)
                if ni is not None and out[ni] != NODATA:
                    vals.append(int(out[ni]))
            if vals:
                out[col_i] = np.int32(round(np.mean(vals)))
                changed = True
        if not changed:
            break
        nodata_mask = out == NODATA
        if not nodata_mask.any():
            return out

    # 8-neighbourhood (including diagonals)
    neighbours_8 = neighbours_4 + [(1, 1), (1, -1), (-1, 1), (-1, -1)]
    for _ in range(10):
        changed = False
        for col_i in np.where(nodata_mask)[0]:
            cx, cy = int(ix[col_i]), int(iy[col_i])
            vals = []
            for dx, dy in neighbours_8:
                ni = _lookup(cx + dx, cy + dy)
                if ni is not None and out[ni] != NODATA:
                    vals.append(int(out[ni]))
            if vals:
                out[col_i] = np.int32(round(np.mean(vals)))
                changed = True
        if not changed:
            break
        nodata_mask = out == NODATA
        if not nodata_mask.any():
            return out

    # Radial expansion (capped at max_radius). Deliberately NO early break
    # on a no-change radius: a hole whose nearest valid column sits at
    # distance r fills only once the scan reaches radius r, so quiet
    # smaller radii are expected along the way, not a convergence signal.
    # (The old `if not changed: break` made max_radius unreachable past
    # the first empty radius.)
    for radius in range(2, max_radius + 1):
        for col_i in np.where(nodata_mask)[0]:
            cx, cy = int(ix[col_i]), int(iy[col_i])
            vals = []
            # Scan the hollow square at distance = radius
            for dx in range(-radius, radius + 1):
                for dy in ( -radius, radius):
                    ni = _lookup(cx + dx, cy + dy)
                    if ni is not None and out[ni] != NODATA:
                        vals.append(int(out[ni]))
            for dy in range(-radius + 1, radius):
                for dx in ( -radius, radius):
                    ni = _lookup(cx + dx, cy + dy)
                    if ni is not None and out[ni] != NODATA:
                        vals.append(int(out[ni]))
            if vals:
                out[col_i] = np.int32(round(np.mean(vals)))
        nodata_mask = out == NODATA
        if not nodata_mask.any():
            return out

    remaining = int(nodata_mask.sum())
    if remaining:
        logger.info("ground hole-fill: %d columns still NODATA after "
                     "max_radius=%d", remaining, max_radius)
    return out
