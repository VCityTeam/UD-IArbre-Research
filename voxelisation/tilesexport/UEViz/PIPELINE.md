# Pipeline: from `area.npz` to 3D Tiles in Unreal

The complete path from a voxelised area to an interactive Unreal scene,
with every command. Companion docs: visualisation ideas in
[VISUALISATIONS_AND_EXPERIMENTS.md](VISUALISATIONS_AND_EXPERIMENTS.md),
format internals and failure modes in `../AssistingRuns/docs/`.

## The chain at a glance

```
LiDAR .laz tiles
      |  voxelizer.area_cli  (voxelisation, hours)
      v
outputs/RunN/area_output/
      +-- area.npz                     canonical voxel store (ColumnStore)
      +-- area_stream.bin + .idx.json  tiled 32-byte-record payload
      +-- area_*.png                   2D maps (height, class, density)
      |  voxelizer.tileset_cli  (3D Tiles export, ~10 min)
      v
tilesexport/RunN/
      +-- tileset.json                 entry point (LOD tree + geo-transform)
      +-- tile_*.glb                   full-detail leaves (100 m, 0.25 m voxels)
      +-- node_*.glb                   coarse LOD levels (0.5 m ... 8 m voxels)
      |  AssistingRuns/serve_run.py  (HTTP, instant)
      v
http://localhost:8765/tileset.json
      |
      +-- browser: /viewer.html (CesiumJS), iTowns
      +-- Unreal Engine 5.8 + Cesium for Unreal  (this folder)
```

## Stage 1 - Voxelise an area (if not already done)

Reference command (this is how the source run recorded as `Run8` in the internship report was produced; the default `--group-intervals` is active, so the store and payload are the grouped variant):

```powershell
python -m voxelizer.area_cli area `
    --xmin 1834000 --ymin 5170000 --xmax 1837000 --ymax 5173000 `
    --laz-dir inputs\laz --output-dir outputs\RunN\area_output `
    --cell-xy 0.25 --cell-z 0.1 --tile-m 100.0 `
    --stream --viz3d-stream --max-instances 10000000 `
    --json inputs\quickhelpers\nuage-de-points-lidar-2023-de-la-metropole-de-lyon.json
```

- Coordinates are EPSG:3946 (RGF93 / CC46) metres.
- `--cell-xy/--cell-z` set voxel resolution (0.25 m x 0.1 m for the
  `Run8` configuration).
- `--viz3d-stream` also writes the tiled payload
  (`area_stream.bin/.idx.json`) - the fastest input for Stage 2.
- Expect tens of minutes and tens of GB RAM for a 3 km x 3 km area at
  that resolution (measured on `Run8`: ~41 min, 22 GB peak, 751 M points
  becoming 187 M grouped records; the raw store of the same configuration
  holds 317 M intervals). Table 11 of the internship report has the full
  resolution/grouping sweep. No run output is tracked here; `outputs/`
  holds local runs only.

## Stage 2 - Export 3D Tiles

Two entry points, same result (an LOD-pyramid tileset):

**A. From the payload** (preferred when `area_stream.*` exists - skips
re-tiling, ~10 min for Run8):

```powershell
python -m voxelizer.tileset_cli from-payload `
    outputs\RunN\area_output\area_stream.idx.json `
    outputs\RunN\area_output\area_stream.bin `
    --out-dir tilesexport\RunN --crs EPSG:3946
```

**B. From the store** (when you only have `area.npz`, or want to
filter/subset during export):

```powershell
python -m voxelizer.tileset_cli from-store outputs\RunN\area_output\area.npz `
    --out-dir tilesexport\RunN --crs EPSG:3946 `
    [--keep-classes 3,4,5]  [--region XMIN YMIN XMAX YMAX]  [--tile-m 100]
```

Useful knobs (both modes build the LOD pyramid by default; `--flat`
restores the legacy single-level layout - do not use it for full areas):

| knob | effect |
|---|---|
| `--keep-classes 3,4,5` | export only some ASPRS classes (e.g. vegetation-only tileset) |
| `--region xmin ymin xmax ymax` | spatial subset at full detail (metric, EPSG:3946) |
| `--tile-m` | leaf tile size; 100 m is the tested sweet spot |
| `--crs` | native CRS used to compute the georeferencing transform |

What the export guarantees (all verified for Run1): 3D Tiles 1.1, passes
the official `3d-tiles-validator` with 0 errors; glTF content pre-rotated
for the spec's Y-up convention (renders upright everywhere); a
`root.transform` that places the model at its true location (Lyon);
`geometricError` cascade 64->0 so any client streams a ~10 MB overview
first and full detail only near the camera.

Sanity check after any export:

```powershell
npx 3d-tiles-validator -t tilesexport\RunN\tileset.json -r report.json
```

## Stage 3 - Serve

```powershell
cd tilesexport\AssistingRuns
python serve_run.py ..\RunN          # default port 8765
```

Keep this terminal open - every viewer (browser and Unreal) fetches from
it. Quick visual check before touching Unreal:
**http://localhost:8765/viewer.html**.

## Stage 4 - Unreal Engine

One-time setup is already done in this folder (see [README.md](README.md)
for the layout): `IarbreVoxels.uproject` (UE 5.8), Cesium for Unreal
v2.28.0 in `Plugins/`, Python remote execution enabled.

```powershell
& "<UE_5.8_ROOT>\Engine\Binaries\Win64\UnrealEditor.exe" `
  ".\IarbreVoxels\IarbreVoxels.uproject"
```

Open the saved map `Content/CesiumTest.umap` (already configured), or
build a scene from scratch either by hand (README steps) or headlessly:

```powershell
cd tilesexport\UEViz\tools
python ue_drive.py example_setup_scene.py
```

### The four rules that prevent the OOM crash

Learned the hard way (34 GiB and a dead editor):

1. **Create Physics Meshes = OFF** on the tileset actor unless you need
   gameplay collision. Collision cooking for ~250k-box tiles was the top
   memory consumer.
2. **Maximum Screen Space Error: keep the default 16** (4 if you want
   desktop-grade sharpness). The recorded sweep in
   [../AssistingRuns/docs/01_pipeline_overview.md](../AssistingRuns/docs/01_pipeline_overview.md)
   puts the useful band at 4 to 16; 32 and 64 render identically to 16 at
   whole-area range and cost real shape up close. Raise it to 48 only as
   a named memory-pressure exception: the one 48-vs-16 memory comparison
   on record (8.6 GiB against the 34 GiB crash) had physics ON in the
   34 GiB case too, so rule 1 is the load-bearing one, not the threshold.
3. **Camera above the model, never inside it, when the tileset loads.**
   A camera inside the content makes every leaf tile "needed" at once.
4. **Check the tileset's Georeference property** points at *your*
   georeference actor. If left unset, the plugin silently creates a
   default one - at Cesium's demo location in Denver - and your model
   lands 4,000+ km from the world origin.

### Driving the editor from scripts

With the project's Python remote execution enabled, any editor-side
Python can be sent from a terminal - no clicks:

```powershell
python tools\ue_drive.py <script.py>
```

`tools/example_setup_scene.py` (safe scene bootstrap) and
`tools/example_screenshot.py` (named repeatable viewpoints) are working
templates: everything `unreal.*` - spawning actors, changing materials,
camera paths, Sequencer, rendering - is scriptable this way. This is the
backbone for reproducible experiments.

## Height datum handling

The exporter converts the NGF-IGN69 (geoid) LiDAR altitudes to
ellipsoidal heights automatically via the RAF geoid grid (+49.70 m at
the Run1 origin; grid fetched from the PROJ CDN on first use), so the
model lands at its true height against real terrain. Offline exports
fall back with a logged WARNING - controls (`--vertical-crs`,
`--height-offset`, `--no-geoid`) and details in
`../AssistingRuns/docs/03_troubleshooting.md`.
