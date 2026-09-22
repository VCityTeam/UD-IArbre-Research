"""
@ingroup t2_algos


Ceiling-terminated ray walker (``CeilingDDA``), a subclass of
``ray_trace.ColumnGridDDA``.

An upward ray leaves a column's topmost interval and can only exit through
open sky; nothing above the ceiling can block it. ``CeilingDDA`` computes that
ceiling per column once, then stops an upward walk the moment the ray passes
it, so shadow work over a whole area does not pay for empty air above the
city. Downward and horizontal rays fall through to the parent walker's exact
DDA.

The early-out is an optimisation of the reference walker, not a second
implementation of it: the two agree ray for ray, pinned by
``tests/test_ray_columns_equivalence.py`` (randomised sweeps at three cell
sizes plus targeted ceiling, roof-only and empty-region cases) and re-proved
on real stores by ``code_verification/exp05_ray_equivalence_real.py``.

This is the walker the solar and shadow code uses; ``ray_trace.ColumnGridDDA``
remains the reference it is checked against.
"""
from __future__ import annotations

import math
from dataclasses import replace

from .data_structures import ColumnStore
from .ray_trace import ColumnGridDDA, DDAConfig, RayHit

__all__ = ["CeilingDDA"]

_NEIGHBOURS = ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1))


class CeilingDDA(ColumnGridDDA):
    """``ColumnGridDDA`` plus an exact early-out for rays that escape upward.

    Drop-in replacement: same constructor, same ``trace`` signature, same
    return type. Ceilings are cached per column and computed lazily, so a
    workload that reuses columns (every shadow map does) pays for each column
    with geometry once (an all-empty neighbourhood yields no ceiling, which
    is not cached, so it is recomputed on each call).

    NOT thread-safe, unlike the base class: that lazy caching means ``trace()``
    WRITES ``self._ceiling_cache`` and ``self._global_ceiling``, so a single
    instance shared across threads is mutated concurrently. The cached values
    are deterministic functions of an immutable store, so under CPython the
    practical cost is duplicated work rather than a wrong hit, but the base
    class's "trace() only reads" property does not hold here. Give each thread
    or worker process its own instance.
    """

    def __init__(self, store: ColumnStore):
        """Construct the base walker on *store* and start with an empty per-column ceiling cache and no store-wide ceiling."""
        super().__init__(store)
        self._ceiling_cache: dict[tuple[int, int], int] = {}
        self._global_ceiling: int | None = None

    # ------------------------------------------------------------------
    # Ceilings
    # ------------------------------------------------------------------

    def column_ceiling(self, ix: int, iy: int) -> int | None:
        """Exclusive ceiling of this column and its 4-neighbourhood.

        This is ``max(z_end)`` - one PAST the highest occupied voxel index,
        since intervals are half-open - so ``iz >= ceiling`` means nothing
        is occupied at or above ``iz`` anywhere in the neighbourhood.

        Returns ``None`` when neither the column nor any neighbour holds any
        interval, in which case there is no geometry to be above.
        """
        key = (ix, iy)
        hit = self._ceiling_cache.get(key)
        if hit is not None:
            return hit
        top = None
        cols = self.store.columns
        for dx, dy in _NEIGHBOURS:
            col = cols.get((ix + dx, iy + dy))
            if col is None or len(col.z_end) == 0:
                continue
            t = int(col.z_end.max())
            if top is None or t > top:
                top = t
        if top is not None:
            self._ceiling_cache[key] = top
        return top

    def global_ceiling(self) -> int:
        """Exclusive ceiling over the whole store: ``max(z_end)``, one past
        the highest occupied voxel index.

        Always an int, never None - unlike ``column_ceiling``, an empty store
        answers -1 rather than "no ceiling", which is the value that makes
        every upward ray terminate at once, as it should when there is no
        geometry anywhere.
        """
        if self._global_ceiling is None:
            ze = self.store._ze
            self._global_ceiling = int(ze.max()) if ze.shape[0] else -1
        return self._global_ceiling

    # ------------------------------------------------------------------
    # Traversal
    # ------------------------------------------------------------------

    def trace(
        self,
        origin: tuple[float, float, float],
        direction: tuple[float, float, float],
        config: DDAConfig | None = None,
    ) -> RayHit | None:
        """Trace as the base class does, with the exact early-out for upward rays.

        When ``dz > 0`` and the config neither makes air solid nor makes
        missing columns solid, the rule is: take the local
        column_ceiling() for a vertical ray (``dx == dy == 0``) and the
        global_ceiling() otherwise; return None at once when there is no
        ceiling or the origin voxel ``iz0`` is already at or above it;
        otherwise clamp ``config.max_t`` (in the base class's dominant-axis
        scaling of ``t``) so the walk stops at ``z_min + (ceiling + 1) *
        cell_z``. Every other ray is handed to ``ColumnGridDDA.trace``
        unchanged. Fills the ceiling caches as a side effect.
        """
        if config is None:
            config = DDAConfig()

        dx, dy, dz = direction
        # The shortcut is only sound for an upward ray in a configuration
        # where air passes and absent columns are not solid.
        if (dz > 0.0
                and not config.treat_air_as_solid
                and not config.solid_when_missing):
            st = self.store
            ox, oy, oz = origin
            iz0 = int(math.floor((oz - st.z_min) / st.cell_z))
            if dx == 0.0 and dy == 0.0:
                ix0 = int(math.floor((ox - st.x_min) / st.cell_xy))
                iy0 = int(math.floor((oy - st.y_min) / st.cell_xy))
                ceiling = self.column_ceiling(ix0, iy0)
            else:
                ceiling = self.global_ceiling()

            # Already above everything: the ray can only escape.
            if ceiling is None or iz0 >= ceiling:
                return None

            # Below the ceiling: still walk, but only as far as the ceiling.
            # Past it every voxel is provably MEASURED_AIR (see module
            # docstring), so continuing to the default max_t of 1000.0 just
            # steps through air - 10,000 steps at cell_z=0.1. Clamping is
            # exact, not a heuristic: no hit exists beyond this point.
            #
            # ``t`` is measured against a direction scaled so its largest
            # component is 1, which is how ColumnGridDDA.trace normalises, so
            # the conversion below must use that same scaling.
            scale = max(abs(dx), abs(dy), abs(dz))
            if scale > 0.0:
                dz_n = dz / scale
                z_top = st.z_min + (ceiling + 1) * st.cell_z
                t_ceiling = (z_top - oz) / dz_n
                if t_ceiling < config.max_t:
                    config = replace(config, max_t=max(0.0, t_ceiling))

        return super().trace(origin, direction, config)
