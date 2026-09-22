"""
@ingroup t0_socle


Pre-flight cost estimation and RAM guarding for area runs.

    estimate_area_run()   header/inventory-only cost model: tiles, km^2,
                          predicted store size, raster size, peak RAM
                          (shown in the GUI pre-flight dialog before every
                          area run).
    estimate_store_bytes() the shape model on its own, for callers that
                          already know their covered area.
    suggest_cell_xy()     coarsest-to-finest ladder search for a grid that
                          fits a RAM budget.
    current_rss_mb()      this process's resident set size (psutil, with a
                          stdlib fallback), None if unavailable.
    total_ram_mb()        physical RAM, for sane dialog defaults.
    RamBudgetExceeded     raised by the area loops when RSS crosses the
                          budget; carries the partial store so callers can
                          still write partial outputs instead of losing all
                          completed work.

Store cost uses the struct-of-arrays layout (16 B/column + 13 B/interval).
Given true column and interval counts that layout model is exact to within a
few tens of bytes, so the whole accuracy of an estimate sits in two shape
numbers: how many covered ground cells hold a point, and how many intervals
each of those columns carries.

Both are calibrated from measurement rather than assumed, over ten 2 km x 2 km
areas of Grand Lyon built at four grids (1.0 m, 0.5 m, 0.25 m, and 0.25 xy
with 0.1 z):

    occupancy       0.951 / 0.947 / 0.937 / 0.937 on the four grids. Airborne
                    LiDAR over dense urban ground leaves almost no empty
                    column. Per area it ranges 0.690 to 1.000.
    iv_per_column   2.116 / 1.776 / 1.480 / 1.481. Falling with the horizontal
                    cell and unaffected by the vertical one: finer horizontal
                    cells put fewer points in a column, so the returns resolve
                    into fewer class runs.

Both are fitted as power laws in cell_xy; see the CAL_* constants below. The
planning figures err towards over-estimating on purpose, because the module
exists to stop a run before it exhausts RAM, so under-prediction is the
failure that matters.

Where a header point count is available (local mode reads every LAZ header
already; a streamed inventory JSON carries no count) two further facts
constrain the estimate: an interval holds at least one point, so intervals
never exceed points, and the measured intervals-per-point gives a second
estimate that sees point density where the area-only model is blind to it.
The planning figure takes the larger of the two and then the hard point
ceiling.

Known limitation: ``est_peak_mb`` models the whole merged store resident in
one process, which is what ``--shard --merge-shards`` exists to avoid, so on
sharded runs at fine grids it over-estimates a footprint the run never
materialises. It remains the right figure for the unsharded path and a safe
ceiling for the sharded one.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

logger = logging.getLogger(__name__)

# Struct-of-arrays store cost (see data_structures.py module docstring).
COLUMN_BYTES = 16            # packed key (8) + offset (8)
INTERVAL_BYTES = 13          # z_start(4) + z_end(4) + cls(1) + count(4)
LEGACY_COLUMN_BYTES = 874    # measured cost of the old dict-of-Column layout

# Documented fallbacks: the constants used before the calibration below.
# They are what ``calibrated=False`` reproduces exactly, and what an
# explicit occupancy=/iv_per_column= argument replaces the calibration
# with. Both are now known to be wrong on Grand Lyon: 0.6 understates the
# column count by a factor of 1.6, and 2.2 is only right near a 2 m grid.
DEFAULT_OCCUPANCY = 0.6      # fraction of covered ground cells that get >=1 point
DEFAULT_IV_PER_COLUMN = 2.2  # urban average intervals per occupied column
WORKING_OVERHEAD_MB = 700.0  # interpreter + laspy chunk + matplotlib + slack
SAFETY_FACTOR = 1.3          # 30% overhead: allocator fragmentation,
                             # transient temporaries, estimate error margin

# ---------------------------------------------------------------------------
# Calibration - the 2026-08-08 campaign, ten 2 km areas x four grids.
#
# Fitted by least squares on log(y) against log(cell_xy) over the three
# isotropic grids (1.0 / 0.5 / 0.25 m), each point being the ten-area
# aggregate. The three knots are collinear in log-log to better than half a
# percent, so one power law does the work of a piecewise curve:
#
#   quantity        1.0 m    0.5 m   0.25 m   fit residual
#   occupancy       0.9511   0.9469   0.9365   +0.11 / -0.21 / +0.11 %
#   iv_per_column   2.1157   1.7761   1.4803   +0.12 / -0.24 / +0.12 %
#   iv_per_point    0.02270  0.07587  0.25016  +0.23 / -0.45 / +0.23 %
#
# The fourth grid (0.25 xy, 0.1 z) is the control and was NOT fitted: the
# fit lands within 0.23 % of it too, which is the measured statement that
# these quantities depend on cell_xy alone and carry no cell_z term.
#
# iv_per_point is corpus-density-dependent by construction (the campaign
# measures 88.7 points/m2) and varies 1.8x more across areas than
# iv_per_column does, so it is used as a guard, not as the primary estimate.
#
# Provenance: the constants are least-squares fits to the per-area gate
# files of the 2026-08-08 campaign, one gate file per area and grid, and the
# fitted model was checked against the resident set size the same areas
# actually reached. That campaign is archived outside the delivery, so
# neither its gate files nor its results ship with this tree.
# ---------------------------------------------------------------------------
CAL_OCCUPANCY_A = 0.952170        # occupancy(cell_xy) = A * cell_xy ** k,
CAL_OCCUPANCY_K = 0.011175        # capped at OCCUPANCY_CEILING
CAL_IV_PER_COLUMN_A = 2.118291    # iv_per_column(cell_xy) = A * cell_xy ** k
CAL_IV_PER_COLUMN_K = 0.257663
CAL_IV_PER_POINT_A = 0.02274777   # intervals per point, same form
CAL_IV_PER_POINT_K = -1.731162

# Safety margin, derived from the measured per-area spread - the occupancy
# and iv/col columns of the per-grid tables above:
#   occupancy - the campaign measures 0.690 to 1.000 across ten areas and
#     1.0 is also the mathematical ceiling, so planning at 1.0 makes the
#     column count an upper bound outright. It costs 5.0 % at 1.0 m over the fitted central
#     value and 6.8 % at 0.25 m.
#   iv_per_column - the worst of the ten areas sits 1.184x above the fitted
#     value (A06, 2.508 against 2.118 at 1.0 m; the tail shrinks to 1.144 at
#     0.25 m). 1.20 covers the observed maximum with a little headroom,
#     because ten areas are a sample and not the population.
#   ungrouped stores - the calibration is measured on grouped stores, which
#     is the default. --no-group-intervals leaves the un-coalesced runs in
#     place: the measured excess is 1.185x more intervals (6,630,970
#     against 5,593,880) at 1.0 m and 1.180x at 0.5 m. 1.19 covers both
#     and applies only when grouping is off.
OCCUPANCY_CEILING = 1.0
IV_AREA_TAIL = 1.20
UNGROUPED_IV_FACTOR = 1.19

# Raster budget shared with visualization.DEFAULT_MAX_PIXELS (kept numeric
# here so this module stays import-light instead of pulling the rendering
# stack in; visualization imports nothing from us, so the two constants are
# kept equal by hand).
# Cost is 19 B/px (five scalar rasters 16 B + RGB image 3 B):
#   32 MP ~ 0.6 GB | 64 MP ~ 1.2 GB | 128 MP ~ 2.4 GB | 256 MP ~ 4.9 GB.
# 64-128 MP is the suggested budget; 256 MP ships as the default for
# extra map detail. Already scalable per run at the API level
# (render_tile_map(max_pixels=...) / run_area_sharded(map_max_pixels=...)).
DEFAULT_MAX_PIXELS = 256_000_000
RASTER_BYTES_PER_PIXEL = 19  # five scalar rasters (16 B) + RGB image (3 B)


class RamBudgetExceeded(RuntimeError):
    """
    Raised by the area loops when process RSS crosses the user's budget.

    @param partial_store The ColumnStore assembled so far (may be None/empty).
    @param tiles_done Progress at the moment of the abort, in tiles.
    @param tiles_total Tiles the run had selected in total.
    @param rss_mb The resident set size in MB that triggered the abort.
    @param budget_mb The budget in MB that was crossed.
    """

    def __init__(self, msg: str, *, partial_store=None,
                 tiles_done: int = 0, tiles_total: int = 0,
                 rss_mb: float = 0.0, budget_mb: float = 0.0):
        """Build the error with *msg* and attach the partial store, progress counters and the RSS/budget figures as attributes."""
        super().__init__(msg)
        self.partial_store = partial_store
        self.tiles_done = tiles_done
        self.tiles_total = tiles_total
        self.rss_mb = rss_mb
        self.budget_mb = budget_mb


# ---------------------------------------------------------------------------
# RSS / RAM helpers (psutil preferred, stdlib fallback, graceful None)
# ---------------------------------------------------------------------------
def current_rss_mb() -> float | None:
    """Resident set size of THIS process in MB, or None if unmeasurable."""
    try:
        import psutil
        return psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:  # noqa: BLE001
        pass
    try:  # POSIX fallback; ru_maxrss is peak (KB on Linux) - still useful.
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except Exception:  # noqa: BLE001
        return None


def total_ram_mb() -> float | None:
    """Physical RAM in MB, or None if psutil is unavailable."""
    try:
        import psutil
        return psutil.virtual_memory().total / (1024 * 1024)
    except Exception:  # noqa: BLE001
        return None


def check_rss_budget(budget_mb: float | None) -> tuple[bool, float]:
    """Return (exceeded, rss_mb_or_0). Never raises."""
    if not budget_mb:
        return False, 0.0
    rss = current_rss_mb()
    if rss is None:
        return False, 0.0
    return rss > float(budget_mb), rss


# ---------------------------------------------------------------------------
# Tile-extent gathering (inventory JSON for stream mode; headers for local)
# ---------------------------------------------------------------------------
def _rects_from_inventory(json_file: Path, bbox, tile_pitch: int,
                          limit: int) -> list[tuple[float, float, float, float]]:
    """``(x_min, y_min, x_max, y_max)`` of each inventory tile of *json_file* in the origin window of *bbox* at *tile_pitch*, in ``select_tiles`` order, truncated to the first *limit* when *limit* is positive. The same selection stream mode downloads."""
    from ._download_common import load_values, select_tiles
    values = load_values(Path(json_file))
    from ._download_common import origin_window
    selected = select_tiles(values, *origin_window(bbox, tile_pitch))
    if limit > 0:
        selected = selected[:limit]
    return [(float(t["x_min"]), float(t["y_min"]),
             float(t["x_max"]), float(t["y_max"])) for t in selected]


def _rects_and_counts_from_laz_dir(
        laz_dir, bbox) -> list[tuple[float, float, float, float, int]]:
    """
    Tile extents AND header point counts.

    ``read_laz_header`` returns the point count anyway, so the count is free
    here: local mode already pays for one header read per candidate tile.
    """
    from .area import select_area_tiles
    from .io_laz import read_laz_header
    kept, _z = select_area_tiles(laz_dir, tuple(bbox))
    rects = []
    for p in kept:
        try:
            mins, maxs, n = read_laz_header(p)
            rects.append((float(mins[0]), float(mins[1]),
                          float(maxs[0]), float(maxs[1]), int(n)))
        except Exception as exc:  # noqa: BLE001
            logger.warning("preflight: unreadable header %s: %s", p, exc)
    return rects


# ---------------------------------------------------------------------------
# The calibrated shape functions
# ---------------------------------------------------------------------------
def calibrated_occupancy(cell_xy: float) -> float:
    """
    Fraction of covered ground cells holding at least one point.

    Power law in cell_xy, capped at 1.0. Measured 0.951 at
    1.0 m, 0.947 at 0.5 m, 0.937 at 0.25 m over ten Grand Lyon areas; the
    per-area range is 0.690 to 1.000 and is a property of the area, stable
    across cell size to three decimals.
    """
    return min(OCCUPANCY_CEILING,
               CAL_OCCUPANCY_A * float(cell_xy) ** CAL_OCCUPANCY_K)


def calibrated_iv_per_column(cell_xy: float) -> float:
    """
    Intervals per occupied column, after grouping.

    Power law in cell_xy: 2.116 at 1.0 m, 1.776 at 0.5 m,
    1.480 at 0.25 m. cell_z does not enter - refining z by 2.5x at fixed xy
    moved the measured value from 1.480 to 1.481.
    """
    return CAL_IV_PER_COLUMN_A * float(cell_xy) ** CAL_IV_PER_COLUMN_K


def calibrated_iv_per_point(cell_xy: float) -> float:
    """
    Intervals per LiDAR point, after grouping - the point-informed estimate.

    Calibrated at a corpus density of 88.7 points/m2: 0.0227 at 1.0 m,
    0.0759 at 0.5 m, 0.2502 at 0.25 m. Because the ratio scales inversely
    with point density, it is the branch that sees a delivery denser than
    the calibration corpus, which the area-only model cannot.
    """
    return CAL_IV_PER_POINT_A * float(cell_xy) ** CAL_IV_PER_POINT_K


def estimate_store_bytes(
    covered_m2: float,
    cell_xy: float,
    *,
    n_points: int | None = None,
    group_intervals: bool = True,
    occupancy: float | None = None,
    iv_per_column: float | None = None,
    calibrated: bool = True,
) -> dict:
    """
    The shape model on its own: covered ground -> columns, intervals, bytes.

    Returns both a central estimate (``*_mid``, what the calibration says a
    typical Grand Lyon area does) and a planning estimate (the plain keys,
    what a run should be sized against). The planning estimate is the one
    ``est_peak_mb`` is built from; it is deliberately the larger.

    @param covered_m2 Ground area actually covered by the selected tiles, in
        square metres.
    @param cell_xy Horizontal cell size in metres. The only grid parameter the
        shape model depends on - see calibrated_iv_per_column.
    @param n_points Header point total for the covered ground, if known
        (``None`` = unknown). Enables the point-informed branch and the hard
        interval ceiling.
    @param group_intervals Whether the run will coalesce adjacent same-class
        intervals (default True, the pipeline default). False adds
        ``UNGROUPED_IV_FACTOR``.
    @param occupancy Override the calibrated occupancy with a fixed value
        (``None`` = use the calibration; also forced to ``None`` when
        ``calibrated=False``).
    @param iv_per_column Override the calibrated intervals-per-column with a
        fixed value (``None`` = use the calibration).
    @param calibrated When False (default True), reproduces the
        uncalibrated model exactly (``DEFAULT_OCCUPANCY``,
        ``DEFAULT_IV_PER_COLUMN``, no tail, no point branch), kept so old
        numbers stay reproducible.
    @return A dict of the shape model: ``possible_columns``, the occupancy and
        iv-per-column pairs (mid and planning), ``est_columns``,
        ``est_intervals`` and their ``_mid`` variants,
        ``est_intervals_from_points``, ``bound_source`` (which branch bound
        the estimate) and the byte totals ``est_store_bytes``,
        ``est_store_bytes_mid`` and ``est_store_bytes_legacy``.
    """
    covered_m2 = float(covered_m2)
    cell_xy = float(cell_xy)
    possible = covered_m2 / (cell_xy * cell_xy)
    ungrouped = 1.0 if group_intervals else UNGROUPED_IV_FACTOR

    if not calibrated:
        occupancy = DEFAULT_OCCUPANCY if occupancy is None else occupancy
        iv_per_column = (DEFAULT_IV_PER_COLUMN if iv_per_column is None
                         else iv_per_column)
        n_points = None
        ungrouped = 1.0

    if occupancy is None:
        occ_mid = calibrated_occupancy(cell_xy)
        occ_hi = OCCUPANCY_CEILING
    else:
        occ_mid = occ_hi = float(occupancy)
    if iv_per_column is None:
        ivpc_mid = calibrated_iv_per_column(cell_xy)
        ivpc_hi = ivpc_mid * IV_AREA_TAIL
    else:
        ivpc_mid = ivpc_hi = float(iv_per_column)

    columns_mid = possible * occ_mid
    intervals_mid = columns_mid * ivpc_mid * ungrouped
    columns = possible * occ_hi
    intervals = columns * ivpc_hi * ungrouped
    intervals_from_points = None
    source = "area"

    if n_points:
        n_points = float(n_points)
        # A column holds at least one point, and so does an interval.
        columns_mid = min(columns_mid, n_points)
        columns = min(columns, n_points)
        intervals_mid = min(intervals_mid, n_points)
        intervals_from_points = n_points * calibrated_iv_per_point(cell_xy) \
            * ungrouped
        if intervals_from_points > intervals:
            intervals = intervals_from_points
            source = "points"
        if intervals > n_points:
            intervals = n_points
            source = "point_ceiling"

    return {
        "possible_columns": possible,
        "occupancy": occ_hi,
        "occupancy_mid": occ_mid,
        "iv_per_column": ivpc_hi * ungrouped,
        "iv_per_column_mid": ivpc_mid * ungrouped,
        "est_columns": columns,
        "est_intervals": intervals,
        "est_columns_mid": columns_mid,
        "est_intervals_mid": intervals_mid,
        "est_intervals_from_points": intervals_from_points,
        "bound_source": source,
        "est_store_bytes": columns * COLUMN_BYTES + intervals * INTERVAL_BYTES,
        "est_store_bytes_mid": (columns_mid * COLUMN_BYTES
                                + intervals_mid * INTERVAL_BYTES),
        "est_store_bytes_legacy": columns * LEGACY_COLUMN_BYTES,
    }


# ---------------------------------------------------------------------------
# The estimate itself
# ---------------------------------------------------------------------------
def estimate_area_run(
    bbox,
    *,
    cell_xy: float,
    cell_z: float,
    stream: bool,
    json_file: Path | str | None = None,
    laz_dir: Path | str | None = None,
    clip: bool = True,
    tile_pitch: int = 500,
    limit: int = 0,
    occupancy: float | None = None,
    iv_per_column: float | None = None,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    n_points: int | None = None,
    group_intervals: bool = True,
    calibrated: bool = True,
) -> dict:
    """
    Predict the cost of an area run WITHOUT reading a single point.

    Uses only the inventory JSON (stream mode) or LAZ headers (local mode).
    Returns a dict with, among others:

        n_tiles, covered_km2, hull (metric), est_columns, est_intervals,
        est_store_mb            struct-of-arrays store at completion
        est_store_mb_mid        the same, at the calibration's central
                                values instead of the planning ones
        est_store_mb_legacy     what the old dict layout would have cost
        n_points                header point total for the covered ground,
                                None when unavailable (stream mode)
        bound_source            which branch set est_intervals: "area",
                                "points", or "point_ceiling"
        native_raster_px / native_raster_mb   the un-guarded map cost
        raster_mb               map cost after the pixel-budget guard
        est_peak_mb             (store + guarded raster) x SAFETY_FACTOR,
                                plus working overhead
        rough_download_mb       stream mode only (very rough, ~40 MB/tile)

    The shape numbers come from the calibration described in the module
    docstring; ``occupancy=`` / ``iv_per_column=`` override them with a
    fixed value and ``calibrated=False`` restores the uncalibrated model
    in full. ``n_points`` is read from the LAZ headers automatically in
    local mode and can be supplied by any caller that knows it.

    est_columns / est_intervals / est_store_mb / est_peak_mb are planning
    figures: calibrated, then pushed to the conservative end of the measured
    per-area spread, because the cost of under-predicting here is a machine
    thrashing its pagefile. The ``*_mid`` keys carry the central estimate.
    """
    bbox = tuple(float(v) for v in bbox)
    counts: list[float] | None = None
    if stream:
        if json_file is None:
            raise ValueError("stream=True estimate requires json_file")
        rects = _rects_from_inventory(Path(json_file), bbox, tile_pitch, limit)
    else:
        if laz_dir is None:
            raise ValueError("stream=False estimate requires laz_dir")
        rects_n = _rects_and_counts_from_laz_dir(laz_dir, bbox)
        rects = [r[:4] for r in rects_n]
        counts = [float(r[4]) for r in rects_n]

    xs, ys, xe, ye = bbox
    covered_m2 = 0.0
    covered_points = 0.0
    hull = None
    for i, (x0, y0, x1, y1) in enumerate(rects):
        if clip:
            cx0, cy0 = max(x0, xs), max(y0, ys)
            cx1, cy1 = min(x1, xe), min(y1, ye)
        else:
            cx0, cy0, cx1, cy1 = x0, y0, x1, y1
        if cx1 <= cx0 or cy1 <= cy0:
            continue
        area = (cx1 - cx0) * (cy1 - cy0)
        covered_m2 += area
        if counts is not None:
            full = (x1 - x0) * (y1 - y0)
            # Header counts are per whole tile; a clipped tile contributes
            # its area share. Uniform density inside a tile is the only
            # assumption available without reading points.
            covered_points += counts[i] * (area / full if full > 0 else 1.0)
        if hull is None:
            hull = [cx0, cy0, cx1, cy1]
        else:
            hull[0] = min(hull[0], cx0); hull[1] = min(hull[1], cy0)
            hull[2] = max(hull[2], cx1); hull[3] = max(hull[3], cy1)

    if n_points is None and counts is not None:
        n_points = int(covered_points)

    n_tiles = len(rects)
    shape = estimate_store_bytes(
        covered_m2, cell_xy, n_points=n_points,
        group_intervals=group_intervals, occupancy=occupancy,
        iv_per_column=iv_per_column, calibrated=calibrated)
    est_columns = shape["est_columns"]
    est_intervals = shape["est_intervals"]
    est_store = shape["est_store_bytes"]
    est_store_legacy = shape["est_store_bytes_legacy"]

    if hull is not None:
        native_w = int(math.ceil((hull[2] - hull[0]) / cell_xy))
        native_h = int(math.ceil((hull[3] - hull[1]) / cell_xy))
    else:
        native_w = native_h = 0
    native_px = native_w * native_h
    guarded_px = min(native_px, max_pixels) if native_px else 0
    raster_bytes = guarded_px * RASTER_BYTES_PER_PIXEL
    native_raster_bytes = native_px * RASTER_BYTES_PER_PIXEL

    est_peak = ((est_store + raster_bytes) * SAFETY_FACTOR
                + WORKING_OVERHEAD_MB * 1024 * 1024)

    return {
        "bbox": bbox,
        "stream": bool(stream),
        "clip": bool(clip),
        "cell_xy": float(cell_xy),
        "cell_z": float(cell_z),
        "n_tiles": n_tiles,
        "covered_km2": covered_m2 / 1e6,
        "covered_m2": covered_m2,
        "hull": tuple(hull) if hull else None,
        "occupancy": shape["occupancy"],
        "iv_per_column": shape["iv_per_column"],
        "occupancy_mid": shape["occupancy_mid"],
        "iv_per_column_mid": shape["iv_per_column_mid"],
        "calibrated": bool(calibrated),
        "group_intervals": bool(group_intervals),
        "n_points": int(n_points) if n_points else None,
        "bound_source": shape["bound_source"],
        "est_columns": est_columns,
        "est_intervals": est_intervals,
        "est_columns_mid": shape["est_columns_mid"],
        "est_intervals_mid": shape["est_intervals_mid"],
        "est_intervals_from_points": shape["est_intervals_from_points"],
        "est_store_mb": est_store / (1024 * 1024),
        "est_store_mb_mid": shape["est_store_bytes_mid"] / (1024 * 1024),
        "est_store_mb_legacy": est_store_legacy / (1024 * 1024),
        "max_pixels": int(max_pixels),
        "native_raster_px": native_px,
        "native_raster_w": native_w,
        "native_raster_h": native_h,
        "native_raster_mb": native_raster_bytes / (1024 * 1024),
        "raster_mb": raster_bytes / (1024 * 1024),
        "est_peak_mb": est_peak / (1024 * 1024),
        # Rough, clearly-labelled transfer figure for streamed downloads.
        # The 2,842-tile corpus measures median 66.9 MB / mean 75.0 MB per
        # compressed tile (the Genstats corpus record, LAZ_STATS.md), so
        # 40 MB/tile is roughly half the measured mean - read the figure
        # as a lower-bound order of magnitude only.
        "rough_download_mb": (n_tiles * 40.0) if stream else 0.0,
    }


def format_estimate(est: dict) -> str:
    """Human-readable multi-line summary of an estimate (log / dialog)."""
    lines = [
        f"Tiles selected      : {est['n_tiles']:,}",
        f"Covered area        : {est['covered_km2']:.2f} km2 "
        f"({'clipped to bbox' if est['clip'] else 'unclipped tiles'})",
        f"Voxel grid          : cell_xy={est['cell_xy']} m, "
        f"cell_z={est['cell_z']} m",
    ]
    if est.get("n_points"):
        lines.append(f"Header point total  : {est['n_points']:,}")
    lines += [
        f"Est. columns        : {est['est_columns']:,.0f} "
        f"(planning occupancy {est['occupancy']:.0%}, "
        f"calibrated {est.get('occupancy_mid', est['occupancy']):.0%})",
        f"Est. intervals      : {est['est_intervals']:,.0f} "
        f"({est['iv_per_column']:.2f} per column, "
        f"set by the {est.get('bound_source', 'area')} branch)",
        f"Est. store size     : {est['est_store_mb']:,.0f} MB planning, "
        f"{est.get('est_store_mb_mid', est['est_store_mb']):,.0f} MB "
        f"calibrated central "
        f"(old dict layout would be {est['est_store_mb_legacy']:,.0f} MB)",
        f"Native map raster   : {est['native_raster_w']:,} x "
        f"{est['native_raster_h']:,} px = {est['native_raster_mb']:,.0f} MB "
        f"(guarded to {est['raster_mb']:,.0f} MB)",
        f"Estimated peak RAM  : {est['est_peak_mb']:,.0f} MB",
    ]
    if est["stream"]:
        lines.append(f"Rough download      : ~{est['rough_download_mb']:,.0f} MB "
                     f"(very rough, ~40 MB/tile)")
        lines.append("Note: streamed inventories carry no point count, so the "
                     "point-informed bound is unavailable here.")
    return "\n".join(lines)


# Finest first: the ladder the GUI and the docs offer. suggest_cell_xy walks
# it in this order and returns the first entry that fits, which is therefore
# the finest affordable cell.
CELL_LADDER = (0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0)


def suggest_cell_xy(est: dict, budget_mb: float,
                    ladder=CELL_LADDER) -> float | None:
    """
    Finest cell_xy from ``ladder`` whose predicted peak fits ``budget_mb``.

    Re-runs the shape and raster model over the covered area already
    measured in ``est``, so it costs no further header reads. Returns None
    when even the coarsest rung does not fit.
    """
    covered_m2 = est.get("covered_m2")
    if covered_m2 is None:
        covered_m2 = float(est["covered_km2"]) * 1e6
    hull = est.get("hull")
    max_pixels = int(est.get("max_pixels", DEFAULT_MAX_PIXELS))
    n_points = est.get("n_points")
    group = bool(est.get("group_intervals", True))
    calibrated = bool(est.get("calibrated", True))

    best = None
    for cell in sorted(ladder):
        shape = estimate_store_bytes(covered_m2, cell, n_points=n_points,
                                     group_intervals=group,
                                     calibrated=calibrated)
        if hull:
            w = int(math.ceil((hull[2] - hull[0]) / cell))
            h = int(math.ceil((hull[3] - hull[1]) / cell))
            raster = min(w * h, max_pixels) * RASTER_BYTES_PER_PIXEL
        else:
            raster = 0
        peak_mb = ((shape["est_store_bytes"] + raster) * SAFETY_FACTOR
                   / (1024 * 1024) + WORKING_OVERHEAD_MB)
        if peak_mb <= float(budget_mb):
            best = cell if best is None else min(best, cell)
    return best
