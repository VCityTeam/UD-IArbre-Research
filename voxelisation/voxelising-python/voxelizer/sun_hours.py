"""Direct sun hours per column, computed twice: opaque and class-aware.
@ingroup t2_algos


Objective 4 asks for ray casting that accounts for interactions between voxels
of different territorial classes. Computing the SAME quantity twice - once with
every material opaque, once with vegetation partially transmissive - isolates
exactly what the volumetric material model contributes. The difference between
the two maps is the answer; either map alone is not.

METHOD
------
For each column, and for each sampled sun position above the horizon, cast a
ray from a point ``height_above_ground_m`` above the top of that column's
LOWEST interval towards the sun and accumulate

    sun_hours = sum over samples of ( tau * dt )

where ``tau`` is the transmittance along that ray (1 = unobstructed, 0 =
blocked) and ``dt`` the sampling interval in hours. With every material opaque
``tau`` is 0 or 1 and this reduces to the conventional direct-sun-hours
computation; with finite vegetation extinction it becomes a
transmittance-weighted duration.

WHAT THIS IS NOT
----------------
This is DIRECT beam only. There is no diffuse sky component, no reflection, no
atmospheric attenuation and no cloud. A real irradiance figure needs all four,
and none of them is available from LiDAR geometry alone. The quantity here is
"how long is this column in unobstructed view of the solar disc, weighted by
how much of the beam survives the canopy in the way", which is a geometric
statement about the city, not a radiometric one about energy. Section 4.4 of
the report states this limitation rather than implying irradiance.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from .data_structures import ColumnStore
from .ray_columns import CeilingDDA
from .solar import day_arc, grid_convergence_deg, sun_vector_grid
from .transmittance import Extinction, transmittance

logger = logging.getLogger(__name__)

__all__ = ["SunHoursResult", "compute_sun_hours"]


@dataclass
class SunHoursResult:
    """Per-column direct sun hours under one extinction model."""

    keys: list[tuple[int, int]]
    hours: np.ndarray                    # float32, one per column
    n_positions: int
    step_minutes: int
    date: str
    convergence_deg: float
    extinction_label: str
    meta: dict = field(default_factory=dict)

    def summary(self) -> dict:
        """Statistics of ``hours`` as a dict.

        ``n_columns`` is the count; ``mean_h``, ``median_h``, ``min_h`` and
        ``max_h`` are rounded to 4 decimals; ``frac_over_2h`` is the fraction
        of columns with ``hours >= 2.0`` and ``frac_zero`` the fraction with
        ``hours <= 1e-9``. With no columns every statistic is None.
        """
        h = self.hours
        if h.size == 0:
            # min() and max() raise on an empty array, and mean()/median()
            # warn and return nan. No columns is a legitimate outcome (an
            # empty subset, an area clipped to nothing), so report the count
            # and leave every statistic undefined rather than failing here.
            return {
                "n_columns": 0,
                "mean_h": None, "median_h": None,
                "min_h": None, "max_h": None,
                "frac_over_2h": None, "frac_zero": None,
            }
        return {
            "n_columns": int(h.size),
            "mean_h": round(float(h.mean()), 4),
            "median_h": round(float(np.median(h)), 4),
            "min_h": round(float(h.min()), 4),
            "max_h": round(float(h.max()), 4),
            "frac_over_2h": round(float((h >= 2.0).mean()), 4),
            "frac_zero": round(float((h <= 1e-9).mean()), 4),
        }


def compute_sun_hours(
    store: ColumnStore,
    lat_deg: float,
    lon_deg: float,
    date_utc: datetime,
    ext: Extinction,
    *,
    label: str,
    crs: str = "EPSG:3946",
    step_minutes: int = 15,
    columns: list[tuple[int, int]] | None = None,
    height_above_ground_m: float = 1.5,
    max_distance_m: float = 400.0,
    convergence_deg: float | None = None,
    progress: bool = False,
) -> SunHoursResult:
    """Direct sun hours for every (or a subset of) column in *store*.

    ``height_above_ground_m`` (1.5 m by default) is measured from the top of
    the column's LOWEST interval - ``col.z_end[0]``, not ``store.ground_idx``.
    Rays start above that interval rather than inside it so the column's own
    lowest voxel does not block every ray immediately.

    That lowest interval IS the ground surface only in a penetrated column. In
    a ground-less column (returns but no ground return: roof-only or
    canopy-only, a third to half of real urban columns - see
    ``decoder.classify_voxel_at``) it is the roof or the canopy, so the sample
    point sits 1.5 m above that structure, not 1.5 m above the terrain. Those
    columns therefore report roof- or canopy-level exposure, and are not
    comparable to the eye-level reading the same number means elsewhere.

    ``max_distance_m`` (400 m by default) truncates every ray. Geometry beyond
    that radius cannot block anything, so a column shaded only by something
    further away is recorded as open sky and the figure is biased UPWARD. The
    bias is largest near the horizon, where a ray spends its whole budget
    travelling sideways: at 15 degrees elevation 400 m of path gains only about
    107 m of height. The truncation is the same in every arm of a comparison,
    so differences between runs at one setting stand; the absolute level
    carries the bias.
    """
    if convergence_deg is None:
        try:
            cx = store.x_min + 0.5 * store.cell_xy * _grid_index_sum(store, 0)
            cy = store.y_min + 0.5 * store.cell_xy * _grid_index_sum(store, 1)
            convergence_deg = grid_convergence_deg(cx, cy, crs)
        except Exception as exc:  # noqa: BLE001
            # Not a free pass: 0 convergence rotates every shadow by the
            # grid's meridian convergence (about 1.3 deg at Lyon, ~2.3 m at
            # 100 m - see solar.grid_convergence_deg). Say so instead of
            # silently degrading; pass convergence_deg=0.0 to opt in quietly.
            convergence_deg = 0.0
            logger.warning(
                "grid convergence could not be computed (%s); using 0.0 deg "
                "- azimuths are grid-north relative, shadows rotate by the "
                "local meridian convergence (~1.3 deg at Lyon).", exc)

    arc = day_arc(lat_deg, lon_deg, date_utc, step_minutes=step_minutes)
    vecs = []
    for when, _sp in arc:
        v = sun_vector_grid(lat_deg, lon_deg, when, convergence_deg)
        if v is not None:
            vecs.append(v)
    dt_h = step_minutes / 60.0

    keys = list(columns if columns is not None else store.columns.keys())
    fast = CeilingDDA(store)
    hours = np.zeros(len(keys), dtype=np.float32)

    # Sun rays are oblique, so the ceiling handed to ``transmittance`` must be
    # the STORE-WIDE one. A column's local ceiling bounds only that column and
    # its four neighbours; an oblique ray leaves that neighbourhood and can
    # still meet geometry above the local ceiling further downrange. Passing
    # the local value made a climbing ray stop at the source column's roofline
    # and report full sun straight through a building standing ten columns
    # away. ``ray_columns.CeilingDDA.trace`` draws the same distinction, and
    # its module docstring carries the proof.
    ceil = fast.global_ceiling()

    for i, (ix, iy) in enumerate(keys):
        col = store.columns.get((ix, iy))
        if col is None:
            continue
        # FROZEN ANCHOR. ``col.z_end[0]`` is the top of the column's LOWEST
        # interval in canonical (z_start, class) order. That is the ground
        # surface only where the lowest interval is ground. Intervals of
        # different classes may occupy the same voxels - about 45 percent of
        # adjacent interval pairs in the measured urban merge overlap in z -
        # so where a canopy or roof interval starts at or below the ground
        # interval's start, the lowest interval is that structure and z_end[0]
        # is ITS top. The ray then starts 1.5 m above the canopy rather than
        # 1.5 m beneath it, and the under-canopy shading this measurement
        # exists to show vanishes for exactly those columns. Every recorded
        # sun-hours figure was produced with this anchor, so it stands as
        # written and the limitation is carried in the report.
        ground_top = int(col.z_end[0])          # top of the lowest interval
        z0 = store.z_min + ground_top * store.cell_z + height_above_ground_m
        x = store.x_min + (ix + 0.5) * store.cell_xy
        y = store.y_min + (iy + 0.5) * store.cell_xy
        total = 0.0
        for v in vecs:
            r = transmittance(store, (x, y, z0), v, ext,
                              max_distance_m=max_distance_m, ceiling=ceil)
            if r.tau > 0.0:
                total += r.tau * dt_h
        hours[i] = total
        if progress and i and i % 5000 == 0:
            print(f"    {i:,}/{len(keys):,} columns", flush=True)

    return SunHoursResult(
        keys=keys, hours=hours, n_positions=len(vecs),
        step_minutes=step_minutes, date=date_utc.strftime("%Y-%m-%d"),
        convergence_deg=round(convergence_deg, 5), extinction_label=label,
        meta={"height_above_ground_m": height_above_ground_m,
              "max_distance_m": max_distance_m,
              "max_distance_note": (
                  "rays stop at max_distance_m; obstructions beyond it are "
                  "not seen, which biases hours upward, most at low sun "
                  "elevation"),
              "cell_xy": store.cell_xy, "cell_z": store.cell_z},
    )


def _grid_index_sum(store: ColumnStore, axis: int) -> float:
    """Sum of the smallest and largest occupied column index on *axis*.

    Not a span: half of this sum is the mid index, which is what the caller
    wants when it places the area's centre for the grid-convergence lookup.
    """
    ks = store._keys
    if ks.shape[0] == 0:
        return 0.0
    from .data_structures import _unpack_keys
    ix, iy = _unpack_keys(ks)
    a = ix if axis == 0 else iy
    return float(int(a.max()) + int(a.min())) / 1.0
