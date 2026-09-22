"""
Collapse each column's overlapping intervals to one label per voxel.
@ingroup t2_algos


Cross-class intervals that share a z-range within the same column are
resolved by majority vote: the interval with the highest point count wins.

Because voxels can be fine (down to 0.25 m xy, 0.1 m z at the fine end of
the resolution sweep; the CLI default is 0.5 m cubed), a segment's competing
intervals often carry only one or two points each, so a strict
"highest count, ties -> smallest class code" rule decides many voxels on a
single-point margin and lets an arbitrary numeric artefact (the class code)
settle the rest.  ``resolve()`` therefore treats near-equal counts as a tie
and breaks it with signals that mean something:

  1. **Near-tie band** (``tie_margin`` / ``tie_rel``): intervals whose count
     is within a small absolute (or relative) margin of the best count are
     considered tied, instead of letting a one-point lead win outright.
  2. **Class demotion** (``demote_uncertain``): in a near-tie, the "I don't
     know" buckets - created/never-classified, unclassified, low- and
     high-point noise, and IGN artefact - never take a voxel from a genuinely
     labelled class.  (Overridable with an explicit ``class_priority``.)
  3. **Taller support** (``prefer_taller``): among still-tied candidates,
     the interval whose *originating run* is taller wins - a 3-voxel
     vegetation run backed by two points is more trustworthy than a
     single-voxel blip backed by the same two points.
  4. **Smallest class code**: final, purely deterministic fallback (this is
     the whole of the previous policy).

Counts are attributed **proportionally**: when an overlap splits a run into
several segments, each segment carries the fraction of the run's point count
that matches its share of the run's height, so a run keeps exactly its count
when it wins its whole span and only a share when it wins part. Note
that TOTAL point counts are NOT conserved across resolve(): a run that
loses a contested span drops its share of the count outright.  Those points
are DISCARDED, not transferred - the winner's count is computed from its own
run alone (``share`` below uses ``best_ct``) and is unaffected by how many
losers it displaced.  A class whose every run lies inside a winner's span
therefore disappears entirely, which is the intended proportional outcome and
not a bug.  Downstream count consumers (``denoise.min_points_filter``) see
these post-attribution counts.  (The previous
code gave every segment the run's full count and summed on merge, inflating
counts n-fold for split runs - which this policy would otherwise trigger more
often, and which ``denoise`` consumes.)

The output is a NEW ``ColumnStore`` whose intervals are non-overlapping and
in canonical (z_start, class) order.  A one-interval column is passed through
unchanged - there is nothing to resolve.  A zero-interval column is DROPPED:
the sweep returns it as it is, and the store builder then skips any column
that resolved to no intervals, so it contributes no key to the output.

Legacy behaviour (strict count, ties -> smallest class code) is still
reachable::

    resolve(store, tie_margin=0, prefer_taller=False, demote_uncertain=False)

Neighbour consistency (the *other* signal worth using for coin-flip voxels)
is deliberately NOT done here: it is a spatial/morphological operation, and
this module stays strictly column-local and deterministic.  It lives in
``denoise.morphological_filter``, which already carries the 8-neighbour,
z-range-overlap machinery.  In the shipped pipeline that filter runs BEFORE
this pass - ``postprocess_cli``'s fixed order is min-points -> morph ->
absorb -> resolve -> group - so the spatial reasoning is done on the raw
intervals and ``resolve`` sees what it leaves.  What this module is built
around is the two staying SEPARATE passes, not which side of ``resolve`` the
spatial one falls on.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from .data_structures import ColumnStore, Column
from .classes_config import (
    CREATED_NEVER_CLASSIFIED,
    UNCLASSIFIED,
    LOW_POINT_NOISE,
    HIGH_NOISE,
    ARTEFACT,
)

# Classes that represent "no confident label" - they should never win a
# near-tie against a genuinely labelled class.
_DEMOTED_DEFAULT: frozenset[int] = frozenset({
    CREATED_NEVER_CLASSIFIED,   # 0
    UNCLASSIFIED,               # 1
    LOW_POINT_NOISE,            # 7
    HIGH_NOISE,                 # 18
    ARTEFACT,                   # 65
})


@dataclass(frozen=True)
class _Policy:
    """Resolved tiebreak policy, threaded down to _emit_segment()."""
    tie_margin: int
    tie_rel: float
    prefer_taller: bool
    prio: Callable[[int], int]


def resolve(
    store: ColumnStore,
    *,
    tie_margin: int = 1,
    tie_rel: float = 0.0,
    prefer_taller: bool = True,
    demote_uncertain: bool = True,
    class_priority: Sequence[int] | None = None,
) -> ColumnStore:
    """
    Return a NEW ``ColumnStore`` where every voxel has at most one class.

    Notes
    -----
    Only the *tiebreak* changed; a decisive count still wins outright. This
    function is column-local and deterministic. See the module docstring for
    the neighbour-consistency companion step.

    @param store            Source store, possibly with cross-class overlapping intervals.
    @param tie_margin       Counts within this *absolute* margin of the best count in a
                            segment are treated as tied (default 1). ``0`` restores strict
                            "highest count wins".
    @param tie_rel          Optional *relative* near-tie band (default 0.0): counts >=
                            ``best * (1 - tie_rel)`` are also treated as tied. Combined
                            (unioned) with ``tie_margin``. Useful when counts are large and
                            a one-point margin is meaningless.
    @param prefer_taller    Among tied candidates, prefer the one whose originating interval
                            spans more voxels, i.e. taller structural support (default True).
    @param demote_uncertain In a near-tie, never let created/never-classified, unclassified,
                            low/high noise, or artefact win over a genuinely labelled class
                            (default True). Ignored when ``class_priority`` is given.
    @param class_priority   Explicit preference order for near-ties, earlier entries preferred.
                            Overrides ``demote_uncertain``. Classes not listed rank after all
                            listed ones (and are then ordered by ``prefer_taller`` /
                            class code).
    @return A new ColumnStore with non-overlapping intervals in canonical (z_start, class)
            order, on the same grid as *store*. Columns that resolve to no intervals are
            dropped, and near-tie counts are attributed proportionally and rounded once, so
            total point counts are NOT conserved; see the module docstring.
    @throws ValueError When ``tie_margin < 0``, or ``tie_rel`` is outside ``[0, 1)``.
    """
    if tie_margin < 0:
        raise ValueError("tie_margin must be >= 0")
    if not (0.0 <= tie_rel < 1.0):
        raise ValueError("tie_rel must be in [0, 1)")

    if class_priority is not None:
        rank = {int(c): i for i, c in enumerate(class_priority)}
        n_listed = len(rank)
        prio: Callable[[int], int] = lambda c: rank.get(c, n_listed)
    elif demote_uncertain:
        prio = lambda c: 1 if c in _DEMOTED_DEFAULT else 0
    else:
        prio = lambda c: 0

    policy = _Policy(tie_margin=int(tie_margin), tie_rel=float(tie_rel),
                     prefer_taller=bool(prefer_taller), prio=prio)

    # Accumulate numpy arrays only; the per-interval Python int lists this
    # used to build for ix/iy are exactly the layout the store redesign
    # forbids. Column keys are replicated once at the end via np.repeat.
    col_keys: list[tuple[int, int]] = []
    col_niv: list[int] = []
    all_zs: list[np.ndarray] = []
    all_ze: list[np.ndarray] = []
    all_cl: list[np.ndarray] = []
    all_ct: list[np.ndarray] = []

    for (ix, iy), col in store.columns.items():
        resolved = _resolve_column(col, policy)
        if len(resolved) == 0:
            continue
        col_keys.append((ix, iy))
        col_niv.append(len(resolved))
        all_zs.append(resolved.z_start)
        all_ze.append(resolved.z_end)
        all_cl.append(resolved.cls)
        all_ct.append(resolved.count)

    if not all_zs:
        return ColumnStore(store.x_min, store.y_min, store.z_min,
                           store.cell_xy, store.cell_z)

    return ColumnStore.from_intervals(
        store.x_min, store.y_min, store.z_min,
        store.cell_xy, store.cell_z,
        np.repeat(np.array([k[0] for k in col_keys], dtype=np.int32),
                  col_niv),
        np.repeat(np.array([k[1] for k in col_keys], dtype=np.int32),
                  col_niv),
        np.concatenate(all_zs),
        np.concatenate(all_ze),
        np.concatenate(all_cl),
        np.concatenate(all_ct),
        assume_canonical=False,
    )


def _resolve_column(col: Column, policy: _Policy) -> Column:
    """
    Resolve overlapping intervals in one column by (near-tie) majority vote.

    Sweep line over interval start/end events. At each z-segment where the
    active set is constant, _emit_segment() applies ``policy`` to pick
    the label.
    """
    n = len(col)
    if n <= 1:
        return col

    zs = col.z_start
    ze = col.z_end
    cls = col.cls
    ct = col.count

    events = []  # (z, is_start, idx)
    for i in range(n):
        events.append((int(zs[i]), True,  i))
        events.append((int(ze[i]), False, i))

    events.sort(key=lambda x: (x[0], not x[1]))
    # Sort order: increasing z, start-before-end at equal z. With the
    # half-open [z_start, z_end) convention an interval ending at z is NOT
    # active at z, and no segment is emitted between two events at the same
    # z (emission requires z > prev_z), so this ordering has no effect on
    # the output - it is kept purely so the event array has one canonical,
    # reproducible order.

    # active entries: (count, class_code, idx, height) - height is the
    # originating interval's voxel span, the "taller support" signal.
    active: list[tuple[int, int, int, int]] = []
    prev_z = None

    result_zs: list[int] = []
    result_ze: list[int] = []
    result_cl: list[int] = []
    result_ct: list[int] = []

    for z, is_start, idx in events:
        if prev_z is not None and z > prev_z:
            _emit_segment(prev_z, z, active, policy,
                          result_zs, result_ze, result_cl, result_ct)

        if is_start:
            height = int(ze[idx]) - int(zs[idx])
            active.append((int(ct[idx]), int(cls[idx]), idx, height))
        else:
            active = [a for a in active if a[2] != idx]

        prev_z = z

    return Column(
        z_start=np.array(result_zs, dtype=np.int32),
        z_end=np.array(result_ze, dtype=np.int32),
        cls=np.array(result_cl, dtype=np.uint8),
        # result_ct holds proportional (float) shares; round once here.
        count=np.rint(np.array(result_ct, dtype=np.float64)).astype(np.int32),
    )


def _emit_segment(
    z_lo: int, z_hi: int,
    active: list[tuple[int, int, int, int]],
    policy: _Policy,
    result_zs: list[int],
    result_ze: list[int],
    result_cl: list[int],
    result_ct: list[int],
) -> None:
    """Emit one segment [z_lo, z_hi) with the best active interval.

    Selection: intervals within the near-tie band of the best count are
    candidates; among them the winner minimises
    ``(prio(class), -height if prefer_taller else 0, class)``.
    """
    if not active:
        return  # gap (no interval covers this segment)

    max_count = max(a[0] for a in active)
    lo = max_count - policy.tie_margin
    if policy.tie_rel > 0.0:
        lo = min(lo, max_count * (1.0 - policy.tie_rel))

    # Candidate set: everything still in contention after the near-tie band.
    # (The best-count interval always qualifies, so this is never empty.)
    best = None  # (key, count, class, height)
    for count, class_code, _idx, height in active:
        if count < lo:
            continue
        # ``prefer_taller`` ranks the TALLEST run first (hence -height); with it
        # off the height term must drop OUT of the key entirely (constant 0) so
        # the tie falls through to the smallest class code, which is what the
        # module header advertises as the legacy behaviour.  Using +height here
        # instead would silently break ties by SHORTEST run, reaching the
        # class code only when heights happen to tie.
        height_key = -height if policy.prefer_taller else 0
        key = (policy.prio(class_code), height_key, class_code)
        if best is None or key < best[0]:
            best = (key, count, class_code, height)

    _key, best_ct, best_cls, best_h = best

    # Proportional count attribution. An overlap splits an interval into
    # several segments; giving each segment the interval's FULL count and
    # then summing on merge inflates the count (n_segments x). Attribute the
    # count by the fraction of the originating run this segment covers, so a
    # run that wins its whole span keeps exactly its count, and one that wins
    # only part keeps only that part's share. (result_ct holds floats;
    # _resolve_column rounds once at the end.)
    share = best_ct * (z_hi - z_lo) / best_h

    if result_zs and result_cl[-1] == best_cls and result_ze[-1] == z_lo:
        result_ze[-1] = z_hi
        result_ct[-1] += share
    else:
        result_zs.append(z_lo)
        result_ze.append(z_hi)
        result_cl.append(best_cls)
        result_ct.append(share)
