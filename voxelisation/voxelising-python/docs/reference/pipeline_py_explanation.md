# `pipeline.py` - Full Explanation

## Overview

`pipeline.py` is the per-tile voxelizer workflow for the IA.rbre project. It converts raw `.laz` / `.las` LiDAR point cloud files into per-tile PNG images (four visualization modes) and a statistics summary (`stats.txt`).

It deliberately does **not** import the 3-D visualizer (`visualizer3d.py` / PyVista) - the `.bat` workflows it was built for only need 2-D image output.

---

## Module-Level Constants and Imports

| Symbol | Value | Meaning |
|--------|-------|---------|
| `MODES` | `("max_height", "max_points_class", "orthophoto_class", "n_intervals")` | The four rendering modes supported for 2-D PNG output. |

Imports: `logging`, `Path`, `numpy`. From the local package: `ColumnStore` (from `data_structures`), `voxelize_laz` (from `voxelize`).

---

## `_format_stats(stats, header_lines)`

Takes the dictionary produced by `ColumnStore.stats()` and renders it as a human-readable text block with:

- **Header lines** (file name, cell sizes, etc.)
- **Global stats:** `n_columns`, `n_intervals`, `n_points`, `avg_intervals_per_column`
- **Points per class:** class name, raw count, percentage
- **Intervals per class:** class name, raw count, percentage

Used to produce `stats.txt` files for individual tiles.

---

## `_raw_tile_arrays(store)`

Computes five raw per-pixel NumPy arrays for a single tile: the four map-mode arrays plus `min_height`, kept for the combined absolute-height view. One integer per column (i.e. per pixel in the output image). No normalization or colorization is done here - just the raw numbers.

| Array | Meaning | Sentinel value |
|-------|---------|---------------|
| `max_height` | Highest occupied voxel index in each column. | `-1` = empty column |
| `min_height` | Lowest occupied voxel index in each column. | `-1` = empty column |
| `n_intervals` | Number of vertical RLE intervals in each column. | `0` = empty column |
| `orthophoto_class` | Class code of the topmost interval in each column. | `-1` = empty column |
| `max_points_class` | Class code with the most total points in the column (weighted bincount). | `-1` = empty column |

Returns `None` for an empty store, otherwise a dict that includes the array geometry metadata (`ix_min`, `iy_min`, `ix_max`, `iy_max`, `x_min_m`, `y_min_m`, `cell_xy`, `cell_z`).

Row order is north-up (`row = iy_max - iy`), matching the existing `visualization.render_tile_map`.

**How `max_points_class` works:** If a column has only one interval, that class wins. Otherwise, a weighted `np.bincount` sums the total point count per class across all intervals in the column, and the class with the highest sum wins.

---

## Where the colouring actually happens: `visualization.py`

`pipeline.py` computes numbers and delegates every pixel decision to `visualization.py`. The raster machinery lives there, split into three stages so that the sharded area pipeline can mosaic many per-tile stores into one bounded set of rasters and colorize only at the end.

### `visualization._class_lut()`

Builds a `(256, 3)` uint8 RGB lookup table. All 256 rows start at `[128, 128, 128]` (grey), then every code present in `CLASS_COLORS` (from `classes_config.py`) overwrites its row. Unmapped class codes therefore render as the grey fallback, not as black - black is reserved for "no data", which the colorizer masks separately.

### `visualization.make_rasters(H, W)` and `scatter_summaries(rasters, summ, *, ix_min, iy_max, agg=1)`

`make_rasters` allocates the five scalar rasters every map mode reads: `top` (int32, sentinel `INT32_MIN`), `bot` (int32, sentinel `INT32_MAX`), `niv` (int32, zeros), `top_cls` and `dom_cls` (int16, sentinel `-1`).

`scatter_summaries` scatters one store's `ColumnStore.column_summaries()` output into those rasters with numpy scatter operations - no Python loop over columns. Rows are north-up (`row = (iy_max - iy) // agg`). Reductions are `np.maximum.at` for `top`, `np.minimum.at` for `bot`, `np.add.at` for `niv`, and plain fancy assignment (last write wins as the representative) for the two class rasters, so the function is safe to call repeatedly with different stores that share a frame. Columns falling outside the frame are dropped with a warning rather than wrapped onto the opposite edge.

### `visualization.colorize_rasters(rasters, mode, height_mode="default")`

Turns the scalar rasters into the uint8 `(H, W, 3)` image for one mode:

| Mode | How it works |
|------|-------------|
| `max_height` | Grayscale from the `top` raster. `height_mode` picks the vertical reference: `"default"` auto-contrasts between the lowest and highest tops, `"relative"` uses `top - bot` per column (nDSM/CHM style), `"absolute"` scales the tops from the tile's lowest occupied voxel. Cells where `top` is still the sentinel stay black. |
| `n_intervals` | Grayscale from the `niv` raster, scaled by its own maximum. |
| `orthophoto_class` / `max_points_class` | Looks each class code up in `_class_lut()`; `orthophoto_class` reads `top_cls`, `max_points_class` reads `dom_cls`. Cells with the `-1` sentinel keep the initial black. |

Any other mode raises `ValueError`.

### `visualization.aggregation_factor(native_pixels, max_pixels)` and the pixel budget

`render_tile_map` will not allocate an unbounded raster. `aggregation_factor` returns the smallest integer `s` with `native_pixels / s^2 <= max_pixels`; when it exceeds 1 the columns are aggregated into coarser pixels and a warning is logged, because the map is then a downsampled overview. `DEFAULT_MAX_PIXELS` is 256 million.

---

## Writing the PNGs

`pipeline.py` has no PNG writer of its own. `process_single_tile` selects the Agg backend, calls `visualization.plot_tile_map()` (which wraps `render_tile_map` = `make_rasters` + `scatter_summaries` + `colorize_rasters`, then `ax.imshow`) and lets matplotlib's `fig.savefig(out, dpi=120)` write the file, so each map keeps its axis labels and title.

---

## `process_single_tile(laz_path, out_dir, ...)`

The main single-tile entry point. Produces:

| Output | Description |
|--------|-------------|
| `<tile>_max_height.png` | Grayscale height map |
| `<tile>_max_points_class.png` | Color-coded class map (by point count) |
| `<tile>_orthophoto_class.png` | Color-coded class map (by topmost class) |
| `<tile>_n_intervals.png` | Grayscale interval-count map |
| `stats.txt` | Text summary of all metrics |
| `columns/` (optional) | Per-column diagnostic PNGs (if `columns_mode != 'skip'`) |

### Step-by-step:

1. Creates the output directory.
2. Calls `voxelize_laz(laz_path, cell_xy=cell_xy, cell_z=cell_z)` to convert the LAZ into a `ColumnStore`.
3. Calls `store.stats()` to get the statistics dictionary.
4. Writes `stats.txt` via `_format_stats()`.
5. For each of the four modes, calls the matplotlib-based `plot_tile_map()` (from `visualization.py`) to produce per-tile PNGs with per-tile normalization.
6. Optionally calls `write_column_diagnostics()` for detailed column-level PNGs.

### Parameters:

- `columns_mode`: `'all'` / `'top'` / `'diag'` / `'skip'` - controls per-column PNGs and diagnostics. `'skip'` disables the whole `columns/` folder.
- `columns_top_n`: How many columns to keep in `'top'` mode.

---

## Data Flow Diagram

```
  .laz file
      |
      v
  voxelize_laz()         -> ColumnStore
      |
      +-> store.stats() -> stats.txt
      |
      +-> plot_tile_map() -> <tile>_<mode>.png  (x4)
      |
      +-> write_column_diagnostics() -> columns/  (optional)
```

---

## Key Design Decisions

1. **Sentinel values**: `-1` means "no data" in height/class arrays; `0` means "no data" in the interval-count array. This is important for masking - empty columns stay black in the final image.

2. **Masked merge**: When rendering, only pixels where the tile has actual data (>= 0 or > 0 depending on mode) are written. This keeps unoccupied cells black.

3. **Per-tile normalization**: Each tile's color scale is normalized to its own min/max, ensuring good contrast regardless of the absolute values.
