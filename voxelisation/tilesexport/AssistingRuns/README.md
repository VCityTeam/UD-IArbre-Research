# AssistingRuns - local visualisation kit for 3D Tiles runs

Everything needed to view any exported voxel tileset (`tilesexport/RunN/`)
in a browser, plus documentation of how the export works and how to move
the data into other Cesium-based applications (Unreal Engine, Unity,
CesiumJS apps, Cesium ion).

> **Availability note.** This kit ships complete - `serve_run.py`,
> `cesium_viewer.html`, these docs, and the Unreal project source under
> `../UEViz/` - but two heavy artifacts are absent by design: the exported
> tilesets themselves (`tilesexport/RunN/` - `Run1` alone is 1,025 `.glb`
> files, 1,026 counting `tileset.json`, 6.1 GiB) and the Cesium
> for Unreal plugin binaries. Every command below that names `..\Run1`
> therefore needs a tileset regenerated first: `python -m
> voxelizer.tileset_cli` (see `../UEViz/PIPELINE.md`, Stage 2); for the
> plugin see `../UEViz/README.md`.

## Quickstart (one command + one URL)

```powershell
cd tilesexport\AssistingRuns
python serve_run.py ..\Run1
```

Then open **http://localhost:8765/viewer.html** in any browser.

You get the full area immediately (coarse overview streams first thanks to
the LOD pyramid), and detail refines as you zoom in. Press `O` for the
whole-area overview, `C` for a close-up at the area centre. A green
stats box shows tiles selected / pending / geometry MB / failures.

## What's in this folder

| File | Role |
|---|---|
| `serve_run.py` | HTTP server for a run directory. Adds the CORS + no-cache headers browsers need, and serves the bundled viewer at `/viewer.html`. Works for any run: `python serve_run.py ..\Run2 --port 9000`. |
| `cesium_viewer.html` | Stand-alone CesiumJS viewer (no Cesium ion account needed - globe disabled, tileset rendered in its own georeferenced frame). Loads the sibling `tileset.json` by default, or any URL via `?tileset=...`. |
| `patch_tileset_bv.py` | Audits and repairs parent/child `boundingVolume` containment in an exported `tileset.json`, in place (`--audit-only` measures without writing; keeps a `.bak-prebvfix` backup; axis-aligned boxes only). Needed only for tilesets exported before the exporter unioned boxes bottom-up - see `docs/03_troubleshooting.md`. |
| `docs/00_store_to_screen.md` | The spine: how `area.npz` becomes pixels along all three paths (self-contained Three.js HTML, streaming viewer, 3D Tiles) and which document covers each station. Start here. |
| `docs/01_pipeline_overview.md` | How a run becomes 3D Tiles: record payload -> GLB tiles -> LOD pyramid -> tileset.json. |
| `docs/02_local_visualisation.md` | Serving and viewing in detail, including the iTowns alternative and how the two-server setup works. |
| `docs/03_troubleshooting.md` | Every failure mode we actually hit (tipped model, `RangeError`, holes, CORS...), with causes and fixes. |
| `docs/04_migration_unreal_unity.md` | Taking the tileset to Cesium for Unreal, Cesium for Unity, plain CesiumJS pages, and Cesium ion. |

## Provenance and naming (read once, avoid confusion)

- The kit was exercised against the payload of the production
  voxelisation run recorded as `Run8` in the internship report (0.25 m x 0.1 m
  cells, **grouped** store, 187 M records). The *raw* store of that same
  configuration holds 317 M intervals; both variants came from the resolution
  sweep tabulated in Table 11 of the report. No run output is tracked in this
  repository: `outputs/` holds local runs only, and the Quickstart in the root
  `README.md` regenerates one. Produce an `area.npz`, export it with
  `voxelizer.tileset_cli`, and serve the result with this kit. "RunN" numbering
  is **per directory**: a number used here does not correspond to an `outputs/`
  run number.
- This kit is the delivery end of the voxelizer's 3D Tiles path
  (`voxelizer/tileset_exporter.py` + `tileset_cli`, documented in
  `Voxelizer-Documentation.md`, section 13) and realises the IA.rbre
  integration direction described in the internship report
  (Sections 4.3.4 and 6.2):
  the voxel city consumable by any standards-compliant geospatial
  client alongside the other IA.rbre layers.

## Requirements

- Python 3 (standard library only - no packages).
- Internet access **once per session** for the viewer page: CesiumJS itself
  loads from Cesium's CDN. The tile data never leaves your machine.

## The 30-second mental model

A "run" folder is a complete, self-describing 3D Tiles 1.1 dataset:

```
Run1/
+-- tileset.json      <- entry point: the tile tree, bounding boxes,
|                        LOD errors, and the geo-transform to Lyon
+-- tile_*.glb        <- 758 full-detail leaves (100 m squares, 0.25 m voxels)
+-- node_*.glb        <- 267 coarse overview tiles (0.5 m ... 8 m voxels)
```

Any spec-compliant 3D Tiles client - CesiumJS, iTowns, Cesium for
Unreal/Unity - reads `tileset.json`, shows the coarse levels instantly,
and streams finer tiles only where the camera looks. Serving the folder
over plain HTTP is all it takes; that is exactly what `serve_run.py` does.
