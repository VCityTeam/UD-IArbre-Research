# Single Tile Workflow

## Overview

The voxelizer processes one `.laz` file at a time. It reads the point cloud,
turns it into a compressed column store, and writes PNG maps and a `stats.txt`
into a folder like `Run1/18410_51825/`. You can also ask it to draw individual
column cross-sections for debugging.

## Entry Point

```powershell
# CLI
python -m voxelizer single inputs/laz/18410_51825.laz -o outputs/Run4/single --cell-xy 1.0 --cell-z 0.5 --columns-mode diag

# Or via the .bat (opens the area-voxelizer GUI, which includes a single-file tab)
scripts/run_area.bat
```

## Data Flow

```
1. READ LAZ
   io_laz.py: read_laz(path)
   -----------------------------------------
   Input:  .laz file on disk
   Output: x, y, z (float64 arrays), cls (uint8 array)
   Effect: 25 B per point in the returned arrays, so ~1.5 GB for a
           60M-point tile. read_laz_chunks(path, chunk_size) is the
           escape hatch: the default 5M-point chunk is ~125 MB, and the
           whole-file and chunked paths share stage 2 below, so they
           produce identical stores.

2. VOXELIZE
   voxelize.py: voxelize(x, y, z, cls, cell_xy, cell_z)
   -----------------------------------------
   Steps:
     a. Optional class filter (keep_classes)
     b. Compute bounding box origin (x_min, y_min, z_min)
     c. Compute voxel indices: ix, iy, iz = floor((coord - min) / cell)
     d. Lexsort by (ix, iy, cls, iz) - the run-forming order, class_first,
        which is the default. VOXELIZER_RUN_ORDER=height_first sorts
        (ix, iy, iz, cls) instead; both give the same per-voxel classes and
        per-class point counts and differ only in run structure.
        height_first is the compatibility order and the one every result
        produced before the switch was written with.
     e. Detect unique cells: group by (ix, iy, iz, cls) with point counts
     f. Detect RLE intervals: merge consecutive iz with same (ix, iy, cls)
     g. Group the intervals by column and record each column's offset
   Output: ColumnStore (x_min, y_min, z_min, cell_xy, cell_z, plus the six
           flat arrays: _keys/_off per column, _zs/_ze/_cl/_ct per interval -
           16 B per column, 13 B per interval)

3. COMPUTE STATS
   data_structures.py: store.stats()
   -----------------------------------------
   Steps:
     a. Concatenate all column cls + count arrays
     b. np.bincount for per-class point totals and interval totals
     c. Compute avg_intervals_per_column
   Output: stats dict

4. WRITE stats.txt
   pipeline.py: _format_stats(stats, header)
   -----------------------------------------
   Output: <out_dir>/stats.txt

5. WRITE 4 TILE MAPS
   visualization.py: plot_tile_map(store, mode)
   -----------------------------------------
   Modes: max_height, max_points_class, orthophoto_class, n_intervals
   Each mode: render_tile_map() -> numpy array -> matplotlib imshow -> PNG
   Output: <out_dir>/<tile_stem>_<mode>.png (4 files)

6. WRITE COLUMNS (optional)
   column_diagnostics.py: write_column_diagnostics(store, ...)
   -----------------------------------------
   Depends on columns_mode:
     'diag' -> 4 diagnostic PNGs (samples, top_complex, by_class, histogram)
     'top'  -> 4 diagnostics + top N column PNGs
     'all'  -> 4 diagnostics + one PNG per occupied column. A dense 500 m
               tile at the default 0.5 m cell has roughly 800k occupied
               columns, so this mode is capped: --columns-all-max (area
               runs) defaults to 500,000 columns and refuses above it,
               quoting the estimated hours and GB; 0 disables the cap.
               Output fans out into blk_XXXX_YYYY/ subdirectories
               automatically on large runs.
     'skip' -> nothing
   Output: <out_dir>/columns/diagnostics/*.png
           <out_dir>/columns/per_column/*.png (if mode=top/all)
```

## Output Structure

```
outputs/Run4/single/
  18410_51825/
    stats.txt
    18410_51825_max_height.png
    18410_51825_max_points_class.png
    18410_51825_orthophoto_class.png
    18410_51825_n_intervals.png
    columns/
      diagnostics/
        columns_samples.png
        columns_top_complex.png
        columns_by_class.png
        histogram_intervals.png
      per_column/
        col_ix0339_iy0319.png
        col_ix0353_iy0286.png
        ...
  shards/                       (only with --shards; one level up from <stem>/)
    18410_51825.npz
    manifest.json
```

With `--shards` the tile's `ColumnStore` is also persisted next to the tile
folder as `<output-dir>/shards/<tile_stem>.npz` plus a `manifest.json`, the
same shard format an area run writes (`voxelizer.shards/2`). The save is
`ColumnStore.save()` of the store the run already built - no second decode -
and the manifest's one record carries the same extents and counts the area
pipeline records per tile, so the folder is a valid input to
`merge_streaming`, `shard_diagnostics` and `archive_cli`. Because one run
writes one shard, each tile wants its own `--output-dir`; the store keeps the
tile's own grid origin, so a single-tile shard is self-contained but not
mergeable with a shard on a different origin. See `how-to-use.md` section 2 and
`execution-steps.md` section 1.

## Beyond one tile: the area and sharded workflows

The same core serves two multi-tile workflows, both reached through
`python -m voxelizer.area_cli area`:

- **In-process area** (`area.process_area`, optionally `--stream`): every tile
  intersecting the bounding box lands on one shared grid with a single
  vertical datum, and the result is one merged `ColumnStore`. Peak memory
  during voxelization is the whole area. The output stages that follow are
  not: with `--isolate-stages` (on by default) each of `stats`,
  `persist_npz`, `maps`, `col_diag` and the 3-D viewers runs in its own
  child process attached to an on-disk `store_raw/` by memory map, which
  both frees the parent's in-heap store during rendering and contains a
  native crash in one stage to that stage. The reductions those stages run
  are written as out-of-core folds in `store_streaming.py`, so their memory
  follows the fold's batch size rather than the store's length: `stats` is a
  `StatsFold` with histogram-based medians, `col_diag` folds its histogram,
  top-N and category picks, and the map stage sizes its rasters from a
  banded pass over the packed keys (`key_frame`) and fills them from batched
  column summaries (`summaries_batches`). `--stage-timeout` sets a wall-clock limit per
  stage and defaults to 0, meaning no limit: a fixed limit cannot tell a big
  stage from a stuck one, because the value that separates them scales
  with the store. `--only-stage` restricts the run
  to named stages, and `--resume-from-store` re-runs them later from a
  `store_raw/` kept with `--keep-raw-store`.
- **Sharded area** (`--shard` -> `sharding.run_area_sharded`): each tile is
  voxelized into its own `.npz` under `shards/`, so peak memory is one tile.
  This is the path for areas beyond RAM. `stats.txt`, the four mosaic maps,
  the full and ROI 3-D views and everything under `columns/` are computed
  from the shards themselves, without any merged store, by
  `shard_diagnostics.py`: a column lives in exactly one shard, because the
  tiles partition the grid, so every reduction behind those artifacts is a
  fold over disjoint parts and peak memory is one shard plus the
  accumulators. It is also the crash-resilient path: `--resume-shards`
  continues an interrupted run from its finished shards, `--isolate-tiles`
  contains native decoder crashes to a single tile via the `shard_worker`
  child process (failures listed in `shards/failed_tiles.json`), and
  `--retry-lazrs` adds one second-opinion decode with the single-thread
  lazrs backend. Shard writes are atomic, so a stop or a crash never
  corrupts finished progress.

What still needs `--merge-shards` is the merged store and what is exported
from it: `area.npz`, `area_raw.npz`, `store_raw/` and the streaming viewer.
Asking for one of those without the merge is refused before the first tile
is read - `--keep-raw-store`, `--keep-area-raw`, `--only-stage`,
`--stage-timeout` and `--viz3d-stream` are the five flags on that list. The
merge itself (`merge_streaming.py`) is out of core: `store_raw/` is
assembled on disk one key band at a time and the stages attach to it by
memory map, so peak RAM follows the band budget rather than the size of the
merged store. `--merge-band-intervals` is that budget, in intervals per
band, default 40,000,000 for about 1.4 GB peak.

See `diagrams/pipeline_dataflow_diagram.md` (Path C), `how-to-use.md` in the
package root, and `problem-solving.md` sections 4.1 and 6.4, which ships
under `docs/` at the delivery root.

## Features

- Tkinter GUI for file selection and area-by-coordinates (`gui_area.py`)
- Direct Python return of the stats dict (useful for programmatic access)
- Supports `--cell-xy` and `--cell-z` parameters
- Auto-increments `Run<N>` to avoid overwriting previous results
- Same `columns_mode` options (`all`/`top`/`diag`/`skip`)
- `--shards` saves the tile's store as `shards/<tile_stem>.npz` +
  `shards/manifest.json`, the same shard format an area run writes, so one file
  becomes a shard the shard tooling can read
- `--viz3d` renders the singleton Three.js viewers; `--viz3d-stream` renders
  the streaming viewer, which carries every interval and bounds the GPU
  working set at view time instead of decimating the payload
- A finished run carries its own `view_stream.cmd` (and `view_cesium.cmd` /
  `view_itowns.cmd` beside a tileset), generated by `launchers.py`, so
  opening a result is a double-click; `python -m voxelizer serve DIR` is the
  same server invoked by hand

## Post-processing a finished store

The semantic passes are store-to-store and reachable from the command line
through `postprocess_cli`, so a cleaned store feeds every existing output
path unchanged:

```powershell
python -m voxelizer.postprocess_cli area.npz area_clean.npz --min-points 4 --morph --absorb --resolve
```

The passes always run in the order min-points -> morph -> absorb -> resolve
-> group, whichever order the flags are given in. `--group` re-groups
afterwards, `--group-gap M` bounds the vertical gap it may bridge, and
`--dry-run` reports what each pass would move without writing anything.
