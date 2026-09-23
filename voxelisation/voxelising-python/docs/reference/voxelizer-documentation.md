# UD-IArbre Voxelizer: From LiDAR Point Cloud to Column-Compressed RLE

## A Complete Guide to the Pipeline, the Data Structures, and the Design Decisions

*Written for: internship supervisors, GitHub contributors, and anyone learning about 3D urban data processing*

---

## Table of Contents

1. [What This Document Is](#1-what-this-document-is)
2. [The Big Picture: What Are We Building?](#2-the-big-picture-what-are-we-building)
3. [What Is LiDAR and Why Do We Need a Voxelizer?](#3-what-is-lidar-and-why-do-we-need-a-voxelizer)
4. [The Pipeline: A Bird's-Eye View](#4-the-pipeline-a-birds-eye-view)
5. [Stage 0: Reading the Point Cloud](#5-stage-0-reading-the-point-cloud)
6. [Stage 1: From Points to Voxels](#6-stage-1-from-points-to-voxels)
7. [Stage 2: From Voxels to Runs (The RLE)](#7-stage-2-from-voxels-to-runs-the-rle)
8. [The Data Structure: ColumnStore](#8-the-data-structure-columnstore)
9. [What the RLE Looks Like in Practice](#9-what-the-rle-looks-like-in-practice)
10. [Post-Processing: Grouping and Merging](#10-post-processing-grouping-and-merging)
11. [Design Decisions and Trade-offs](#11-design-decisions-and-trade-offs)
12. [Why This Encoding Is the Right Substrate](#12-why-this-encoding-is-the-right-substrate)
13. [Downstream: Refinement, Analysis, Reconstruction & Multi-Viewer Export](#13-downstream-refinement-analysis-reconstruction--multi-viewer-export)
14. [Glossary](#14-glossary)

---

## 1. What This Document Is

This document has three purposes:

**As the technical companion to the internship report**, it records the thought process, design decisions, and technical architecture of the voxelizer component of the UD-IArbre research project. It is written to demonstrate understanding of the problem space and justify the chosen approach.

**As GitHub documentation**, it is the reference for anyone cloning the repository, trying to understand the code, or contributing to the project. It explains *what* the code does and *why* it is structured that way.

**As a teaching document**, it assumes the reader is smart but not yet familiar with LiDAR processing, voxelization, or run-length encoding. Every concept is introduced from first principles. If you have never worked with 3D point clouds before, you should still be able to follow along.

---

## 2. The Big Picture: What Are We Building?

### The Research Context

IA.rbre (Intelligence Artificielle pour l'arbre) is a research project that studies urban vegetation, its interaction with buildings, and its effects on microclimate - shade, cooling, air quality. To do this, we need a 3D model of the city that answers questions like:

- *"Does this building facade receive direct sunlight at 14:00 in July?"*
- *"How much tree canopy covers this street?"*
- *"If we plant a tree here, where will its shadow fall?"*

### The Data Source

We work with **LiDAR** (Light Detection and Ranging) data from IGN, the French national mapping agency. These are massive files - a full 500m x 500m tile of Lyon contains roughly 16 to 60 million points, and 16.2 million is the median over the 2,842-tile Grand Lyon corpus. Read that range as the typical tile, not as a floor: 58 tiles hold under a million points and the smallest holds 7,676, because a tile at the edge of the delivery is clipped to whatever slice of its square the flight actually covered (the 7,676-point tile carries 28 m by 24 m of ground). Each point has:

- `x, y` - horizontal position in RGF93/CC46 (EPSG:3946) coordinates (meters)
- `z` - altitude above sea level (meters)
- `classification` - a semantic label (ground, vegetation, building, water, etc.)

### The Problem

Raw LiDAR points are **unstructured**. They are just a cloud of individually measured dots. You cannot ask *"what is at this location?"* efficiently because you would have to search through millions of points every time. The voxelizer solves this by converting the point cloud into a **structured, compressed, queryable 3D grid**.

### The Output

A **ColumnStore** - a column-compressed voxel grid where each vertical column stores a run-length-encoded sequence of semantic labels. Think of it as a city represented as a grid of pillars, where each pillar is annotated: *"from 0 to 5 meters: ground, from 5 to 20 meters: vegetation, from 20 to 35 meters: building."*

---

## 3. What Is LiDAR and Why Do We Need a Voxelizer?

### LiDAR in Plain Terms

Imagine an airplane flying over a city with a very fast laser scanner. The laser sends out millions of pulses of light. Each pulse hits something - the ground, a tree, a roof, a car - and bounces back. By measuring the time it takes for the light to return, the system knows the exact 3D position of every surface it hit.

The result is a **point cloud**: a list of millions of (x, y, z) coordinates, each with a classification code telling you what kind of surface produced the return.

### The Classification Codes (ASPRS Standard)

| Code | Meaning | Examples |
|------|---------|----------|
| 2 | Ground | Pavement, bare earth, terrain |
| 3 | Low vegetation | Grass, crops (< 50cm) |
| 4 | Medium vegetation | Shrubs, bushes (50cm - 1.5m) |
| 5 | High vegetation | Trees, canopy (> 1.5m) |
| 6 | Building | Roofs, facades, chimneys |
| 8 | Upper canopy tier | The top of tall crowns; IGN uses code 8 for this, not for ASPRS "model key point" |
| 9 | Water | Rivers, lakes |
| 17 | Bridge deck | Road surfaces on bridges |

The height cutoffs on codes 3, 4 and 5 are heights above ground, and they are IGN's, which is why a 1.4 m shrub is class 4 and a 1.6 m one is class 5. The Grand Lyon 2023 delivery carries codes 1 to 9 only; codes 17 and above appear in the table because `classes_config.py` keeps the whole scheme, not because these tiles use them.

IGN also uses custom codes (64-67) for power lines, artefacts, virtual points under bridges, and unconfirmed building-like objects.

### Why Points Are Not Enough

A point cloud is a measurement, not a model. It has gaps (no laser pulse ever hit that spot), it has noise (a bird or a dust particle reflected a pulse), and it is completely unstructured. To answer questions about the city, we need to turn it into something with volume and semantics. That something is a **voxel grid**.

### Voxels: 3D Pixels

Just as a digital image divides a 2D plane into pixels, a voxel grid divides 3D space into **voxels** (volumetric pixels). Each voxel is a small box - 0.5 m x 0.5 m x 0.5 m by default here, and 0.25 m x 0.25 m x 0.1 m at the finest setting this project ran. Every point from the LiDAR cloud falls into one voxel. By collapsing the millions of points into a much smaller number of voxels, we:

1. **Reduce data size** dramatically (from ~30 million points to ~3 million occupied voxels)
2. **Create structure** - every voxel has a known grid position
3. **Enable fast queries** - finding what is at (x, y, z) becomes an array lookup

---

## 4. The Pipeline: A Bird's-Eye View

The entire pipeline can be summarized in three sentences:

1. **Read** the LiDAR file into arrays of (x, y, z, class).
2. **Quantize** each point into a voxel index (ix, iy, iz) and collapse duplicates, summing point counts.
3. **Run-length encode** the vertical stacks of voxels into intervals of constant class.

Here is the full pipeline in visual form:

```
+------------------+     +---------------------+     +---------------------+     +-----------------+
|   LAZ/LAS File   |---->|  Points as Arrays   |---->|  Unique Voxel Cells  |---->|  ColumnStore    |
| (7.7K-60.9M pts) |     |  x, y, z, cls       |     |  (ix,iy,iz,cls,count)|     |  (RLE encoded)  |
+------------------+     +---------------------+     +---------------------+     +-----------------+
                              laspy read               _cells_from_points()         _store_from_cells()
```

And the pipeline code entry points:

```
voxelize_laz(path) --> read_laz(path) --> voxelize(x, y, z, cls)
                                                |
                                                +--> _cells_from_points()  [Stage 1]
                                                +--> _store_from_cells()   [Stage 2]
```

The two internal stages are deliberately factored so that the **whole-file path** and the **streamed (chunked) path** share the exact same final stage and produce **identical** stores. That is what makes large areas tractable: a 3km x 3km region can be processed in chunks without changing the output format.

Everything above produces the **raw `ColumnStore`** (the measurement of record, section 13). Downstream of it sits an optional, composable **refinement / analysis / reconstruction layer** - `denoise -> absorb -> resolve -> ground_index -> decoder / ray_trace`, plus `reconstruct` (-> LAS/LAZ) and the **3D Tiles export** (`tiled_exporter -> tileset_exporter`). The semantic passes are reachable from the command line through `postprocess_cli` (store in, store out), and remain optional rather than automatic stages of an area run. They are covered in section 13; the substrate reasons they are cheap are in section 12.

---

## 5. Stage 0: Reading the Point Cloud

### What Happens

The input is a LAZ file - a compressed version of the LAS format, which is the industry standard for LiDAR data exchange. We use the `laspy` library with the LASzip backend (the most robust decompressor for large IGN tiles).

### The Code (`io_laz.py`)

```python
def read_laz(path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Returns x, y, z, cls as numpy arrays."""
    las = laspy.read(path, laz_backend=LazBackend.Laszip)
    x = np.asarray(las.x, dtype=np.float64)   # RGF93/CC46 easting, in meters
    y = np.asarray(las.y, dtype=np.float64)   # RGF93/CC46 northing, in meters
    z = np.asarray(las.z, dtype=np.float64)   # Altitude above sea level, in meters
    cls = np.asarray(las.classification, dtype=np.uint8)  # Semantic label
    return x, y, z, cls
```

### Why float64 for Coordinates

RGF93/CC46 (EPSG:3946) coordinates are large numbers - for Lyon, x is around 1,830,000 and y is around 5,175,000. If we used float32, the spacing between representable values at this magnitude is about 0.25 meters. That is larger than our voxel size, so we would lose precision and misalign points. float64 gives us nanometer precision, which is more than enough.

### Why We Stream for Large Areas

A single LAZ tile can be ~70 MB compressed, decompressing to ~400 MB in memory. For a multi-tile area, this adds up. The `read_laz_chunks()` function uses `laspy`'s `chunk_iterator` to yield batches of ~5 million points at a time, so peak memory stays bounded regardless of how many tiles the area spans.

---

## 6. Stage 1: From Points to Voxels

### The Core Idea

Every point lives in continuous 3D space. We want to snap it to a grid. This is called **quantization** or **binning**. For each point:

```
ix = floor((x - x0) / cell_xy)
iy = floor((y - y0) / cell_xy)
iz = floor((z - z0) / cell_z)
```

Where:
- `(x0, y0, z0)` is the **grid origin** - the bottom-left-back corner of the voxel grid
- `cell_xy` is the horizontal voxel size (typically 0.5m or 1.0m)
- `cell_z` is the vertical voxel size (0.5 m by default; 0.1 m at the finest setting)

### Why Different Cell Sizes?

The default is **cubic** - 0.5 m in all three axes - because cubic cells make a storage-cost comparison across resolutions read like for like. Anisotropy is then used *deliberately*, not by default, whenever the vertical axis is what carries the information: the production recommendation is 1.0 m horizontal with 0.5 m vertical, and the finest probe goes to 0.25 m horizontal with 0.1 m vertical. A 0.1 m vertical cell separates a curb from a sidewalk, or a low bush from medium vegetation, where a 1 m vertical cell blurs them together; horizontally, 0.5 m or 1 m is enough because urban objects (buildings, tree crowns) are much larger than that in plan view. `design-decisions.md` section 5 argues each of the three settings; it ships under `docs/` at the delivery root.

### The Algorithm (`_cells_from_points` in `voxelize.py`)

This is the most computationally intensive step, and it is done entirely in **vectorized numpy** - no Python loop over points. The abridged core below omits the production guards that reject non-finite coordinates and indices outside the signed-int32 grid:

```python
def _cells_from_points(x, y, z, cls, x0, y0, z0, cell_xy, cell_z):
    # 1. Quantize all points into voxel indices
    ix = np.floor((x - x0) / cell_xy).astype(np.int32)
    iy = np.floor((y - y0) / cell_xy).astype(np.int32)
    iz = np.floor((z - z0) / cell_z).astype(np.int32)

    # 2. Sort canonically. The default class_first order is
    #    (ix, iy, cls, iz); height_first, the compatibility order, is
    #    (ix, iy, iz, cls) and is what this fragment shows.
    order = np.lexsort((cls, iz, iy, ix))       # height_first
    # order = np.lexsort((iz, cls, iy, ix))     # class_first, the default
    ix_s, iy_s, iz_s, cls_s = ix[order], iy[order], iz[order], cls[order]

    # 3. Find where the voxel key changes
    voxel_changed = (
        (np.diff(ix_s) != 0) |
        (np.diff(iy_s) != 0) |
        (np.diff(iz_s) != 0) |
        (np.diff(cls_s) != 0)
    )
    starts = np.concatenate(([0], np.where(voxel_changed)[0] + 1))

    # 4. Extract unique cells with summed point counts
    u_ix   = ix_s[starts]
    u_iy   = iy_s[starts]
    u_iz   = iz_s[starts]
    u_cls  = cls_s[starts]
    u_count = np.diff(np.concatenate((starts, [len(ix_s)]))).astype(np.int32)

    return u_ix, u_iy, u_iz, u_cls, u_count
```

**Note on the edge case:** The actual implementation (the guard in `voxelize._cells_from_points`) handles the case where no points survive a class filter or crop by returning five empty arrays. The guard sits just after the `lexsort` and before the `diff`, which is what matters: it avoids `np.diff` on an empty array and makes the function safe to call with zero-length input - a common occurrence when a tile's points all fall outside a `clip_bbox` or `keep_classes` filter.

### Walkthrough of the Algorithm

**Step 1 - Quantize:** Every point gets its voxel index. We now have four arrays of the same length as the input: `ix, iy, iz, cls`.

**Step 2 - Sort:** We sort all points with `np.lexsort`, by `(ix, iy, cls, iz)` under the default `class_first` order or by `(ix, iy, iz, cls)` under the `height_first` compatibility order. Either way all points that fell into the same voxel AND have the same class end up together as a contiguous block; the two orders differ only in how the next stage's runs come out.

**Step 3 - Find boundaries:** `np.diff` tells us where the sorted array changes value. Each change marks the start of a new unique `(ix, iy, iz, cls)` combination.

**Step 4 - Collapse:** At each start position, we record one unique cell. The count of points in that cell is simply the distance between consecutive start positions.

### Why We Track Class per Cell

A single voxel can contain points of **multiple classes**. A roof voxel might have both building returns and vegetation returns from a tree branch hanging over it. By keying on `(ix, iy, iz, cls)`, we keep these separate. The store is therefore a **multiset** - it preserves per-class point counts per voxel, rather than forcing an immediate "one class per voxel" decision.

### What the Output Looks Like

For the 1 km x 1 km eastern-Lyon bounding box of the encoder experiment (three of the four 500 m tiles of the box, so ~0.75 km^2 of ground actually covered), at the 0.5 m cubed default:
- Input: 74,265,365 points
- Output: 11,737,162 unique cells (occupied voxel/class pairs), each with a point count, in 2,978,898 occupied columns

At the recommended 1.0 m x 0.5 m the same points give 5,094,916 unique cells in 749,384 columns. Both counts are read back out of the stores in `Experiments/experiments/out/`.

The unique cells come out in whichever canonical order was used, `(ix, iy, cls, iz)` by default, and that is the order the next stage expects. `VOXELIZER_RUN_ORDER` selects it: `class_first` is the default and makes each run maximal per `(ix, iy, cls)`, `height_first` is the compatibility order every pre-switch store was built with.

---

## 7. Stage 2: From Voxels to Runs (The RLE)

### The Core Idea

Now that we have unique cells, we want to compress them vertically. Within a single `(ix, iy)` column, if we have consecutive voxels at increasing `iz` values all with the same class, we merge them into one **interval** (run): `(z_start, z_end, cls, count)`.

This is **Run-Length Encoding (RLE)** applied to the vertical dimension. Instead of storing every occupied voxel individually, we store ranges: *"from z=20 to z=55, this column is all vegetation, with 72 points total."*

### The Algorithm (`_store_from_cells` in `voxelize.py`)

```python
def _store_from_cells(u_ix, u_iy, u_iz, u_cls, u_count, x0, y0, z0, cell_xy, cell_z, *, aggregate):
    if aggregate:
        # Re-aggregate duplicate cells (needed for the chunked/streamed path)
        if _run_order() == "class_first":
            order = np.lexsort((u_iz, u_cls, u_iy, u_ix))
        else:
            order = np.lexsort((u_cls, u_iz, u_iy, u_ix))
        u_ix, u_iy, u_iz = u_ix[order], u_iy[order], u_iz[order]
        u_cls, u_count = u_cls[order], u_count[order]
        changed = (
            (np.diff(u_ix)  != 0) |
            (np.diff(u_iy)  != 0) |
            (np.diff(u_iz)  != 0) |
            (np.diff(u_cls) != 0)
        )
        starts = np.concatenate(([0], np.where(changed)[0] + 1))
        u_count = np.add.reduceat(u_count, starts).astype(np.int32)
        u_ix, u_iy, u_iz, u_cls = (
            u_ix[starts], u_iy[starts], u_iz[starts], u_cls[starts]
        )

    # 1. Detect where a new interval starts
    interval_changed = (
        (np.diff(u_ix)  != 0) |   # x changes
        (np.diff(u_iy)  != 0) |   # y changes
        (np.diff(u_cls) != 0) |   # class changes
        (np.diff(u_iz)  != 1)     # z does NOT increment by 1
    )
    interval_starts = np.concatenate(([0], np.where(interval_changed)[0] + 1))
    interval_ends   = np.concatenate((interval_starts[1:], [len(u_ix)]))

    # 2. Extract interval properties
    iv_ix      = u_ix[interval_starts]
    iv_iy      = u_iy[interval_starts]
    iv_z_start = u_iz[interval_starts]
    iv_z_end   = u_iz[interval_ends - 1] + 1   # exclusive
    iv_cls     = u_cls[interval_starts]
    iv_count   = np.add.reduceat(u_count, interval_starts)

    # 3. Hand flat interval arrays to the ColumnStore
    store = ColumnStore.from_intervals(
        x0, y0, z0, cell_xy, cell_z,
        iv_ix, iv_iy, iv_z_start, iv_z_end, iv_cls, iv_count,
        assume_canonical=(_run_order() == "height_first"),
    )
    return store
```

### Walkthrough of the Algorithm

**Step 1 - Detect interval boundaries:** An interval (run) is a maximal sequence of cells where:
- `(ix, iy)` stays the same (same column)
- `cls` stays the same (same class)
- `iz` increments by exactly 1 (consecutive vertical voxels)

If any of these conditions breaks, a new interval starts. The key line is `(np.diff(u_iz) != 1)` - this catches gaps (empty voxels between occupied ones) and class changes.

**Step 2 - Extract intervals:** For each interval, we record:
- `iv_ix, iv_iy` - which column this interval belongs to
- `iv_z_start` - the first voxel index in the run (inclusive)
- `iv_z_end` - the first voxel index AFTER the run (exclusive, Python-style)
- `iv_cls` - the class code
- `iv_count` - the sum of all point counts in this run

**Step 3 - Build the ColumnStore:** The flat interval arrays are handed directly to `ColumnStore.from_intervals()`, which canonicalizes them as `(ix, iy, z_start, cls)` when required. The `height_first` compatibility order already has that order and therefore skips the redundant sort.

### Why `aggregate=True` Matters

When processing a large area in chunks, each chunk produces its own set of cells. Some cells may appear in multiple chunks (if the chunks overlap, or if two tiles contribute points to the same voxel). With `aggregate=True`, the concatenated cell arrays are re-sorted and duplicate `(ix, iy, iz, cls)` cells have their counts summed. This makes the streamed result **mathematically identical** to processing all points at once.

### The Key Property

After this stage, the data is no longer "a list of points." It is "a list of vertical runs in a grid of columns." This is the RLE. The compression ratio is significant: a column that originally had 1000 points might compress to 3-5 intervals.

---

## 8. The Data Structure: ColumnStore

### Philosophy

The `ColumnStore` is the heart of the system. It holds the entire voxelized tile or area as a **column-compressed grid**. The design philosophy is:

- **Columns know their vertical contents**, but nothing about horizontal neighbors.
- **Every urban question we care about is vertical**: ground here? tree above? building cover?
- Horizontal adjacency would cost enormous memory for no analytical gain.

### The Flat Arrays (Struct-of-Arrays Layout)

A previous version of the store used a Python dictionary: `dict[(ix, iy) -> Column]`. This had ~874 bytes of overhead per occupied column. At 0.25m resolution, a single 500m tile has up to 4 million columns - **3 GB of pure overhead**, which caused consistent out-of-memory failures on large areas.

The current design uses six flat numpy arrays:

```python
_keys : uint64  (n_cols,)     # packed (ix, iy), sorted ascending
_off  : int64   (n_cols + 1,) # per-column offsets into the interval arrays
_zs   : int32   (n_ivs,)      # interval z_start (inclusive)
_ze   : int32   (n_ivs,)      # interval z_end (exclusive)
_cl   : uint8   (n_ivs,)      # ASPRS class code per interval
_ct   : int32   (n_ivs,)      # point count per interval
```

A seventh optional array `_gi` (int32 per column, the ground index) is cached lazily on first access via `store.ground_idx`; it adds 4 bytes per occupied column to the memory footprint when present. (int32 rather than int16: at fine `cell_z` a legitimate ground index exceeds 32767, and in int16 it wrapped to a plausible-looking negative index instead of to `NODATA`.)

**Memory cost: 16 bytes per column + 13 bytes per interval.** A measured ~16x less than the dictionary form at the real ~2.7-interval mix - 209 B against 13 B per interval, 16.1x (an earlier `sys.getsizeof` estimate suggested 30-50x, but it double-counted the shared-key instance dict and is withdrawn).

### How the Key Packing Works

Column indices `(ix, iy)` are signed 32-bit integers (they can be negative, since the grid origin is arbitrary). To pack them into a single uint64 while preserving lexicographic order:

```python
def _pack_keys(ix, iy):
    ixu = (np.asarray(ix, dtype=np.int64) + (1 << 31)).astype(np.uint64)
    iyu = (np.asarray(iy, dtype=np.int64) + (1 << 31)).astype(np.uint64)
    return (ixu << 32) | iyu
```

The offset `+ 2^31` shifts signed 32-bit values into the unsigned range `0..2^32-1`, so lexicographic `(ix, iy)` order equals numeric key order. This makes column lookup a **binary search** (O(log n)) instead of a hash table lookup.

### Accessing a Column

```python
col = store.columns[(ix, iy)]  # returns a Column view (zero-copy slice)
# col.z_start: array of interval starts
# col.z_end:   array of interval ends
# col.cls:     array of class codes
# col.count:   array of point counts
```

The `columns` property is a read-only `Mapping` view over the flat arrays. `__getitem__` does a binary search on `_keys` and returns a `Column` whose arrays are zero-copy slices of the flat arrays.

### The Column View

```python
@dataclass
class Column:
    z_start: np.ndarray   # int32, shape (n,)
    z_end:   np.ndarray   # int32, shape (n,)
    cls:     np.ndarray   # uint8, shape (n,)
    count:   np.ndarray   # int32, shape (n,)

    def __len__(self) -> int:
        return len(self.z_start)

    def iter_intervals(self):
        for k in range(len(self.z_start)):
            yield Interval(
                z_start_idx=int(self.z_start[k]),
                z_end_idx=int(self.z_end[k]),
                class_id=int(self.cls[k]),
                point_count=int(self.count[k]),
            )
```

A Column is all the intervals of one `(ix, iy)` position, stored as parallel numpy arrays. When obtained from a store, the arrays are zero-copy views - constructing a Column costs nothing beyond the transient object. Note that `iter_intervals()` materializes `Interval` dataclass instances one at a time, so prefer the raw arrays (`.z_start`, `.z_end`, `.cls`, `.count`) in tight loops over many columns to avoid allocating millions of throwaway objects.

### Persistence

The store can be saved and loaded as a compressed `.npz` file:

```python
store.save("area.npz")           # write compressed flat arrays + metadata
store = ColumnStore.load("area.npz")  # load back
```

Or as a memory-mappable raw directory:

```python
store.save_dir("store_raw/")     # one .npy per array + meta.json
store = ColumnStore.load_dir("store_raw/", mmap=True)  # zero-copy, lazy
```

The memory-mappable form is used by the stage runner, which is on by default (`--isolate-stages`): each output stage - `stats`, `persist_npz`, `maps`, `col_diag`, `viz3d_roi`, `viz3d_full`, `viz3d_stream` - runs in its own child process that attaches to the store without inflating it into memory, so the OS can evict pages under pressure, the parent's in-heap store is freed during rendering, and a native crash in one stage costs that stage only: the run retries it once and writes every other output. `--stage-timeout` adds a wall-clock limit per stage and defaults to 0, meaning none, because a fixed limit cannot tell a big stage from a stuck one: the value that separates them scales with the store. Attaching is only half of it - most whole-store reductions in `data_structures` build at least one array as long as the store, which at metropolis scale is fatal several times over. `store_streaming.py` holds the out-of-core rewrites the stages actually call: `StatsFold` with histogram-based medians instead of `np.median`, batched interval histograms, top-N selection and category picks, `key_frame` for the map rasters' extent through a banded unpack of the packed keys, and `summaries_batches` for the per-column summaries the map renderers consume. Each is written as a fold, so the same code also composes over a set of shards.

---

## 9. What the RLE Looks Like in Practice

### A Simple Example

Consider a single column at `(ix=100, iy=200)` that goes through:

- z=0 to z=5: ground (class 2), 340 points
- z=5 to z=20: high vegetation (class 5), 128 points
- z=20 to z=35: building (class 6), 89 points

In the ColumnStore, this column is stored as:

```python
Column(
    z_start = np.array([0, 5, 20], dtype=np.int32),
    z_end   = np.array([5, 20, 35], dtype=np.int32),
    cls     = np.array([2, 5, 6], dtype=np.uint8),
    count   = np.array([340, 128, 89], dtype=np.int32),
)
```

A human-readable string format could be:
```
0-5:G(340)  5-20:V(128)  20-35:B(89)
```

Or in the compact notation from the discussion:
```
0G 5V 20B
```

### A Complex Example

A column on a building facade with a tree in front might have:

```python
Column(
    z_start = np.array([0, 2, 4, 6, 8, 12], dtype=np.int32),
    z_end   = np.array([2, 4, 6, 8, 10, 25], dtype=np.int32),
    cls     = np.array([2, 5, 6, 5, 6, 6], dtype=np.uint8),
    count   = np.array([50, 12, 45, 8, 30, 120], dtype=np.int32),
)
```

Human-readable:
```
0-2:G(50)  2-4:V(12)  4-6:B(45)  6-8:V(8)  8-10:B(30)  12-25:B(120)
```

This column has 6 intervals - the interleaved vegetation and building runs are produced by LiDAR returns on a tree standing against a facade. The RLE preserves this faithfully; it does not force a "one class per height" decision.

The facade arrives as two building runs rather than one because voxels 10 and 11 are empty: the tree occludes that slice of it and no return lands there. A gap is the only thing that can separate two same-class runs in one column. Touching runs of the same class, `8-10:B` followed immediately by `10-25:B`, cannot come out of the encoder at all, in either run order - it closes a run on a class change, so it would have written one `8-25:B`.

### Cross-Class Overlaps

Because the voxelizer keys on `(ix, iy, iz, cls)`, a single physical voxel can produce **two intervals at the same z-range** if it contains points of two different classes. This is rare at fine resolution (0.1 m vertical) but becomes common at 1 m^3. The `class_overlap_stats()` method measures how often this occurs - it is a diagnostic for deciding whether `resolve()` (majority vote per voxel) is needed.

---

## 10. Post-Processing: Grouping and Merging

### Grouping: `ColumnStore.grouped()`

At fine `cell_z`, sparse LiDAR returns can fragment one physical object into many small intervals. A tree canopy might produce 40 intervals of 1-2 voxels each, separated by tiny gaps where no laser pulse happened to hit.

The `grouped(max_gap_cells)` operation merges **consecutive same-class intervals** across gaps of at most `max_gap_cells` empty voxels:

```python
grouped_store = store.grouped(max_gap_cells=3)  # merge across gaps of <=3 empty cells
```

Properties:
- Counts are summed; total points per column and per class are **exactly preserved**
- Top interval's class and z_end are unchanged (max-height and orthophoto maps are unaffected)
- The raw store is not modified; a new store is returned

This is typically applied as an optional post-pass. In an area run, the ungrouped store is written first as `area_raw.npz`, then the grouped store drives the outputs. `area_raw.npz` is an intermediate: `--intermediates auto` removes it after a full success unless `--keep-area-raw` (or `--intermediates keep`) retains it. Shard runs without `--merge-shards` differ in provenance: `stats.txt` and the `columns/` diagnostics reproduce the merged-and-grouped results directly from the shards, while the mosaic maps and the shard-assembled 3-D views render the raw per-tile shard contents; the map and stats headers state which store they describe.

### Merging: `ColumnStore.merge()`

When processing a multi-tile area, each tile produces its own ColumnStore on the **same shared grid** (same origin, same cell sizes). Merging combines them:

```python
area_store = tile1_store.merge(tile2_store, inplace=True)
area_store.merge(tile3_store, inplace=True)
# ... etc
```

If the tiles are non-overlapping (the normal case), merge reduces to a **disjoint union**: concatenate the flat arrays and sort by key. No per-column Python work is needed. If tiles overlap (rare), shared columns are stitched by `_merge_two_columns()`, which coalesces same-class touching runs and sums their counts.

### Merging out of core: `merge_streaming.py`

`merge_many` is the right algorithm in the wrong container. It loads every shard, concatenates the six flat arrays into one pre-allocated buffer set, sorts the combined columns once and gathers, so peak RAM is the sources plus a full second copy of the merged store. Measured on the 1 m metropolis run (2,842 shards, 648.5 M columns, 1.453 G intervals): about 24 GB of loaded shards, then a 4.83 GiB `keys` allocation that a 32 GB machine cannot serve. At 0.5 m the same store is about 2.58 G columns and 5.4 G intervals, roughly 111 GB of arrays, and `merge_many` refuses it outright in any case, because its offsets are int32 and 5.4 G intervals overflow the 2^31 guard.

Nothing about the *result* needs that much memory: the merged store is written to disk immediately afterwards and every consumer memory-maps it. Only the assembly insisted on holding it whole. `merge_streaming.py` assembles `store_raw/` on disk instead, one key band at a time, and the stages attach to the finished directory by memory map. Peak RAM follows the band budget rather than the size of the merged store, which is what makes a whole-metropolis merge at 0.5 m possible on 32 GB. `--merge-band-intervals` sets that budget in intervals per band (default 40,000,000, about 1.4 GB peak); each band is assembled whole in memory, and a shard is read once per band its column range touches, so a larger band re-reads the compressed shards fewer times.

The merge is not required to inspect a shard set. A column lives in exactly one shard, because the tiles partition the grid, so every reduction behind `stats.txt`, the four mosaic maps and everything under `columns/` is a fold over disjoint parts already sitting on disk. `shard_diagnostics.py` is that fold: one sequential pass over the shards feeds the `store_streaming` accumulators, the order-sensitive selections are resolved globally on the packed key, and each selected column is rendered by loading only the shard that owns it. Peak private memory is one shard plus the accumulators, whatever the size of the corpus.

---

## 11. Design Decisions and Trade-offs

### Why Columns and Not a Full 3D Array?

A dense 3D array for a 500m x 500m x 50m tile at the 0.5 m cubed default would be:
- 1000 x 1000 x 100 = **100 million voxels** (and 2000 x 2000 x 500 = **2 billion** at the 0.25 m / 0.1 m fine probe)
- Even as a sparse structure, the memory overhead of indexing all empty space is enormous

The column-compressed approach only stores **occupied** columns. Empty columns simply do not exist in the store. This is why the 74.3-million-point experiment area at 0.5 m cubed costs 2,978,898 x 16 B + 6,833,550 x 13 B = 136.5 MB of flat arrays - 26.2 MB once `savez_compressed` has deflated them to `area_0.5m_0.5m.npz` - instead of multiple gigabytes.

### Why Not a Sparse Voxel Octree (SVO) Directly?

An SVO is excellent for rendering and ray tracing but is complex to build incrementally (needed for streaming) and harder to query for simple vertical questions ("what is the ground height here?"). The column-RLE is the **right intermediate representation**: easy to build from points, easy to query, and easy to convert to an SVO later (the intervals directly tell you where to split).

### The 0.5 m Cubed vs. 1.0 m / 0.5 m vs. 0.25 m / 0.1 m Decision

Three cell sizes appear in this project, and they are three different KINDS of thing rather than three points on one dial. `docs/design-decisions.md` section 5 argues why; this is what each one costs.

| Setting | Role | Points | Columns | Intervals | Store |
|:---|:---|---:|---:|---:|---:|
| 0.5 m cubed | code default (`voxelize()`, every CLI, the GUI) | 74,265,365 | 2,978,898 | 6,833,550 | 124.6 MB payload |
| 1.0 m xy / 0.5 m z | analysis recommendation | 74,265,365 | 749,384 | 2,195,849 | 37.5 MB payload |
| 0.25 m xy / 0.1 m z | finest probe | 751,592,610 | 116,500,559 | 317,402,458 | 1.32 GB `.npz` |

The first two rows are the same 1 km x 1 km eastern-Lyon bounding box measured twice - three of its four 500 m tiles, so ~0.75 km^2 of ground covered (`Experiments/experiments/out/metrics.json`); the third is the 3 x 3 km production sweep, a different and 10x larger area, so read it as an order of magnitude and not as a fourth column of the same experiment.

`payload` in that table is the serialized `rle_v0_raw` stream of the encoder experiment, the `payload_bytes` field of `Experiments/experiments/out/<tag>__rle_v0_raw.json`, because that is the quantity the encoder comparison measures. It is a different number from the 136.5 MB above: that one is the in-memory cost of the flat arrays, 16 B per column plus 13 B per interval, while the serialized stream also carries the per-column framing. The two describe the same store, 124.6 MB written and 136.5 MB resident.

Key observations:

- **Columns scale with 1/(cell_xy)^2.** Halving the horizontal cell multiplies the column count by almost exactly four - in the 3 x 3 km sweep, 7.3 M columns at 1.0 m, 29.2 M at 0.5 m, 116.5 M at 0.25 m.
- **Store size is dominated by intervals**, not columns: 13 B per interval against 16 B per column in memory, and there are always more intervals. Going from 1.0 m / 0.5 m to 0.5 m cubed on the same points triples the intervals (2.20 M -> 6.83 M) and the serialized payload with them (37.5 -> 124.6 MB, 3.3x).
- **Vertical refinement is nearly free once grouped.** In the sweep, at 0.25 m horizontal, going from 0.25 m to 0.1 m vertical leaves the grouped interval count essentially unchanged (187.2 M against 187.3 M) while the raw count grows 17 %: fine vertical cells mostly split runs rather than adding information.
- **Mixed-voxel risk grows at coarser resolutions**: a 0.1 m tall cell is unlikely to straddle a building/vegetation boundary; a 1 m^3 cell frequently does, which is why the `resolve()` step (majority-vote per voxel) is needed for clean per-column semantic models at 1 m^3.

The trade-off: the code default is the FINER **0.5 m cubed**, because a user who chooses nothing should get the artefact that discards least. The report then RECOMMENDS **1.0 m / 0.5 m** for analysis products, since the sweep showed the coarser grid 3.3x smaller with every building footprint and tree crown still surviving. **0.25 m / 0.1 m** is the neighbourhood-scale probe and the stress case, not a production default: at 3 x 3 km it costs 317 M intervals and a 1.32 GB store.

### Why Store Point Counts?

The `_ct` (count) array records how many raw LiDAR points contributed to each interval. This is not needed for rendering, but it is essential for:
- **Quality control**: intervals with very few points are likely noise
- **Resolution analysis**: comparing total points vs. total voxels tells us coverage density
- **Majority voting**: when `resolve()` needs to decide which class wins a mixed voxel, point counts are the natural weight

### Anisotropic vs. Isotropic Voxels

The default is isotropic (0.5 m cubed) and that is deliberate: cubic cells make the storage-cost comparison across resolutions read like for like, so the sweep's interval counts are directly comparable. Anisotropy is then a deliberate departure, not the norm - 1.0 m / 0.5 m for analysis products and 0.25 m / 0.1 m for the fine probe - taken where vertical structure is what carries the information and horizontal detail below 0.25 m would not change a plantability answer. Urban LiDAR rewards that trade: buildings and terrain have strong vertical structure, and an isotropic grid fine enough to resolve it horizontally would cost `1/(cell_xy)^2` in columns for nothing.

---

## 12. Why This Encoding Is the Right Substrate

### For Ray Tracing / Shade Analysis

A column grid is a full 3-D structure (multiple intervals per column represent overhangs, canopy above ground, and bridges - things a 2.5-D heightfield cannot hold). To trace a ray:

The implemented walker (`ray_trace.ColumnGridDDA`) is a full 3-D Amanatides-Woo
DDA: it maintains the parametric distance to the next boundary on all three
axes and steps whichever is nearest, one voxel at a time, including in z. It
does NOT do a per-column binary search: despite the column-organised data
structure sometimes attracting a "2.5-D" label, nothing in the traversal is
2.5-D. That column-walk remains a possible future optimisation, not the
thing this code does.

The per-voxel walk is correct but its cost scales with `1 / cell_z`, since an
escaping ray keeps stepping through empty air. For the shadow workload that
was the bottleneck, so `ray_columns.CeilingDDA` adds an EXACT early-out:
above the highest occupied voxel of a column and its four neighbours every
gap is provably measured air, so an upward ray can terminate there. The
recorded benchmark measures escape rays 12,547x to 114,207x faster (growing as
cells get finer) and the shadow workload's ground-hit rays up to ~225x faster,
1x at 1 m cells where the reference walker already stopped on the first solid
voxel (`Experiments/ray_benchmark.json`; single-machine timings, quoted as
orders of magnitude), with identical results asserted against
the reference walker by `tests/test_ray_columns_equivalence.py` (the tests/
and Experiments/ evidence cited through this guide live in the private
VCity monorepo, `Projects/IArbre/Stage-voxelisation`). The column
binary search the old text described remains a legitimate future optimisation,
just not the thing the code does today.

### For Sky-View Factor

Sky-view factor asks: "from this point, what fraction of the sky hemisphere is visible?" For each direction (azimuth + elevation), you trace a ray upward with the same walker described above - there is still no per-column binary search anywhere in the traversal. What the column organisation buys here is a provable stopping rule rather than a different search:
- Every column carries its own ceiling, `max(z_end)` over its intervals, which is one reduction over the flat arrays rather than a per-query search
- Above the ceiling of a column and its four orthogonal neighbours, `ray_columns.CeilingDDA` shows every gap voxel must decode to `MEASURED_AIR`, so an upward ray that passes that height can only escape and is terminated there
- For a vertical ray the column never changes, so its own local ceiling governs; for an oblique ray only the store-wide maximum `z_end` is safe, which is weaker but still terminates a ray that has climbed above all geometry
- Anything below the ceiling is walked voxel by voxel exactly as `ray_trace.ColumnGridDDA` would, so the answers are identical (asserted ray for ray by `tests/test_ray_columns_equivalence.py`)

### For Reverse Octree / SVO Building

The question "is this octree node uniform?" becomes: "do the runs spanning this z-range agree across these columns?" This is cheap because the runs are already the answer. The SVO build is a **merge over the store**, not a re-voxelization from points.

### For 3D Visualization

The existing `visualizer3d.py` already proves this works: it flattens intervals into per-box geometry arrays (centers, sizes, colors) and renders them with GPU instancing. A 500 m tile at 1m^3 resolution produces ~562K intervals - comfortably handled by modern browsers via Three.js InstancedMesh.

### For 3D Tiles / Web & Engine Streaming

Because every interval is already one instanced box, the store maps almost directly onto the OGC **3D Tiles** streaming format: `tiled_exporter.py` walks one tile-row band at a time and writes tile-contiguous instance records (`.bin` + per-tile `.idx.json`), and `tileset_exporter.py` packs each tile into a `.glb` with `EXT_mesh_gpu_instancing` (one unit box per class, per-instance TRANSLATION + SCALE). That single tileset is the hub every downstream viewer can consume - CesiumJS and iTowns natively, Unreal and Unity through the Cesium plugins. See section 13 for the exporter's capabilities - the georeferenced ECEF `root.transform` (`_enu_to_ecef_transform`) and the streamed quadtree LOD pyramid that `convert_to_3d_tiles_lod()` builds by default - and for the multi-viewer roadmap.

---

## 13. Downstream: Refinement, Analysis, Reconstruction & Multi-Viewer Export

### The Encoding, Concretely

Per column, the decoder's **logical view** of the encoding is **5 bytes of header + 4 bytes per run** -- a compact representation for on-the-fly classification, **not** the physical on-disk layout (which uses the struct-of-arrays described in section 8, with absolute int32 `_zs`/`_ze`):

```
(ix, iy)  ->  g : int32      # ground z-index (NODATA if none)        <- logical view
             n : uint8       # run count
             n x { z_start : int16  (delta from g)
                   length  : uint8
                   cls     : uint8  }
```

The physical store stores instead `_zs`/`_ze` (int32 absolute voxel indices), `_cl` (uint8), `_ct` (int32 count), plus `_keys`/`_off` for column indexing -- see section 8 for the full layout.

**Decoder alphabet:** there are exactly three emptiness states, defined in
the return-value constants `MEASURED_AIR` and `OPAQUE_INTERIOR` at the top of `voxelizer/decoder.py`. There is no "sky" state.

- `iz < ground_z - SUBSURFACE_BAND` -> `SUBSURFACE` (-3), solid earth.
  `SUBSURFACE_BAND = 2`, but it is compared against the EXCLUSIVE
  `ground_z_idx`, so the effective gap left below the top ground voxel is
  **ONE** voxel, not two (see the module docstring of `voxelizer/ground_index.py`).
  This state is INFERRED, never measured: an airborne laser stops at the
  first opaque surface and records nothing below it, so there is no LiDAR
  evidence about what is underground. The decoder deduces it from the
  ground index - below the measured ground surface, treat the space as
  solid - and a ray consuming it is consuming a deduction. The other two
  states rest on returns that exist.
- Stored runs -> their own class code.
- Gap in a penetrated column (a ground return exists) -> `MEASURED_AIR` (-1).
- Gap in a ground-less column (no ground return, e.g. under a roof): below
  the column's highest return -> `OPAQUE_INTERIOR` (-2); at or above that
  top the beam demonstrably passed on its way down -> `MEASURED_AIR` (-1).
- Above the top run in a penetrated column -> `MEASURED_AIR` (-1), by the same
  rule as any other gap. Querying an arbitrarily large `iz` returns
  `MEASURED_AIR`, not a distinct sky value.

> `decoder.py` exposes exactly these three states - `MEASURED_AIR`,
> `OPAQUE_INTERIOR` and `SUBSURFACE`. There is no `SKY` constant and none
> is returned.

A column's debug string is literally: `G0 | V3-5 | B7-12`.

### Implemented: The Refinement, Analysis & Reconstruction Layer

Everything earlier drafts of this document listed as "not yet implemented" now exists, as a set of small, composable, **top-level** modules. Each takes a `ColumnStore` and returns a new one (or a derived array); none mutate the input, so they chain cleanly and the raw store stays the measurement of record.

| Module (`file.py`) | Public API | What it does |
|---|---|---|
| `resolve.py` | `resolve(store)` | Collapse cross-class overlaps to **one label per voxel** - majority by point count, ties -> smallest class code. Output intervals are non-overlapping and canonical. |
| `denoise.py` | `min_points_filter`, `morphological_filter` | Drop intervals with too few LiDAR points, or too few same-class Moore (8-)neighbours overlapping their z-range (spatially unsupported -> noise). Applied before `absorb` and `resolve`. |
| `absorb.py` | `absorb_interior(store)` | Absorb vegetation runs genuinely buried inside a **building envelope** (z-range-specific 8-neighbour test + cardinal-direction escape for balcony trees + small-run noise override). Recovers the vegetation the plain sandwich rule lost against facades - the failure mode the v4 majority encoder exhibits - by absorbing only what is genuinely enclosed: (1) the building-overlap test is made at the V-run's specific z-range rather than column-wide; (2) a run that sticks out in >=2 of the four cardinal directions is kept (the "balcony tree" escape); (3) runs of <=2 voxels sandwiched by building are absorbed straight away as facade noise. |
| `ground_index.py` | `compute_ground_indices`, `fill_ground_holes`, `NODATA` | Per-column `ground_z_idx` (top of the highest class-2 run); interpolate holes. Cached lazily on the store (`_gi`) and used to anchor the decoder. |
| `decoder.py` | `classify_voxel`, `classify_gap`, `interval_gap_type`, `MEASURED_AIR`, `OPAQUE_INTERIOR`, `SUBSURFACE` | Three-way **emptiness decoder**: occupied class / measured air (-1) / opaque interior (-2, under a roof) / subsurface (-3, below ground). Nadir-beam mitigation via a neighbourhood check. |
| `ray_trace.py` | `ColumnGridDDA(store).trace(origin, dir)` | full 3-D **Amanatides-Woo** ray-walker: per-voxel DDA on all three axes, consuming the decoder's emptiness states. The proof-of-concept for IArbre shade / sky-view queries. |
| `ray_columns.py` | `CeilingDDA(store).trace(origin, dir)`, `column_ceiling`, `global_ceiling` | The same walker with an EXACT early-out: above the ceiling (`max(z_end)`) of a column and its four neighbours every gap is provably measured air, so an upward ray can stop there. Identical hits to `ray_trace`, asserted ray for ray by `tests/test_ray_columns_equivalence.py`. Ceilings are cached lazily inside `trace()`, so an instance is not thread-safe (the base class is). |
| `transmittance.py` | `transmittance(store, origin, dir, ext)`, `Extinction`, `TransmittanceResult`, `NO_DATA` | Replaces the boolean hit with a transmittance in [0, 1], accumulated by Beer-Lambert over PATH LENGTH in metres (`tau = exp(-sum_c k_c L_c)`), so a canopy passes part of the beam and a roof passes none. Opaque classes and the decoder's interior/subsurface states take `k = inf` and end the walk at `tau = 0`; a ray leaving the processed footprint hits `NO_DATA`, which is deliberately not the same as "inside a building". `Extinction` carries no default vegetation coefficient by design - callers pass the fitted one. |
| `solar.py` | `sun_position`, `sun_vector_grid`, `day_arc`, `grid_convergence_deg`, `SunPosition` | Solar position by the NOAA/Meeus low-precision formulation (~0.01 degrees, no new dependency), refraction optional, plus the sun direction expressed in PROJECTED grid axes - which needs the grid-convergence correction between true north and EPSG:3946 grid north. |
| `sun_hours.py` | `compute_sun_hours`, `SunHoursResult` | Per-column direct sun hours: for every sampled sun position of a day arc, one transmittance ray from `height_above_ground_m` (1.5 m) above the top of the column's LOWEST interval, summing `tau * dt`. Direct beam only - no diffuse, reflection, atmospheric attenuation or cloud. Meant to be run twice (opaque vs class-aware): the difference is what the material model contributes. |
| `surface_model.py` | `surface_store`, `surface_rasters`, `write_views`, `load_views` | The 2.5-D surface and raster arms of the objective (v) comparison. |
| `reconstruct.py` | `store_to_points`, `store_to_las`, `store_to_laz`, `store_from_laz`, `verify_exact`, `verify_roundtrip` | **Inverse of voxelize, and its inverse.** Three export modes: `density` (one point per original LiDAR point via `count`), `one_per_voxel`, and `exact` (one per voxel + `count - height` padding). The last round-trips the six store arrays bit-exactly when re-voxelized on the same grid, and re-saving reproduces the `.npz` byte-identically as well (the packaged writer is deterministic; `tests/test_archive_cli.py` pins the pack/unpack SHA-256 equality). Files carry a CRS and an `IARBRE` grid VLR, so `store_from_laz` rebuilds with no sidecar. Preserves x, y, z, class; RGB/intensity/returns are not in the store (see the module's loss table). Rationale: `laz_roundtrip_design.md`. |
| `archive_cli.py` | `pack`, `unpack` | Archive a sharded run's **raw** per-tile stores as exact LAS/LAZ (`shards/*.npz` <-> `shards_laz/*.laz`) at 0.3-1.7x the size (finer grids favour the LAZ), verified per shard. Refuses `area.npz`: the grouped store is not reliably invertible, so it is regenerated from the shards instead. |

**Recommended chain** (all optional, all applied at export/analysis time - never at ingest):

```
raw ColumnStore -> denoise -> absorb -> resolve -> (ground_index ->) decoder / ray_trace
                                                        |
                                                        +-> reconstruct  -> LAS/LAZ point cloud
                                                        +-> tiled_exporter -> tileset_exporter -> 3D Tiles
```

That is the order `postprocess_cli` applies whatever order the flags are given in (`PASS_ORDER`, section 13): min-points and morph first, then absorb, then resolve, then an optional re-group. Absorption comes before `resolve` because it reasons about vegetation runs that `resolve` may relabel, and grouping comes last because every earlier pass can leave gaps worth closing.

**Round-tripping the store itself** is a separate axis from the chain above.
`reconstruct(mode="exact")` + `voxelize` is the only lossless pair, and it
applies to the **raw** store only:

```
shards/*.npz  --archive_cli pack-->  shards_laz/*.laz  --unpack-->  shards/*.npz
   (raw, per tile)                 (exact, 0.3-1.7x by LOD)         (bit-identical)
                                                                         |
                                        deterministic merge + grouping <--+
                                                                         |
                                                                    area.npz
```

`area.npz` is never archived: it is the *grouped* store, and grouping merges
intervals across empty z-gaps in a way that is not reliably invertible. It is
regenerated from the restored shards instead, which is why
`shards/manifest.json` now records `group_intervals` / `group_gap_m` /
`keep_classes` / `epsg` (`run_utils.make_provenance`). Full reasoning and
measurements: [`laz_roundtrip_design.md`](laz_roundtrip_design.md).

Dependency notes: `decoder` needs the ground index; `denoise` and `ray_trace` build on the decoder's states; `data_structures` lazily imports `ground_index` to cache `_gi`.

`postprocess_cli.py` gives the semantic passes a command line of their own: one store in, one store out, written atomically, with the input either a `.npz` or a raw `store_raw/` directory attached by memory map.

```
python -m voxelizer.postprocess_cli area.npz area_clean.npz \
    --min-points 4 --morph --absorb --resolve
```

The passes always run in the order min-points -> morph -> absorb -> resolve -> group, whichever order the flags are given in; each pass is skipped unless its flag is present; `--dry-run` reports what each would move without writing; `--stats` prints the full per-class point table for every pass rather than only the classes whose totals moved. Every store-driven tool downstream - the 2-D maps, the 3-D viewers, 3D Tiles, reconstruction - reads the result unchanged, which is what makes "the denoised, absorbed, resolved city as 3-D Tiles" a pipeline of existing commands.

They are still **not wired as automatic area-run stages** (the `STAGES` set is `stats / persist_npz / maps / col_diag / viz3d_roi / viz3d_full / viz3d_stream`, plus the internal `_crash_test`). Adding a `refine` stage - run the chain, persist a `city_model.npz` view alongside `area_raw.npz` - is the obvious next integration step.

### 3D Tiles Export

`tileset_exporter.convert_to_3d_tiles_lod()` is the default converter: it turns the streaming payload (`.bin` + `.idx.json`) into an OGC **3D Tiles 1.1** tileset - one `tileset.json` plus one `.glb` per node, full-detail leaves beneath a quadtree of coarsened interior levels. `convert_to_3d_tiles()` is the legacy flat root->leaves layout, kept behind `--flat`. Each `.glb` uses `EXT_mesh_gpu_instancing` - one shared unit box per class, per-instance TRANSLATION + SCALE - so a tile of N boxes ships ~Nx24 bytes of instance data (2 x VEC3 float32) rather than ~Nx700 bytes of triangulated vertex+index data. That is an order-of-magnitude cut in size and export time as a design estimate, not a benchmark: the triangulating exporter it would be compared against predates this repository (see `docs/design-decisions.md` section 11). The payload's 32-byte record layout, which the exporter reads, is: `xyz` (3xfloat32, 12 B), `sx,sy,sz` (3xfloat32, 12 B), `rgb` (3xuint8, 3 B), `cls` (5xuint8, 5 B -- only byte 0 used; bytes 1-4 are reserved). `tileset_cli.py` exposes `from-store` (store -> payload -> tileset in one call; the store is a `.npz` or a raw `store_raw/` directory, attached by `ColumnStore.load_any`) and `from-payload` (reuse an existing payload).

Two properties make the tileset loadable by a globe viewer.

**Georeferencing.** `_enu_to_ecef_transform()` reprojects the store origin from
EPSG:3946 through a local ENU frame into ECEF and writes the 4x4 as
`root.transform`, so Cesium places the tiles on the real globe instead of at
the earth's core. The orthometric-vs-ellipsoidal height difference is applied
automatically from the IGN RAF geoid grid via `pyproj` (~+49.70 m near Lyon).
`--no-geoid`, `--height-offset` and `--vertical-crs` (default `EPSG:5720`)
control it; if the grid cannot be resolved the exporter warns rather than
silently mis-placing the model.

**LOD pyramid.** `convert_to_3d_tiles_lod()` writes a real quadtree whose
interior nodes come from `_coarsen_records()`: per (column, class) union of
z-intervals onto a grid 2x coarser at each level up, not a vote over child
cells. `geometricError` is `error_factor` (8) times the level's voxel size,
halving down the tree, with `refine: "REPLACE"` and full-detail leaves at
`geometricError: 0.0` (the ladder is argued in
`docs/design-decisions.md` section 11). `--flat` selects the legacy flat root->leaves layout instead.

The pyramid is built STREAMING. Leaves are walked in Morton (Z-)order, whose
defining property is that the descendants of every quadtree node form one
contiguous run at every level; a node is therefore complete the moment its last
child arrives, and is written at once - children concatenated in identity order
so the content does not depend on the walk, emitted as a `.glb`, coarsened 2x
into its parent, then freed. Only one open node per level is ever held, so the
peak follows tree depth rather than area: measured peaks of 6.14 GB at 100 m
leaves and 6.09 GB at 50 m, and a 25 m export (11,780 leaves, 8 levels)
completes with the peak dominated by payload construction rather than by the
pyramid. Finer leaves also pay off client-side, where they let a 3D Tiles
client cull tightly enough to keep street-level detail within its per-instance
limits.

Covered by `tests/test_tileset_georeference.py`, `tests/test_tileset_lod.py`,
`tests/test_tileset_streaming.py` and `tests/test_tileset_bv_containment.py` -
the last pins a rule the LOD converter originally broke: every node's bounding
volume must contain its children's, which it guarantees by unioning each node's
box with its children's on the way back up (see
`docs/problem-solving.md` 5.7).

**Serving what was written.** A `tileset.json` is not a document a browser
renders, and a streaming `*_stream.html` that kept its `.bin` sidecar cannot
fetch byte ranges of it over `file://`. Both need a local HTTP origin, so the
package ships two servers and generates the launchers that start them.
`serve_voxel_html.py` serves the streaming viewer with HTTP Range support.
`serve_tiles.py` serves a tileset with the content types glTF viewers check
(`model/gltf-binary` for `.glb`, which CPython's `mimetypes` has no entry for)
and with caching off, since a tileset is regenerated in place under stable
file names and a cached tile is then a wrong tile. `launchers.py` writes the
`.cmd` files that carry which server, with which arguments, for which
directory: `view_stream.cmd` beside a `*_stream.html`, `view_cesium.cmd` and
`view_itowns.cmd` beside a `tileset.json`. For the three server launchers the console window is the
server's lifetime, and each file says so in its own header.
`python -m voxelizer serve DIR` reaches the same two servers by
hand, reading the kind off the directory. `VOXELIZER_BIND` overrides the
loopback bind default for containers that publish a port, and
`VOXELIZER_NO_BROWSER=1` suppresses the browser launch.

### The Multi-Viewer Roadmap (3D Tiles -> Unreal / Unity / Cesium / iTowns)

**Guiding principle: one canonical export, many consumers.** The georeferenced **3D Tiles 1.1** tileset the exporter writes is the hub. Every viewer either consumes 3D Tiles natively (Cesium, iTowns) or through a plugin (Unreal/Unity via Cesium for Unreal/Unity). Keep the LAZ export (`reconstruct.py`) as a parallel fast path for engine-native point rendering.

The plan below was written as four phases. Three of them have since been walked, so it is recorded here as history plus what is left.

**Phase 1 (delivered): iTowns.** iTowns was picked first because it is three.js-based (same stack as `visualizer3d.py`), French/IGN, and speaks RGF93/Lambert via proj4, so it places the tileset with minimal fuss and is the natural home for Grand Lyon data. A `C3DTilesLayer` on `tileset.json` renders the exported tileset.

**Phase 2 (delivered): Cesium.** With the exporter's ECEF transform, CesiumJS is a drop-in `Cesium3DTileset`. The same `tileset.json`, served as static files, renders at its true Lyon coordinates with detail streamed by the LOD pyramid, which also validated the transform for the engine plugins.

**Phase 3 (delivered for Unreal; Unity untried): the Cesium plugins.** *Cesium for Unreal* consumes the same tileset directly, inheriting its georeferencing and streaming/LOD, and the tileset renders in Unreal Engine through it - no bespoke importer. *Cesium for Unity* is the same route into Unity and has not been exercised here. *Engine-native alternative (parallel, cheap):* for pure point-cloud looks, export `reconstruct.store_to_laz()` and use Unreal's built-in **LiDAR Point Cloud** plugin (reads LAS/LAZ directly) or Unity's **Pcx** - the fastest way to simply *see* the data in-engine, at the cost of the voxel/LOD model.

**Phase 4 (not started): shared polish.** Optional greedy-meshing branch (collapse coplanar voxel faces into quads -> `.glb` meshes) for engines that prefer meshes / Unreal Nanite; per-instance point-count -> opacity/confidence shading; batched glb writes; `b3dm`/implicit tiling if tile counts explode. Enriching the tiles with per-instance metadata for click-and-query styling belongs here too.

| Target | Path | Status | Notes |
|---|---|---|---|
| **3D Tiles hub** | `convert_to_3d_tiles_lod()` in `tileset_exporter` | Shipped - the canonical export | unlocks everything else |
| **iTowns** | `C3DTilesLayer` on the hub | Renders | three.js-native, native Lambert |
| **Cesium** | `Cesium3DTileset` on the hub | Renders | uses the ECEF transform + geoid |
| **Unreal** | Cesium for Unreal on the hub | Renders | or LiDAR plugin on `reconstruct` LAZ (points) |
| **Unity** | Cesium for Unity on the hub | Not attempted | or Pcx on `reconstruct` LAZ (points) |

Every phase is integration on top of the tileset the exporter writes: nothing in the hub changed to reach any of the three viewers. The report's Section 5.3 records the same result. (The Unreal and Unity phases were integration experiments on the private workspace's tilesexport kit; this delivery ships the CesiumJS and iTowns viewers.)

### The Raw Store Is the Measurement of Record

The architectural decision underneath everything above is that the raw voxelized representation precedes optional semantic refinement. Area runs group intervals by default before rendering, but grouping and the other simplifications never overwrite the raw representation in memory. Persist that raw representation with `--keep-area-raw` (or retain the raw per-tile shards); the grouped or refined city model is a **view** of it. This means:

1. You can always go back to the original data
2. Different experiments can use different simplification rules
3. The raw store can be re-processed with better algorithms as they are developed

This follows the same principle as raw camera sensor data vs. a JPEG: you keep the raw.

Keeping it costs disk, and the ungrouped store is the largest artefact a run produces, so the retention flags are explicit. `--keep-area-raw` keeps `area_raw.npz`; in a sharded run it also triggers a second out-of-core merge pass to produce it alongside the grouped `area.npz`, at one band of peak memory either way. `--keep-raw-store` keeps `store_raw/`, the memory-mappable form the stages read, and is what `--resume-from-store` later needs. `--intermediates` is the umbrella policy over both plus `templaz/`: `auto`, the default, deletes them on full success and, if any stage failed, keeps everything and prints the exact `--resume-from-store` command. Declining the raw store loses no data in a sharded run: the ungrouped form lives in the per-tile shards, and can be rebuilt over a smaller sub-area with `--resume-shards --no-group-intervals`.

---

## 14. Glossary

| Term | Definition |
|------|------------|
| **ASPRS** | American Society for Photogrammetry and Remote Sensing; defines standard LiDAR classification codes |
| **Cell** | A single voxel, identified by grid indices `(ix, iy, iz)` |
| **Column** | All intervals sharing the same `(ix, iy)` - one vertical stack |
| **ColumnStore** | The main data structure; a column-compressed voxel grid |
| **IGN** | Institut national de l'information geographique et forestiere; French national mapping agency |
| **Interval** | A vertical run of consecutive voxels with the same class; `(z_start, z_end, cls, count)` |
| **LAZ** | Compressed LAS format; standard for LiDAR data exchange |
| **RGF93/CC46 (EPSG:3946)** | The projected CRS used by the Grand Lyon LiDAR data; zone CC46 of RGF93 |
| **LiDAR** | Light Detection and Ranging; laser-based 3D scanning from aircraft |
| **Multiset** | A set that allows multiple occurrences of the same element; here, a voxel can have multiple class records |
| **Partition** | A division where each element belongs to exactly one category; here, one class per voxel |
| **Point cloud** | An unordered collection of 3D points from a LiDAR scan |
| **RLE** | Run-Length Encoding; compressing repeated values into (value, count) pairs |
| **SVO** | Sparse Voxel Octree; a hierarchical acceleration structure for 3D queries |
| **Voxel** | Volumetric pixel; a cube in 3D grid space |
| **Voxelization** | The process of converting a point cloud into a voxel grid |

---

## Appendix: File Organization

The package is 49 modules including `__init__.py`, which carries the
authoritative inventory in dependency order (Levels 0-4 plus the CLI/GUI
tier). The listing below groups them by role.

```
voxelizer/
+-- __init__.py              # Package init (levelled re-exports, L0-L4)
+-- __main__.py              # CLI entry point (single-tile workflow: --shards, --keep-raw-store)
+-- _download_common.py      # Shared download utilities
+-- area.py                  # Multi-tile area workflow (process_area)
+-- area_cli.py              # CLI for area processing
+-- area_outputs.py          # Shared output writers + per-stage isolation (imported by both area_cli and sharding - keeps the layering acyclic)
+-- classes_config.py        # ASPRS/IGN class codes, names, colors
+-- cli_common.py            # Shared CLI argument definitions
+-- column_diagnostics.py    # Per-column visual diagnostics
+-- data_structures.py       # ColumnStore, Column, Interval (+ lazy ground-index cache)
+-- download_laz.py          # IGN tile downloading
+-- download_orthos.py       # Orthophoto downloading
+-- gui_area.py              # GUI for file / area selection + Store Tools (eight tabs)
+-- io_laz.py                # LAZ/LAS reading (laspy wrapper)
+-- pipeline.py              # Single-tile workflow (PNG maps, stats.txt)
+-- preflight.py             # RAM budget estimation
+-- run_utils.py             # Run directory management
+-- sharding.py              # Bounded-memory sharded area processing
+-- shard_diagnostics.py     # The column diagnostics and statistics computed from a SHARD SET, no merged store
+-- merge_streaming.py       # Out-of-core shard merge: the merged store assembled on disk, one key band at a time
+-- store_streaming.py       # Out-of-core reductions over a mapped store (folds, so they also compose over shards) + the atomic .npz writer
+-- stage_runner.py          # Per-stage child process isolation
+-- shard_worker.py          # Per-TILE child process isolation for sharded runs
+-- visualization.py         # 2D map rendering (matplotlib); exports: plot_column, plot_tile_map, render_tile_map (internal helpers: make_rasters, scatter_summaries, colorize_rasters, aggregation_factor, assign_overlap_lanes)
+-- voxel_runner_diagnos.py  # Runtime diagnostics monitor
+-- voxelize.py              # Core voxelization (_cells_from_points, _store_from_cells)
|
|   # -- Refinement / analysis / reconstruction layer (top-level; each takes a
|   #    ColumnStore and returns a new one; the semantic passes are reachable
|   #    from the CLI through postprocess_cli.py - see section 13) --
+-- resolve.py               # Collapse cross-class overlaps -> one label/voxel
+-- denoise.py               # Min-points + morphological cell-level filters
+-- absorb.py                # Vegetation->building envelope absorption
+-- ground_index.py          # Per-column ground_z_idx (anchors the decoder)
+-- decoder.py               # Three-way emptiness decoder (air/interior/subsurface)
+-- ray_trace.py             # full 3-D Amanatides-Woo DDA ray-walker
+-- ray_columns.py           # Same walker + the exact ceiling early-out for escaping rays
+-- transmittance.py         # Beer-Lambert transmittance along a ray (class-aware extinction)
+-- solar.py                 # Sun position (NOAA/Meeus) + grid-convergence correction
+-- sun_hours.py             # Per-column direct sun hours over a day arc
+-- surface_model.py         # The 2.5-D surface and raster arms of the objective (v) comparison
+-- reconstruct.py           # ColumnStore <-> LAS/LAZ (density/one_per_voxel/exact)
+-- archive_cli.py           # pack/unpack raw shards as exact LAZ (verified)
+-- postprocess_cli.py       # CLI for the passes above: store in, store out (min-points/morph/absorb/resolve/group)
|
|   # -- 3-D visualisation (flat files, no subpackage) --
+-- serve_voxel_html.py      # HTTP server for the streaming viewer (Range/206)
+-- serve_tiles.py           # HTTP server for a 3-D Tiles directory (glTF content types, no caching, bundled viewer pages)
+-- launchers.py             # Generated view_stream.cmd / view_cesium.cmd / view_itowns.cmd
+-- tiled_exporter.py        # Streaming tiled payload (.bin + .idx.json) + view-dependent viewer
+-- visualizer3d.py          # 3-D HTML/Three.js viewer (compact) + PLY/PyVista
+-- viz3d_cli.py             # 3-D rendering CLI: single / from-store / stream
|
|   # -- 3D Tiles export --
+-- tileset_exporter.py      # .bin/.idx.json -> tileset.json + per-tile .glb (EXT_mesh_gpu_instancing)
+-- tileset_cli.py           # CLI: from-store / from-payload
|
+-- data/                    # Bundled static viewer pages (Cesium, iTowns) served by serve_tiles
```

---

*Project: UD-IArbre (VCityTeam / LIRIS, Universite Lumiere Lyon 2)*
*Author: Nikolaos Vynios - Research Intern*
