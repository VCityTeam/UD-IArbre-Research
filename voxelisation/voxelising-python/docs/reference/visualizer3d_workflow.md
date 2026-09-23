# 3D Visualization - How `visualizer3d.py` Works

This guide covers one of the three 3-D export paths. The other two are
`tiled_exporter.py` (the streaming payload and its view-dependent viewer) and
`tileset_exporter.py` (3D Tiles for Cesium and Unreal). All three are compared
in [`02_visualization_formats.md`](02_visualization_formats.md), which also
records why three formats exist, what they share through `viz_common.py`, and
the option of dropping the PyVista arm if that dependency is ever unwanted.

## A) Simple Terms

### The Big Idea

Imagine you have a 3D jigsaw puzzle made of coloured boxes. Each box
represents a chunk of air captured by a LiDAR scan - ground, building,
tree, etc. The `visualizer3d.py` module takes the compressed column
store (a compact list of "from height X to height Y, it's class C")
and explodes it into actual 3D boxes you can fly through.

### The Three Viewing Modes

| Mode | What you get | What you need |
| --- | --- | --- |
| **HTML** | A single `.html` file you double-click to open in a browser. You fly through with WASD keys like a video game. | A browser with network access on first load. |
| **PyVista** | An interactive 3D window that pops up inside Python. | `pip install pyvista` (optional). |
| **PLY** | A `.ply` file you open in CloudCompare, Blender, MeshLab, etc. | Any 3D viewer. Most portable format. |

The HTML path is the main one. It produces a file you can email to
someone, and they just open it in Chrome/Firefox - no Python and no server.
The voxel data is self-contained (base64 in the page), but the Three.js
modules are fetched from a CDN (unpkg, plus a pako fallback from jsdelivr on
browsers without native `DecompressionStream`), so the first load needs
network access.

There is a fourth path, built on a different module: the **streaming
viewer** (`tiled_exporter.py`), which carries every interval and bounds the
GPU working set at view time rather than thinning the payload. It is
described at the end of this document.

### How the HTML File Works

Think of it like a recipe card:

1. **Collect the ingredients.** Walk every column, every interval, and
   turn each one into a box with a position, height, and colour.

2. **Pack the boxes into a suitcase.** Squeeze all the box data
   (positions, heights, colours) into one binary blob, then encode it
   as base64 text so it can sit inside an HTML file.

3. **Write the recipe.** The HTML file is a template with the data
   baked in. It includes Three.js (a 3D graphics library) from a CDN,
   and JavaScript that unpacks the data and draws the boxes using
   GPU-instanced rendering - one cube prototype, thousands of copies.

4. **Open it.** Double-click the file. Your browser loads Three.js,
   decodes the base64 blob, creates an InstancedMesh with one box per
   interval, and lets you fly through with WASD + mouse.

### The Auto-Thinning Trick

A full 500 m tile (median ~16M points) can have millions of intervals. That's too many boxes
for smooth rendering. The module auto-thins: it skips every Nth column
until the box count drops below a budget (default 5,000,000). You still
see the overall structure; you just lose some fine detail. For a close-up
of a small area, it renders everything without thinning.

Thinning is what the streaming viewer avoids: it writes every interval and
decides at view time which spatial tiles are resident on the GPU. When
completeness matters, that is the path to take.

---

## B) Detailed Explanation

### Entry Points

The module has three primary entry points:

```python
from voxelizer.visualizer3d import export_html, show_pyvista, export_ply
# or, via the package front door:
# from voxelizer import export_html, show_pyvista, export_ply
```

It also exports the geometry collector and merger, the `.vxg` grid codec,
the thin-stride estimator and the shared `render_store_3d` driver that the
CLI and the area pipeline both call.

All three share the same geometry collection step via
`_collect_voxel_geometry()`, which flattens the `ColumnStore` into
parallel NumPy arrays. The backends differ only in how they consume
those arrays.

### The Geometry Pipeline

Every backend starts with this shared step:

```
ColumnStore
  |
  v
_collect_voxel_geometry(store, classes, region, stride, max_boxes)
  |
  v
dict {
  centers:   float32 (N, 3)  - box centres (centred on tile mid-point)
  sizes_z:   float32 (N,)    - vertical extent of each box in metres
  classes:   uint8   (N,)    - ASPRS class code per box
  colors:    uint8   (N, 3)  - RGB per box (from CLASS_COLORS)
  cell_xy:   float           - horizontal footprint in metres
  origin:    (x, y, z)       - metric offset subtracted from centres
  n_boxes:   int             - == N
  applied_stride: int        - the stride that was actually used
  bbox:      (xmin, ymin, zmin, xmax, ymax, zmax) in centred coords
}
```

#### Step 1: Region & Class Filtering

Before expanding any intervals, columns are filtered:

- **Region filter:** If a metric bounding box `(x0, y0, x1, y1)` is
  given, only columns whose centre falls inside the box are kept. This
  is how the ROI view works - you zoom into a 200 m square at full
  detail.

- **Class filter:** If a whitelist of class codes is given (e.g.
  `{5, 6}` for vegetation + buildings), only intervals with those
  classes are kept. This cuts work by orders of magnitude before
  any boxes are created.

Both filters operate on column keys `(ix, iy)`, not on individual
intervals, so they're fast.

#### Step 2: Stride / Auto-Thinning

The `stride` parameter keeps one out of every `stride x stride` columns
in a spatially regular grid. `stride=1` keeps everything, `stride=4`
keeps ~6%.

If `max_boxes` is set and the estimated box count exceeds the budget,
the module auto-calculates an additional stride factor:

```python
extra = ceil(sqrt(expected_boxes / max_boxes))
applied_stride = stride * extra
```

The estimate uses the average intervals per column from a 1000-column
sample, which is tight enough to size the stride correctly without
expanding every interval first.

#### Step 3: Expand Intervals -> Per-Box Arrays

One vectorized gather, no Python loop over columns and no second pass.
The kept columns give a per-column interval count `niv_k`; the flat index
array into the store's interval arrays is built in one expression,
`np.repeat(off[cols], niv_k)` for the start offset of each column repeated
over its run, plus a within-run ramp, and `z_start`, `z_end` and `cls` are
then read by fancy-indexing with it.

Deriving both "how many" and "where" from the same `_off` array is the
point: the earlier code counted in one pass and filled in another, and the
two could disagree, which is how a native access violation got in. With
one expression that divergence cannot come back.

For each interval `(ix, iy, z_start, z_end, cls)`:

```
cx = x_min + (ix + 0.5) * cell_xy - cx0    # centred x
cy = y_min + (iy + 0.5) * cell_xy - cy0    # centred y
zc = (z_start + z_end) * 0.5 * cell_z + (z_min - cz0)  # centred z
h  = (z_end - z_start) * cell_z            # box height
```

The centre coordinates `(cx0, cy0, cz0)` are the mid-point of the
bounding box of kept columns. Subtracting this keeps all coordinates in
the range `[-250, +250]` for a 500 m tile, which is well inside Float32
precision. RGF93/CC46 (EPSG:3946) coordinates are ~10^6 m, and Float32 has ~7
significant digits - without centring, you'd lose ~1 m of precision.

#### Step 4: Colour Lookup

A 256-entry RGB lookup table is built once from `CLASS_COLORS`:

```python
lut = np.full((256, 3), 128, dtype=np.uint8)  # grey fallback
for code, rgb in CLASS_COLORS.items():
    lut[code] = rgb
colors = lut[classes_out]
```

This is a single NumPy fancy-index operation - fast for any N.

#### Step 5: Bounding Box

The bbox in the centred frame is computed from the extremes of all box
centres +/- half-extents. This is used by the HTML template to position
the camera and ground plane.

---

### HTML Export (`export_html`)

#### Data Packing

The boxes ship as grid indices, not floats. Each box is described by its
column and vertical run `(ix, iy, iz0, nz)`, and the four index arrays
are packed back-to-back into one struct-of-arrays blob:

```
[N x uintK]  ix     K = 16, 32 or 64 - the narrowest dtype
[N x uintK]  iy         that holds every index in this store
[N x uintK]  iz0
[N x uintK]  nz
```

Boxes are sorted by class first, so each class is one contiguous slice;
the header carries a `[code, start, count]` table plus the scalar
reconstruction constants (`x_min`, `y_min`, `z_min`, `cell_xy`,
`cell_z`, the per-axis index minima and the centring origin). Colours
are not shipped: the browser rebuilds them from the class table.

At the usual uint16 width that is 8 bytes per box before compression.
The blob is zlib-compressed and then base64-encoded into the HTML, and
because the indices are near-monotone integer runs the compressor
crushes them: a 500k-box export measures ~0.4 MB, where the same box
count would need ~4 MB raw, or ~5.4 MB once base64 expansion is counted,
if the indices were incompressible.

#### The HTML Template

The template (`_HTML_TEMPLATE`) is a self-contained HTML file with:

1. **CSS:** Dark theme, floating panels for info/legend/help, loading
   screen.

2. **Import map:** Loads Three.js 0.160.0 from the unpkg CDN.

3. **JavaScript module:**
  - Decodes the base64 blob, inflates the zlib stream through the
     browser's native `DecompressionStream` (falling back to pako from
     jsdelivr where that is missing), and reads the result as typed
     arrays.
  - Creates a `THREE.Scene` with z-up orientation (matches LiDAR).
  - Sets up two directional lights + ambient for depth-cued shading.
  - Creates one `THREE.InstancedMesh` per class, all sharing a unit
     `BoxGeometry`. The payload is class-sorted, so each class owns a
     contiguous slice of it.
  - Fills the instance matrix: scale to `(cell_xy, cell_xy, sizeZ)`,
     position to `(cx, cy, cz)`, written straight into
     `instanceMatrix.array` with no `Matrix4` allocated per box.
  - Gives each mesh a flat material in its class colour, so there is no
     per-instance colour buffer at all.
  - Adds a dark ground plane at `zmin - 0.5` for orientation.
  - Sets up two camera modes: OrbitControls (default) and pointer-lock
     fly-through (WASD + Q/E).
  - Builds a class legend with checkboxes. Toggling a checkbox sets that
     class's `mesh.visible`, which drops the whole draw call.
  - Animation loop: updates camera, renders, shows FPS + camera pos.

#### Class Toggle Mechanism

An `InstancedMesh` cannot cheaply add or remove instances, so the classes
are separated before they reach the GPU: the payload is sorted by class,
each class gets its own mesh over its own contiguous slice, and a toggle
is `mesh.visible = checked`. That is O(1) whatever the class holds, the
GPU skips the entire draw call, and no instance matrix is ever rewritten.

#### Camera Setup

Default position: oblique view looking at the bbox centre from 60% of
the span away, offset in x/y/z. Press `T` to toggle between orbit and
fly mode. In fly mode, `W/A/S/D` move, `Q/E` down/up, `Shift` sprint
(4x speed), mouse wheel adjusts base speed, `R` resets.

---

### PyVista Export (`show_pyvista`)

Builds a single `pyvista.PolyData` mesh from all boxes:

1. Each box = 8 vertices + 12 triangles (unit cube scaled per-box).
2. Vertices computed vectorized: `centers[:, None, :] + unit * sizes`.
3. Triangle indices tiled and offset per box.
4. Cell colours repeated 12x (one per triangle).
5. Rendered with `pv.Plotter`, eye-dome lighting for depth.

Returns the `PolyData` so callers can save to `.vtm` if desired.

---

### PLY Export (`export_ply`)

Writes a binary little-endian PLY file:

- Vertices: interleaved `float32 x/y/z + uint8 r/g/b` (or `float64`
  if `keep_world_coords=True` for GIS overlay).
- Faces: `uint8 count=3` + three `int32` indices per triangle.
- Same cube mesh construction as PyVista but emitted directly to the
  binary file.

---

### CLI Entry Point (`viz3d_cli.py`)

Three subcommands:

```powershell
python -m voxelizer.viz3d_cli single     PATH.laz  -o OUTPUT_DIR [OPTIONS]
python -m voxelizer.viz3d_cli from-store STORE -o OUTPUT_DIR [OPTIONS]      # .npz or store_raw/
python -m voxelizer.viz3d_cli stream     STORE --out PAGE.html [OPTIONS]    # .npz or store_raw/
```

`single` voxelizes one LAZ tile and renders from the result.
`from-store` renders from a saved store, such as a run's `area.npz` or its
raw `store_raw/` directory, with no
voxelization at all - the grid is loaded from disk, which is how a finished
run's viewers are re-rendered without repeating the expensive part.
`stream` writes the streaming viewer described below.

`single` and `from-store` produce two HTML files:

| File | Description |
| --- | --- |
| `<label>_full.html` | Whole tile or area, auto-thinned to `--max-boxes` (default 5M). |
| `<label>_roi.html` | Square ROI of `--roi-size` metres (default 200), full detail. |

`single` voxelizes once (`voxelize_laz`) and calls `export_html` twice with
different parameters. ROI centre defaults to the occupied area's centre, not
the nominal tile corner, so edge tiles get a useful view.

Key options for `single` and `from-store`:

| Option | Default | Description |
| --- | --- | --- |
| `--cell-xy` | `0.5` | Horizontal voxel size in metres (`single` only) |
| `--cell-z` | `0.5` | Vertical voxel size in metres (`single` only) |
| `--max-boxes` | `5000000` | Auto-thinning budget for the full view |
| `--roi-size` | `200.0` | Side of the square ROI window in metres |
| `--roi-cx` / `--roi-cy` | occupied centre | ROI centre in RGF93/CC46 (EPSG:3946) metres |
| `--no-full` | - | Skip the full HTML |
| `--no-roi` | - | Skip the ROI HTML |
| `--grid` | off | Also write a `.vxg` grid cache, the same lossless quantized encoding the HTML embeds. `load_grid()` reads it back into a geometry dict for `export_html_from_geom` without re-reading the LAZ |
| `--label` | the `.npz` stem | Base name for the output files and the page title (`from-store` only) |
| `--delete-laz` | off | Delete the source file after a successful voxelization (`single` only) |

The store argument of `from-store` and `stream` is a `.npz` **or** a raw
`store_raw/` directory, attached by memory map (`ColumnStore.load_any`).

---

### The Streaming Viewer (`tiled_exporter.py`, `viz3d_cli stream`)

The singleton export answers "too many boxes" by removing boxes. The
streaming path answers it by keeping all of them on disk and bounding what
the GPU holds. The payload is written as a `.bin` sidecar plus a spatial
index, tiled at `--tile-m` metres (default 64), and the page fetches byte
ranges of it as the camera moves.

| Option | Default | Description |
| --- | --- | --- |
| `--out PATH` | required | The `.html` to write; the `.bin` sibling is automatic |
| `--max-instances N` | `4000000` | GPU working-set budget in instances (about 76 B of GPU memory each, so 4M is roughly 0.3 GB). It decides how many tiles are resident, never how many boxes inside them: the page keeps the tiles nearest the camera, and where it looks, at full detail up to the budget and draws the rest as cheap per-tile impostor silhouettes |
| `--tile-m F` | `64` | Spatial tile size in metres. Smaller tiles cull more finely; larger ones make fewer, bigger range requests |
| `--inline-threshold-mb N` | `64` | Base64-embed the `.bin` in the page and delete the sidecar when it is smaller than this |
| `--region XMIN YMIN XMAX YMAX` | whole store | Metric subset of the store |
| `--keep-classes` | every class | Comma/space list of ASPRS classes |
| `--stride`, `--max-boxes` | - | Accepted for compatibility and **ignored**, with a warning printed. The exporter always writes every interval at full detail; shrink the payload with `--region` or `--keep-classes` |

Nothing is strided and no record is dropped, so the payload always holds
every interval. A page that kept its `.bin` sidecar cannot be opened from
the file system, because a `file://` page cannot fetch byte ranges of a
sidecar; it needs an HTTP origin. That is what `serve_voxel_html.py`
provides, and what the generated `view_stream.cmd` beside the page starts
with a double-click. `python -m voxelizer serve DIR` is the same server
invoked by hand. The console window a launcher opens is the server's
lifetime, and closing it stops the server. A small export whose payload was
inlined into the page opens either way.

The streaming viewer is exported from one merged store, because
`tiled_exporter` walks a single store's key order. A shard-only run does not
have one, which is why `--viz3d-stream` is refused there without
`--merge-shards`.

---

### Performance Characteristics

| Metric | Value |
| --- | --- |
| 500 m tile at `cell_xy=1.0` (median ~16M points) | ~500k-3M intervals, under the 5M default budget, so no auto-thin |
| HTML file size (500k boxes) | ~0.4 MB on a synthetic export; the uncompressed bound is 8 B/box, so ~4 MB |
| HTML load time (Chrome) | 1-3 seconds |
| InstancedMesh render | 60 FPS on mid-range GPU |
| 200 m ROI (no thinning) | ~50k-200k boxes, well under 1 MB of HTML |

### Coordinate System

- Data uses RGF93/CC46 (EPSG:3946), z = altitude.
- HTML viewer is z-up (`camera.up.set(0, 0, 1)`).
- Centred coordinates: subtract the tile centre, which puts a 500 m tile
  in `[-250, +250]`, for Float32 precision.
- `keep_world_coords=True` in PLY export restores original RGF93/CC46 (EPSG:3946)
  values (requires Float64).
