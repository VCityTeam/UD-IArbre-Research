"""Class-aware transmittance along a ray: the interaction model objective 4 asks for.
@ingroup t2_algos


WHY THIS EXISTS
---------------
``ray_trace``/``ray_columns`` answer "what does this ray hit first". That is
binary occlusion: `_check_voxel` returns on the first occupied voxel whatever its
class, so a leaf stops a ray exactly as a concrete wall does. The internship
brief asks for ray casting that accounts for "interactions between voxels
representing different territorial elements", and a boolean hit cannot express
that a canopy passes some light and a roof passes none.

This module replaces the boolean with a transmittance in [0, 1], accumulated
along the ray by the Beer-Lambert law:

    tau = exp(-sum_over_classes(k_c * L_c))

where ``L_c`` is the PATH LENGTH IN METRES through class ``c`` and ``k_c`` is
that class's extinction coefficient in inverse metres. Opaque classes
(buildings, ground, water, and the decoder's OPAQUE_INTERIOR and SUBSURFACE
states) take ``k = inf`` and terminate the walk at tau = 0.

WHY PATH LENGTH, NOT VOXEL COUNT
--------------------------------
Counting voxels would make the result depend on the grid rather than on the
geometry: an oblique ray crosses more voxels than a vertical one through the
same thickness of canopy, and a finer ``cell_z`` multiplies the count without
changing the physics. Path length is computed from the DDA's own ``t``
parameters with the direction normalised to UNIT length, so ``t`` is metres and
a 45 degree ray correctly accumulates sqrt(2) metres per metre of height.

``tests/test_transmittance.py`` pins this as a resolution-invariance property:
the same physical scene at ``cell_z`` 0.5 and 0.25 must return the same tau.
That is the test a voxel-counting implementation would fail, and it is the
reason this module does not simply reuse the hit walker with a counter.

ON THE COEFFICIENTS
-------------------
``Extinction.per_class`` deliberately has NO default vegetation value. A
coefficient invented here would be an assumption, and every downstream number
would inherit it while looking like a measurement. The design instead derives
them from the corpus itself (canopy thickness against observed ground-return
fraction, ``k = -ln(p) / H``). That fit has since landed
(``Experiments/derive_extinction.py`` -> ``extinction_fit.json``, re-run at
both sweep resolutions in ``code_verification`` exp07): callers still pass
coefficients explicitly, now sourced from the fit, and any figure must state
the coefficient it used - still labelled provisional, because the estimator
counts returns rather than pulses (see the derivation script's bias note).
``Extinction.opaque_only()`` is provided for the degenerate case, and is what
the equivalence test uses to check this module against the boolean walker.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .classes_config import VEGETATION_CLASSES
from .data_structures import ColumnStore
from .decoder import (
    MEASURED_AIR, OPAQUE_INTERIOR, SUBSURFACE, classify_voxel_at, class_at_slice,
)

__all__ = ["Extinction", "TransmittanceResult", "transmittance", "NO_DATA"]

_INF = float("inf")

# Distinct from the decoder's OPAQUE_INTERIOR (-2). "There is no column here"
# and "this voxel is inside a building" are different statements, and
# conflating them is a modelling error: a ray leaving the processed footprint
# would be counted as shadowed, wrongly darkening every column near the edge of
# an area. At low sun elevation that border is large - reaching 100 m of height
# at 15 degrees takes 373 m of horizontal travel.
#
# ray_trace.DDAConfig makes the same distinction and defaults
# solid_when_missing=False, so the default here matches the reference walker:
# absent data is TRANSPARENT, and a caller who wants the opposite must say so.
NO_DATA = -4


@dataclass
class Extinction:
    """Extinction coefficients per class, in inverse metres.

    ``per_class`` maps a class code (or a decoder state: MEASURED_AIR,
    OPAQUE_INTERIOR, SUBSURFACE) to ``k``. ``inf`` means opaque. A class absent
    from the mapping falls back to ``default`` - which defaults to ``inf``, so
    an unlisted material blocks rather than silently passing light. Erring
    towards opaque keeps an omission visible as too much shadow rather than
    invisible as too little.
    """

    per_class: dict[int, float] = field(default_factory=dict)
    default: float = _INF

    def k(self, cls: int) -> float:
        """Extinction coefficient of *cls* in inverse metres: ``per_class[cls]`` when listed, otherwise ``default``."""
        return self.per_class.get(cls, self.default)

    @classmethod
    def opaque_only(cls) -> "Extinction":
        """Everything solid is opaque; air and absent data pass.

        Reproduces the boolean walker under its default configuration
        (``solid_when_missing=False``), with one exception: a ray that grazes
        exactly along a voxel corner or edge puts two boundary crossings at the
        same ``t``, and the two walkers may resolve that tie into different
        voxels. Equivalence therefore holds off the knife edge, which is why
        the producers of ray origins offset them by a small epsilon rather than
        starting a ray exactly on a face.
        """
        return cls(per_class={MEASURED_AIR: 0.0, NO_DATA: 0.0}, default=_INF)

    @classmethod
    def provisional_canopy(cls, k_veg: float) -> "Extinction":
        """Vegetation classes share one coefficient; everything else opaque.

        PROVISIONAL by name on purpose: ``k_veg`` must come from Stage 3's fit,
        and any figure produced with this constructor must be reported with the
        coefficient it used.
        """
        per = {MEASURED_AIR: 0.0, NO_DATA: 0.0}
        for c in VEGETATION_CLASSES:
            per[int(c)] = float(k_veg)
        return cls(per_class=per, default=_INF)


@dataclass
class TransmittanceResult:
    """Outcome of one transmittance query."""

    tau: float                       # in [0, 1]
    # Metres travelled per class code / state. A blocking voxel is credited
    # with its FULL crossing segment - the distance from the entry point to
    # the far boundary - before the walk stops, even though no light survives
    # past the entry face. ``distance_m`` counts that segment for the same
    # reason. So on a blocked ray these two are the geometry of the walk, not
    # the depth at which the beam died.
    path_by_class: dict[int, float]
    blocked_by: int | None           # class that drove tau to 0, if any
    distance_m: float                # total metric distance walked
    steps: int

    @property
    def blocked(self) -> bool:
        """True when ``tau <= 0.0``, meaning an opaque (``k = inf``) voxel ended the walk."""
        return self.tau <= 0.0


def transmittance(
    store: ColumnStore,
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
    ext: Extinction,
    *,
    max_distance_m: float = 1000.0,
    max_steps: int = 1_000_000,
    tau_floor: float = 1e-6,
    check_neighbourhood: bool = True,
    ceiling: int | None = None,
) -> TransmittanceResult:
    """Accumulate transmittance along a ray.

    @param store              The ColumnStore to walk; its lazy dense column index is
                              forced here, and absent data (no column) reads as
                              ``NO_DATA``, which is transparent, not building interior.
    @param origin             ``(x, y, z)`` ray origin in world coordinates (CRS metres).
    @param direction          ``(dx, dy, dz)`` ray direction; need not be unit length,
                              normalised internally so that ``t`` and every reported path
                              length are in METRES.
    @param ext                Per-class extinction coefficients in inverse metres. An
                              ``inf`` coefficient blocks and ends the walk at ``tau = 0``.
    @param max_distance_m     Stop after this many metres (default 1000.0).
    @param max_steps          Stop after this many DDA steps (default 1,000,000). A second
                              bound on the loop alongside ``max_distance_m``; whichever is
                              reached first ends the walk.
    @param tau_floor          Stop once transmittance falls below this (default 1e-6). The
                              remaining contribution cannot matter and continuing costs time;
                              optically thick media would otherwise be walked to
                              ``max_distance_m`` for no change in the answer.
    @param check_neighbourhood Passed straight to ``decoder.classify_voxel_at`` (default
                              True). True applies the nadir-beam neighbourhood check, so a
                              gap beside a building reads as opaque interior; False takes
                              the simple nadir assumption, where any gap in a penetrated
                              column is measured air. It therefore changes which classes the
                              walk accumulates, not only how fast it runs.
    @param ceiling            Optional voxel index above which the ray is known to meet only
                              air. It must be valid for THIS ray's direction, and the caller
                              picks it: ``ray_columns.CeilingDDA.column_ceiling`` for a
                              strictly vertical ray, ``CeilingDDA.global_ceiling`` for
                              anything oblique, since an oblique ray leaves the column whose
                              neighbourhood the local ceiling covers. Given a valid ceiling
                              this is purely an optimisation for ``tau`` and ``blocked_by``,
                              which are identical without it; the walk stops at the ceiling,
                              so ``distance_m``, ``steps`` and the air entry of
                              ``path_by_class`` stop accumulating there instead of running on
                              to ``max_distance_m``. Given an invalid one it changes the
                              answer.
    @return A TransmittanceResult whose ``tau`` is in [0, 1]; a zero-length direction
            returns the all-transmitting result (tau 1.0, no path, distance 0).
    """
    ox, oy, oz = origin
    dx, dy, dz = direction
    norm = math.sqrt(dx * dx + dy * dy + dz * dz)
    if norm == 0.0:
        return TransmittanceResult(1.0, {}, None, 0.0, 0)
    dx, dy, dz = dx / norm, dy / norm, dz / norm   # unit -> t is metres

    st = store
    cell_x = cell_y = st.cell_xy
    cell_z = st.cell_z

    ix = int(math.floor((ox - st.x_min) / cell_x))
    iy = int(math.floor((oy - st.y_min) / cell_y))
    iz = int(math.floor((oz - st.z_min) / cell_z))

    step_x = 0 if dx == 0 else (1 if dx > 0 else -1)
    step_y = 0 if dy == 0 else (1 if dy > 0 else -1)
    step_z = 0 if dz == 0 else (1 if dz > 0 else -1)

    t_delta_x = abs(cell_x / dx) if dx != 0 else _INF
    t_delta_y = abs(cell_y / dy) if dy != 0 else _INF
    t_delta_z = abs(cell_z / dz) if dz != 0 else _INF

    def _first_boundary(o, mn, cell, i, d):
        """``t`` (metres) at which the ray leaves voxel *i* along one axis: ``(mn + (i + 1) * cell - o) / d`` for ``d > 0``, ``(mn + i * cell - o) / d`` for ``d < 0``, inf when ``d == 0``."""
        if d > 0:
            return (mn + (i + 1) * cell - o) / d
        if d < 0:
            return (mn + i * cell - o) / d
        return _INF

    t_max_x = _first_boundary(ox, st.x_min, cell_x, ix, dx)
    t_max_y = _first_boundary(oy, st.y_min, cell_y, iy, dy)
    t_max_z = _first_boundary(oz, st.z_min, cell_z, iz, dz)

    tau = 1.0
    optical_depth = 0.0
    path: dict[int, float] = {}
    blocked_by: int | None = None
    t = 0.0
    steps = 0

    # Column-index cache across voxels of the same (ix, iy). The ray only
    # changes column when it steps in x or y, so a near-vertical ray - the
    # shadow / sky-view workload - resolves its column ONCE rather than once per
    # voxel. This removes the per-voxel ``_find`` (searchsorted over up to
    # billions of column keys) that dominated this loop. ``class_at_slice``
    # then reads the class straight off the flat arrays with no Column and
    # no further searchsorted.
    st.ensure_dense_index()
    cache_ix = cache_iy = None
    cache_ci = -1

    while t < max_distance_m and steps < max_steps:
        # One count per voxel entered, taken here so that every exit below
        # reports the same thing. Counting at the exits instead used to leave
        # the max_distance_m break at the only path that credited a voxel's
        # segment to path_by_class without counting the step.
        steps += 1

        # Class of the voxel we are currently inside.
        if ix != cache_ix or iy != cache_iy:
            cache_ci = st._find((ix, iy))
            cache_ix, cache_iy = ix, iy
        if cache_ci < 0:
            cls = NO_DATA          # absent data, NOT building interior
        else:
            c = class_at_slice(st, cache_ci, iz)
            cls = c if c is not None else classify_voxel_at(
                st, cache_ci, ix, iy, iz,
                check_neighbourhood=check_neighbourhood)

        # Distance travelled inside THIS voxel.
        t_next = min(t_max_x, t_max_y, t_max_z)
        if t_next > max_distance_m:
            t_next = max_distance_m
        seg = t_next - t
        if seg < 0.0:
            seg = 0.0

        # A zero-length segment is skipped entirely: no path length is credited
        # and, in particular, an OPAQUE voxel entered exactly on its far
        # boundary does NOT block the ray here. This happens when two boundary
        # crossings coincide, i.e. when the ray passes through a voxel corner
        # or edge, so the voxel it grazes has no thickness along the ray and
        # attenuates nothing. The walk blocks one voxel later instead, at the
        # first solid voxel it genuinely crosses.
        if seg > 0.0:
            k = ext.k(cls)
            path[cls] = path.get(cls, 0.0) + seg
            if k == _INF:
                blocked_by = cls
                tau = 0.0
                t = t_next
                break
            if k != 0.0:
                optical_depth += k * seg
                tau = math.exp(-optical_depth)
                if tau <= tau_floor:
                    t = t_next
                    break

        if t_next >= max_distance_m:
            t = max_distance_m
            break

        # Advance to the next voxel.
        t = t_next
        if t_max_x < t_max_y:
            if t_max_x < t_max_z:
                ix += step_x
                t_max_x += t_delta_x
            else:
                iz += step_z
                t_max_z += t_delta_z
        else:
            if t_max_y < t_max_z:
                iy += step_y
                t_max_y += t_delta_y
            else:
                iz += step_z
                t_max_z += t_delta_z

        # Early-out: above the ceiling only air remains (see ray_columns
        # for the proof). Exactness has two conditions, and the second is on
        # the caller. Air and absent data must carry k = 0, which every
        # provided Extinction constructor guarantees. AND the ceiling passed
        # in must be valid for THIS ray's direction: a vertical ray never
        # leaves its column, so the local column ceiling holds; an oblique ray
        # does leave it, so only the store-wide ceiling holds. Handing an
        # oblique ray a local ceiling stops it at the source column's roofline
        # and returns tau = 1 through geometry it would have hit.
        if ceiling is not None and step_z > 0 and iz > ceiling:
            break

    return TransmittanceResult(
        tau=tau, path_by_class=path, blocked_by=blocked_by,
        distance_m=t, steps=steps,
    )
