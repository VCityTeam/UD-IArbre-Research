"""
Three-way emptiness decoder for the column-compressed voxel grid.
@ingroup t2_algos


Classifies every voxel as one of four states:
  - Class code (0-255): occupied by a LiDAR-labelled interval
  - MEASURED_AIR (-1):  LiDAR beam traversed this voxel - genuine empty space
  - OPAQUE_INTERIOR (-2): No beam entered - under a roof, inside a building
  - SUBSURFACE (-3):      Below the ground surface - solid earth

The nadir-beam mitigation (Section 5 of nadir_beam_caveat.md) is
implemented in ``classify_gap()`` with the ``check_neighbourhood`` flag.
"""

from __future__ import annotations

import numpy as np

from .data_structures import ColumnStore, Column
from .classes_config import BUILDING
from .ground_index import NODATA, SUBSURFACE_BAND

# -- Decoder return values ----------------------------------------------
MEASURED_AIR    = -1   # LiDAR beam traversed this voxel
OPAQUE_INTERIOR = -2   # No beam entered (under roof / inside building)
SUBSURFACE      = -3   # iz < ground_z - SUBSURFACE_BAND -> solid earth


def classify_voxel(
    store: ColumnStore,
    ix: int, iy: int, iz: int,
    *,
    check_neighbourhood: bool = True,
) -> int:
    """
    Classify a single voxel (ix, iy, iz).

    @param store The column-compressed voxel grid (``ColumnStore``).
    @param ix Voxel x index (grid coordinates, not metres).
    @param iy Voxel y index (grid coordinates, not metres).
    @param iz Voxel z index (grid coordinates, not metres).
    @param check_neighbourhood When True (default), apply the nadir-beam
        neighbourhood check for gap voxels (conservative: gaps beside
        buildings -> opaque interior). When False, use the simple nadir
        assumption (any gap in a penetrated column -> measured air).
    @return int: the class code (0-255) if the voxel is occupied;
        MEASURED_AIR (-1) if it is a gap in a penetrated column;
        OPAQUE_INTERIOR (-2) if no beam entered, which includes the case
        where no column exists at (ix, iy); SUBSURFACE (-3) if it lies
        below the ground surface.
    """
    col_idx = store._find((ix, iy))
    if col_idx < 0:
        return OPAQUE_INTERIOR  # no data -> opaque

    cls = class_at_slice(store, col_idx, iz)
    if cls is not None:
        return cls  # occupied -> return class code

    return classify_voxel_at(store, col_idx, ix, iy, iz,
                             check_neighbourhood=check_neighbourhood)


def classify_voxel_at(store, col_idx: int, ix: int, iy: int, iz: int,
                      *, check_neighbourhood: bool = True) -> int:
    """Gap-voxel classification when the column index is already known.

    ``classify_voxel`` recomputes the column index (a ``_find`` = searchsorted)
    even after its caller has one, and re-tests occupancy through the flat
    arrays (``class_at_slice``). A hot caller that has already located the
    column and established
    the voxel is a gap - the ray walkers do exactly this - calls here instead,
    which skips both. It assumes ``col_idx >= 0`` and that ``iz`` is a gap
    (i.e. ``class_at_slice(store, col_idx, iz) is None``); the result is the
    same value ``classify_voxel`` would return for a gap voxel.

    @param store The column-compressed voxel grid (``ColumnStore``).
    @param col_idx Index of the column in the store's canonical order;
        must be >= 0 (the caller has already located the column).
    @param ix Voxel x index (grid coordinates, not metres); only used by
        the neighbourhood check inside ``classify_gap``.
    @param iy Voxel y index (grid coordinates, not metres); same use as
        ``ix``.
    @param iz Voxel z index (grid coordinates, not metres), assumed to be
        a gap in this column.
    @param check_neighbourhood Passed through to ``classify_gap``: True
        applies the nadir-beam neighbourhood check, False takes the simple
        nadir assumption (any gap in a penetrated column is measured air).
    @return int: MEASURED_AIR (-1), OPAQUE_INTERIOR (-2) or SUBSURFACE
        (-3). In a column whose ``ground_idx`` entry is NODATA the split
        is at the column's highest exclusive ``z_end`` (``top``):
        MEASURED_AIR when ``iz >= top``, OPAQUE_INTERIOR below it.
        Otherwise SUBSURFACE when ``iz < ground_z - SUBSURFACE_BAND``
        (``ground_z`` being the exclusive z_end of the highest ground
        interval), else the verdict of ``classify_gap``.
    """
    gi = store.ground_idx
    ground_z = int(gi[col_idx])

    if ground_z == int(NODATA):
        # The column has returns but no ground return (roof-only or
        # canopy-only). ABOVE its highest return the beam demonstrably passed
        # on its way down to that return - the same nadir reasoning
        # classify_gap applies to penetrated columns - so that region is
        # measured air. At or below the top return nothing was measured:
        # opaque interior. Without this split, every voxel above a roof-only
        # column reads as solid, the reference walker and CeilingDDA disagree
        # (CeilingDDA's above-ceiling escape is provably right here), and
        # oblique shadow rays phantom-hit the open sky above roofs - which
        # affects a third to half of real urban columns: the NODATA share of
        # ``ground_idx`` is 33.8 percent on the 1.0 m sweep store
        # (338,107 of 999,170 columns) and 44.9 percent at 0.25/0.1
        # (6,911,228 of 15,393,959), counted directly on those stores.
        s, e = int(store._off[col_idx]), int(store._off[col_idx + 1])
        top = int(store._ze[s:e].max())  # z_end is exclusive
        return MEASURED_AIR if iz >= top else OPAQUE_INTERIOR

    if iz < ground_z - SUBSURFACE_BAND:
        return SUBSURFACE

    return classify_gap(
        store, ix, iy, iz, col_idx, ground_z,
        check_neighbourhood=check_neighbourhood,
    )


def classify_gap(
    store: ColumnStore,
    ix: int, iy: int, iz: int,
    col_idx: int, ground_z: int,
    *,
    check_neighbourhood: bool = True,
) -> int:
    """
    Classify a gap voxel (iz between intervals) in a penetrated column.

    With ``check_neighbourhood=True``, applies the nadir-beam mitigation:
    a gap beside a building (where every penetrated neighbour at this
    height is building) is classified as OPAQUE_INTERIOR instead of
    MEASURED_AIR, preventing the "light shaft beside building" artefact.

    Notes
    -----
    NODATA NEIGHBOURS ABSTAIN, and this is frozen. A neighbour that is
    OCCUPIED at *iz* votes on its class alone, whatever its ground index. A
    neighbour with a gap at *iz* votes only if it is itself penetrated: where
    its ground index is NODATA it is skipped and counts neither for nor
    against building, exactly as an absent neighbour column does. A gap whose
    four neighbours are all absent or all unpenetrated therefore leaves no
    voter at all and returns MEASURED_AIR. That is the conservative direction
    for this check, which exists only to DEMOTE air to opaque interior: it
    does so on positive evidence (every voter is building) and never on the
    absence of evidence.

    @param store The column-compressed voxel grid (``ColumnStore``).
    @param ix Voxel x index (grid coordinates, not metres) of the gap
        voxel; the four orthogonal neighbours are (ix +- 1, iy) and
        (ix, iy +- 1).
    @param iy Voxel y index (grid coordinates, not metres) of the gap
        voxel.
    @param iz Voxel z index (grid coordinates, not metres) of the gap
        voxel; the height at which each neighbour is probed.
    @param col_idx Index of the column in the store's canonical order.
        Not read by the body; accepted for the calling convention shared
        with ``classify_voxel_at``.
    @param ground_z The column's entry from ``store.ground_idx``: the
        EXCLUSIVE z_end of the highest ground interval, one past the top
        ground voxel (the column IS penetrated). Not read by the body:
        the subsurface test against it happens in the caller.
    @param check_neighbourhood Enable the nadir neighbourhood check.
        When False the function returns MEASURED_AIR at once.
    @return int: OPAQUE_INTERIOR (-2) when at least one 4-neighbour is
        penetrated at ``iz`` and every penetrated 4-neighbour is BUILDING
        at ``iz``; MEASURED_AIR (-1) otherwise (including when no
        neighbour votes at all).
    """
    if not check_neighbourhood:
        return MEASURED_AIR  # fast path: nadir assumption

    # 4-neighbourhood check: for each orthogonal neighbour that exists
    # and is penetrated, check if that neighbour is BUILDING at iz.
    # If ANY neighbour is NOT building -> air is plausible -> measured air.
    # If ALL penetrated neighbours are building -> opaque interior (the
    # beam probably hit the neighbouring building instead).
    building_class = BUILDING
    has_non_building_neighbour = False
    any_penetrated_neighbour = False

    gi = store.ground_idx
    for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
        nk = (ix + dx, iy + dy)
        n_col = store.columns.get(nk)
        if n_col is None:
            continue  # no data at this neighbour -> skip

        # Check if the neighbour is penetrated at this height by looking
        # for ANY interval overlapping iz (not just building).
        if _class_at_voxel(n_col, iz) is None:
            # Neighbour has no occupied voxel at iz - check if the
            # column itself is penetrated (has a ground return).
            n_col_idx = store._find(nk)
            if n_col_idx < 0:
                continue
            if int(gi[n_col_idx]) == int(NODATA):
                continue  # neighbour not penetrated -> skip

        # Neighbour is penetrated at this height. Is it building?
        any_penetrated_neighbour = True
        if not _voxel_is_class(n_col, iz, building_class):
            has_non_building_neighbour = True
            break  # at least one non-building neighbour -> air

    if any_penetrated_neighbour and not has_non_building_neighbour:
        # Every penetrated neighbour at this height is building.
        # The beam probably did NOT pass through (ix, iy) at this
        # height - the neighbouring building blocked it.
        return OPAQUE_INTERIOR

    return MEASURED_AIR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _class_at_voxel(col: Column, iz: int) -> int | None:
    """
    Return the class code occupying voxel iz in *col*, or None if the
    voxel is a gap (between intervals).

    Intervals in a column may OVERLAP across classes: canonical order is
    (z_start, cls), so z_end is NOT monotonic (see
    ColumnStore.class_overlap_stats). The covering interval is therefore
    not necessarily the one with the largest z_start <= iz - every
    interval starting at or below iz is a candidate. We scan them from
    the latest-starting down and return the first that still covers iz,
    which is the same tie-break the old single-probe check applied
    whenever it did find a cover (so non-overlapping columns behave
    bit-identically).

    np.searchsorted bounds the candidate range in O(log n); the cover
    scan is O(candidates) worst case, bounded by the column's interval
    count (a few intervals on average, under two hundred at most).
    """
    zs = col.z_start
    ze = col.z_end
    i = int(np.searchsorted(zs, iz, side="right"))
    # Every zs[j] for j < i satisfies zs[j] <= iz; a candidate covers iz
    # iff its exclusive end lies beyond it.
    for j in range(i - 1, -1, -1):
        if ze[j] > iz:
            return int(col.cls[j])
    return None


def class_at_slice(store, ci: int, iz: int) -> int | None:
    """Class occupying voxel ``iz`` in column index ``ci``, or None (gap).

    The allocation- and searchsorted-free twin of _class_at_voxel(),
    working directly on the store's flat arrays via the column's (start, end)
    offsets. Two differences from ``_class_at_voxel``, both deliberate and
    both result-preserving:

    * no ``Column`` object is materialised (the ray walkers call this once per
      voxel, so a per-voxel dataclass would be pure churn);
    * the covering interval is found by a plain reverse scan rather than
      ``np.searchsorted``: columns hold a handful of intervals, so a Python
      scan is faster than a numpy call on this per-voxel path. Scanning from
      the last interval down and returning the first whose ``[zs, ze)`` covers
      ``iz`` reproduces ``_class_at_voxel``'s tie-break exactly (latest-starting
      cover wins), which the differential test asserts ray for ray.

    @param store The ``ColumnStore`` whose flat arrays (``_zs``, ``_ze``,
        ``_cl``) are read directly through ``_slice_at``.
    @param ci Index of the column in the store's canonical order; must be
        a valid index (>= 0).
    @param iz Voxel z index (grid coordinates, not metres).
    @return int class code of the latest-starting interval whose
        half-open ``[z_start, z_end)`` covers ``iz``, or None when ``iz``
        is a gap in this column.
    """
    s, e = store._slice_at(ci)
    zs = store._zs
    ze = store._ze
    cl = store._cl
    for j in range(e - 1, s - 1, -1):
        if zs[j] <= iz < ze[j]:
            return int(cl[j])
    return None


def _voxel_is_class(col: Column, iz: int, cls: int) -> bool:
    """True if voxel iz falls inside an interval of class *cls*."""
    c = _class_at_voxel(col, iz)
    return c is not None and c == cls


def interval_gap_type(
    store: ColumnStore,
    ix: int, iy: int,
    z_lo: int, z_hi: int,
    *,
    check_neighbourhood: bool = True,
) -> int:
    """
    Classify all voxels in the range [z_lo, z_hi) as one type.

    This is a batch version of ``classify_voxel`` for a contiguous
    z-range that is known to be a gap (no intervals overlap it). Raises
    ``ValueError`` if the range does overlap an interval.

    Uniformity is NOT tested. The one value returned is the classification
    of the range's MID-POINT, taken as representative: a range straddling
    the subsurface band line, or the top of a ground-less column, is not
    uniform and resolves to whichever side its mid-point falls on. The
    inline comments below mark each place that convention applies.

    Intended for classifying empty segments without iterating per-voxel.
    No production caller uses it today - the ray walkers classify per
    voxel via ``classify_voxel_at`` - it is exercised by the regression
    tests (test_regression_audit.py).

    @param store The column-compressed voxel grid (``ColumnStore``).
    @param ix Column x index (grid coordinates, not metres).
    @param iy Column y index (grid coordinates, not metres).
    @param z_lo Inclusive lower voxel z index of the range.
    @param z_hi Exclusive upper voxel z index of the range.
    @param check_neighbourhood Passed through to ``classify_gap`` for the
        mid-point voxel: True applies the nadir-beam neighbourhood check,
        False takes the simple nadir assumption.
    @return int decoder state, never a class code (the range is verified
        to be a gap). OPAQUE_INTERIOR (-2) for an empty range
        (``z_lo >= z_hi``) or a column absent from the store. Otherwise
        the classification of the mid-point voxel ``(z_lo + z_hi) // 2``:
        in a column whose ``ground_idx`` is NODATA, MEASURED_AIR (-1)
        when the mid-point is at or above the column's highest exclusive
        z_end and OPAQUE_INTERIOR below it; else SUBSURFACE (-3) when the
        mid-point is below ``ground_z - SUBSURFACE_BAND``, else the
        verdict of ``classify_gap`` at the mid-point.
    @throws ValueError when an interval of the column overlaps
        ``[z_lo, z_hi)``, i.e. some interval has ``z_start < z_hi`` and
        ``z_end > z_lo``.
    """
    if z_lo >= z_hi:
        return OPAQUE_INTERIOR  # empty range

    col = store.columns.get((ix, iy))
    if col is None:
        return OPAQUE_INTERIOR

    # Verify the range is indeed a gap. An interval overlaps [z_lo, z_hi)
    # iff z_start < z_hi and z_end > z_lo. z_start is sorted (canonical
    # order) so it bounds the candidates; z_end is NOT sorted under
    # cross-class overlaps, so the ends are vector-checked rather than
    # probed at a single index. The old single-probe check missed both an
    # interval lying entirely inside the range and an early long interval
    # covering it, so the documented ValueError was never raised for
    # those.
    zs = col.z_start
    ze = col.z_end
    i_hi = int(np.searchsorted(zs, z_hi, side="left"))  # zs[j] < z_hi for j < i_hi
    bad = np.nonzero(ze[:i_hi] > z_lo)[0]
    if bad.size:
        j = int(bad[-1])
        raise ValueError(f"Range [{z_lo}, {z_hi}) overlaps an interval "
                         f"at [{int(zs[j])}, {int(ze[j])}) in column ({ix}, {iy})")

    col_idx = store._find((ix, iy))
    if col_idx < 0:
        return OPAQUE_INTERIOR

    gi = store.ground_idx
    ground_z = int(gi[col_idx])

    if ground_z == int(NODATA):
        # Same semantics as classify_voxel_at: above the top return of a
        # ground-less column the beam demonstrably passed, so measured air;
        # below it, opaque interior. A range straddling the top is not
        # uniform, so classify by its mid-point - the same representative-
        # mid-point convention this function already uses for classify_gap.
        top = int(col.z_end.max())
        if z_lo >= top:
            return MEASURED_AIR
        if z_hi <= top:
            return OPAQUE_INTERIOR
        return MEASURED_AIR if (z_lo + z_hi) // 2 >= top else OPAQUE_INTERIOR

    # A range is classified by its representative mid-point throughout this
    # function (NODATA branch above, classify_gap below); the subsurface
    # test uses the same convention so a range straddling the band line
    # resolves to whichever side its mid-point falls on - the same answer
    # classify_voxel gives for that voxel (iz < ground_z - SUBSURFACE_BAND).
    mid_iz = (z_lo + z_hi) // 2
    if mid_iz < ground_z - SUBSURFACE_BAND:
        return SUBSURFACE
    return classify_gap(
        store, ix, iy, mid_iz, col_idx, ground_z,
        check_neighbourhood=check_neighbourhood,
    )
