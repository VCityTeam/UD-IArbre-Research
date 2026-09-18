# 02 - Local visualisation, in detail

## Option A (recommended): bundled Cesium viewer

```powershell
cd tilesexport\AssistingRuns
python serve_run.py ..\Run1            # add --port 9000 for a second run
```

Open **http://localhost:8765/viewer.html**.

What this does:

- `serve_run.py` serves the run folder as static files, with two headers
  added to every response:
  - `Access-Control-Allow-Origin: *` - lets viewers on *other* origins
    (e.g. the iTowns dev server on port 8080) fetch the tiles. Browsers
    block cross-origin `fetch()` without it.
  - `Cache-Control: no-store` - after re-exporting a run you always see
    the new tiles, never a stale browser cache (a real debugging trap:
    a fixed GLB kept "failing" once purely because the browser cached the
    broken version).
- `/viewer.html` is served from this folder (`cesium_viewer.html`), so
  the viewer page and the tile data share one origin - the default
  workflow involves no CORS at all.

The viewer is plain CesiumJS from Cesium's CDN. No Cesium ion account or
token is involved: the globe and imagery layers are disabled and the
tileset is rendered in its own georeferenced frame against a black
background. `?tileset=http://host:port/tileset.json` loads a different
source.

Reading the stats box:

- **tiles selected** - tiles chosen for the current view after LOD/culling.
  Whole-area view: a handful of coarse nodes. Close-up: coarse + leaves.
- **pending / processing** - network fetches in flight / GLBs being
  turned into GPU resources.
- **geometry MB** - resident GPU geometry. Should stay in the hundreds of
  MB even when zoomed in; it is bounded by the LOD mechanism.
- **failed** - must stay 0. Non-zero means fetch/parse errors; check the
  browser console (F12).

## Option B: iTowns (second-opinion viewer)

iTowns is IGN France's geospatial web framework, built on three.js - a
genuinely independent implementation from CesiumJS, so it is a good
cross-check. Setup (once):

```powershell
git clone --depth 1 https://github.com/iTowns/itowns.git <workdir>\itowns
cd <workdir>\itowns
npm install
npm start        # webpack dev server on http://localhost:8080
```

Then, with `serve_run.py` also running (that's where CORS matters):

```
http://localhost:8080/examples/3dtiles_loader.html?3dtiles=http://localhost:8765/tileset.json
```

Notes from experience:

- **Raise `sseThreshold` to 8 before you judge performance.** The iTowns
  example ships `sseThreshold: 2`, which is the "load everything at once"
  setting: at 2 the client refines the whole pyramid to its leaves from
  about 600 m out, selecting all 758 full-detail tiles (~4.2 GiB) and
  pinning the GPU. At 8 the same view costs a fraction of that. In the
  F12 console on the example page:

  ```js
  const layer = view.getLayers(l => l.isC3DTilesLayer)[0];
  layer.sseThreshold = 8;
  view.notifyChange(layer);
  ```

  Or edit `sseThreshold` where the example constructs its `C3DTilesLayer`
  to make it stick. The measured cost curve is in
  `01_pipeline_overview.md`.
- The example's basemap/terrain layers load from French government
  servers; if those fail you still get the tileset, just no globe imagery.
- iTowns' `GlobeView` camera is planet-scale - for a 3 km dataset the
  navigation feels coarse ("tiny and unintuitive"). Use it to *verify*
  rendering, not as the daily driver; the bundled Cesium viewer handles
  local-scale navigation better.
- Programmatic camera help (F12 console on the example page):

  ```js
  const { Coordinates } = itowns;
  view.controls.lookAtCoordinate({
    coord: new Coordinates('EPSG:4978', 4444574.0, 368662.2, 4544758.9),
    range: 300, tilt: 30,
  });
  ```

  (That ECEF point is Run1's origin - the area centre.)

## Serving multiple runs at once

Each run gets its own port:

```powershell
python serve_run.py ..\Run1 --port 8765
python serve_run.py ..\Run2 --port 8766
```

Then compare side by side in two tabs, or point one viewer at another
run: `http://localhost:8765/viewer.html?tileset=http://localhost:8766/tileset.json`.

## Do I need a "real" web server?

No. 3D Tiles is deliberately just static files + HTTP GET. Anything that
serves files works: this script, nginx, an S3/GCS bucket, GitHub Pages...
The only server-side requirements are correct content lengths and (for
cross-origin viewers) the CORS header.
