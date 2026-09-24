# Points vs. Intervals

## A) Simple Terms

A **point** is a single dot that a LiDAR laser recorded - one measurement
of "there's something here at this exact location." A single tree might
produce thousands of points as the laser bounces off different leaves.

An **interval** is a compressed summary of a vertical run of same-type
material. Instead of listing every individual dot, the voxelizer says
"from here to here, it's all tree." One interval might represent
hundreds of points stacked vertically.

Think of it like a zipped file: points are the raw data, intervals are
the compressed version. The more vertical continuity there is (long
stretches of the same class), the better the compression.

## B) Detailed Explanation

### Points: The Raw Input

Each LiDAR return is a **point** with four attributes:

| Attribute | Type | Example |
| --- | --- | --- |
| `x` | float64 | 1841346.5 m (RGF93/CC46 EPSG:3946) |
| `y` | float64 | 5182781.5 m (RGF93/CC46 EPSG:3946) |
| `z` | float64 | 365.2 m (altitude) |
| `classification` | uint8 | 5 (high_vegetation) |

The example values are a representative point of tile 18410_51825, the
tile worked through below: its extent is x 1841000.0-1841499.99,
y 5182500.0-5182999.99 and z 247.08-448.81 m, with a median z of 366.5 m.

Across the whole Grand Lyon 2023 corpus (2,842 tiles), a tile holds
anywhere from 7,676 to 60,937,237 points - median 16.2 million, mean
18.3 million (`tests/diagtests/genstats/LAZ_STATS.md`, section 1; that
frozen census is kept in the private monorepo). The
file `18410_51825.laz` is the largest of them, with 60.9 million points.

Points are **not** the voxelizer's output. They're the input. The
voxelizer reads them, assigns each to a grid cell, and compresses.

### Unique Cells: The Intermediate Step

Before RLE compression, the voxelizer groups points into **unique cells**.
A unique cell is defined by `(ix, iy, iz, class)` - one specific voxel
position with one specific class.

If 15 LiDAR returns hit the same 1 m x 1 m x 0.5 m voxel and all are
class 5 (high_vegetation), they collapse to one unique cell with
`count=15`. This is the first compression: 15 points -> 1 entry.

But if that same voxel has 12 class-5 returns and 3 class-4 returns
(mixed vegetation), it produces **two** unique cells at the same `iz`:
one for class 5 (count=12) and one for class 4 (count=3). The voxelizer
does not merge or vote - it preserves both.

### Intervals: The Compressed Output

An **interval** is a maximal vertical run of unique cells where:

1. The column `(ix, iy)` is the same
2. The class is the same
3. Consecutive z-indices differ by exactly 1

Formally, given sorted unique cells within one column:

```
Cell 1: iz=5,  class=2 (ground)
Cell 2: iz=6,  class=2 (ground)      <- same class, iz increments by 1 -> same interval
Cell 3: iz=7,  class=2 (ground)      <- same class, iz increments by 1 -> same interval
Cell 4: iz=8,  class=5 (high_veg)    <- class changed -> new interval
Cell 5: iz=9,  class=5 (high_veg)
```

This produces **2 intervals** from 5 cells:

```
Interval 0: z_start=5, z_end=8 (exclusive), class=2, count=sum of points in cells 5,6,7
Interval 1: z_start=8, z_end=10 (exclusive), class=5, count=sum of points in cells 8,9
```

Note: `z_end` is **exclusive** (Python slice convention). An interval
with `z_start=5, z_end=8` covers voxels 5, 6, and 7 - that's 3 voxels
tall, or 3 x cell_z = 1.5 m at the default 0.5 m resolution.

### Compression Ratios

For tile 18410_51825 (cell_xy=1.0 m, cell_z=0.5 m):

| Metric | Value |
| --- | --- |
| Input points | 60,937,237 |
| Unique cells | (intermediate, not stored) |
| Output intervals | 3,423,078 |
| **Compression ratio** | **~17.8x** |

The compression ratio varies by class. The four classes below are the
ones worth commenting on, not the whole tile: they account for 52.3 M of
the 60.9 M points (85.7 %) and 2.14 M of the 3.42 M intervals (62.4 %).
Classes 4, 3 and 1 make up the remainder and are omitted.

| Class (partial - 4 of the 7 present) | Points | Intervals | Ratio | Why |
| --- | ---: | ---: | ---: | --- |
| ground | 7,619,435 | 708,802 | 10.7x | Flat surface, but split across many columns |
| high_vegetation | 22,859,898 | 909,752 | 25.1x | Dense canopy, long vertical runs |
| class_8 | 21,347,267 | 508,381 | 42.0x | Large contiguous blocks |
| building | 425,550 | 9,652 | 44.1x | Solid geometric shapes, few class breaks |

Lower ratios (ground at 10.7x) mean the class is spread across many
columns with relatively thin vertical runs. Higher ratios (building at
44.1x) mean the class forms solid blocks that compress well.

### What `avg_intervals_per_column` Tells You

This metric (from `stats.txt`) is the total intervals divided by occupied
columns. The figure below is not that: for tile 18410_51825,
3,423,078 / 250,000 = **13.69** divides by the tile's whole 500 m x 500 m
grid at 1 m, so it is intervals per grid cell. Occupied columns are a
subset of those 250,000, so the per-occupied-column figure is at least
13.69; no shipped artifact records the occupied count for this tile at
1.0 m xy / 0.5 m z, so the exact value is not quotable here.

- **1.0-2.0**: Clean column - ground + one vegetation layer, minimal
  class mixing. Ideal for RLE.
- **2.0-5.0**: Moderate complexity - ground + multiple vegetation layers
  or a building edge. Normal for mixed urban/natural terrain.
- **5.0-15.0**: Fragmented - lots of class switching within the column.
  Often caused by mixed-class voxels, class_8 contamination, or LiDAR
  returns spanning vegetation height thresholds.
- **15.0+**: Highly fragmented - the column classification is very noisy.
  Every class change breaks the RLE.

The histogram of intervals per column (available as `histogram_intervals.png`
in the diagnostics output) shows the distribution across the whole tile.
A long tail on the log-log plot indicates a "wall-fragmentation tail" - 
columns where LiDAR returns on vertical surfaces (building walls, tree
trunks) produce many short intervals.

### Why Intervals Matter

Intervals are the fundamental unit for everything downstream:

1. **Storage**: the persisted store costs 13 bytes per interval plus
   16 bytes per column (`data_structures.py`, re-measured by
   `code_verification/exp04`). For tile 18410_51825 that is
   3,423,078 x 13 B = 44.5 MB of intervals plus at most 250,000 x 16 B =
   4.0 MB of column index (250,000 is the tile's full 500 x 500 grid at
   1.0 m; only occupied columns are actually stored), so 48.5 MB at the
   outer bound - against 60,937,237 x 28 B =
   1,706 MB (1.71 GB) for the same points as uncompressed LAS 1.2 point
   format 1 records. That is a 35x reduction in bytes.
2. **Visualization**: Each interval becomes one colored box in the 3D
   viewer or one horizontal band in the column plot.
3. **Queries**: "What's at height X?" becomes a binary search over
   sorted intervals in the relevant column - O(log n) instead of
   scanning all points.
4. **Diagnostics**: The column plots (`col_ix*_iy*.png`) show intervals
   directly. Each colored band is one interval; gaps between bands are
   empty voxels (air).
