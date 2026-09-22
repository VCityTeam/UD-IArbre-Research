"""
Full 3-D Amanatides-Woo DDA ray-walker on the column-compressed voxel grid.
@ingroup t2_algos


The traversal is three-dimensional: it maintains the parametric distance to the
next boundary on each of the x, y and z axes and advances whichever is nearest,
stepping one voxel at a time in z as well as in xy. Despite the column-organised
DATA STRUCTURE sometimes attracting a "2.5D" label, nothing in the traversal is
2.5-D: there is no per-column binary search over intervals (that remains a
possible future optimisation, not the thing this code does). The column store
is a full 3-D model (multiple intervals per column ARE the overhangs), and this
walker treats it as one.

At each voxel it checks whether the cell is occupied (-> hit), solid emptiness
via the decoder (-> hit), or measured air (-> continue).

For the shadow-casting workload, ``ray_columns.CeilingDDA`` subclasses this with
an EXACT early-out for upward escape rays; this class stays the reference
implementation that the differential test compares against.

Usage
-----
    dda = ColumnGridDDA(store)
    hit = dda.trace((x0, y0, z0), (dx, dy, dz))
    if hit is not None:
        print(hit.ix, hit.iy, hit.iz, hit.cls, hit.t)
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from .data_structures import ColumnStore
from .decoder import (
    classify_voxel_at, MEASURED_AIR, OPAQUE_INTERIOR, SUBSURFACE,
    _class_at_voxel, class_at_slice,
)


@dataclass
class RayHit:
    """
    Result of a ray-trace: the first voxel intersection.
    """
    ix: int        # column index (x)
    iy: int        # column index (y)
    iz: int        # voxel index (z)
    cls: int       # class code, or a decoder sentinel: SUBSURFACE /
                   # OPAQUE_INTERIOR (and MEASURED_AIR when
                   # treat_air_as_solid turns air into a hit)
    t: float       # ray parameter at intersection
    x: float       # world x at intersection
    y: float       # world y at intersection
    z: float       # world z at intersection


@dataclass
class DDAConfig:
    """
    Tuning parameters for the DDA ray-walker.
    """
    max_t: float = 1000.0
    max_steps: int = 1_000_000
    hit_start: bool = True
    solid_when_missing: bool = False
    treat_interior_as_solid: bool = True
    treat_subsurface_as_solid: bool = True
    treat_air_as_solid: bool = False


class ColumnGridDDA:
    """
    DDA ray-walker over a ColumnStore.

    Thread-safe after construction: ``__init__`` forces the store's lazy
    ground-index cache (below), and ``trace()`` then reads the store and its
    own locals, so one instance can serve many threads.

    One write does happen inside ``trace()``: the first call builds the
    store's dense column index (``ensure_dense_index``). That is idempotent
    and answer-preserving - the index returns exactly what the binary search
    returns, hits and misses alike, and a declined build is remembered - so
    concurrent first calls can duplicate the work but cannot change any
    answer. A caller that wants the write to happen once, before any thread
    starts, calls ``store.ensure_dense_index()`` itself.

    That guarantee is specific to THIS class. The ``ray_columns.CeilingDDA``
    subclass fills lazy ceiling caches from inside ``trace()`` and therefore
    does not inherit it - see its docstring.
    """

    def __init__(self, store: ColumnStore):
        """Bind *store* and touch ``store.ground_idx`` once so its lazy cache is built before any thread calls ``trace()``."""
        self.store = store
        # Column lookup runs directly against the store's flat arrays and is
        # split across the two: ``trace`` calls ``store._find`` once per
        # (ix, iy) - caching ``ci`` so a run of voxels in one column resolves
        # it once - and hands that ``ci`` to ``_check_voxel``, which never
        # searches. So this walker needs no per-instance columns view and
        # materialises no Column per voxel.
        # This removes real allocation churn on the OCCUPIED path. Note it does
        # NOT remove the churn on the GAP path, which still goes through
        # ``decoder.classify_voxel_at`` -> ``classify_gap`` ->
        # ``store.columns.get(...)`` on the four neighbours. Correctness
        # is asserted ray for ray by tests/test_ray_columns_equivalence.py.
        #
        # Force the lazy ground-index compute NOW, while still
        # single-threaded: classify_voxel consults store.ground_idx for
        # every gap voxel, and its first access WRITES the store's cache.
        # Touching it here makes the class docstring's thread-safety
        # claim true - after construction, trace() only reads.
        _ = store.ground_idx

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def trace(
        self,
        origin: tuple[float, float, float],
        direction: tuple[float, float, float],
        config: DDAConfig | None = None,
    ) -> RayHit | None:
        """
        Trace a ray from *origin* in *direction* through the grid.

        @param origin     ``(x, y, z)`` ray origin in world coordinates (CRS metres).
        @param direction  ``(dx, dy, dz)`` ray direction vector; need not be unit
                          length, but must be non-zero. Rescaled internally so the
                          largest component is 1, which makes ``t`` read as metres
                          travelled along the dominant axis.
        @param config     Tuning parameters; defaults to ``DDAConfig()``, whose
                          ``hit_start`` also tests the origin voxel and whose
                          ``treat_air_as_solid``, ``treat_interior_as_solid`` and
                          ``treat_subsurface_as_solid`` decide which decoder
                          classes count as a hit.
        @return The first solid voxel intersected, as a RayHit, or None if the ray
                escapes the grid without intersecting anything solid (also None
                when *direction* is the zero vector).
        """
        if config is None:
            config = DDAConfig()

        ox, oy, oz = origin
        dx, dy, dz = direction

        # Scale so the largest |component| is 1. One DDA step along that
        # dominant axis then advances t by exactly one cell size in metres
        # (t_delta = cell / |d| = cell there), so t reads as metres travelled
        # along the dominant axis - which is the unit DDAConfig.max_t is in.
        # Unit-LENGTH normalisation would make t metres along the ray
        # instead; both traverse the same cells, only the parameterisation
        # of t differs.
        scale = max(abs(dx), abs(dy), abs(dz))
        if scale == 0.0:
            return None
        dx, dy, dz = dx / scale, dy / scale, dz / scale

        st = self.store
        cell_x, cell_y, cell_z = st.cell_xy, st.cell_xy, st.cell_z

        # Current grid voxel (the ray originates inside this voxel)
        ix = int(math.floor((ox - st.x_min) / cell_x))
        iy = int(math.floor((oy - st.y_min) / cell_y))
        iz = int(math.floor((oz - st.z_min) / cell_z))

        # Step direction and t distance to NEXT boundary in each axis.
        # For a zero component the step is 0 (no movement in that axis).
        step_x = 0 if dx == 0 else (1 if dx > 0 else -1)
        step_y = 0 if dy == 0 else (1 if dy > 0 else -1)
        step_z = 0 if dz == 0 else (1 if dz > 0 else -1)

        t_delta_x = abs(cell_x / dx) if dx != 0 else math.inf
        t_delta_y = abs(cell_y / dy) if dy != 0 else math.inf
        t_delta_z = abs(cell_z / dz) if dz != 0 else math.inf

        # t at which the ray reaches the NEXT grid boundary.
        # For a zero component (dx/dy/dz == 0) t_max is inf - the ray
        # never moves in that axis.
        if dx > 0:
            next_x = st.x_min + (ix + 1) * cell_x
            t_max_x = (next_x - ox) / dx
        elif dx < 0:
            next_x = st.x_min + ix * cell_x
            t_max_x = (next_x - ox) / dx
        else:
            t_max_x = math.inf

        if dy > 0:
            next_y = st.y_min + (iy + 1) * cell_y
            t_max_y = (next_y - oy) / dy
        elif dy < 0:
            next_y = st.y_min + iy * cell_y
            t_max_y = (next_y - oy) / dy
        else:
            t_max_y = math.inf

        if dz > 0:
            next_z = st.z_min + (iz + 1) * cell_z
            t_max_z = (next_z - oz) / dz
        elif dz < 0:
            next_z = st.z_min + iz * cell_z
            t_max_z = (next_z - oz) / dz
        else:
            t_max_z = math.inf

        t = 0.0
        steps = 0

        # Cache the column index across voxels of the same (ix, iy). A ray only
        # changes column when it steps in x or y, so a near-vertical ray - the
        # entire shadow / sky-view workload - looks its column up ONCE instead
        # of once per voxel. This removes thousands of redundant ``_find``
        # (searchsorted) calls per ray, a real speed-up and a large reduction
        # of the per-voxel searchsorted volume. State is a local of this
        # call, so trace() stays thread-safe. Sentinel avoids a valid (0,0).
        st.ensure_dense_index()
        cache_ix = cache_iy = None
        cache_ci = -1

        def _col_index(cx: int, cy: int) -> int:
            """Store index of column (cx, cy), or -1 when absent; ``st._find`` runs only when the column differs from the cached one."""
            nonlocal cache_ix, cache_iy, cache_ci
            if cx != cache_ix or cy != cache_iy:
                cache_ci = st._find((cx, cy))
                cache_ix, cache_iy = cx, cy
            return cache_ci

        if config.hit_start:
            hit = self._check_voxel(_col_index(ix, iy), ix, iy, iz,
                                    t, ox, oy, oz, config)
            if hit is not None:
                return hit

        while t < config.max_t and steps < config.max_steps:
            steps += 1

            # Advance to the next grid boundary in the axis with the
            # smallest t.
            if t_max_x < t_max_y:
                if t_max_x < t_max_z:
                    t = t_max_x
                    ix += step_x
                    t_max_x += t_delta_x
                else:
                    t = t_max_z
                    iz += step_z
                    t_max_z += t_delta_z
            else:
                if t_max_y < t_max_z:
                    t = t_max_y
                    iy += step_y
                    t_max_y += t_delta_y
                else:
                    t = t_max_z
                    iz += step_z
                    t_max_z += t_delta_z

            # World position at this grid point
            x = ox + t * dx
            y = oy + t * dy
            z = oz + t * dz

            hit = self._check_voxel(_col_index(ix, iy), ix, iy, iz,
                                    t, x, y, z, config)
            if hit is not None:
                return hit

        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_voxel(
        self, ci: int, ix: int, iy: int, iz: int, t: float,
        x: float, y: float, z: float,
        config: DDAConfig,
    ) -> RayHit | None:
        """
        Check the voxel at (ix, iy, iz).  ``ci`` is the column's index in the
        store's key array (or -1 if the column is absent), supplied by the
        caller so it is not recomputed per voxel. Returns a RayHit if the
        voxel is solid according to *config*; otherwise None.

        The occupied-voxel scan works on the store's flat array slices
        directly (no Column object is materialised) and is result-identical
        to ``decoder._class_at_voxel`` - same latest-starting-cover
        tie-break, deliberately different mechanics (see
        ``decoder.class_at_slice``); the differential test
        (tests/test_ray_columns_equivalence.py) asserts identical results.
        """
        st = self.store

        # No column data
        if ci < 0:
            if config.solid_when_missing:
                return RayHit(ix, iy, iz, OPAQUE_INTERIOR, t, x, y, z)
            return None

        cls = self._class_at_voxel_fast(ci, iz)
        if cls is not None:
            return RayHit(ix, iy, iz, cls, t, x, y, z)

        # Gap voxel - use the decoder, passing the column index we already have
        # so it does not recompute it (see classify_voxel_at).
        dec = classify_voxel_at(st, ci, ix, iy, iz)

        if dec == MEASURED_AIR:
            if config.treat_air_as_solid:
                return RayHit(ix, iy, iz, dec, t, x, y, z)
            return None  # pass through

        if dec == OPAQUE_INTERIOR and config.treat_interior_as_solid:
            return RayHit(ix, iy, iz, dec, t, x, y, z)

        if dec == SUBSURFACE and config.treat_subsurface_as_solid:
            return RayHit(ix, iy, iz, dec, t, x, y, z)

        return None

    def _class_at_voxel_fast(self, ci: int, iz: int) -> int | None:
        """Class occupying voxel ``iz`` in column index ``ci``, or None (gap).

        Thin wrapper over decoder.class_at_slice(), the single shared
        allocation- and searchsorted-free implementation, so this walker and
        the transmittance walker cannot drift.
        """
        return class_at_slice(self.store, ci, iz)

    @staticmethod
    def _class_at_voxel_ray(col, iz: int) -> int | None:
        """
        Return the class occupying voxel *iz* in *col*, or None if
        the voxel is a gap.

        Delegates to decoder._class_at_voxel() - the single shared
        implementation. Retained for callers that hold a Column object;
        ``_check_voxel`` uses ``_class_at_voxel_fast`` on the flat arrays
        instead, to avoid materialising a Column per voxel.
        """
        return _class_at_voxel(col, iz)
