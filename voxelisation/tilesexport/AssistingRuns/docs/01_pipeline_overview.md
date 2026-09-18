# 01 - Pipeline overview: from voxel run to 3D Tiles

This explains what actually happens when a run is exported, and what every
file in a `RunN/` folder means. No 3D Tiles background assumed.

## The chain

```
area_cli / stage_runner            tileset_cli from-payload
   (voxelisation)                     (3D Tiles export)
        |                                   |
        v                                   v
area_stream.bin  ---------------->  tileset.json
area_stream.idx.json                tile_*.glb    (full-detail leaves)
   (tiled record payload)           node_*.glb   (LOD overview levels)
```

### Input: the tiled record payload

The voxeliser's `viz3d_stream` stage writes every voxel interval of the
area as a flat binary stream of **32-byte records**, spatially grouped
into 100 m x 100 m tiles:

| field | type | meaning |
|---|---|---|
| `xyz` | 3 x float32 | box **centre**, metres, area-local coordinates |
| `siz` | 3 x float32 | box dimensions (0.25 x 0.25 x height) |
| `rgb` | 3 x uint8 | class colour |
| `cls` | 5 x uint8 | class code (first byte) + padding |

`area_stream.idx.json` is the table of contents: per-tile byte offsets,
counts, bounds, plus the grid geometry (`cell_xy=0.25`, `cell_z=0.1`,
`tile_m=100`, area bbox, shared origin in EPSG:3946).

One record is one *merged vertical run* of same-class voxels in one
column: the payload is written from the **grouped** store
(`--group-intervals`, the default), which is why the source area's
751 M points become only 187 M records. Naming note: this grouped
0.25 m x 0.1 m production run is recorded in the internship report as
`Run8`; the raw store of the same configuration holds 317 M intervals
(see Table 11 of the report for the full resolution/grouping sweep). Do
not confuse it with `tilesexport/Run1`, which is the export produced
from it. No run output is tracked in this repository: `outputs/` holds
local runs only, and the Quickstart in the root `README.md` regenerates
one.

### The export command

Once you have produced an `area.npz` and exported its payload with
`--viz3d-stream`, stage 2 reads that payload:

```powershell
python -m voxelizer.tileset_cli from-payload ^
    outputs\RunN\area_output\area_stream.idx.json ^
    outputs\RunN\area_output\area_stream.bin ^
    --out-dir tilesexport\Run1 --crs EPSG:3946
```

By default this builds the **LOD pyramid** layout (`--flat` restores the
legacy single-level export, which no viewer handles well at this scale -
see `03_troubleshooting.md`).

## What the exporter produces

### 1. Leaf tiles - `tile_N.glb`

Each 100 m payload tile becomes one binary glTF. The geometry is **GPU
instancing** (`EXT_mesh_gpu_instancing`): a single 36-vertex unit cube is
stored once, and every record contributes only a per-instance
`TRANSLATION` + `SCALE` (24 bytes on the GPU). One mesh/material per
class gives the class colouring. This is roughly an order of magnitude
smaller and faster than storing 12 triangles per box (the exact multiple
was never measured).

Every GLB also contains one parent node named `zup_to_yup` with a -90
degree X-axis rotation. Reason: glTF's convention is **+Y up**, and 3D
Tiles runtimes rotate all glTF content +90 degrees about X at load time
to bring it into the tileset's **+Z up** frame. Our voxel data is
authored Z-up, so the baked -90 cancels the runtime's +90 exactly.
Without this node the whole city renders tipped on its side (we learned
this the hard way - see troubleshooting).

### 2. Overview tiles - `node_L_i_j.glb`

Leaves are grouped 2x2 per level into a quadtree. Each interior node
carries real downsampled geometry: children's boxes are binned onto a
grid twice as coarse (x, y **and** z), and overlapping/touching z-runs of
the same class are unioned. For Run1:

| level | file prefix | tiles | tile size | voxel size | geometricError |
|---|---|---|---|---|---|
| 5 (root) | `node_5_` | 1 | 3200 m | 8 m | 64 |
| 4 | `node_4_` | 4 | 1600 m | 4 m | 32 |
| 3 | `node_3_` | 16 | 800 m | 2 m | 16 |
| 2 | `node_2_` | 55 | 400 m | 1 m | 8 |
| 1 | `node_1_` | 191 | 200 m | 0.5 m | 4 |
| 0 (leaves) | `tile_` | 758 | 100 m | 0.25 m | 0 |

The root is ~10 MB - the entire 3 km x 3 km area in one fetch.

### 3. The tree - `tileset.json`

Ties everything together:

- **`root.transform`** - a 4x4 matrix placing the area-local frame onto
  the Earth (ECEF). It is computed from the run's shared origin in its
  native CRS (EPSG:3946, Lyon) via pyproj, building an East-North-Up
  frame at that point. This is why viewers put the city at real Lyon
  coordinates with no manual placement.
- **`boundingVolume.box`** per tile - axis-aligned box in the local frame.
- **`geometricError`** per tile - "how wrong is this tile, in metres, if
  you show it instead of its children". Viewers compare its screen-space
  projection against a pixel budget (`maximumScreenSpaceError`, default
  16 px in CesiumJS) to decide when to refine. Leaves have error 0 =
  never refined further.
- **`refine: "REPLACE"`** - children *replace* the parent when they load
  (as opposed to `ADD`, where they would draw on top).

## Why the pyramid matters (the one-paragraph version)

Without interior levels, "look at the whole area" forces a viewer to
fetch **all** full-detail tiles at once - 4.2 GB and 187 M instances,
which overflows engine limits and crashes or leaves holes. With the
pyramid, the same view costs one 10 MB root tile, and the expensive
leaves only stream where the camera actually zooms. Bounded memory, no
crashes, works identically in every 3D Tiles client.

## Choosing the client's error budget (`maximumScreenSpaceError` / `sseThreshold`)

The `geometricError` column above is `error_factor` (8) x the level's
voxel size. A node refines when `geometricError / metres-per-pixel`
exceeds the client's threshold, so the *client* setting, not the export,
decides how much of the 1025-node tree becomes resident. Measured in
iTowns on the patched Run1, viewport 886x408, FOV 30 degrees:

| threshold | whole area (4 km, 5.27 m/px) | street (1.2 km, 1.58 m/px) |
|---|---|---|
| 2 | 17.9 M inst (~1.4 GB) - all 55 L2 nodes | 57.1 M inst (~4.6 GB, ~70 s to settle) |
| 4 | 5.19 M | 22.5 M |
| 8 | 1.49 M | 11.1 M |
| 16 | bare root: 435,345 inst (~35 MB est. GPU) | 5.3 M - buildings still individually resolved |
| 32 | bare root (identical to 16) | 2.4 M - roof shape lost |
| 64 | bare root (identical to 16) | 1.5 M - blocky |

At whole-area range the root's own screen error is ~12.2 px, below all
three of 16/32/64, so nothing refines and those three views are
byte-identical.

- **The iTowns example ships `sseThreshold: 2`** - that is the
  "everything loads at once" flood, and it is why a first look at the
  whole area can pull gigabytes. `02_local_visualisation.md` gives the
  console line that raises it.
- **8 is the recommended setting** - it keeps buildings individually
  resolved while staying far from the flood, and it is what the iTowns
  instructions now tell you to set. **4** if you want more detail on a
  desktop with headroom; Cesium's and Unreal's default
  `maximumScreenSpaceError` of 16 is generally sane and needs no change.
- **32 and 64 are never worth it**: identical to 16 at range, and they
  cost real shape up close.
- Smoothness follows the same curve: under a forced 10 Hz orbit, 2
  renders at 3.0 fps against 22.2 at 16.

Why 2 is a bad deal rather than merely expensive: a 0.25 m voxel
subtends 0.16 px at 1.2 km, so native leaf detail is invisible beyond
about 190 m of range at this viewport - the threshold buys gigabytes of
GPU memory for sub-pixel geometry.

Two caveats. **16 is knife-edge at whole-area range** (12.2 px root
error against a 16 px threshold): a taller browser window flips the same
camera from bare root to refined. And every number here scales with
viewport height (same scene, 800 px tall: 59.4 M instances at
threshold 2). Re-exporting with `error_factor = 1.0` is the wrong fix
for the flood - see `DESIGN_DECISIONS.md` section 11.
