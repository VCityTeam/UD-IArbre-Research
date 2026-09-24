# How Columns, Gridding, and Voxels Work

## A) Simple Terms

Imagine you take a drone photo of a forest and divide it into a grid of
squares, like graph paper laid over the landscape. Each square on the paper
is one **column** - a vertical tube reaching from the ground up through
the air.

Inside each column, LiDAR laser pulses hit different things at different
heights: ground, bushes, tree trunks, leaves, maybe a roof. Each of those
hits is a **point** - a tiny dot in 3D space with an x, y, z coordinate
and a label (class) saying what it hit.

The voxelizer takes all those dots and sorts them into the grid. For each
column, it looks up and says "at height 0-0.5 m there's ground, at
1.5-8 m there's a tree trunk, at 8-15 m there's canopy." Each of those
vertical spans is an **interval** - a compressed description of what's in
that part of the column.

The result: instead of storing 60 million individual dots, you store
roughly 3 million vertical spans. Much smaller, much faster to query.

## B) Detailed Explanation

### The Horizontal Grid

The input is a `.laz` file - a classified LiDAR point cloud. Every point
has `(x, y, z)` coordinates in metres (typically RGF93/CC46 EPSG:3946 for IGN data)
and a `classification` code (ASPRS standard: 2=ground, 5=high vegetation,
6=building, etc.).

The voxelizer first computes the bounding box of all points:

```
x_min = min(x),  y_min = min(y),  z_min = min(z)
```

It then overlays a regular 2D grid on the horizontal plane:

```
ix = floor((x - x_min) / cell_xy)
iy = floor((y - y_min) / cell_xy)
```

where `cell_xy` is the horizontal cell size in metres (default **0.5 m**, set
once in `voxelizer/cli_common.py:add_cell_args` and matched by `voxelize()` and
`run_area()`; the worked figures below use 1.0 m purely because it makes the
arithmetic legible).
Each unique `(ix, iy)` pair identifies one **column** - a vertical tube
with a `cell_xy x cell_xy` footprint.

For a standard 500 m x 500 m IGN tile at `cell_xy=1.0 m`, the grid is
500 x 500 = 250,000 potential columns. Only columns that received at
least one LiDAR point actually exist in memory.

### The Vertical Axis (Voxels)

Within each column, the z-axis is sliced into **voxels** of height
`cell_z` (default 0.5 m):

```
iz = floor((z - z_min) / cell_z)
```

Each voxel is a small box of size `(cell_xy x cell_xy x cell_z)` metres.
A voxel at index `iz` covers the altitude range:

```
[z_min + iz x cell_z,  z_min + (iz + 1) x cell_z)
```

### From Points to Unique Cells

All points are sorted canonically: by `(ix, iy, class, iz)` under the
default `class_first` run order, or by `(ix, iy, iz, class)` under the
`height_first` compatibility order that every pre-switch store was built
with. Either way, consecutive points that share the same
`(ix, iy, iz, class)` tuple are grouped into one **unique cell** with a
point count. This is the first compression step - a single
1 m x 1 m x 0.5 m voxel might receive 20 LiDAR returns, but if they
all have the same class, they collapse to one entry.

Crucially, if a voxel contains points of *different* classes (e.g., both
class 4 and class 5), it produces **two separate entries** at the same
z-index. The voxelizer preserves all class information rather than doing
a majority-vote collapse.

### RLE Compression: Intervals

The unique cells arrive in that same canonical order, `(ix, iy, class, iz)`
by default. The voxelizer then detects **intervals** - maximal vertical runs
where:

- `(ix, iy)` stays the same (same column)
- `class` stays the same (same material)
- `iz` increments by exactly 1 each step (no gaps)

Any break in these conditions starts a new interval. For example, in one
column you might get:

```
iz=10, class=ground      -+
iz=11, class=ground       |- interval 1: ground,    voxels 10-12  (3 voxels)
iz=12, class=ground      -+
iz=13  (empty - air gap, no points)      <- gap -> next run starts a new interval
iz=14, class=low_veg     -+
iz=15, class=low_veg     -+- interval 2: low_veg,   voxels 14-15  (2 voxels)
iz=16, class=med_veg     -+
iz=17, class=med_veg     -+- interval 3: med_veg,   voxels 16-17  (2 voxels)
iz=18, class=high_veg    -+
iz=19, class=high_veg     |
iz=20, class=high_veg     |- interval 4: high_veg,  voxels 18-22  (5 voxels)
iz=21, class=high_veg     |
iz=22, class=high_veg    -+
```

That column produces **4 intervals** from 12 occupied voxels. The RLE
compression ratio is 12/4 = 3.0x for this column.

> Counting the iz rows listed above gives 3 + 2 + 2 + 5 = 12 occupied
> voxels in 4 intervals, which is what `ColumnStore.from_intervals`
> yields for the same data.

### How Columns Are Counted

The number of occupied columns equals the number of distinct `(ix, iy)`
keys in `store.columns`. For a 500 m tile at 1 m resolution, the
theoretical maximum is 250,000, but columns with no LiDAR returns (open
water, data gaps) don't exist.

### Storage Layout

The store is not a container of per-column objects. All intervals of the
whole tile live in four flat parallel numpy arrays, and two more arrays say
which slice of them belongs to which column:

| Array | dtype | Bytes | Meaning |
| --- | --- | --- | --- |
| `_zs` | int32 | 4 | First voxel index of each interval (inclusive) |
| `_ze` | int32 | 4 | End voxel index of each interval (exclusive, Python-style) |
| `_cl` | uint8 | 1 | ASPRS class code for the interval |
| `_ct` | int32 | 4 | Total LiDAR points that fell into this interval |
| `_keys` | uint64 | 8 | One packed `(ix, iy)` per column, sorted ascending |
| `_off` | int64 | 8 | Per-column start offset into the four interval arrays |

So an interval costs **13 bytes** and a column costs **16 bytes** - measured
on a fresh build-and-save of a real tile by `code_verification/exp04`. For the
roughly 3 million intervals of the 500 m tile sketched at the top of this
document, that is 3,000,000 x 13 B = **39 MB**, plus at most
250,000 x 16 B = 4 MB of column index. The retired design held
one Python `Column` object per column and a list of `Interval` dataclasses;
`tracemalloc` puts those at 209 B per interval, so the same 3 million
intervals would have cost 627 MB - about 16x more.

### The ColumnStore

The `ColumnStore` holds everything:

- `x_min, y_min, z_min`: origin of the grid in metric coordinates
- `cell_xy, cell_z`: voxel dimensions in metres
- the six flat arrays above, plus `_gi`, a lazily computed per-column
  ground index used by the decoder

Only occupied columns appear in `_keys`, so empty grid cells (no LiDAR
points) cost nothing. `store.columns` still reads like the old
`(ix, iy) -> Column` mapping - `len()`, `in`, `[]`, `.get()`, `.items()` -
but it is a read-only view: indexing it hands back a `Column` whose four
arrays are zero-copy slices of the flat arrays rather than a stored object.
Because the keys are sorted and the packing preserves `(ix, iy)` order,
finding one column is a single `np.searchsorted`.
