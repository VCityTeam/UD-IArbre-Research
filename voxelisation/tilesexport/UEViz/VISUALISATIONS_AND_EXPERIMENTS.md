# Visualisation types & experiment ideas

What can be built on the `area.npz -> 3D Tiles -> Unreal` pipeline, split
into what works **today** with existing commands, and what needs a small
exporter extension first.

## Visualisation types available today

### 1. Full classified voxel city (the default)
Everything, coloured by ASPRS class (ground tan, vegetation greens,
buildings brick red, water blue). One command, one tileset - the Run1 setup.

### 2. Class-filtered layers
`--keep-classes` at export produces thematic tilesets:

```powershell
# vegetation only (low/medium/high/upper-canopy = ASPRS 3,4,5,8 -
# matches VEGETATION_CLASSES in voxelizer/classes_config.py; class 8 is
# the de facto upper-canopy tier of the Grand Lyon deliveries)
python -m voxelizer.tileset_cli from-store ...\area.npz `
    --out-dir tilesexport\RunN_veg --keep-classes 3,4,5,8

# buildings only (ASPRS 6)
python -m voxelizer.tileset_cli from-store ...\area.npz `
    --out-dir tilesexport\RunN_bld --keep-classes 6
```

Serve each on its own port and add several `Cesium3DTileset` actors in
one Unreal level -> independently toggleable layers (canopy on/off over
bare terrain, buildings-only massing model, etc.).

### 3. Region subsets
`--region xmin ymin xmax ymax` cuts a neighbourhood at full detail:
small tilesets that fit the Cesium ion free tier, load in seconds, and
suit close-range work (VR, cinematics, per-quartier studies).

### 4. Resolution variants
Re-voxelise at different `--cell-xy/--cell-z` (0.5 m / 1 m) for
lightweight massing models, or visually cap detail per scene by raising
*Maximum Screen Space Error* (48 -> 128 shows mostly the coarse pyramid -
an instant "abstract" city).

### 5. In-Unreal treatments (no re-export needed)
- **Lighting/time-of-day**: drive the directional light (or the Sun
  Position Calculator plugin) - voxel trees cast real shadows on ground.
- **Post/materials**: the plugin exposes a material slot per tileset -
  height-tinted, emissive-by-class, translucent-canopy looks are material
  work only.
- **Cinematics**: Sequencer flythroughs + Movie Render Queue for video;
  `tools/example_screenshot.py` for repeatable stills.
- **Hybrid globe context**: add Cesium World Terrain + imagery (free ion
  token; geoid-corrected exports - including the patched Run1 - sit at
  their true height).
- **Packaged builds / VR**: the project packages like any UE game for
  stakeholder walkthroughs.

### 6. Side-by-side engines
The same tileset URL loads simultaneously in CesiumJS
(`/viewer.html`), iTowns, and Unreal - engine-comparison screenshots for
papers come free.

## Requires an exporter extension first (roadmap)

| idea | missing piece |
|---|---|
| click-a-voxel -> attributes; per-class runtime styling/filtering in one tileset | write `EXT_mesh_features` + `EXT_structural_metadata` (class, point count, height-above-ground per instance) in `_tile_glb` |
| multi-epoch change view (e.g. 2018 vs 2023 canopy) | voxelise both epochs; add a diff export (records present in A, B, both) |
| lighter GPU footprint / exotic-viewer compatibility | greedy-meshed variant (merged faces instead of instanced boxes) |

*(A fourth item - true placement on terrain via the RAF geoid grid,
+49.70 m at the Run1 origin - has been implemented: the exporter now
applies it automatically; see `PIPELINE.md` section Height datum handling.)*

These items mirror the future-work directions of the internship
report (Section 6.2): metadata-rich exports, temporal diffing between
LiDAR HD epochs, and meshed variants. A successor
picking up either document continues the same roadmap.

## Experiments worth running (IA.rbre context)

**Shading & microclimate**
1. *Canopy shadow atlas*: sun at hourly positions for solstices/equinox;
   top-down orthographic captures; count shaded ground pixels per hour ->
   shade-provision maps of existing canopy. Cross-check against the
   `sunlight-shadow` project (a separate project, not part of this
   repository).
2. *Solar roof potential*: buildings-only tileset + sun sweep; measure
   irradiated roof-voxel area, subtract hours shaded by trees.

**Greening scenarios**
3. *Virtual planting*: duplicate a district subset, spawn hypothetical
   tree voxels (or UE foliage assets, georeferenced) along streets;
   before/after shadow + view comparisons for de-sealing candidates
   (ties into the `desealing/` work - also a separate project, not part of
   this repository).
4. *Canopy-loss stress test*: vegetation layer hidden vs shown over bare
   terrain+buildings - the "city without its trees" visual for public
   engagement.

**Analysis on the voxel structure**
5. *Sight-line studies*: line traces from street level (needs physics ON
   temporarily, on a small region subset) - where does canopy block
   building facades, which streets have green visual corridors?
6. *Vertical structure profiles*: fly a clipping-style low camera through
   forest patches; the interval data (canopy floating above trunk-space)
   is directly visible - compare stand structure between parks.

**Data quality / methodology**
7. *Acquisition QA fly-through*: LiDAR occlusion shadows, water
   misclassification, scan-line artefacts are obvious in 3D at street
   level; screenshot-log defects with `tools/example_screenshot.py`
   viewpoints so they're re-checkable across runs.
8. *Run-vs-run comparison*: two exports side by side (two servers, two
   actors) to isolate parameter effects - e.g. grouped vs raw store of
   the same area, or two resolutions from the TestOutputs sweep.
9. *Performance envelope*: SSE sweep (16/32/48/96) x physics on/off,
   recording memory and FPS - publishable engine-cost numbers for
   voxel 3D Tiles (we already have the two extremes: 34 GiB crash at
   SSE 16 + physics; stable 8.6 GiB at SSE 48 without).

**Storytelling**
10. *Seasonal narrative*: time-of-day + seasonal sun cinematics over the
    voxel city; heat-island rasters draped as decals for
    canopy-vs-temperature visuals.

Each experiment is scriptable end-to-end via `tools/ue_drive.py`
(camera + capture + actor toggles), which is what makes them repeatable
across runs and parameter sets.
