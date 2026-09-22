"""Solar position, and the sun direction expressed in the projected grid axes.
@ingroup t0_socle


ALGORITHM AND WHY THIS ONE
--------------------------
This implements the NOAA Solar Calculator formulation, which is Meeus's
low-precision solar position algorithm [42] as published by NOAA GML [41]. It
is accurate to roughly 0.01 degrees for dates within a few centuries of J2000,
degrading to about 0.1 degrees at the extremes.

Three alternatives were considered and rejected, in each case for a stated
reason rather than by default:

* **NREL Solar Position Algorithm** (Reda and Andreas [43]) is the reference
  implementation for solar engineering, accurate to +/-0.0003 degrees over
  6000 years. It is roughly ten times the code and needs a large table of
  periodic terms. That accuracy is meaningless here: a 0.0003 degree error
  moves a shadow by 0.5 mm at 100 m, whereas the voxel grid quantises at
  100 to 1000 mm and the grid-convergence correction below is itself only
  applied to about six significant figures. Precision far below the
  discretisation of the model cannot change any answer this project reads
  off the grid.

* **PSA algorithm** (Blanco-Muriel et al. [44]) is compact and accurate to
  about 0.01 degrees, comparable to NOAA, but is fitted for 1999-2015 and
  degrades outside that window. The LiDAR campaign is 2023 and later campaigns
  may follow, so an algorithm with a fitted validity window is the wrong choice
  for something meant to be re-run.

* **Michalsky's approximation** [45] is simpler still but claims only
  0.01 degrees for 1950-2050, a bounded validity window like the PSA's.

NOAA/Meeus sits at the point where accuracy stops mattering for this
application and complexity is still low enough to implement, read and verify
without a dependency - which matters because the project's design constraints
(Section 2.1) rule out adding libraries casually. No solar library is installed
in this environment (`pvlib`, `astral`, `ephem`, `skyfield` are all absent),
so the alternative to implementing it was adding a dependency.

REFRACTION
----------
Atmospheric refraction lifts the apparent sun by about 0.5 degrees at the
horizon and by a negligible amount overhead. It is applied here using the
standard NOAA piecewise approximation. For shadow work it matters only near
sunrise and sunset, where shadows are longest and least reliable anyway; the
`apply_refraction` flag exists so the effect can be isolated rather than
assumed.

THE GRID CONVERGENCE TRAP
-------------------------
A solar azimuth is measured from TRUE north. The voxel store's axes are
PROJECTED grid axes (RGF93/CC46, EPSG:3946), and grid north differs from true
north by the grid convergence - about 1.3 degrees at Lyon. Feeding a true
azimuth straight into a grid-aligned ray rotates every shadow by that angle,
which at 100 m displaces a shadow tip by 2.3 m.

The correction is therefore explicit, and split across two functions.
`grid_convergence_deg` measures it, reusing the finite-difference technique of
the 3-D Tiles exporter (which steps one metre along each projected axis):
step along projected grid north - 100 m by default - see where that lands
geodetically, and derive the true bearing of grid north. Determining the
convergence numerically rather than from a closed form avoids sign and
hemisphere convention errors, which are the usual way this goes wrong.
`sun_vector_grid` then APPLIES whatever convergence it is handed; its
`convergence_deg` argument defaults to 0.0, i.e. no correction at all, so a
caller that skips `grid_convergence_deg` silently gets grid-north azimuths.
`sun_hours.compute_sun_hours` computes it when its own argument is None.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

__all__ = ["SunPosition", "sun_position", "grid_convergence_deg",
           "sun_vector_grid", "day_arc"]


@dataclass(frozen=True)
class SunPosition:
    """Apparent solar position at an instant."""

    elevation_deg: float      # above the horizon; negative = below
    azimuth_deg: float        # clockwise from TRUE north
    declination_deg: float
    equation_of_time_min: float

    @property
    def is_up(self) -> bool:
        """True when ``elevation_deg > 0.0``, so the apparent sun is strictly above the horizon."""
        return self.elevation_deg > 0.0


def _julian_day(when_utc: datetime) -> float:
    """Julian Day for a UTC instant (Meeus ch. 7)."""
    if when_utc.tzinfo is None:
        when_utc = when_utc.replace(tzinfo=timezone.utc)
    when_utc = when_utc.astimezone(timezone.utc)
    y, m = when_utc.year, when_utc.month
    d = (when_utc.day
         + (when_utc.hour + (when_utc.minute + when_utc.second / 60.0) / 60.0)
         / 24.0)
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return (math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1))
            + d + b - 1524.5)


def sun_position(lat_deg: float, lon_deg: float, when_utc: datetime,
                 *, apply_refraction: bool = True) -> SunPosition:
    """Apparent solar elevation and azimuth (azimuth clockwise from true north)."""
    jd = _julian_day(when_utc)
    t = (jd - 2451545.0) / 36525.0                      # Julian centuries

    # Geometric mean longitude and anomaly of the sun.
    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    mr = math.radians(m)

    # Equation of centre -> true longitude -> apparent longitude.
    c = ((1.914602 - t * (0.004817 + 0.000014 * t)) * math.sin(mr)
         + (0.019993 - 0.000101 * t) * math.sin(2 * mr)
         + 0.000289 * math.sin(3 * mr))
    true_long = l0 + c
    omega = 125.04 - 1934.136 * t
    app_long = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))

    # Obliquity of the ecliptic, with the nutation correction.
    seconds = 21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))
    e0 = 23.0 + (26.0 + seconds / 60.0) / 60.0
    e = e0 + 0.00256 * math.cos(math.radians(omega))

    decl = math.degrees(math.asin(
        math.sin(math.radians(e)) * math.sin(math.radians(app_long))))

    # Equation of time, in minutes.
    y = math.tan(math.radians(e / 2.0)) ** 2
    l0r = math.radians(l0)
    ecc = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    eot = 4.0 * math.degrees(
        y * math.sin(2 * l0r)
        - 2.0 * ecc * math.sin(mr)
        + 4.0 * ecc * y * math.sin(mr) * math.cos(2 * l0r)
        - 0.5 * y * y * math.sin(4 * l0r)
        - 1.25 * ecc * ecc * math.sin(2 * mr))

    if when_utc.tzinfo is None:
        when_utc = when_utc.replace(tzinfo=timezone.utc)
    utc = when_utc.astimezone(timezone.utc)
    minutes = utc.hour * 60.0 + utc.minute + utc.second / 60.0
    true_solar_time = (minutes + eot + 4.0 * lon_deg) % 1440.0
    hour_angle = true_solar_time / 4.0 - 180.0
    if hour_angle < -180.0:
        hour_angle += 360.0

    latr, declr, har = (math.radians(lat_deg), math.radians(decl),
                        math.radians(hour_angle))
    cos_zen = (math.sin(latr) * math.sin(declr)
               + math.cos(latr) * math.cos(declr) * math.cos(har))
    cos_zen = max(-1.0, min(1.0, cos_zen))
    zenith = math.degrees(math.acos(cos_zen))
    elev = 90.0 - zenith

    if apply_refraction:
        elev += _refraction_deg(elev)

    # Azimuth, clockwise from true north.
    denom = math.cos(latr) * math.sin(math.radians(zenith))
    if abs(denom) < 1e-12:
        az = 180.0 if lat_deg > 0 else 0.0
    else:
        ratio = ((math.sin(latr) * math.cos(math.radians(zenith)) - math.sin(declr))
                 / denom)
        ratio = max(-1.0, min(1.0, ratio))
        az = math.degrees(math.acos(ratio))
        # NOAA's two branches. Getting these the wrong way round mirrors the
        # sun about the north-south axis: sunrise then reports a north-WEST
        # bearing instead of north-east, which is what the east/west test
        # caught during development. Afternoon (hour angle positive) takes the
        # +180 branch; morning takes 540 - az.
        if hour_angle > 0.0:
            az = (az + 180.0) % 360.0
        else:
            az = (540.0 - az) % 360.0
    return SunPosition(elev, az, decl, eot)


def _refraction_deg(elev_deg: float) -> float:
    """NOAA piecewise atmospheric refraction correction, in degrees."""
    if elev_deg > 85.0:
        return 0.0
    te = math.tan(math.radians(elev_deg))
    if elev_deg > 5.0:
        r = 58.1 / te - 0.07 / te ** 3 + 0.000086 / te ** 5
    elif elev_deg > -0.575:
        r = (1735.0 + elev_deg * (-518.2 + elev_deg *
             (103.4 + elev_deg * (-12.79 + elev_deg * 0.711))))
    else:
        r = -20.772 / te
    return r / 3600.0


def grid_convergence_deg(easting: float, northing: float, crs: str,
                         *, step: float = 100.0) -> float:
    """Angle from true north to grid north at a point, degrees, east positive.

    Determined numerically: step north along the PROJECTED axis and measure the
    true bearing of that step. A closed form exists for each projection, but
    getting its sign and hemisphere conventions right is exactly the kind of
    detail that fails silently, and the numerical answer is checkable against a
    map.
    """
    from pyproj import Geod, Transformer

    to_geo = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    lon0, lat0 = to_geo.transform(easting, northing)
    lon1, lat1 = to_geo.transform(easting, northing + step)
    az, _, _ = Geod(ellps="WGS84").inv(lon0, lat0, lon1, lat1)
    # az is the TRUE bearing of a step along GRID north.
    return (az + 180.0) % 360.0 - 180.0


def sun_vector_grid(lat_deg: float, lon_deg: float, when_utc: datetime,
                    convergence_deg: float = 0.0,
                    *, apply_refraction: bool = True
                    ) -> tuple[float, float, float] | None:
    """Unit vector pointing TOWARDS the sun, in projected grid axes.

    Returns ``None`` when the sun is below the horizon, so callers cannot
    accidentally trace towards a sun that has set.

    ``convergence_deg`` comes from grid_convergence_deg(). Passing 0
    silently assumes grid north is true north, which at Lyon rotates every
    shadow by 1.3 degrees.
    """
    sp = sun_position(lat_deg, lon_deg, when_utc,
                      apply_refraction=apply_refraction)
    if not sp.is_up:
        return None
    grid_az = math.radians(sp.azimuth_deg - convergence_deg)
    elev = math.radians(sp.elevation_deg)
    horiz = math.cos(elev)
    # Azimuth is clockwise from north, so +x is east = sin(az), +y is north.
    return (horiz * math.sin(grid_az), horiz * math.cos(grid_az), math.sin(elev))


def day_arc(lat_deg: float, lon_deg: float, date_utc: datetime,
            step_minutes: int = 15, *, apply_refraction: bool = True
            ) -> list[tuple[datetime, SunPosition]]:
    """Every sampled instant of one UTC day at which the sun is above the horizon."""
    # An aware datetime in another zone names its UTC day only after
    # conversion: replacing tzinfo alone would reread its local wall clock as
    # UTC and pick the wrong day. A naive datetime is read as UTC, as before.
    if date_utc.tzinfo is not None and date_utc.utcoffset() is not None:
        date_utc = date_utc.astimezone(timezone.utc)
    base = date_utc.replace(hour=0, minute=0, second=0, microsecond=0,
                            tzinfo=timezone.utc)
    out = []
    for i in range(0, 24 * 60, step_minutes):
        when = base + timedelta(minutes=i)
        sp = sun_position(lat_deg, lon_deg, when,
                          apply_refraction=apply_refraction)
        if sp.is_up:
            out.append((when, sp))
    return out
