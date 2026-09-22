"""
Smart cross-class absorption: absorb vegetation runs into building when
they are genuinely inside the building envelope.
@ingroup t2_algos


The refined rule:

    For each V run in each column:
      1. If run_height_voxels <= ``max_noise_run_voxels`` (2 by default)
         AND sandwiched by B -> absorb immediately (noise override: a facade
         misclassification one or two voxels tall is not worth the
         neighbourhood tax).
      2. If NOT sandwiched by B (vertically) -> keep (real tree, ground-
         level shrub, etc.).
      3. If sandwiched by B (vertically):
         a. Z-range-specific neighbourhood check: for the 8 neighbours,
            count those with a BUILDING interval whose [z_start, z_end)
            overlaps the V-run's [z_start, z_end).
         b. If n_building / n_penetrated >= min_neighbour_building ->
            passed horizontal test (penetrated = neighbours with ANY
            interval at this z-range; if none is penetrated, keep).
         c. Cardinal-direction escape: if at least 2 of the 4 cardinal
            directions (N/S/E/W, each probed two cells out) have NO
            building at this z-range -> keep even if step 3b says absorb
            (the tree sticks out of the building envelope on at least
            two sides, not necessarily opposite ones).
         d. Else -> absorb (buried inside building envelope).

    After the pass, a column where any run was absorbed has its
    absorber-class runs coalesced: touching or overlapping same-class runs
    become one maximal run and their point counts add. Relabelling leaves
    the new absorber run flush against the runs that sandwiched it, and
    the canonical target form keeps same-class runs maximal and gapped -
    the coalesce restores it for the runs the relabel touched. Per-voxel
    classification and per-class point totals are unchanged; columns the
    pass does not modify go through untouched.

References
----------
- Original experiment: rle_v4_majority_per_run lost 27 % vegetation
  (J_veg = 0.73) because it absorbed trees against facades.
- Three tweaks refine the plain sandwich rule:
  1. Z-range-specific (not column-level) neighbourhood check
  2. Cardinal-direction escape for "balcony tree" cases
  3. Small-run noise override

Classes handled
---------------
  absorbed: {LOW_VEGETATION, MEDIUM_VEGETATION, HIGH_VEGETATION,
             HIGH_VEGETATION_UPPER}  (codes 3, 4, 5, 8)
  absorber: BUILDING  (code 6)
"""

from __future__ import annotations

import numpy as np

from .classes_config import (
    BUILDING,
    LOW_VEGETATION, MEDIUM_VEGETATION,
    HIGH_VEGETATION, HIGH_VEGETATION_UPPER,
)
from .classes_config import VEGETATION_CLASSES
from .data_structures import ColumnStore, Column, _unpack_keys, _pack_one

# Alias of the shared set so a change in classes_config propagates here.
_VEG = VEGETATION_CLASSES


def absorb_interior(
    store: ColumnStore,
    *,
    absorbed: frozenset = _VEG,
    absorber: int = BUILDING,
    min_neighbour_building: float = 0.75,
    max_noise_run_voxels: int = 2,
) -> ColumnStore:
    """
    Return a new store where absorbed-class runs sandwiched by absorber
    inside a building-dominant neighbourhood are reclassified to absorber.

    @param store                 Input store; not modified.
    @param absorbed              Class codes that may be absorbed; defaults to the
                                 shared vegetation set.
    @param absorber              Class code that absorbs; defaults to BUILDING (6).
    @param min_neighbour_building Minimum absorber fraction (default 0.75) among the
                                 PENETRATED members of the 8-neighbourhood, those with any
                                 interval at the run's z-range. Unpenetrated or absent
                                 neighbours are excluded from the denominator; if no
                                 neighbour is penetrated, the run is kept.
    @param max_noise_run_voxels  Runs of this height or shorter (default 2) are absorbed
                                 unconditionally when sandwiched by absorber (Tweak 3).
    @return A new ColumnStore on the same grid as *store*, with absorbed-class runs
            potentially reclassified. In a column where anything was absorbed, the
            absorber-class runs are then coalesced (touching or overlapping runs become
            one, point counts summed), so an absorption never leaves absorber runs
            touching. Columns the pass does not modify go through unchanged, including any
            touching same-class pairs the input already carried (the voxelizer's
            interleave splits; see ``class_overlap_stats``).
    """
    out_ix: list[int] = []
    out_iy: list[int] = []
    out_zs: list[int] = []
    out_ze: list[int] = []
    out_cl: list[int] = []
    out_ct: list[int] = []

    # Neighbourhood lookups go through store._find (binary search on the
    # packed key array, O(log n)) - the previous dict of (ix, iy) tuples
    # over EVERY column is exactly the per-column Python layout the store
    # redesign removed (~874 B of Python overhead per occupied column,
    # measured; see the data_structures.py docstring). The dense index, when
    # it fits its memory cap, makes each of those eight lookups one array
    # read instead of a binary search.
    store.ensure_dense_index()

    for (ix, iy), col in store.columns.items():
        result_zs: list[int] = []
        result_ze: list[int] = []
        result_cl: list[int] = []
        result_ct: list[int] = []
        absorbed_any = False

        k = 0
        while k < len(col):
            cls_k = int(col.cls[k])
            zs_k = int(col.z_start[k])
            ze_k = int(col.z_end[k])
            ct_k = int(col.count[k])

            # Is this an absorbed-class run?
            if cls_k in absorbed and _is_sandwiched(col, k, absorbed, absorber):
                height_voxels = ze_k - zs_k

                # Tweak 3: small-run noise override
                if height_voxels <= max_noise_run_voxels:
                    absorbed_any = True
                    result_cl.append(absorber)
                    result_zs.append(zs_k)
                    result_ze.append(ze_k)
                    result_ct.append(ct_k)
                    k += 1
                    continue

                # Z-range-specific neighbourhood check (Tweak 1)
                n_building, n_penetrated = _count_building_neighbourhood(
                    store, ix, iy, zs_k, ze_k, absorber,
                )

                # If no penetrated neighbour at this height, keep as-is
                # (we have no information to override the LiDAR label).
                if n_penetrated == 0:
                    result_zs.append(zs_k); result_ze.append(ze_k)
                    result_cl.append(cls_k); result_ct.append(ct_k)
                    k += 1
                    continue

                fraction_building = n_building / n_penetrated
                if fraction_building >= min_neighbour_building:
                    # Tweak 2: cardinal-direction escape
                    if _cardinal_escape(store, ix, iy,
                                        zs_k, ze_k, absorbed, absorber):
                        # Tree sticks out in 2+ directions -> keep
                        result_zs.append(zs_k); result_ze.append(ze_k)
                        result_cl.append(cls_k); result_ct.append(ct_k)
                    else:
                        # Buried -> absorb
                        absorbed_any = True
                        result_cl.append(absorber)
                        result_zs.append(zs_k)
                        result_ze.append(ze_k)
                        result_ct.append(ct_k)
                else:
                    # Not enough building in neighbourhood -> keep
                    result_zs.append(zs_k); result_ze.append(ze_k)
                    result_cl.append(cls_k); result_ct.append(ct_k)
            else:
                # Not an absorbed class, or not sandwiched -> keep
                result_zs.append(zs_k); result_ze.append(ze_k)
                result_cl.append(cls_k); result_ct.append(ct_k)

            k += 1

        # A relabel leaves the new absorber run flush against (or, under a
        # cross-class z-overlap, overlapping) the runs that sandwiched it;
        # the canonical form keeps same-class runs maximal and gapped, so
        # restore it before the rebuild. Columns without an absorption
        # skip this and pass through with their exact input structure.
        if absorbed_any:
            result_zs, result_ze, result_cl, result_ct = \
                _coalesce_absorber_runs(result_zs, result_ze,
                                        result_cl, result_ct, absorber)

        # Append this column's (possibly modified) intervals
        n_out = len(result_zs)
        if n_out > 0:
            out_ix.extend([ix] * n_out)
            out_iy.extend([iy] * n_out)
            out_zs.extend(result_zs)
            out_ze.extend(result_ze)
            out_cl.extend(result_cl)
            out_ct.extend(result_ct)

    if not out_zs:
        return ColumnStore(store.x_min, store.y_min, store.z_min,
                           store.cell_xy, store.cell_z)

    return ColumnStore.from_intervals(
        store.x_min, store.y_min, store.z_min, store.cell_xy, store.cell_z,
        np.array(out_ix, dtype=np.int32),
        np.array(out_iy, dtype=np.int32),
        np.array(out_zs, dtype=np.int32),
        np.array(out_ze, dtype=np.int32),
        np.array(out_cl, dtype=np.uint8),
        np.array(out_ct, dtype=np.int32),
        assume_canonical=False,
    )


# ---------------------------------------------------------------------------
# Helper: restore the canonical form after a relabel
# ---------------------------------------------------------------------------

def _coalesce_absorber_runs(
    zs: list[int], ze: list[int], cl: list[int], ct: list[int],
    absorber: int,
) -> tuple[list[int], list[int], list[int], list[int]]:
    """
    Merge touching or overlapping *absorber*-class runs into maximal runs.

    Only absorber-class runs can need it: a relabel converts an absorbed
    run to *absorber* in place, while every other class keeps the geometry
    the canonical input arrived with. Runs of other classes pass through;
    the absorber runs are sorted by z_start and swept once - a run whose
    z_start <= the accumulated z_end joins the accumulated run (exclusive
    ends, so equality is a touch), extending it to the further end and
    adding its point count. Per-class point totals and per-voxel
    classification are unchanged, and ``from_intervals`` re-sorts the
    rebuild, so the return order does not matter.
    """
    other = [(zs[i], ze[i], cl[i], ct[i])
             for i in range(len(cl)) if cl[i] != absorber]
    runs = sorted((zs[i], ze[i], ct[i])
                  for i in range(len(cl)) if cl[i] == absorber)
    if not runs:
        return zs, ze, cl, ct
    m_zs = [runs[0][0]]
    m_ze = [runs[0][1]]
    m_ct = [runs[0][2]]
    for s, e, c in runs[1:]:
        if s <= m_ze[-1]:
            m_ze[-1] = max(m_ze[-1], e)
            m_ct[-1] += c
        else:
            m_zs.append(s)
            m_ze.append(e)
            m_ct.append(c)
    out_zs = [t[0] for t in other] + m_zs
    out_ze = [t[1] for t in other] + m_ze
    out_cl = [t[2] for t in other] + [absorber] * len(m_zs)
    out_ct = [t[3] for t in other] + m_ct
    return out_zs, out_ze, out_cl, out_ct


# ---------------------------------------------------------------------------
# Helper: check if a run is vertically sandwiched by absorber
# ---------------------------------------------------------------------------

def _is_sandwiched(
    col: Column,
    run_idx: int,
    absorbed: frozenset,
    absorber: int,
) -> bool:
    """
    True if the run at *run_idx* in *col* is vertically sandwiched by
    *absorber* intervals in the same column (above AND below).

    The scan walks outward from *run_idx* in each direction, skipping
    over other absorbed-class runs. A run is "sandwiched" only if an
    absorber interval is found BOTH below and above it before the scan
    meets a run of any other class or the end of the column. Runs at
    the very top or bottom of the column are therefore NOT sandwiched
    (open sky above, or the column floor below, is not an absorber).

    FROZEN HEURISTIC. The scan uses LIST POSITION and class only, never the
    z distance between the run and the absorber it finds, and it errs in both
    directions:

    * Distance-blind. An absorber reached by walking outward counts however
      far away it is, so a building run tens of metres above a vegetation run
      still reads as sandwiching it. The caller's height-specific
      neighbourhood check is what keeps this from firing widely.
    * Overlap-blind, the opposite way. Canonical interval order is
      (z_start, class), so an absorber whose z-span CONTAINS the run can sort
      BELOW it on the strength of a lower z_start. Scanning upward from
      *run_idx* then never reaches that absorber, and a run genuinely
      enclosed by one absorber interval reads as not sandwiched.

    The first can absorb a run it should leave alone; the second declines to
    absorb one it could. Every recorded absorb result was produced with this
    rule, so it stands as written and the limitation is reported instead.
    """
    n = len(col)
    has_absorber_below = False
    has_absorber_above = False

    # Check below: scan downward from run_idx-1
    for j in range(run_idx - 1, -1, -1):
        if int(col.cls[j]) == absorber:
            has_absorber_below = True
            break
        if int(col.cls[j]) in absorbed:
            continue  # skip other absorbed runs
        break  # different non-absorber class -> not sandwiched

    # Check above: scan upward from run_idx+1
    for j in range(run_idx + 1, n):
        if int(col.cls[j]) == absorber:
            has_absorber_above = True
            break
        if int(col.cls[j]) in absorbed:
            continue  # skip other absorbed runs
        break  # different non-absorber class -> not sandwiched

    return has_absorber_below and has_absorber_above


# ---------------------------------------------------------------------------
# Helper: count building neighbourhood at a specific z-range (Tweak 1)
# ---------------------------------------------------------------------------

def _interval_overlaps_class(
    col: Column,
    z_lo: int,
    z_hi: int,
    cls: int,
) -> bool:
    """
    True if *col* has an interval of class *cls* whose [z_start, z_end)
    overlaps [z_lo, z_hi).

    Implementation note: intervals may OVERLAP across classes, so z_end
    is NOT monotonic in canonical (z_start, cls) order - a binary search
    on z_end (what this function and its twin in ``denoise`` used to do)
    is invalid and produced false negatives. z_start IS sorted, so we
    bound the candidates on it and vector-check their ends: an interval
    overlaps iff z_start < z_hi and z_end > z_lo.

    Keep in sync with ``denoise._interval_overlaps_class`` (same
    algorithm; duplicated so that module keeps ``data_structures`` as its
    only internal import rather than pulling in ``absorb`` for one helper -
    the documented layering itself would permit that import, so this is a
    choice, not a constraint).
    """
    if z_lo >= z_hi:
        return False  # empty range overlaps nothing
    zs = col.z_start
    ze = col.z_end
    cl = col.cls
    i_hi = int(np.searchsorted(zs, z_hi, side="left"))  # zs[j] < z_hi for j < i_hi
    if i_hi == 0:
        return False
    return bool(np.any((ze[:i_hi] > z_lo) & (cl[:i_hi] == cls)))


def _count_building_neighbourhood(
    store: ColumnStore,
    ix: int, iy: int,
    z_lo: int, z_hi: int,
    absorber: int,
) -> tuple[int, int]:
    """
    Count how many of the 8 neighbours of (ix, iy) have an *absorber*
    interval overlapping [z_lo, z_hi), and how many neighbours are
    penetrated (have any interval at this height).

    Returns (n_building, n_penetrated).
    """
    n_building = 0
    n_penetrated = 0

    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            nk = (ix + dx, iy + dy)
            ni = store._find(nk)
            if ni < 0:
                continue
            n_col = store._column_at(ni)

            # Check if neighbour has ANY interval overlapping this z-range
            # (i.e. it's "penetrated" at this height). Same candidate
            # bounding as _interval_overlaps_class: z_end is not sorted
            # under cross-class overlaps, so bound on z_start (sorted)
            # and vector-check the ends.
            zs = n_col.z_start
            ze = n_col.z_end
            j_hi = int(np.searchsorted(zs, z_hi, side="left"))
            has_data = bool(np.any(ze[:j_hi] > z_lo)) if j_hi else False
            if not has_data:
                continue

            n_penetrated += 1

            if _interval_overlaps_class(n_col, z_lo, z_hi, absorber):
                n_building += 1

    return n_building, n_penetrated


# ---------------------------------------------------------------------------
# Helper: cardinal-direction escape check (Tweak 2)
# ---------------------------------------------------------------------------

_CARDINAL_DIRS = [(0, 1), (0, -1), (1, 0), (-1, 0)]


def _cardinal_escape(
    store: ColumnStore,
    ix: int, iy: int,
    z_lo: int, z_hi: int,
    absorbed: frozenset,
    absorber: int,
) -> bool:
    """
    True if at least 2 cardinal directions (N/S/E/W) from (ix, iy) have
    NO building at this z-range.

    A real tree against a building will have at least 2 sides open
    (e.g. north and east if the building is to the south-west). A tree
    buried in a courtyard will have building on all 4 sides.

    Returns True -> keep the vegetation (tree sticks out).
    Returns False -> absorb (buried in building envelope).
    """
    open_directions = 0

    for dx, dy in _CARDINAL_DIRS:
        nk = (ix + dx * 2, iy + dy * 2)
        # Check 2 cells out (1 cell immediately adjacent may be the
        # same building; 2 cells out gives a clearer picture of the
        # neighbourhood typology).
        ni = store._find(nk)
        if ni < 0:
            open_directions += 1
            continue
        n_col = store._column_at(ni)
        if not _interval_overlaps_class(n_col, z_lo, z_hi, absorber):
            open_directions += 1

    return open_directions >= 2
