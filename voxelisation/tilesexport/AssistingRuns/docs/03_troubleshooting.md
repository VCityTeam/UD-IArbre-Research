# 03 - Troubleshooting: every failure we actually hit

Each entry: **symptom -> root cause -> fix/status**. These are not
hypothetical; all of them occurred while bringing Run1 up.

## Model renders tipped over / as a tilted flat sheet

**Symptom.** In Cesium ion's preview the city appeared as a small tilted
sheet floating in space; fragments displaced from their bounding boxes.

**Cause.** glTF content is **+Y-up by spec**; 3D Tiles runtimes rotate
all glTF tile content +90 degrees about X at load time to reach the
tileset's **+Z-up** frame. Our GLBs originally baked Z-up coordinates
directly, so every compliant viewer (Cesium, iTowns, Unreal, Unity - all
apply the same spec rotation) tipped the model 90 degrees.

**Fix (in exporter, done).** Every GLB wraps its content in a node
`zup_to_yup` with rotation quaternion `[-sqrt2/2, 0, 0, sqrt2/2]` (-90
degrees about X). Runtime rotation then cancels exactly. If you ever see
a tipped model again, check that node exists in the GLBs.

## Cesium: `RangeError: Map maximum size exceeded`, most tiles fail

**Symptom.** Console floods with
`RangeError: Map maximum size exceeded at createPickId` +
`A 3D tile failed to load`; only a small patch of the area renders.
Locally this also appeared as "crashes after ~97 tiles".

**Cause.** CesiumJS allocates a **pick ID per glTF instance** into a
JavaScript `Map`, and a JS `Map` holds at most 2^24 ~= 16.7 M entries. The
old flat export made Cesium load all 758 leaves = 187 M instances at
once. This is an engine-architecture limit - no viewer setting fixes it.

**Fix (structural, done).** The LOD pyramid keeps the resident set for
*whole-area framing* to a few coarse tiles - verified at ~7 tiles,
`failed: 0`.

**But the pyramid alone does not cover close range.** That verification
only exercised the overview, and the same cap is reached again as soon as
the camera descends: at Cesium's default `maximumScreenSpaceError` of 16,
a 250 m close-up selects 87 tiles = 28.0 M instances and street level 99
tiles = 31.3 M, both past the 16.7 M ceiling. See the next entry.

## Zooming in kills the tab: "Aw, Snap! Out of Memory", GPU at 0 percent

**Symptom.** The overview is fine, then a close-up freezes the tab for
tens of seconds and it dies with `Error code: Out of Memory`. While it
hangs, the GPU sits at 0 percent and system RAM is nowhere near full -
which makes it look like a hang rather than an exhaustion.

**Cause.** Instance count, not bytes and not GPU load. Nothing had reached
the GPU yet: the renderer process was still building per-instance state on
the CPU and hit its own ceiling first. Measured on the reference export, whose
leaves carry a median 271 k instances each (602 k at the worst):

| view | tiles at SSE 16 | instances | vs the 16.7 M cap |
|---|---|---|---|
| overview 3 km | 4 | 1.49 M | 9 % |
| mid 1.5 km | 16 | 5.19 M | 31 % |
| close-up 250 m | 87 | 28.0 M | **1.7x over** |
| street 60 m | 99 | 31.3 M | **1.9x over** |

For scale: one renderer process held ~7.6 M instances in ~3.5 GB, about
**470 bytes per instance** - roughly 15x what the project's own streaming
viewer spends (32 bytes). 3D Tiles has no way to express "stop at N
instances"; screen-space error is a quality target with no memory ceiling,
which is why a standard client will happily select a set it cannot hold.

**Fix (done, in `cesium_viewer.html`).** Three settings, all measured:

1. `tileset.maximumScreenSpaceError = 48` - holds at every altitude
   (close-up 7.6 M, street 13.5 M). 32 is **not** enough: street level is
   still 17.8 M, 1.1x over. At range 48 and 16 select identically, so the
   detail cost is confined to close-ups.
2. `ctrl.minimumZoomDistance = 25` - a crash guard, not a comfort setting.
   Once the camera is *inside* a tile's bounding box its distance is zero,
   so screen-space error is infinite and that subtree refines to leaves no
   matter how high the threshold. Keeping the camera outside the boxes is
   what bounds the worst case.
3. `tileset.cacheBytes` - bounds what stays resident across a long
   fly-through, so memory does not creep tile by tile.

The figures above come from replaying the client's REPLACE traversal over
the tileset's real bounding volumes and summing the instances of the
selected set against the cap. That replay ignores frustum culling, so its
counts are a conservative upper bound - a real close-up selected 4 tiles
where the replay predicted 22.

**The structural fix** is smaller leaves: `--tile-m 50` on
`tileset_cli from-store` quarters the instances per tile, so the client
culls at four times the granularity and close-ups cost proportionally
less. Note `--tile-m` exists only on `from-store` - a payload built by
`from-payload` has its tiling already baked into the `.bin`. The build
side is free: every level of the pyramid streams - leaves walked in
Morton order, each interior node written the moment its last child
arrives - so a 50 m or 25 m export peaks at the same memory as the 100 m
one.

## Whole area = 4.2 GB fetch; browser tab dies / OOM

**Cause.** The legacy `--flat` layout has no intermediate LOD: root ->
758 full-detail children. Any client framing the whole bounding volume
must fetch everything (frustum culling can't help when everything is in
the frustum, and there is no coarser representation to fall back to).

**Fix (done).** Default export now builds the pyramid. Don't use
`--flat` for real-size areas.

## Holes in the ground / vegetation floating over gaps

**Symptom.** Patches of terrain missing; canopy hovering with nothing
under it; looks like broken data.

**Cause.** Failed tile loads (memory-limited browser, killed fetches).
Each failed 100 m tile removes ground *and* vegetation of that square;
canopy overhanging from a *loaded* neighbour tile then looks like it
floats. The data itself is continuous - cross-check with
`outputs/RunN/area_output/area_orthophoto_class.png`.

**Diagnosis tip.** In the bundled viewer watch `failed:` in the stats
box; in iTowns check `layer.tilesRenderer.stats` in the console. With
the pyramid this should simply no longer happen.

**Genuine (not-a-bug) gaps.** Two visual effects are real properties of
LiDAR voxel data: air between canopy and ground (few returns from trunk
space) and occlusion shadows behind buildings. They exist in Runs 7/8
too - just invisible there against a black background from top-down
views.

## `tile_N.glb` rejected by iTowns/three.js: `Invalid typed array length`

**Cause (historical, fixed).** The exporter's unit-cube was built with 36
vertices, but its index accessor declared 36 indices over a buffer that
actually held only 24 - a glTF spec violation. Cesium silently tolerated
it and rendered corrupted geometry; three.js's stricter loader refused
with `RangeError: Invalid typed array length: 36`. `_make_unit_box`
today emits 36 positions, 36 normals and a 36-entry identity index
buffer (the non-indexed layout: each face's four corners are duplicated
across its two triangles rather than shared), so all three counts agree;
validated by the official `3d-tiles-validator` (0 errors) - but if a
future edit touches `_make_unit_box`/`_tile_glb`, rerun:

```powershell
npx 3d-tiles-validator -t path\to\tileset.json -r report.json
```

## The validator passes but parents don't contain their children

**Symptom.** None visible. Geometry is in the right place, tiles load,
`3d-tiles-validator` reports 0 errors. An audit of the shipped
`Run1/tileset.json` nevertheless found **556** parent/child pairs whose
child box was not inside the parent's - 29 by more than 1 mm, worst
overhang **90.0 m**, all of them at parent depth 4 - plus **79**
descendants sticking out of the root box (worst 88.0 m).

**Cause (fixed in the exporter).** The LOD converter mixed two
conventions: leaves claimed their full nominal 100 m tile square, while
interior nodes hugged the tight extent of the coarsened records they
held. A sparse leaf therefore stuck out of its parent. The spec forbids
this because a client may cull an entire subtree on the parent's box
alone; the actual GLB geometry never left any parent box (0.00 m
measured escape), so the defect was in culling correctness and
conformance, not in what you see.

**Fix (in exporter, done).** `convert_to_3d_tiles_lod` now unions every
node's box with its children's on the way back up (root included), and
applies the 0.05 m minimum z half-extent *before* the union so a
floor-inflated flat child cannot escape. Pinned by
`tests/test_tileset_bv_containment.py`, whose mutation guard re-runs the
exporter with the union disabled and fails if the violations do not come
back.

**Repairing a run exported before the fix.** The GLBs are unaffected -
boxes live only in `tileset.json`:

```powershell
python patch_tileset_bv.py --audit-only ..\RunN\tileset.json   # measure
python patch_tileset_bv.py ..\RunN\tileset.json                # repair
```

It keeps a `tileset.json.bak-prebvfix` backup and refuses anything but
axis-aligned boxes. The exported tileset audited here and its iTowns copy
both came out clean (0 violations, 0 root escapees). No export is tracked in
this repository, so run the audit on your own `tileset.json`. Expect the root
box to grow a little (the reference export's y half-extent went 1256 -> 1300
m); that is correct containment, not a regression.

**Important.** `3d-tiles-validator` does not check bounding-volume
containment at all; it reported 0 errors before the fix too. Only an
audit like the one above catches this class of defect.

## Viewer loads but network shows CORS errors on tiles

**Cause.** Serving the tiles with a bare `python -m http.server` (no
CORS header) while the viewer page comes from a different origin.

**Fix.** Use `serve_run.py` (adds the header), or serve viewer + data
from the same origin (the default `/viewer.html` workflow).

## Fixed a GLB but the browser still shows the old error

**Cause.** Browser cached the broken file (same URL).

**Fix.** `serve_run.py` sends `Cache-Control: no-store`, making this
impossible. If using another server: hard-reload (Ctrl+F5) or bust the
cache.

## Model sits ~50 m below terrain in apps with real terrain

**Cause (fixed in the exporter).** LiDAR altitudes are orthometric
(NGF-IGN69, a geoid-based height); ECEF placement needs *ellipsoidal*
heights. Around Lyon the geoid sits **+49.70 m** above the ellipsoid
(RAF grid at the export's origin), so a tileset exported without the
correction appears ~50 m too low against Cesium World Terrain.
Irrelevant for globe-less viewing; relevant the moment you overlay real
terrain.

**Fix (automatic since the geoid-aware exporter).**
`tileset_exporter._enu_to_ecef_transform` now converts the origin
through the compound CRS (`EPSG:3946+5720`), pulling the RAF geoid grid
from the PROJ CDN on first use. The reference `tileset.json` was patched
accordingly. Controls on both `tileset_cli` subcommands:
`--vertical-crs` (default `EPSG:5720`), `--height-offset 49.7` (manual
value, e.g. offline), `--no-geoid` (legacy behaviour).

**Caveat.** Offline with no cached grid, the export logs a WARNING and
falls back to orthometric heights (the old behaviour) - watch for
`Geoid grid ... not applied` in the export log; re-export online or pass
`--height-offset`.

## Cesium ion upload limits

Run1 is ~6.1 GiB (4.2 leaves + 1.9 pyramid); the free tier is 5 GiB.
Options: subset export, storage upgrade, or skip ion - local serving and
self-hosting need no ion at all.
