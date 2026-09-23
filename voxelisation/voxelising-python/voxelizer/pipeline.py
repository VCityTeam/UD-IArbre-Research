"""
Per-tile ("single") voxelizer workflow.
@ingroup t3_orchestr


    process_single_tile()  Voxelize one LAZ, write its PNGs + stats.txt
                           into ``out_dir``. The per-tile folder in
                           <run_dir>/<tile_stem>/ is the CALLER's doing:
                           this function appends nothing to the path it
                           is given.

This is the common, day-to-day path: we almost always work on a per-tile /
per-LAZ basis.

The 3-D visualizer (visualizer3d.py / pyvista) is intentionally not
imported here; the .bat workflows do not need it.
"""

from __future__ import annotations
import logging
from pathlib import Path
import numpy as np

from .data_structures import ColumnStore
from .voxelize import voxelize_laz

logger = logging.getLogger(__name__)

# The four rendering modes the visualization.plot_tile_map supports.
MODES: tuple[str, ...] = (
    "max_height",
    "max_points_class",
    "orthophoto_class",
    "n_intervals",
)

#  --------------------
#  stats.txt formatting
#  --------------------
def _format_stats(stats: dict, header_lines: list[str]) -> str:
    """Render a stats dict (same shape as ColumnStore.stats()) as human-readable text,
    with extra header lines."""
    out: list[str] = []
    out.extend(header_lines)
    out.append("")
    out.append("Global stats:")
    for k in ("n_columns", "n_intervals", "n_points", "avg_intervals_per_column"):
        out.append(f"  {k:30s} {stats[k]:>12}")
    out.append("")
    out.append("Points per class:")
    total_pts = max(stats["n_points"], 1)
    for name, count in stats["points_per_class"].items():
        pct = 100 * count / total_pts
        out.append(f"  {name:25s} {count:>12,d}  ({pct:5.1f} %)")
    out.append("")
    out.append("Intervals per class:")
    total_iv = max(stats["n_intervals"], 1)
    for name, count in stats["intervals_per_class"].items():
        pct = 100 * count / total_iv
        out.append(f"  {name:25s} {count:>12,d}  ({pct:5.1f} %)")

    # -- Vertical extent (absolute altitude, metres) --
    if stats.get("z_min_m") is not None:
        z_lo = stats["z_min_m"]
        z_hi = stats["z_max_m"]
        out.append("")
        out.append("Vertical extent (absolute altitude):")
        out.append(f"  {'lowest altitude (m)':30s} {z_lo:>12.2f}")
        out.append(f"  {'highest altitude (m)':30s} {z_hi:>12.2f}")
        out.append(f"  {'altitude span (m)':30s} {z_hi - z_lo:>12.2f}")

    # -- Column heights (relative: top - bottom of occupied voxels) --
    def _col_loc(col: dict | None) -> str:
        """Location suffix for an extreme-column record: ``"  at tile T (ix, iy)"`` when the record names a tile, ``"  at (ix, iy)"`` otherwise, empty for a missing record."""
        if not col:
            return ""
        tile = col.get("tile")
        base = f"({col['ix']}, {col['iy']})"
        return f"  at tile {tile} {base}" if tile else f"  at {base}"

    if stats.get("column_height_max_m") is not None:
        out.append("")
        out.append("Column heights (top - bottom of occupied voxels):")
        short_c = stats.get("shortest_column")
        tall_c = stats.get("tallest_column")
        out.append(
            f"  {'shortest column (m)':30s} {stats['column_height_min_m']:>12.2f}"
            f"{_col_loc(short_c)}"
        )
        out.append(
            f"  {'tallest column (m)':30s} {stats['column_height_max_m']:>12.2f}"
            f"{_col_loc(tall_c)}"
        )
        if stats.get("column_height_mean_m") is not None:
            out.append(f"  {'mean column height (m)':30s} "
                       f"{stats['column_height_mean_m']:>12.2f}")
        if stats.get("column_height_median_m") is not None:
            out.append(f"  {'median column height (m)':30s} "
                       f"{stats['column_height_median_m']:>12.2f}")

    # -- Column complexity (interval count per column) --
    if stats.get("intervals_per_column_max"):
        out.append("")
        out.append("Column complexity (intervals per column):")
        out.append(f"  {'min intervals / column':30s} "
                   f"{stats['intervals_per_column_min']:>12,d}")
        out.append(f"  {'max intervals / column':30s} "
                   f"{stats['intervals_per_column_max']:>12,d}")
        n_single = stats.get("n_single_interval_columns", 0)
        n_cols = max(stats.get("n_columns", 1), 1)
        pct = 100 * n_single / n_cols
        out.append(f"  {'single-interval columns':30s} "
                   f"{n_single:>12,d}  ({pct:5.1f} %)")

    # -- Shared voxels (cross-class z-overlaps; data keeps every class) --
    if stats.get("n_overlap_columns") is not None:
        n_cols = max(stats.get("n_columns", 1), 1)
        ov_c = stats.get("n_overlap_columns", 0)
        ov_i = stats.get("n_overlap_intervals", 0)
        out.append("")
        out.append("Shared voxels (cross-class overlaps in one column):")
        out.append(f"  {'overlapping intervals':30s} {ov_i:>12,d}")
        out.append(f"  {'columns with class overlap':30s} "
                   f"{ov_c:>12,d}  ({100 * ov_c / n_cols:5.1f} %)")

    return "\n".join(out) + "\n"

#  -------------------------------------------------------------------
#  Raw per-tile arrays (one number per column, for later colorization)
#  -------------------------------------------------------------------
def _raw_tile_arrays(store: ColumnStore) -> dict | None:
    """
    Compute the five per-pixel raw arrays for a tile (no normalization, no
    colorization): the four map-mode arrays plus `min_height` (bottom voxel
    index, kept for the combined absolute-height view). Sentinel values: -1
    in `max_height`, `min_height`, `orthophoto_class`, and
    `max_points_class` means "no data in this column"; 0 in `n_intervals`
    means the same. The row/column layout matches the
    existing visualization.render_tile_map (north up).

    Returns None for an empty store, otherwise a dict with the arrays and
    enough geometry to place the tile inside a larger image.
    """
    if not store.columns:
        return None

    # key_bounds() reads the packed key array directly - the old
    # np.array(list(keys())) built ~15 GB of (ix, iy) tuples on a 116.5M-column
    # area store just to take four extrema (crash diagnosis).
    ix_min, iy_min, ix_max, iy_max = store.key_bounds()
    W = int(ix_max - ix_min + 1)
    H = int(iy_max - iy_min + 1)

    max_height = np.full((H, W), -1, dtype=np.int32)
    min_height = np.full((H, W), -1, dtype=np.int32)  # bottom voxel idx
    n_intervals = np.zeros((H, W), dtype=np.int32)
    orthophoto = np.full((H, W), -1, dtype=np.int16)
    max_pts_cls = np.full((H, W), -1, dtype=np.int16)

    for (ix, iy), col in store.columns.items():
        row = int(iy_max - iy)
        c = int(ix - ix_min)
        # Highest interval by z_end, not the last by z_start: the two differ
        # when a cross-class overlap lets an earlier interval end higher, and
        # both the height raster and the seen-from-above class must follow
        # the interval that actually reaches highest. Ties at the top resolve
        # to the smallest class code (the set-function rule shared with
        # column_summaries), never to a list position.
        m = int(col.z_end.max())
        max_height[row, c] = m
        min_height[row, c] = int(col.z_start[0])
        n_intervals[row, c] = len(col)
        orthophoto[row, c] = int(col.cls[col.z_end == m].min())
        if len(col.cls) == 1:
            max_pts_cls[row, c] = int(col.cls[0])
        else:
            # Weighted bincount: total points per class within this column.
            bc = np.bincount(
                col.cls.astype(np.int64),
                weights=col.count.astype(np.int64),
                minlength=256,
            )
            max_pts_cls[row, c] = int(bc.argmax())

    return {
        "max_height": max_height,
        "min_height": min_height,
        "n_intervals": n_intervals,
        "orthophoto_class": orthophoto,
        "max_points_class": max_pts_cls,
        # Geometry: where this small array sits in the world.
        "ix_min": int(ix_min),
        "iy_min": int(iy_min),
        "ix_max": int(ix_max),
        "iy_max": int(iy_max),
        "x_min_m": float(store.x_min),
        "y_min_m": float(store.y_min),
        "z_min_m": float(store.z_min),
        "cell_xy": float(store.cell_xy),
        "cell_z": float(store.cell_z),
    }

#  ------------------------
#  Public: process one tile
#  ------------------------
def process_single_tile(
    laz_path: Path | str,
    out_dir: Path | str,
    *,
    cell_xy: float = 0.5,
    cell_z: float = 0.5,
    save_raw_to: Path | str | None = None,
    save_store_to: Path | str | None = None,
    keep_classes: set[int] | None = None,
    columns_mode: str = "diag",
    columns_top_n: int = 50,
    columns_all_max: int | None = None,
    height_mode: str = "default",
    return_store: bool = False,
) -> dict | tuple[dict, ColumnStore]:
    """
    Voxelize one tile and write its outputs.

    Outputs (under ``out_dir``, which is typically ``RunN/<tile_stem>/``):
        ``<tile_stem>_max_height.png``
        ``<tile_stem>_max_points_class.png``
        ``<tile_stem>_orthophoto_class.png``
        ``<tile_stem>_n_intervals.png``
        stats.txt
        columns/                          (only if columns_mode != 'skip')
            diagnostics/
                columns_samples.png
                columns_top_complex.png
                histogram_intervals.png
                columns_by_class.png
            per_column/                   (only for 'top' / 'all')
                col_ix0000_iy0324.png ...

    If `save_raw_to` is given, also saves a compressed .npz of the five
    raw per-pixel arrays (the four map arrays plus min_height) into that
    directory. NOTHING IN THIS TREE READS THOSE FILES: no caller passes the
    parameter and there is no combined-view assembler here. It is kept
    because the arrays it writes are the only per-pixel rasters the run
    produces in a re-readable form, and re-deriving them costs a full pass
    over the store - but a caller who sets it is writing for a consumer they
    supply themselves.

    @param laz_path One ``.laz``/``.las`` tile to voxelize.
    @param out_dir Output directory, typically ``RunN/<tile_stem>/``.
    @param cell_xy Horizontal voxel size in metres (default 0.5).
    @param cell_z Vertical voxel size in metres (default 0.5).
    @param save_raw_to Directory for a compressed .npz of the five raw
        per-pixel arrays (the four map arrays plus min_height). ``None``
        (the default) writes nothing.
    @param save_store_to When set, persist the voxel grid itself as a raw
        (memory-mappable) store directory at that path, via
        ``ColumnStore.save_dir`` - the ``store_raw/`` directory the area
        pipeline's isolated output stages attach to. ``None`` (the default)
        writes nothing. This is the grid, not the raster arrays of
        ``save_raw_to``: the two are unrelated. The caller is responsible for
        writing a ``run_params.json`` beside it when the directory is meant to
        be re-enterable by ``area_cli --resume-from-store``.
    @param keep_classes When set, keep only points carrying these ASPRS codes;
        ``None`` (the default) keeps every class.
    @param columns_mode 'diag' / 'top' / 'all' / 'skip' - controls the
        per-column PNGs and the diagnostic figures (default 'diag'). 'diag'
        writes the four diagnostic figures only; 'skip' disables the whole
        ``columns/`` folder.
    @param columns_top_n How many columns to keep in 'top' mode (default 50;
        ignored otherwise).
    @param columns_all_max Cap on the per-column PNGs in 'all' mode, and a
        tri-state: ``None`` (the default) applies the module default cap
        (500,000); ``0`` removes the cap entirely; any positive value is that
        cap. Ignored outside 'all' mode.
    @param height_mode 'default' / 'relative' / 'absolute' - the vertical
        reference of the max_height map only (see
        ``visualization.render_tile_map``; default 'default'). It is also
        recorded in the stats.txt header.
    @param return_store When False (the default) the return value is just
        the stats dict. When True it is the tuple ``(stats, store)`` so a
        caller that also needs the 3-D viewer can reuse the ``ColumnStore``
        this function already built instead of decoding the LAZ a second
        time.
    @return The stats dict, or ``(stats, store)`` when ``return_store`` is
        set.
    """
    laz_path = Path(laz_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tile_stem = laz_path.stem

    store = voxelize_laz(laz_path, cell_xy=cell_xy, cell_z=cell_z,
                         keep_classes=keep_classes)
    stats = store.stats()

    # --- stats.txt ---
    header = [
        f"File: {laz_path.name}",
        f"cell_xy: {cell_xy} m, cell_z: {cell_z} m",
        f"height mode: {height_mode}",
    ]
    (out_dir / "stats.txt").write_text(_format_stats(stats, header), encoding="utf-8")
    logger.info("  wrote %s", out_dir / "stats.txt")

    # --- per-tile PNGs (use the existing renderer for per-tile normalization) ---
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from .visualization import plot_tile_map

    # One summaries pass shared by all four maps. The need_dom test reads as
    # a condition but MODES is a module constant that contains
    # "max_points_class", so it is True on every run; it is written this way
    # so removing that mode from MODES also drops the dominant-class
    # reduction, rather than leaving a silent waste behind.
    summ = store.column_summaries(need_dom=("max_points_class" in MODES))
    for mode in MODES:
        out = out_dir / f"{tile_stem}_{mode}.png"
        fig, ax = plt.subplots(figsize=(8, 8))
        plot_tile_map(store, mode=mode, ax=ax, height_mode=height_mode, summ=summ)
        fig.tight_layout()
        fig.savefig(out, dpi=120)
        plt.close(fig)
        logger.info("  wrote %s", out)

    # --- columns/ folder (diagnostics + optional per-column PNGs) ---
    if columns_mode != "skip":
        from .column_diagnostics import write_column_diagnostics
        write_column_diagnostics(
            store, out_dir / "columns",
            mode=columns_mode, top_n=columns_top_n,
            all_cap=columns_all_max,
            tile_label=tile_stem,
        )

    # --- persist the voxel grid as a raw store directory, if requested ---
    if save_store_to is not None:
        save_store_to = Path(save_store_to)
        save_store_to.parent.mkdir(parents=True, exist_ok=True)
        store.save_dir(save_store_to)
        logger.info("  wrote raw store -> %s", save_store_to)

    # --- cache raw arrays for the combined view, if requested ---
    if save_raw_to is not None:
        raw = _raw_tile_arrays(store)
        if raw is not None:
            save_raw_to = Path(save_raw_to)
            save_raw_to.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(
                save_raw_to / f"{tile_stem}.npz",
                max_height=raw["max_height"],
                min_height=raw["min_height"],
                n_intervals=raw["n_intervals"],
                orthophoto_class=raw["orthophoto_class"],
                max_points_class=raw["max_points_class"],
                # Pack geometry as a tiny array so we don't have to manage pickled metadata.
                meta_i=np.array(
                    [raw["ix_min"], raw["iy_min"], raw["ix_max"], raw["iy_max"]],
                    dtype=np.int32,
                ),
                # meta_f: [x_min, y_min, cell_xy, cell_z, z_min]. z_min would let
                # a combined 'absolute' view recover true altitude across tiles
                # (no consumer of these files exists in the current tree).
                meta_f=np.array(
                    [raw["x_min_m"], raw["y_min_m"], raw["cell_xy"],
                     raw["cell_z"], raw["z_min_m"]],
                    dtype=np.float64,
                ),
            )
    return (stats, store) if return_store else stats
