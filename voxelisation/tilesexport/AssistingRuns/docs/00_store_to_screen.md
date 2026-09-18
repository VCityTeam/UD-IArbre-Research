# 00 - From the .npz store to the screen: the whole viewing chain

This is the spine document: how a voxelised area goes from a saved
ColumnStore (`area.npz`) to pixels, along every path we have. Each
station explains what happens and why, in simple terms first, and points
to the specialist document for depth. If you read only one visualisation
doc, read this one; the numbered docs that follow (01 to 04) then slot
into place.

## The map

```
area.npz  (ColumnStore: per column, RLE intervals of class + height)
   |
   | every viewer shows the same thing: ONE BOX PER INTERVAL
   v
[Station 1]  intervals -> boxes            (geometry collection)
   |
   +-- Path A: self-contained Three.js HTML     (one tile, zero setup)
   +-- Path B: streaming Three.js viewer        (whole areas, one server)
   +-- Path C: 3D Tiles export                  (standard clients:
                 |                               CesiumJS, iTowns,
                 v                               Unreal, Unity, ion)
             tileset.json + GLBs
```

The one idea that unifies everything: an interval `(z_start, z_end,
class)` in one column IS a box. Its footprint is the cell size, its
height is the run length, its colour is the class. No meshing, no
surface reconstruction; the data structure is already the geometry.

## Station 1 - from intervals to boxes

The store holds flat arrays (keys, offsets, z-bounds, classes). The
geometry collector turns each interval into: centre (x, y, z), size
(cell_xy, cell_xy, height), colour (class). This runs vectorised over
the arrays; after the 26 GB lesson (see
`../../../Presentations/Soutenance/ProblemSolving.md` section 4.2) no
per-column Python objects are ever materialised.

Why boxes and not a smooth mesh: boxes are honest (each one is exactly
one measured run), they render by GPU instancing (one cube, millions of
copies, 24 to 32 bytes each), and they keep class identity per element.

## Path A - the self-contained HTML (one tile, zero install)

`python -m voxelizer.viz3d_cli single TILE.laz` or `from-store area.npz`
produces one `.html` file: the box data is packed into a binary blob,
base64-encoded, and baked into a Three.js template. Double-click, fly
with WASD. Auto-thinning keeps the box count near a budget - 5 M
(5,000,000) by default - so any laptop copes; a region option renders a
close-up at full detail instead.

Use it for: sending someone a tile ("just open this file"), quick looks,
close-ups. Not for whole areas: everything lives in one file and one GPU
buffer. Details: `../../../VoxelisingPython/ReadMEs/visualizer3d_workflow.md`.

## Path B - the streaming viewer (whole areas, one small server)

For areas the single file cannot hold, `--viz3d-stream` (or
`viz3d_cli stream area.npz`) writes three files: `area_stream.html`
(the viewer), `area_stream.bin` (every interval as a fixed 32-byte
record, grouped into 100 m tiles), and `area_stream.idx.json` (per-tile
byte offsets and bounds). Serve the folder
(`python -m voxelizer.serve_voxel_html --open`) and the viewer fetches
only what it needs: tiles near the camera load at full detail into a GPU
working set; distant tiles draw as cheap impostor silhouettes; nothing
ever downloads the whole payload.

Use it for: interactive inspection of full areas (hundreds of millions
of intervals) on one machine. This is our own protocol: simple and fast,
but only our viewer speaks it. That limitation is exactly why Path C
exists.

## Path C - 3D Tiles (the standard the rest of the world speaks)

`python -m voxelizer.tileset_cli from-payload <idx> <bin>` (fastest,
reuses Path B's records) or `from-store area.npz` (filter and subset on
the way) converts the same boxes into OGC 3D Tiles 1.1:

- each 100 m tile of records becomes one GLB with GPU instancing
  (one cube mesh, per-instance translation and scale, one material per
  class);
- leaves are grouped 2x2 into a quadtree whose interior nodes carry
  genuinely downsampled geometry (voxels twice as coarse per level),
  built streaming in Morton order in memory bounded by tree depth rather
  than area, so a ~10 MB root shows the whole area instantly;
- `tileset.json` ties it together with bounding boxes, a geometric-error
  cascade (the "when to refine" rule every client understands), and a
  transform that places the model at its true Lyon coordinates.

Serve the folder over plain HTTP (`AssistingRuns/serve_run.py`) and any
spec-compliant client streams it: our bundled CesiumJS page, iTowns,
Unreal Engine and Unity via the Cesium plugins, or Cesium ion hosting.

Why keep B and C both: B is the fastest way for us to look at our own
data; C is the interoperable delivery that costs an export step but
opens every standard viewer. Same boxes, two contracts.

## Where to go next

| You want to | Read |
|---|---|
| understand the payload and GLB/LOD internals | `01_pipeline_overview.md` |
| serve and view locally (CesiumJS, iTowns), read the stats box | `02_local_visualisation.md` |
| fix a rendering/loading problem | `03_troubleshooting.md` |
| load the tileset in Unreal, Unity, ion, other clients | `04_migration_unreal_unity.md` and `../../UEViz/` |
| produce variant tilesets (classes, regions, resolutions, tile size) | `../../UEViz/PIPELINE.md` (knobs) and `../../UEViz/DETAIL_EXPERIMENTS_PLAN.md` (which knob, when, why) |
| the Path A internals (HTML/PLY/PyVista) | `../../../VoxelisingPython/ReadMEs/visualizer3d_workflow.md` |
