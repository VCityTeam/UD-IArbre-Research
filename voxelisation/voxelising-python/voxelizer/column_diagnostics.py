"""
Per-column visual diagnostics for a ColumnStore.
@ingroup t3_orchestr


Two kinds of output:

  1. Four diagnostic figures (one PNG each):
        columns_samples.png       - one representative column per category
        columns_top_complex.png   - the N tallest / most-complex columns
        histogram_intervals.png   - distribution of intervals per column
                                    (linear + log-log)
        columns_by_class.png      - one representative column per class
                                    code present. NOT the whole
                                    classes_config table: the codes are
                                    filtered to <= 31, which keeps ASPRS
                                    0-11 and 13-22 (22 of the table's 26)
                                    and drops IGN 64-67, unreachable under
                                    the 5-bit classification byte of point
                                    format 1. Code 12 is not in the table.

  2. One PNG per occupied column (optional, expensive in time + space):
        col_ix0000_iy0324.png ... Filename encodes voxel indices;
        the plot title also shows world coordinates in metres.

The categories used by the sample figure are not mutually exclusive - 
they're sampling buckets, so a column can qualify for several. We pick
one example from each.

Memory note: per-column rendering goes through `matplotlib.figure.Figure`
+ `FigureCanvasAgg` directly, bypassing pyplot. Pyplot keeps figures
alive globally and is dangerous in a 40 000-figure loop.

Every writer here takes a store plus, optionally, its already-computed
selection (``picks=`` / ``keys=`` / ``count_hist=`` / ``entries=``). That is
what lets the same figures be drawn without the store they describe:
voxelizer.shard_diagnostics computes the selections by folding over a
shard set and then passes a store holding only the selected columns, which
draws identically because a panel reads nothing else.
"""

from __future__ import annotations
import gc
import logging
import math
from pathlib import Path
from typing import Iterable
import numpy as np

from .classes_config import (
    ALL_CLASS_CODES, CLASS_COLORS, CLASS_NAMES,
    GROUND, BUILDING, VEGETATION_CLASSES,
)
from .visualization import assign_overlap_lanes
from .data_structures import ColumnStore, _unpack_keys
from .viz_common import CLASS_LUT_UNKNOWN_RGB
from . import store_streaming

logger = logging.getLogger(__name__)

"""
Column-complexity bands (recalibration): a column with
_COMPLEX_MIN..(_FRAGMENTED_MIN - 1) intervals (5-9) is "complex"; one with
>= _FRAGMENTED_MIN intervals (10+) is "fragmented" - the wall-fragmentation
tail produced by LiDAR returns on vertical facades. The histogram's log-log
panel draws its threshold line at _FRAGMENTED_MIN, the same x = 10 position
the previous (threshold * 2) formula produced, so old and new figures stay
visually comparable.
"""
_COMPLEX_MIN = 5
_FRAGMENTED_MIN = 10
# The two band labels, spelled once: the in-RAM categoriser and the streaming
# one (store_streaming.category_picks) must agree on them, and _CATEGORY_ORDER
# indexes the figure by them.
_COMPLEX_LABEL = f"complex ({_COMPLEX_MIN}-{_FRAGMENTED_MIN - 1} ivls)"
_FRAGMENTED_LABEL = f"fragmented (>={_FRAGMENTED_MIN} ivls)"
# mode=all cap: per-column PNGs were built for single-tile stores; above
# this the request is a misuse of the feature (see write_column_diagnostics).
_MODE_ALL_MAX_COLUMNS = 500_000

# ------------------------------------------------------------
#  Low-level: draw one column on an existing axis (no pyplot)
# ------------------------------------------------------------
def _draw_column(ax, store: ColumnStore, ix: int, iy: int,
                 *, show_labels: bool = True) -> None:
    """
    Draw one column onto `ax` as a stack of class-coloured rectangles.
    The axes' y-limits are set per column - from the first interval's
    z_start up to the LAST interval's z_end - so each panel is
    self-scaled. Under a cross-class overlap the last interval's z_end
    can lie below an earlier interval's top (z_end is not monotonic;
    see ColumnStore.class_overlap_stats), and that taller interval then
    draws past the top of the frame.
    """
    from matplotlib.patches import Rectangle

    col = store.columns[(ix, iy)]
    # Shared voxels (cross-class z-overlaps) render side-by-side in
    # sub-lanes so both classes stay visible instead of the
    # later-drawn one overpainting; unaffected columns are unchanged.
    lanes, widths = assign_overlap_lanes(col.z_start, col.z_end)
    for k in range(len(col)):
        z_lo = store.z_min + int(col.z_start[k]) * store.cell_z
        z_hi = store.z_min + int(col.z_end[k]) * store.cell_z
        cls = int(col.cls[k])
        rgb = tuple(c / 255 for c in
                    CLASS_COLORS.get(cls, (CLASS_LUT_UNKNOWN_RGB,) * 3))
        w = 1.0 / widths[k]
        x0 = lanes[k] * w
        ax.add_patch(Rectangle(
            (x0, z_lo), w, z_hi - z_lo,
            facecolor=rgb, edgecolor="black", linewidth=0.3,
        ))
        if show_labels:
            name = CLASS_NAMES.get(cls, f"class_{cls}")
            ax.text(x0 + w / 2, (z_lo + z_hi) / 2,
                    f"{name}\n({int(col.count[k])} pts)",
                    ha="center", va="center",
                    fontsize=7 if widths[k] == 1 else 6)

    z_min = store.z_min + int(col.z_start[0]) * store.cell_z
    z_max = store.z_min + int(col.z_end.max()) * store.cell_z
    ax.set_xlim(0, 1)
    ax.set_ylim(z_min - 1, z_max + 1)
    ax.set_xticks([])
    ax.set_ylabel("altitude (m)")


def _world_xy(store: ColumnStore, ix: int, iy: int) -> tuple[float, float]:
    """World-coordinate centre of a column, in metres (RGF93/CC46 EPSG:3946 for IGN)."""
    return (
        store.x_min + (ix + 0.5) * store.cell_xy,
        store.y_min + (iy + 0.5) * store.cell_xy,
    )

# -----------------------------------------------------------------------
#  Categorisation - chooses representative columns for the samples figure
# -----------------------------------------------------------------------
def _categorize(store: ColumnStore) -> dict[str, tuple[int, int]]:
    """
    Walk the store once and pick **one** representative column per
    category. The categories overlap (a fragmented building column
    is also "complex"), but we only keep one example each, so a
    single tile produces up to 8 sample panels.

    A category that finds no column is silently absent from the result.
    """
    picks: dict[str, tuple[int, int]] = {}

    prof = store.column_class_profile(
        ground_class=GROUND, building_class=BUILDING,
        veg_classes=VEGETATION_CLASSES)
    nivs = prof["nivs"]
    if nivs.size == 0:
        return picks

    first_cls = prof["first_cls"]
    has_ground = prof["has_ground"]
    has_building = prof["has_building"]
    has_veg = prof["has_veg"]
    n_distinct = prof["n_distinct"]

    ix_all, iy_all = _unpack_keys(store._keys)

    def _first(mask: np.ndarray) -> tuple[int, int] | None:
        """(ix, iy) of the first column (canonical order) where mask is True."""
        if not mask.any():
            return None
        i = int(np.argmax(mask))          # argmax -> first True
        return int(ix_all[i]), int(iy_all[i])

    # Each category is the FIRST column (smallest (ix, iy)) satisfying
    # its predicate. The predicates are NOT all mutually exclusive
    # ("ground + tree" is a strict subset of "vegetation stack"), so the
    # same column could be picked twice; the de-dup below keeps only the
    # first category that claimed it.
    single = nivs == 1
    two = nivs == 2

    p = _first(single & (first_cls == GROUND))
    if p is not None:
        picks["ground only"] = p
    p = _first(single & (first_cls == BUILDING))
    if p is not None:
        picks["building only"] = p

    # n_iv == 2 with ground present. distinct is 1 or 2; "== {ground, building}"
    # <=> distinct==2 & has_building; "ground + (single veg)" <=> distinct==2 &
    # has_veg (the lone non-ground class is then a veg class).
    two_g = two & has_ground & (n_distinct == 2)
    p = _first(two_g & has_building)
    if p is not None:
        picks["ground + building"] = p
    p = _first(two_g & has_veg & ~has_building)
    if p is not None:
        picks["ground + tree"] = p

    p = _first((nivs >= 2) & ~has_building & has_veg)
    if p is not None:
        picks["vegetation stack"] = p

    p = _first(n_distinct >= 3)
    if p is not None:
        picks["multi-class stack"] = p

    # De-duplicate the six predicate picks: a column drawn under one of
    # those labels must not reappear under another (previously "ground +
    # tree" and "vegetation stack" could both show the identical column).
    # The "complex" and "fragmented" picks below are added AFTER this and
    # are not de-duplicated against it, so either can repeat a column an
    # earlier label already took. store_streaming.CategoryFold.result
    # reproduces the same ordering, so both paths agree.
    seen: set[tuple[int, int]] = set()
    picks = {k: v for k, v in picks.items()
             if not (v in seen or seen.add(v))}

    # "complex" = the most-interval column among those with 5-9 intervals
    # (_COMPLEX_MIN <= n < _FRAGMENTED_MIN); "fragmented" = the most-interval
    # column among those with >= _FRAGMENTED_MIN (10+). The bands are DISJOINT
    # since the recalibration (previously the gates '>= 6' and
    # '> 5' always selected the same column, so 'fragmented' was deduplicated
    # away and never appeared). argmax over the masked counts keeps the old
    # tie rule: the first column (canonical order) reaching the band maximum.
    band = (nivs >= _COMPLEX_MIN) & (nivs < _FRAGMENTED_MIN)
    if band.any():
        i = int(np.argmax(np.where(band, nivs, -1)))
        picks[_COMPLEX_LABEL] = (int(ix_all[i]), int(iy_all[i]))

    frag = nivs >= _FRAGMENTED_MIN
    if frag.any():
        i = int(np.argmax(np.where(frag, nivs, -1)))
        picks[_FRAGMENTED_LABEL] = (int(ix_all[i]), int(iy_all[i]))

    return picks

def _top_n_by_intervals(store: ColumnStore, n: int) -> list[tuple[int, int]]:
    """
    Return the `n` (ix, iy) keys with the most intervals, most-complex first.

    Computed on the store's flat arrays via the vectorized ``column_summaries``
    reduction rather than by iterating ``store.columns.items()``. Two reasons:

      * Correctness / robustness. The old form unpacked each mapping item as
        ``(ix, iy), col`` and so silently depended on the exact shape of the
        Mapping view's keys. When the store moved to the struct-of-arrays
        layout that coupling is the kind of thing that breaks on
        a contract drift with a cryptic "too many values to unpack" deep in a
        list comprehension. Reading ``column_summaries()['ix'/'iy'/
        'n_intervals']`` - three parallel numpy arrays - has no per-item
        unpacking to get wrong.
      * Cost. The comprehension materialized one transient ``Column`` per
        occupied column plus an (key, len) tuple - millions of throwaway
        Python objects on a dense 500 m tile (~4M columns) just to rank them.
        The vectorized path stays in C.

    Ties (columns with equal interval counts) are broken by ascending
    (ix, iy) - i.e. canonical column order - so the selection is deterministic
    and reproducible across runs, matching the old items()-iteration order.
    """
    # This selector only reads ix/iy/n_intervals, so skip the dominant-class
    # step (the expensive, OOM-prone part of column_summaries).
    summ = store.column_summaries(need_dom=False)
    nivs = summ["n_intervals"]
    if nivs.size == 0 or n <= 0:
        return []
    n = min(int(n), int(nivs.size))
    # lexsort keys are applied last-key-first: primary = -nivs (most intervals
    # first), secondary = position (stable tie-break in canonical order).
    order = np.lexsort((np.arange(nivs.size), -nivs.astype(np.int64)))[:n]
    ix = summ["ix"][order].tolist()
    iy = summ["iy"][order].tolist()
    return [(int(a), int(b)) for a, b in zip(ix, iy)]

#  -----------------------------------------------------
#  Diagnostic plot 1: representative samples by category
#  -----------------------------------------------------
# Preferred category order (left -> right, top row first). Categories with no
# match are simply skipped.
_CATEGORY_ORDER = [
    "multi-class stack",
    "ground + building",
    _COMPLEX_LABEL,
    _FRAGMENTED_LABEL,
    "ground only",
    "ground + tree",
    "vegetation stack",
    "building only",
]

def write_samples_figure(store: ColumnStore, out_path: Path,
                         tile_label: str = "", *, picks=None) -> Path | None:
    """Write columns_samples.png: one panel per category.

    ``picks`` accepts an already-computed ``{category: (ix, iy)}`` mapping (the
    streaming categoriser's output); by default it is computed here, as before.
    """
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    if picks is None:
        picks = _categorize(store)
    cats = [c for c in _CATEGORY_ORDER if c in picks]
    if not cats:
        logger.warning("No category samples found; skipping samples figure.")
        return None

    n_panels = len(cats)
    n_cols = min(5, n_panels)
    n_rows = math.ceil(n_panels / n_cols)
    fig = Figure(figsize=(3.2 * n_cols, 8 * n_rows))
    FigureCanvasAgg(fig)
    axes = fig.subplots(n_rows, n_cols, squeeze=False)

    for k, cat in enumerate(cats):
        ax = axes[k // n_cols][k % n_cols]
        ix, iy = picks[cat]
        col = store.columns[(ix, iy)]
        z_top = store.z_min + int(col.z_end.max()) * store.cell_z
        z_bot = store.z_min + int(col.z_start[0]) * store.cell_z
        _draw_column(ax, store, ix, iy, show_labels=True)
        ax.set_title(
            f"{cat}\n(ix={ix}, iy={iy})\n"
            f"alt {z_bot:.1f}-{z_top:.1f} m  ({len(col)} ivls)",
            fontsize=9,
        )

    # Hide any unused panels in the last row.
    for k in range(n_panels, n_rows * n_cols):
        axes[k // n_cols][k % n_cols].set_visible(False)

    title = "Sample columns"
    if tile_label:
        title += f" from {tile_label}"
    title += f"  (cell_xy={store.cell_xy} m, cell_z={store.cell_z} m)"
    fig.suptitle(title, fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(out_path, dpi=110)
    return out_path

#  -----------------------------------------------------------
#  Diagnostic plot 1b: one representative column per class code
#  -----------------------------------------------------------
def _figure_class_codes() -> tuple[int, ...]:
    """Class codes eligible for a columns_by_class.png panel.

    Point format 1 caps class codes at 31 (5-bit field): the IGN
    forward-compat codes 64-67 in the table are structurally impossible in
    this dataset, and each requested code costs one full-length bool mask
    (~4x116 MB dead at metropolis scale).
    """
    return tuple(int(c) for c in ALL_CLASS_CODES if c <= 31)


def write_class_representatives_figure(store: ColumnStore, out_path: Path,
                                       tile_label: str = "", *,
                                       entries=None) -> Path | None:
    """Write columns_by_class.png: one representative column per class
    code present in the store, drawn from the classes_config table
    (ASPRS 0-22 + IGN 64-67 via ALL_CLASS_CODES, filtered below to the
    codes reachable under point format 1) - so every NAMED class that
    occurs in the data is visibly represented, not only the ground /
    vegetation / building buckets of the samples figure. Codes outside
    the table (12 and the reserved 23-63) get no panel. The
    representative is the first column (canonical order) containing the
    class, found via the vectorized ColumnStore.class_presence reduction
    (no per-column Python objects). Classes absent from the tile are
    simply skipped; the panel count is bounded by the 22 codes that
    survive the filter, not by the 26 in the table.

    ``entries`` accepts an already-computed ``[(code, ix, iy), ...]`` list
    (the streaming scan's output); by default it is computed here, as before.
    """
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    if not store.columns:
        return None
    if entries is None:
        codes = _figure_class_codes()
        presence = store.class_presence(codes)
        ix_all, iy_all = _unpack_keys(store._keys)
        entries = []
        for code in codes:
            mask = presence[int(code)]
            if mask.any():
                i = int(np.argmax(mask))       # first column containing it
                entries.append((int(code), int(ix_all[i]), int(iy_all[i])))
    if not entries:
        logger.warning("No named classes present; skipping by-class figure.")
        return None

    n_panels = len(entries)
    n_cols = min(6, n_panels)
    n_rows = math.ceil(n_panels / n_cols)
    fig = Figure(figsize=(3.2 * n_cols, 8 * n_rows))
    FigureCanvasAgg(fig)
    axes = fig.subplots(n_rows, n_cols, squeeze=False)

    for k, (code, ix, iy) in enumerate(entries):
        ax = axes[k // n_cols][k % n_cols]
        col = store.columns[(ix, iy)]
        z_top = store.z_min + int(col.z_end.max()) * store.cell_z
        z_bot = store.z_min + int(col.z_start[0]) * store.cell_z
        _draw_column(ax, store, ix, iy, show_labels=True)
        name = CLASS_NAMES.get(code, f"class_{code}")
        ax.set_title(
            f"{name} ({code})\n(ix={ix}, iy={iy})\n"
            f"alt {z_bot:.1f}-{z_top:.1f} m  ({len(col)} ivls)",
            fontsize=9,
        )
    for k in range(n_panels, n_rows * n_cols):
        axes[k // n_cols][k % n_cols].set_visible(False)

    title = "One representative column per class"
    if tile_label:
        title += f" from {tile_label}"
    title += f"  (cell_xy={store.cell_xy} m, cell_z={store.cell_z} m)"
    fig.suptitle(title, fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(out_path, dpi=110)
    return out_path


#  ------------------------------------------
#  Diagnostic plot 2: top-N by interval count
#  ------------------------------------------
# The overview is a bounded grid, not one uncapped row. One row of panels
# 3.5 in wide would make --columns-top-n 200 a 700 in x 10 in canvas =
# 77,000 x 1,100 px at dpi 110, one contiguous ~340 MB Agg buffer requested
# near the RAM ceiling, where a single allocation that large can fail as a
# native access violation rather than as a Python error - and a 77,000
# px-wide PNG is unusable anyway. So width is capped at _TOP_COMPLEX_PER_ROW
# panels and the overview shows at most _TOP_COMPLEX_MAX_PANELS (in mode=top
# the per-column PNGs already hold the FULL top-N individually, so no
# information is lost). Worst-case buffer is ~41 MB regardless of
# --columns-top-n.
_TOP_COMPLEX_PER_ROW = 8
_TOP_COMPLEX_MAX_PANELS = 24


def write_top_complex_figure(store: ColumnStore, out_path: Path,
                             n: int = 3, tile_label: str = "", *,
                             keys=None) -> Path | None:
    """Write columns_top_complex.png: the N columns with most intervals.

    ``keys`` accepts an already-computed ranking (the streaming top-N's
    output); by default it is computed here, as before.
    """
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    if keys is None:
        keys = _top_n_by_intervals(store, n)
    if not keys:
        return None

    n_show = min(len(keys), _TOP_COMPLEX_MAX_PANELS)
    if n_show < len(keys):
        logger.info("    top-complex overview clamped to %d of %d panels "
                    "(the per-column PNGs hold the full top-N in mode=top).",
                    n_show, len(keys))
    shown = keys[:n_show]
    n_cols = min(_TOP_COMPLEX_PER_ROW, n_show)
    n_rows = math.ceil(n_show / n_cols)
    fig = Figure(figsize=(3.5 * n_cols, 10 * n_rows))
    FigureCanvasAgg(fig)
    axes = fig.subplots(n_rows, n_cols, squeeze=False)

    for k, (ix, iy) in enumerate(shown):
        ax = axes[k // n_cols][k % n_cols]
        col = store.columns[(ix, iy)]
        z_top = store.z_min + int(col.z_end.max()) * store.cell_z
        z_bot = store.z_min + int(col.z_start[0]) * store.cell_z
        _draw_column(ax, store, ix, iy, show_labels=True)
        ax.set_title(
            f"(ix={ix}, iy={iy})\n"
            f"alt {z_bot:.1f}-{z_top:.1f} m  ({len(col)} ivls)",
            fontsize=9,
        )
    for k in range(n_show, n_rows * n_cols):   # hide unused trailing panels
        axes[k // n_cols][k % n_cols].axis("off")

    title = "Tallest / most complex columns"
    if tile_label:
        title += f" from {tile_label}"
    if n_show < len(keys):
        title += f"  (showing {n_show} of {len(keys)})"
    fig.suptitle(title, fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out_path, dpi=110)
    return out_path

#  ----------------------------------------------------
#  Diagnostic plot 3: histogram of intervals per column
#  ----------------------------------------------------
def write_intervals_histogram(store: ColumnStore, out_path: Path,
                              tile_label: str = "", *,
                              count_hist=None) -> Path | None:
    """Write histogram_intervals.png: linear + log-log distributions.

    ``count_hist`` accepts the complexity distribution itself - ``hist[k]`` =
    number of columns with k intervals, i.e. what ``np.bincount`` of the
    per-column counts would give (store_streaming.interval_count_histogram
    computes it without ever holding one number per column). Every quantity
    both panels draw is a function of that histogram; the default path still
    goes through ``interval_counts()``, so its output is untouched.
    """
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    if not store.columns:
        return None

    if count_hist is None:
        # Per-column interval counts straight off the offset array (no
        # per-column Column objects; see ColumnStore.interval_counts).
        counts = store.interval_counts()
        if counts.size == 0:
            return None
        bc = np.bincount(counts)
        mean_iv = float(counts.mean())
        hi = int(counts.max())
    else:
        counts = None
        bc = np.asarray(count_hist, dtype=np.int64)
        if bc.size == 0 or bc.sum() == 0:
            return None
        k = np.arange(bc.size, dtype=np.int64)
        mean_iv = float(int((k * bc).sum()) / int(bc.sum()))
        hi = int(np.flatnonzero(bc)[-1])

    fig = Figure(figsize=(13, 5))
    FigureCanvasAgg(fig)
    ax_lin, ax_log = fig.subplots(1, 2)

    # --- Linear panel ---
    # Cap the *view* at 15 if there's a long tail, so the common
    # 1-to-10-intervals range stays readable. Values past the cap simply
    # aren't drawn on this panel. The log panel on the right is where
    # the wall-fragmentation tail belongs.
    cap = min(15, hi)
    bins = np.arange(0.5, cap + 1.5, 1)
    if counts is not None:
        ax_lin.hist(counts[counts <= cap], bins=bins,
                    color="#4477AA", edgecolor="white")
    else:
        # Same bars from the distribution: one sample per occupied bin,
        # weighted by how many columns fall in it.
        ax_lin.hist(np.arange(1, cap + 1, dtype=np.float64), bins=bins,
                    weights=bc[1:cap + 1].astype(np.float64),
                    color="#4477AA", edgecolor="white")
    ax_lin.axvline(mean_iv, color="crimson", linestyle="--",
                   label=f"mean = {mean_iv:.2f}")
    ax_lin.legend()
    ax_lin.set_xlabel("intervals per column")
    ax_lin.set_ylabel("number of columns")
    title = "Distribution of intervals per column"
    if tile_label:
        title += f" ({tile_label})"
    if hi > cap:
        n_past = (int((counts > cap).sum()) if counts is not None
                  else int(bc[cap + 1:].sum()))
        title += f"  (clipped: {n_past:,} columns past {cap})"
    ax_lin.set_title(title)
    ax_lin.set_xticks(np.arange(0, cap + 2, 2))

    # --- Log-log panel ---
    nonzero = np.nonzero(bc)[0]
    # Drop bin 0 (impossible: a column has >=1 interval by construction)
    # without losing bin 1.
    nonzero = nonzero[nonzero >= 1]
    if nonzero.size:
        ys = bc[nonzero]
        ax_log.stem(nonzero, ys, basefmt=" ", linefmt="C3-", markerfmt="C3o")
        ax_log.set_xscale("log")
        ax_log.set_yscale("log")
        ax_log.axvline(_FRAGMENTED_MIN, color="orange", linestyle="--",
                       label=f"'fragmented' threshold "
                             f"(>={_FRAGMENTED_MIN} ivls)")
        ax_log.legend()
    ax_log.set_xlabel("intervals per column (log)")
    ax_log.set_ylabel("number of columns (log)")
    ax_log.set_title("Same distribution, log-log - the wall-fragmentation tail")
    ax_log.grid(True, which="both", alpha=0.3)

    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    return out_path

#  ----------------
#  Per-column PNGs
#  ----------------
def _column_filename(ix: int, iy: int) -> str:
    """`col_ix0000_iy0324.png` -> zero-padded for sortable listings."""
    # Four digits is a minimum width, not a cap: an index past 9999 still
    # prints in full but stops sorting as text. The width stays at four
    # because the shipped per-column PNGs and the docs quoting them use it.
    return f"col_ix{ix:04d}_iy{iy:04d}.png"
def _render_column_into(fig, store: ColumnStore, ix: int, iy: int) -> None:
    """Draw one column into an existing (cleared) Figure with FIXED margins.

    Two deliberate departures from a figure-per-call path: (1) the caller may
    REUSE one Figure + Agg canvas across thousands or millions of columns, so
    the buffer, the font and freetype objects and the canvas are allocated
    once instead of once per column, which keeps native allocation flat over
    a long run instead of churning it per column; (2) fixed
    ``subplots_adjust`` margins replace ``bbox_inches="tight"``, whose tight
    path re-measures every tick and label per figure through native machinery
    (get_tightbbox, then tick creation). The plotted content is identical;
    only the whitespace framing differs slightly (constant margins instead of
    a per-image crop).
    """
    fig.clear()
    ax = fig.subplots()
    col = store.columns[(ix, iy)]
    _draw_column(ax, store, ix, iy, show_labels=True)
    xm, ym = _world_xy(store, ix, iy)
    z_top = store.z_min + int(col.z_end.max()) * store.cell_z
    z_bot = store.z_min + int(col.z_start[0]) * store.cell_z
    ax.set_title(
        f"(ix={ix}, iy={iy})\n"
        f"world ~ ({xm:.1f}, {ym:.1f}) m\n"
        f"alt {z_bot:.1f}-{z_top:.1f} m  ({len(col)} ivls)",
        fontsize=9,
    )
    fig.subplots_adjust(left=0.19, right=0.955, top=0.885, bottom=0.05)

def write_columns_in_bulk(
    store: ColumnStore,
    out_dir: Path,
    keys: Iterable[tuple[int, int]],
    progress_every: int = 2000,
    fan_out_block: int | None = None,
) -> int:
    """
    Write one PNG per column in `keys`. Returns the count written.
    Logs progress every `progress_every` columns since this is the slow
    path (40 000+ PNGs takes a while).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    # ONE figure + Agg canvas for the whole batch (see _render_column_into):
    # native buffer/canvas/fonts are allocated once, not once per column.
    fig = Figure(figsize=(3.2, 8))
    FigureCanvasAgg(fig)
    n = 0
    made_blocks: set = set()
    for (ix, iy) in keys:
        # Fan out into blk_XXXX_YYYY/ subdirectories on large runs: a single
        # directory holding millions of files cripples NTFS enumeration (and
        # feeds real-time AV scanning). With fan_out_block=100 each block
        # holds at most 100x100 = 10,000 files.
        if fan_out_block:
            dest = out_dir / f"blk_{ix // fan_out_block:04d}_{iy // fan_out_block:04d}"
            if dest not in made_blocks:
                dest.mkdir(parents=True, exist_ok=True)
                made_blocks.add(dest)
        else:
            dest = out_dir
        try:
            _render_column_into(fig, store, ix, iy)
            fig.savefig(dest / _column_filename(ix, iy), dpi=80)
        except Exception:  # Python-level only; native faults are uncatchable
            logger.warning("    column (%d, %d) render failed, skipping",
                           ix, iy, exc_info=True)
            continue
        n += 1
        if n % progress_every == 0:
            logger.info("    rendered %d column PNGs ...", n)
        if n % 500 == 0:
            gc.collect()
    return n

#  -------------------------------------------
#  Public entry: write everything for one tile
#  -------------------------------------------
def write_column_diagnostics(
    store: ColumnStore,
    columns_dir: Path,
    *,
    mode: str = "top",
    top_n: int = 50,
    tile_label: str = "",
    all_cap: int | None = None,
    streaming: bool | None = None,
) -> None:
    """
    Write the `columns/` outputs for one tile.

    Layout produced under `columns_dir`:

        diagnostics/
            columns_samples.png
            columns_top_complex.png
            histogram_intervals.png
            columns_by_class.png
        per_column/                    (only for mode 'all' / 'top')
            col_ix0000_iy0324.png
            ...                        (one per column for 'all',
                                        top-N for 'top')

    @param store The ColumnStore to diagnose; may be memory-mapped.
    @param columns_dir Output directory for the ``diagnostics/`` and
        ``per_column/`` folders.
    @param mode 'all'  -> one PNG for every occupied column (slow, big);
        'top'  -> top ``top_n`` columns by interval count (the default);
        'diag' -> 4 diagnostic PNGs only (samples, top complex, histogram,
        per-class representatives), no per_column folder; 'skip' -> nothing
        is written at all.
    @param top_n Number of columns to keep in the 'top' mode overview and
        per-column pages (default 50).
    @param tile_label Label recorded in the figure titles (default empty).
    @param all_cap Column cap for mode 'all', and a tri-state: ``None`` (the
        default) means the module cap ``_MODE_ALL_MAX_COLUMNS`` (500,000), a
        positive integer sets its own cap, and ``0`` means genuinely
        uncapped. Only the CLI's ``--columns-all-max`` spells this out
        otherwise.
    @param streaming Compute the four figures' inputs out of core (see
        voxelizer.store_streaming) instead of with the whole-store
        reductions. ``None`` (the default) decides by the store: a
        memory-mapped one - the only kind that can be too large to reduce in
        RAM, and the only kind ``stage_runner`` builds - takes the streaming
        path; every other caller keeps the in-RAM one. The selections are the
        same either way (same first-match and tie rules), so this is a memory
        switch, not a semantic one. The per-column PNGs are unaffected:
        rendering one column is a binary search plus a few-element slice,
        which costs the same on a mapped store, so the FULL top-N detail
        survives out of core.
    @throws ValueError when ``mode`` is none of 'all', 'top', 'diag' or
        'skip'.
    """
    if mode == "skip":
        return

    columns_dir = Path(columns_dir)
    diag_dir = columns_dir / "diagnostics"
    diag_dir.mkdir(parents=True, exist_ok=True)

    if streaming is None:
        streaming = store_streaming.is_mmap_backed(store)
    picks = top_keys = count_hist = class_entries = None
    if streaming:
        picks = store_streaming.category_picks(
            store, complex_min=_COMPLEX_MIN, fragmented_min=_FRAGMENTED_MIN,
            complex_label=_COMPLEX_LABEL, fragmented_label=_FRAGMENTED_LABEL)
        top_keys = store_streaming.top_n_by_intervals(store, top_n)
        count_hist = store_streaming.interval_count_histogram(store)
        class_entries = store_streaming.class_first_columns(
            store, _figure_class_codes())

    # Diagnostics always produced when columns output is enabled.
    p = write_samples_figure(store, diag_dir / "columns_samples.png",
                             tile_label=tile_label, picks=picks)
    if p: logger.info("    wrote %s", p)
    # The overview honours --columns-top-n (its interior clamp caps the
    # figure at _TOP_COMPLEX_MAX_PANELS); it used to hardcode n=3 and
    # silently ignore the flag.
    p = write_top_complex_figure(store, diag_dir / "columns_top_complex.png",
                                 n=top_n, tile_label=tile_label, keys=top_keys)
    if p: logger.info("    wrote %s", p)
    p = write_intervals_histogram(store, diag_dir / "histogram_intervals.png",
                                  tile_label=tile_label, count_hist=count_hist)
    if p: logger.info("    wrote %s", p)
    p = write_class_representatives_figure(
        store, diag_dir / "columns_by_class.png", tile_label=tile_label,
        entries=class_entries)
    if p: logger.info("    wrote %s", p)

    if mode == "diag":
        return

    # Per-column PNGs (only for 'all' and 'top').
    pc_dir = columns_dir / "per_column"
    if mode == "all":
        # Stream the keys lazily (the view iterates in blocks) instead of
        # ``list(store.columns.keys())`` - that list is ~15 GB of (ix, iy)
        # tuples on a 116.5M-column store and stays resident for the whole
        # render. len() is O(1) off the key array.
        n_cols = len(store.columns)
        # all_cap: None -> module default; 0 -> genuinely uncapped (informed
        # opt-in via --columns-all-max 0); estimates use measured rates
        # (75 ms + 27 KB per PNG on the reused-figure path - the same
        # 0.075 s the estimate below multiplies by).
        cap = _MODE_ALL_MAX_COLUMNS if all_cap is None else int(all_cap)
        if cap > 0 and n_cols > cap:
            # mode=all was built for single tiles (docstring: "40 000+ PNGs
            # takes a while"); on a merged area store it means MILLIONS of
            # files - weeks of wall-clock, a pathological directory, and
            # heavy native allocation churn. Refuse loudly instead of
            # attempting it; the figures above were still written.
            logger.error(
                "    mode=all refused: %s columns > cap of %s (would be "
                "~%.0f h and ~%.0f GB of PNGs at measured rates). Use "
                "--columns-mode top, or make the informed choice explicit "
                "with --columns-all-max (0 = no cap).",
                f"{n_cols:,}", f"{cap:,}",
                n_cols * 0.075 / 3600, n_cols * 27_000 / 1e9)
            return
        logger.info("    writing %d per-column PNGs (mode=all) - this is slow ...",
                    n_cols)
        n = write_columns_in_bulk(store, pc_dir, store.columns,
                                  fan_out_block=100 if n_cols > 10_000
                                  else None)
        logger.info("    wrote %d per-column PNGs in %s", n, pc_dir)
    elif mode == "top":
        keys = top_keys if top_keys is not None else _top_n_by_intervals(store, top_n)
        n = write_columns_in_bulk(store, pc_dir, keys)
        logger.info("    wrote %d per-column PNGs (top by intervals) in %s",
                    n, pc_dir)
    else:
        raise ValueError(
            f"unknown columns mode {mode!r} - pick 'all', 'top', 'diag', or 'skip'"
        )
