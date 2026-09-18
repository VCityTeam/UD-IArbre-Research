# 04 - Migrating to Unreal Engine, Unity, and other Cesium apps

The tileset is standard **3D Tiles 1.1**, using glTF content with
`EXT_mesh_gpu_instancing`. The official validator reports 0 errors and
0 warnings, subject to the two caveats the report records: it reports
that it skipped `EXT_mesh_gpu_instancing`, the extension that carries all
of the geometry, and it does not check bounding-volume containment at all
(see `03_troubleshooting.md`). Everything below consumes it as-is - no
re-export needed.

## The one thing every target needs: an HTTP URL

3D Tiles clients consume a *URL to tileset.json*. For local work that is
exactly what `serve_run.py` provides:

```
http://localhost:8765/tileset.json        (or http://<your-LAN-IP>:8765/...)
```

For anything shared/permanent, copy the run folder to any static host
(nginx, S3 + CloudFront, university web space...) preserving relative
paths, and use that URL. `tileset.json` must stay in the same directory
as the `.glb` files (all content URIs are relative).

## Cesium for Unreal

1. Install the **Cesium for Unreal** plugin (Fab/Marketplace, free), then
   enable it in your project (UE 5.x). A "Cesium" panel appears.
2. From the Cesium panel add **Blank 3D Tiles Tileset** (you do NOT need
   ion or a token for self-hosted data).
3. In the created `Cesium3DTileset` actor's Details:
   - **Source**: *From Url*
   - **URL**: `http://localhost:8765/tileset.json` (serve_run.py running)
4. The level's `CesiumGeoreference` actor decides where "Unreal origin"
   sits on the globe. Set its Origin Latitude/Longitude/Height to the
   dataset so it spawns near world origin with full precision:
   - Latitude `45.7327`, Longitude `4.7416`, Height `280` (Run1 centre).
5. Recommended while iterating: keep *Enable Frustum Culling* and default
   *Maximum Screen Space Error* (16). Lower MSSE = sharper = heavier.

Expectations and caveats:

- Cesium for Unreal is built on **cesium-native**, which handles the same
  spec as CesiumJS: the Y-up rotation, `root.transform`, REPLACE
  refinement, and `EXT_mesh_gpu_instancing` are all consumed natively.
  If instanced content ever renders empty, update the plugin - instancing
  support landed in 2023-era releases; anything current is fine.
- Add **Cesium World Terrain + imagery** (this *does* need a free ion
  token): tilesets exported with the geoid-aware exporter (and the
  patched Run1) sit at their true ellipsoidal height. Only exports made
  with `--no-geoid` (or offline without the RAF grid) need the manual
  ~+49.7 m *Height* nudge - see `03_troubleshooting.md`.
- Class colours arrive as glTF materials (unlit-ish PBR). For per-class
  logic in Unreal (e.g. hide buildings, recolour vegetation) the clean
  path is re-exporting with `EXT_mesh_features`/metadata - a future
  exporter extension; today classes are distinguishable by material only.

## Cesium for Unity

Same mental model, Unity naming:

1. Install **Cesium for Unity** (Unity 2022 LTS+, via their tarball/UPM).
2. `Cesium` menu -> add a **CesiumGeoreference** to the scene; set
   origin lat/lon/height as above.
3. Add a **Cesium3DTileset** component/game object; set **Tileset
   Source: From Url** and the `http://localhost:8765/tileset.json` URL.
4. Same SSE/terrain/height-offset notes as Unreal.

## Plain CesiumJS web app

`cesium_viewer.html` in this folder is a minimal working reference
(about 250 lines): create `Viewer`, `Cesium3DTileset.fromUrl(url)`, add to
`scene.primitives`, `zoomTo`. To embed in a globe app instead of the
black-background local view, keep the globe enabled and add your imagery;
the tileset lands in Lyon by itself thanks to `root.transform`.

## Cesium ion (hosted)

1. ion -> *Add data* -> drag the **contents** of the run folder (or a zip
   of it): `tileset.json` + all `.glb`. Remove `viewer.html`-type extras
   first.
2. Choose "3D Tiles (already tiled)" if asked - the data must be stored
   as-is, not re-tiled.
3. Storage math: free tier = 5 GiB; Run1 ~= 6.1 GiB -> needs a subset or
   an upgraded plan. Delete superseded assets to reclaim quota.
4. Once hosted, every Cesium product can consume it by asset ID + token
   (Unreal/Unity: *From Cesium ion* source instead of *From Url*).

## iTowns / three.js ecosystems

iTowns (see `02_local_visualisation.md`) and anything built on
`3d-tiles-renderer` (NASA-AMMOS) load the tileset from the same URL.
Verified working - this was our second-opinion engine during debugging.

## Compatibility checklist for any new target

1. Does it speak 3D Tiles 1.0/1.1? (All Cesium-family products do.)
2. Can it reach the URL? (CORS header needed if the app is a browser
   page on a different origin - `serve_run.py` handles it.)
3. Does it support `EXT_mesh_gpu_instancing`? (Cesium-family: yes.
   Exotic/minimal glTF viewers looking at a single `.glb` in isolation
   may not - but single GLBs are not the intended consumption path.)
4. Real terrain in the scene? Geoid-corrected exports (the default, and
   the patched Run1) place correctly; only `--no-geoid`/offline exports
   need a manual ~+49.7 m nudge.
