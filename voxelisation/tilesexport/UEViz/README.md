# UEViz - Unreal Engine visualisation of the voxel 3D Tiles

Part of the IA.rbre voxelisation line: this folder realises the
"integration" perspective of the internship report (Section 6.2) by
bringing the exported voxel city into a game engine through the same
3D Tiles contract every other Cesium client uses.

`IarbreVoxels/` is an Unreal Engine **5.8** project (Blueprint-only, no C++
build needed) for viewing the exported tilesets with **Cesium for Unreal**.

Documentation map:

- **[PIPELINE.md](PIPELINE.md)** - the full chain with commands:
  `area.npz` -> 3D Tiles export -> serving -> Unreal, including the four
  rules that prevent the editor OOM crash and how to drive the editor
  from scripts.
- **[DETAIL_EXPERIMENTS_PLAN.md](DETAIL_EXPERIMENTS_PLAN.md)** - the
  detail-level experiment plan: every dial from voxel resolution to
  screen-space error explained (how and why), plus eight concrete
  experiments with commands and measurement recipes.
- **[VISUALISATIONS_AND_EXPERIMENTS.md](VISUALISATIONS_AND_EXPERIMENTS.md)**
  - visualisation types available today (class layers, region subsets,
  lighting studies, cinematics...) and concrete experiment ideas.
- **`tools/`** - `ue_drive.py` (send Python to the running editor) plus
  working example scripts for scene setup and repeatable screenshots.
- `../AssistingRuns/docs/00_store_to_screen.md` - the spine of the
  whole viewing chain (store to Three.js to 3D Tiles); read it first if
  the chain is new to you.
- `../AssistingRuns/docs/` - format internals, browser viewers,
  troubleshooting, and migration notes for other Cesium apps.

## Layout

```
UEViz/
+-- IarbreVoxels/
    +-- IarbreVoxels.uproject          <- EngineAssociation 5.8, plugin enabled
    +-- Config/DefaultEngine.ini       <- DX12, Lumen, OpenWorld startup map
    +-- Content/
    +-- Plugins/CesiumForUnreal/       <- Cesium for Unreal v2.28.0 (not shipped -
                                          see below); UE 5.8 build, project-local
                                          so the engine stays clean
```

The plugin binaries live only on the working machine - repositories do not
carry them (5+ GB). On a fresh clone, install Cesium for Unreal v2.28.0
(UE 5.8 build) into `IarbreVoxels/Plugins/` (or the engine) before opening
the project; the editor will also rebuild its own caches (`Intermediate/`,
`DerivedDataCache/`, `Saved/`) on first launch.

## Launching

Double-clicking the `.uproject` fails when the engine association
registry is stale (a prebuilt engine outside the launcher's registry is
the usual cause). Launch the editor binary directly instead, from this
directory:

```powershell
& "<UE_5.8_ROOT>\Engine\Binaries\Win64\UnrealEditor.exe" `
  ".\IarbreVoxels\IarbreVoxels.uproject"
```

Replace `<UE_5.8_ROOT>` with your Unreal Engine 5.8 installation root.

First launch compiles shaders - expect several minutes.

## Loading the Run1 tileset

1. Serve the tiles (separate terminal, keep running):

   ```powershell
   cd ..\AssistingRuns
   python serve_run.py ..\Run1
   ```

2. In the editor: **Cesium panel** (Window -> Cesium if not visible) ->
   **Blank 3D Tiles Tileset** (+). No ion account/token needed for this.
3. Select the created `Cesium3DTileset` actor -> Details:
   - **Source**: *From Url*
   - **URL**: `http://localhost:8765/tileset.json`
4. Select the `CesiumGeoreference` actor (created automatically) and set:
   - Origin Latitude `45.7327`, Origin Longitude `4.7416`, Origin Height `280`
   This puts the Unreal world origin at the dataset centre so the voxels
   spawn around (0,0,0) with full float precision.
5. The area should appear immediately (coarse LOD first - the pyramid's
   10 MB root), refining as you fly closer. Default *Maximum Screen Space
   Error* (16) is fine; lower = sharper = heavier.

## Known caveats

- If you add **Cesium World Terrain** (needs a free ion token), the voxel
  model now sits at its true height: the exporter applies the RAF geoid
  correction (+49.70 m at the Run1 origin) automatically, and Run1's
  `tileset.json` has been patched. Only `--no-geoid`/offline exports
  still need a manual *Height* nudge; see
  `../AssistingRuns/docs/03_troubleshooting.md`.
- Class colours come through as glTF materials (one per ASPRS class;
  note that vegetation spans classes 3, 4, 5 **and 8**, the de facto
  upper-canopy tier of the Grand Lyon deliveries, and building renders
  brick red per `classes_config.py`).
  Per-class toggling/recolouring inside Unreal would need metadata
  (`EXT_mesh_features`) added to the exporter.
