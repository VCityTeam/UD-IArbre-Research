"""
Matplotlib helpers + the raster machinery behind the four 2-D map modes.
@ingroup t2_algos


Memory fix: ``render_tile_map`` used to allocate dense (H, W) arrays
over the occupied index hull and fill them with a per-column Python loop.
For a multi-km area at cell_xy=0.25 that is tens of GB of rasters and a
GIL-hogging loop over millions of columns. It is now:

  * vectorized - one ``ColumnStore.column_summaries()`` call plus numpy
    scatter operations; no Python loop over columns;
  * guarded - a hard pixel budget (``max_pixels``). If the native raster
    would exceed it, columns are aggregated by an integer factor so the
    output stays within budget (max-height keeps the max, class views keep
    a representative, n_intervals sums). A warning is logged when this
    triggers, because the map is then a downsampled overview.

The scatter (``scatter_summaries``) and colorize (``colorize_rasters``)
stages are exposed separately so the sharded area pipeline can mosaic many
per-tile stores into one bounded set of rasters and colorize at the end.
"""

from __future__ import annotations
import logging
import numpy as np

from .classes_config import CLASS_COLORS, CLASS_NAMES
from .data_structures import ColumnStore
from .viz_common import CLASS_LUT_UNKNOWN_RGB, class_color_lut

logger = logging.getLogger(__name__)

# Default hard budget for any single map raster (pixels) - the point where
# render_tile_map starts aggregating columns into coarser pixels. Cost is
# 19 B/px (five scalar rasters 16 B + RGB image 3 B):
#   32 MP ~ 0.6 GB | 64 MP ~ 1.2 GB | 128 MP ~ 2.4 GB | 256 MP ~ 4.9 GB.
# 64-128 MP is the suggested budget; the shipped default is 256 MP for
# extra detail. It is already a per-call knob (max_pixels= here and on
# plot_tile_map; map_max_pixels= on run_area_sharded) - the aggregation
# guard below is what makes any budget safe.
DEFAULT_MAX_PIXELS = 256_000_000

_TOP_SENTINEL = np.int32(np.iinfo(np.int32).min)   # "no data" for max fields
_BOT_SENTINEL = np.int32(np.iinfo(np.int32).max)   # "no data" for min fields


def _class_lut() -> np.ndarray:
    """256x3 uint8 colour lookup; unknown codes get the grey fallback.

    The table itself lives in viz_common.class_color_lut(), shared with
    visualizer3d and tiled_exporter; tileset_exporter reads CLASS_COLORS
    directly with the same grey fallback, so a class renders the same
    colour everywhere.
    """
    return class_color_lut()


def make_rasters(H: int, W: int) -> dict:
    """Allocate the five scalar rasters used by every map mode."""
    return {
        "top": np.full((H, W), _TOP_SENTINEL, dtype=np.int32),
        "bot": np.full((H, W), _BOT_SENTINEL, dtype=np.int32),
        "niv": np.zeros((H, W), dtype=np.int32),
        "top_cls": np.full((H, W), -1, dtype=np.int16),
        "dom_cls": np.full((H, W), -1, dtype=np.int16),
    }


def scatter_summaries(rasters: dict, summ: dict, *,
                      ix_min: int, iy_max: int, agg: int = 1) -> None:
    """
    Scatter one store's per-column summaries into (possibly shared) rasters.

    ``ix_min`` / ``iy_max`` define the raster frame in column-index space
    (north up: row = (iy_max - iy) // agg). With ``agg > 1`` several columns
    land on one pixel; reductions are max (top), min (bot), sum (niv), and
    representative-wins for the class views. Safe to call repeatedly with
    different stores that share the frame - this is the mosaic path.
    """
    if summ["ix"].size == 0:
        return
    rows = (iy_max - summ["iy"].astype(np.int64)) // agg
    cols = (summ["ix"].astype(np.int64) - ix_min) // agg
    # Bounds guard: a column outside the frame would raise IndexError on
    # the high side but WRAP SILENTLY on the negative side (row -1 = the
    # last row - the north edge painted onto the south edge). The frame
    # is sized to cover inclusive header maxima (sharding), so this only
    # fires when a header lies about its extent - drop those columns
    # loudly rather than paint them in the wrong place.
    H_r, W_r = rasters["top"].shape
    valid = (rows >= 0) & (rows < H_r) & (cols >= 0) & (cols < W_r)
    if not valid.all():
        n_bad = int(valid.size - valid.sum())
        logger.warning("scatter_summaries: dropping %d column(s) outside "
                       "the raster frame (header extents inconsistent "
                       "with actual point indices?)", n_bad)
        rows, cols = rows[valid], cols[valid]
        summ = {k: (v[valid] if isinstance(v, np.ndarray)
                    and v.shape[:1] == valid.shape else v)
                for k, v in summ.items()}
        if rows.size == 0:
            return
    # One code path for every agg: top/bot/niv go through ufunc.at so
    # max/min/sum semantics hold against previously scattered stores (and
    # against duplicate pixels when agg > 1); the class views use direct
    # fancy assignment - the representative per (aggregated) pixel is the
    # last write, as meaningful as any other single representative.
    np.maximum.at(rasters["top"], (rows, cols), summ["top"])
    np.minimum.at(rasters["bot"], (rows, cols), summ["bot"])
    np.add.at(rasters["niv"], (rows, cols), summ["n_intervals"])
    rasters["top_cls"][rows, cols] = summ["top_cls"].astype(np.int16)
    rasters["dom_cls"][rows, cols] = summ["dom_cls"].astype(np.int16)


def colorize_rasters(rasters: dict, mode: str,
                     height_mode: str = "default") -> np.ndarray:
    """Turn the scalar rasters into the uint8 (H, W, 3) image for ``mode``."""
    top = rasters["top"]
    valid = top != _TOP_SENTINEL
    H, W = top.shape
    img = np.zeros((H, W, 3), dtype=np.uint8)

    if mode == "max_height":
        if valid.any():
            topf = top.astype(np.float32)
            if height_mode == "relative":
                # Height above each column's own lowest point (nDSM/CHM style).
                scalar = topf - rasters["bot"].astype(np.float32)
                lo, hi = 0.0, float(scalar[valid].max())
            elif height_mode == "absolute":
                # True altitude in index space: floor of the tile -> its peak.
                scalar = topf
                lo = float(rasters["bot"][valid].min())
                hi = float(topf[valid].max())
            else:  # "default" - original auto-contrast on the tops.
                scalar = topf
                lo = float(topf[valid].min())
                hi = float(topf[valid].max())
            scaled = np.clip((scalar - lo) / max(hi - lo, 1.0) * 255, 0, 255)
            scaled = np.where(valid, scaled, 0).astype(np.uint8)
            img[..., 0] = scaled
            img[..., 1] = scaled
            img[..., 2] = scaled

    elif mode in ("max_points_class", "orthophoto_class"):
        lut = _class_lut()
        src = rasters["dom_cls" if mode == "max_points_class" else "top_cls"]
        vv = src >= 0
        img[vv] = lut[src[vv].astype(np.int64)]

    elif mode == "n_intervals":
        counts = rasters["niv"]
        hi = max(int(counts.max()), 1)
        scaled = (counts / hi * 255).astype(np.uint8)
        img[..., 0] = scaled
        img[..., 1] = scaled
        img[..., 2] = scaled
    else:
        raise ValueError(f"unknown mode {mode!r} - pick max_height / "
                         f"max_points_class / orthophoto_class / n_intervals")
    return img


def aggregation_factor(native_pixels: int, max_pixels: int | None) -> int:
    """Smallest integer factor s with native_pixels / s^2 <= max_pixels."""
    if not max_pixels or native_pixels <= max_pixels:
        return 1
    import math
    return int(math.ceil(math.sqrt(native_pixels / max_pixels)))


def rasters_from_store(store: ColumnStore, *,
                       need_dom: bool = True,
                       max_pixels: int | None = DEFAULT_MAX_PIXELS,
                       summ: dict | None = None,
                       streaming: bool | None = None,
                       batch_intervals: int | None = None):
    """The five scalar rasters for one whole store, plus their frame.

    Two ways in, one result. With ``summ`` (a precomputed
    ``store.column_summaries()`` dict) or on a plain in-RAM store, the
    summaries are scattered in one call. On a memory-mapped store
    (``streaming=None`` decides by ``store_streaming.is_mmap_backed`` - the
    same store-not-flag switch every stage uses) they are scattered one
    whole-column batch at a time from ``store_streaming.summaries_batches``,
    so the peak is one batch plus the pixel-guarded rasters, never a
    per-column array. The raster content is identical either way:
    top/bot/niv are max/min/sum accumulations, and the last-write class
    representative is taken in canonical key order on both paths.

    Returns ``(rasters, frame)``; ``frame`` carries the native extents
    (``ix_min``/``ix_max``/``iy_min``/``iy_max``), the aggregation factor
    ``agg`` and the aggregated raster shape ``H``/``W``. An empty store gets
    1 x 1 all-sentinel rasters, which colorize to the same single black
    pixel ``render_tile_map`` returns for it.
    """
    from .store_streaming import is_mmap_backed, key_frame, summaries_batches

    if streaming is None:
        streaming = summ is None and is_mmap_backed(store)

    if summ is not None or not streaming:
        if summ is None:
            summ = store.column_summaries(need_dom=need_dom)
        if summ["ix"].size == 0:
            frame = None
        else:
            frame = (int(summ["ix"].min()), int(summ["ix"].max()),
                     int(summ["iy"].min()), int(summ["iy"].max()))
    else:
        frame = key_frame(store)

    if frame is None:
        return make_rasters(1, 1), dict(ix_min=0, ix_max=-1, iy_min=0,
                                        iy_max=-1, agg=1, H=1, W=1)
    ix_min, ix_max, iy_min, iy_max = frame
    W = ix_max - ix_min + 1
    H = iy_max - iy_min + 1
    agg = aggregation_factor(H * W, max_pixels)
    if agg > 1:
        logger.warning(
            "map rasters: native %d x %d (%.1f MP) exceeds the %.1f MP "
            "budget - aggregating columns by %dx%d (overview map).",
            W, H, H * W / 1e6, (max_pixels or 0) / 1e6, agg, agg)
    Wa = (W + agg - 1) // agg
    Ha = (H + agg - 1) // agg
    rasters = make_rasters(Ha, Wa)

    if summ is not None:
        scatter_summaries(rasters, summ, ix_min=ix_min, iy_max=iy_max, agg=agg)
    else:
        kwargs = {} if batch_intervals is None else dict(
            batch_intervals=batch_intervals)
        for batch in summaries_batches(store, need_dom=need_dom, **kwargs):
            scatter_summaries(rasters, batch,
                              ix_min=ix_min, iy_max=iy_max, agg=agg)

    return rasters, dict(ix_min=ix_min, ix_max=ix_max, iy_min=iy_min,
                         iy_max=iy_max, agg=agg, H=Ha, W=Wa)


def assign_overlap_lanes(z_start, z_end):
    """
    Assign each interval of ONE column a horizontal lane so cross-class
    intervals that share z (shared voxels) render side-by-side instead of
    overpainting each other. Input arrays are the column's
    intervals in canonical (z_start, class) order; every z-overlap measured
    on this corpus is cross-class, and lanes do not depend on that holding
    (see ColumnStore.class_overlap_stats for what is measured and what is
    only touching).

    Returns ``(lane, width)`` lists: interval k spans horizontally from
    ``lane[k]/width[k]`` to ``(lane[k]+1)/width[k]`` of the column's unit
    width. ``width`` is the high-water lane count of the interval's
    overlap cluster, so non-overlapping intervals keep width 1 (full
    width) and unaffected columns render exactly as before. Greedy
    smallest-free-lane assignment; per-column interval counts are tiny,
    so the Python loop is fine here.
    """
    n = len(z_start)
    lane = [0] * n
    cluster = [0] * n
    cluster_width: dict[int, int] = {}
    active: list[tuple[int, int]] = []   # (z_end, lane) still open
    free: list[int] = []
    high = 0
    cur = -1
    for k in range(n):
        zs = int(z_start[k])
        still = []
        for (zend, ln) in active:
            if zend <= zs:
                free.append(ln)
            else:
                still.append((zend, ln))
        active = still
        if not active:                    # previous cluster fully closed
            cur += 1
            free = []
            high = 0
        if free:
            ln = min(free)
            free.remove(ln)
        else:
            ln = high
            high += 1
        active.append((int(z_end[k]), ln))
        lane[k] = ln
        cluster[k] = cur
        cluster_width[cur] = max(cluster_width.get(cur, 1), high)
    width = [max(1, cluster_width[cluster[k]]) for k in range(n)]
    return lane, width


def plot_column(
    store: ColumnStore,
    ix: int,
    iy: int,
    ax=None,
    show_class_names: bool = True,
):
    """
    Plot one column as a stacked vertical bar.

    Each interval becomes a horizontal band coloured by its semantic class.
    Air gaps (no interval) appear as empty space between bands. Cross-class
    intervals that OVERLAP in z (shared voxels) are drawn side-by-side in
    sub-lanes instead of overpainting, so a voxel holding two
    classes shows both; non-overlapping columns render exactly as before.
    This is the fastest way to eyeball whether the RLE is doing something
    sensible.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    if (ix, iy) not in store.columns:
        raise KeyError(f"No column at ({ix}, {iy}). "
                       f"Available columns: {len(store.columns)}")
    col = store.columns[(ix, iy)]

    if ax is None:
        # matplotlib.figure.Figure (not pyplot) so the figure is garbage-
        # collected with the returned axes instead of living forever in
        # pyplot's global registry (a leak for external API callers).
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        fig = Figure(figsize=(3, 8)); FigureCanvasAgg(fig)
        ax = fig.add_subplot()

    lanes, widths = assign_overlap_lanes(col.z_start, col.z_end)
    for k in range(len(col)):
        z_lo = store.z_min + int(col.z_start[k]) * store.cell_z
        z_hi = store.z_min + int(col.z_end[k])   * store.cell_z
        cls  = int(col.cls[k])
        rgb  = tuple(c / 255 for c in
                     CLASS_COLORS.get(cls, (CLASS_LUT_UNKNOWN_RGB,) * 3))
        w = 1.0 / widths[k]
        x0 = lanes[k] * w
        ax.add_patch(Rectangle(
            (x0, z_lo), w, z_hi - z_lo,
            facecolor=rgb, edgecolor="black", linewidth=0.3,
        ))
        if show_class_names:
            name = CLASS_NAMES.get(cls, f"class_{cls}")
            ax.text(x0 + w / 2, (z_lo + z_hi) / 2,
                    f"{name}\n({int(col.count[k])} pts)",
                    ha="center", va="center",
                    fontsize=7 if widths[k] == 1 else 6)

    # Axis limits run from the first interval's start to the column's highest
    # z_end (max over intervals, so a cross-class overlap where an earlier
    # interval tops out higher stays inside the frame). Padded a bit.
    z_min = store.z_min + int(col.z_start[0]) * store.cell_z
    z_max = store.z_min + int(col.z_end.max())  * store.cell_z
    ax.set_xlim(0, 1)
    ax.set_ylim(z_min - 1, z_max + 1)
    ax.set_xticks([])
    ax.set_ylabel("altitude (m)")
    ax.set_title(f"Column ({ix}, {iy}) - {len(col)} intervals")
    return ax


def render_tile_map(
    store: ColumnStore,
    mode: str = "max_height",
    height_mode: str = "default",
    max_pixels: int | None = DEFAULT_MAX_PIXELS,
    summ: dict | None = None,
    rasters: dict | None = None,
) -> np.ndarray:
    """
    Render the whole tile/area as a 2-D RGB image, one pixel per column
    (or per ``agg x agg`` block of columns if the pixel budget forces
    aggregation - a warning is logged when that happens).

    Four modes:
     - "max_height": pixel brightness = the last interval z_end in
        canonical (z_start, cls) order - equal to the highest occupied
        voxel except in columns with cross-class z-overlaps, where an
        earlier taller interval can exceed it
        (useful for spotting buildings and trees against open ground).
     - "max_points_class": pixel colour = class with the most points in
        each column (highlights the dominant material by volume).
     - "orthophoto_class": pixel colour = class of the topmost interval
        (gives a top-down semantic map, looks a lot like an orthophoto
        coloured by land cover).
     - "n_intervals": pixel brightness = number of intervals in that
        column (a diagnostic view: empty areas are dark, complex
        canopy + building stacks are bright).

    `height_mode` only affects the "max_height" view; the other three
    views are unchanged. It chooses the vertical reference:
     - "default":  scale from the lowest *top* to the highest *top* across
                    columns (auto-contrast, the original behaviour).
     - "relative": pixel = height above each column's OWN lowest point
                    (top - bottom); scale 0 -> tallest column. Terrain slope
                    is removed, so a tree in a valley and a tree on a ridge
                    read the same - this is a normalized-DSM / canopy-height
                    style view.
     - "absolute": pixel = top voxel, scaled from the tile's lowest
                    occupied point to its highest. Black = the true floor of
                    the tile, so terrain and object altitude are preserved
                    (a plain DSM-style view).

    Returns a uint8 array of shape (H, W, 3) we can pass directly to
    matplotlib.imshow or PIL.Image.fromarray.

    ``summ`` - an optional precomputed ``store.column_summaries()`` dict. Pass
    it when rendering several modes of the *same* store so the (expensive)
    per-column reduction runs once instead of once per mode; the caller is then
    responsible for having requested ``need_dom`` if any mode it renders is
    ``max_points_class``. When ``summ`` is None it is computed here, and the
    dominant-class step is skipped unless this mode actually needs it.

    ``rasters`` - a precomputed raster set from rasters_from_store().
    When given, the store is not reduced at all: this call is just the
    colorize step. It is how the maps stage renders four modes from one
    banded pass over a memory-mapped store.

    With neither given, a memory-mapped store is reduced one whole-column
    batch at a time (see rasters_from_store()) - the same rasters,
    bounded RAM.
    """
    if rasters is not None:
        return colorize_rasters(rasters, mode, height_mode=height_mode)
    if not store.columns:
        return np.zeros((1, 1, 3), dtype=np.uint8)

    # Only max_points_class reads dom_cls; the other three modes skip the
    # dominant-class reduction entirely (cheaper, and immune to its OOM).
    rasters, _frame = rasters_from_store(
        store, need_dom=(mode == "max_points_class"),
        max_pixels=max_pixels, summ=summ)
    return colorize_rasters(rasters, mode, height_mode=height_mode)


def plot_tile_map(store: ColumnStore, mode: str = "orthophoto_class", ax=None,
                  height_mode: str = "default",
                  max_pixels: int | None = DEFAULT_MAX_PIXELS,
                  summ: dict | None = None,
                  rasters: dict | None = None):
    """Convenience wrapper around render_tile_map that calls matplotlib.

    ``summ`` and ``rasters`` are forwarded to render_tile_map() so a
    caller rendering all four modes can compute the per-column summaries (or
    the scattered rasters) once and reuse them.
    """
    import matplotlib.pyplot as plt
    img = render_tile_map(store, mode=mode, height_mode=height_mode,
                          max_pixels=max_pixels, summ=summ, rasters=rasters)
    if ax is None:
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        fig = Figure(figsize=(8, 8)); FigureCanvasAgg(fig)
        ax = fig.add_subplot()
    ax.imshow(img, interpolation="nearest")
    title = f"Tile map - {mode}"
    if mode == "max_height" and height_mode != "default":
        title += f" ({height_mode})"
    ax.set_title(title)
    ax.set_xlabel("ix (east -> )")
    ax.set_ylabel("iy (north ^)")
    return ax


# Figure geometry for the four area maps. Kept as constants because two
# callers must agree on them to the pixel: an 8x8 inch figure at 120 dpi is
# the 960x960 px map every recorded area run has produced.
AREA_MAP_FIGSIZE = (8, 8)
AREA_MAP_DPI = 120


def write_area_maps(store: ColumnStore, out_dir, *,
                    height_mode: str = "default",
                    rasters: dict | None = None,
                    modes=None) -> list[str]:
    """Render and write the four area maps as ``area_<mode>.png``.

    THE one renderer for those files. The in-process writer
    (``area_outputs._write_outputs``) and the isolated maps stage
    (``stage_runner._stage_maps``) both call this, so the two paths produce
    byte-identical PNGs; they used to disagree, the stage drawing matplotlib
    figures while the in-process path saved native-resolution
    render_tile_map() rasters through PIL, which meant the same store
    yielded a 960x960 RGBA figure down one route and a raster-sized RGB image
    down the other.

    Rendering goes through ``Figure`` + ``FigureCanvasAgg`` rather than
    ``pyplot``: it is byte-for-byte the same output as ``plt.subplots`` under
    Agg, without switching the backend of a parent process that may be driving
    a GUI. Output is deterministic - matplotlib's PNG metadata here is its
    version banner, carrying no timestamp - so repeat runs of one store agree.

    ``rasters`` lets a caller that already scattered the rasters (see
    rasters_from_store()) reuse them across all four modes instead of
    paying the reduction four times. Returns the file names written.
    """
    from pathlib import Path as _Path
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    if modes is None:
        from .pipeline import MODES as modes
    out_dir = _Path(out_dir)
    written: list[str] = []
    for mode in modes:
        fig = Figure(figsize=AREA_MAP_FIGSIZE)
        FigureCanvasAgg(fig)
        ax = fig.add_subplot()
        plot_tile_map(store, mode=mode, ax=ax, height_mode=height_mode,
                      rasters=rasters)
        fig.tight_layout()
        name = f"area_{mode}.png"
        fig.savefig(out_dir / name, dpi=AREA_MAP_DPI)
        logger.info("wrote %s", out_dir / name)
        written.append(name)
    return written
