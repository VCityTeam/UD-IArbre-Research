# Execution steps - IA.rbre voxelizer CLI reference

All CLI entry points live in the `voxelizer/` package. Run them from the
directory that contains `voxelizer/`, with `python -m voxelizer...`.

Each section is tagged:

- `[CORE]` - needed to use the system at all.
- `[OPTIONAL]` - a real, supported entry point you reach for on purpose.
- `[ADVANCED / EDGE CASE]` - needed only past a specific wall.

Start with the two core commands (sections 1 and 2) and the tile downloader
(section 11); see the Quickstart in `README.md`. The narrative tutorial is
`HowToUse.md`.

How to read the command blocks below: a trailing `\` continues the line in a
POSIX shell (use `^` in cmd.exe and a backtick in PowerShell), square brackets
mark an optional argument and are not typed, and a word in CAPITALS
(`PATH/TO/TILE.laz`, `METRES`, `XMIN YMIN XMAX YMAX`, `STAGE`, `N`) is a
placeholder to replace with a value of your own, as is a bare `...` standing
for the rest of a command you supply.  Once the brackets are dropped and the
placeholders filled in, every block can be typed as it stands: the
explanations are kept outside the blocks, no block puts a `#` comment before a
line continuation, and flags that contradict each other are never shown in the
same invocation.  The few trailing `#` comments are comments in a POSIX shell
and in PowerShell; cmd.exe has none, so drop them there.

## 1. Single-tile pipeline [CORE]

Voxelize one LAZ file.  Writes 2-D maps, column diagnostics, stats.

    python -m voxelizer single PATH/TO/TILE.laz \
        --output-dir outputs/Run1/single \
        --cell-xy 0.5 --cell-z 0.5 \
        --columns-mode diag --columns-top-n 50 \
        --height-mode default

- `--cell-xy` / `--cell-z`: voxel size in metres (default 0.5 / 0.5).
- `--columns-mode`: `diag` (default), `top`, `all` or `skip`, with
  `--columns-top-n` (default 50) sizing the `top` ranking.
- `--height-mode`: `default`, `relative` or `absolute`.
- `--delete-laz`: remove the .laz file after a successful run.

The two 3-D viewers are separate runs.  Given both flags at once the
streaming viewer wins and `--viz3d` is ignored, so pass one or the other.  The
area verb of section 2b resolves the pair the same way:

    python -m voxelizer single PATH/TO/TILE.laz \
        --output-dir outputs/Run1/single \
        --viz3d \
        --max-boxes 5000000 --roi-size 200.0 \
        --roi-cx 1831500.0 --roi-cy 5177000.0

    python -m voxelizer single PATH/TO/TILE.laz \
        --output-dir outputs/Run1/single \
        --viz3d-stream \
        --tile-m 64 --max-instances 4000000 --inline-threshold-mb 64

`--viz3d` writes the full-area page and the ROI page; `--no-full` or
`--no-roi` drops one of them, and passing both is refused.  `--roi-cx` and
`--roi-cy` default to the centre of the occupied area.

Default `--output-dir`: `outputs/RunN/single/` (auto-incrementing).

## 2. Area voxelization (full) [CORE]

Voxelize all LAZ tiles intersecting a bounding box.

    python -m voxelizer.area_cli area \
        --xmin 1831000  --ymin 5176500 \
        --xmax 1832000  --ymax 5177500 \
        --laz-dir inputs/laz  --output-dir outputs/Run1/area_output \
        --cell-xy 0.5  --cell-z 0.5 \
        --keep-classes 2,3,4,5,6 \
        --height-mode default \
        --columns-mode diag  --columns-top-n 50  --columns-all-max 500000 \
        --group-gap 1.5 \
        --chunk-size 5000000

- `--cell-xy` / `--cell-z`: voxel size in metres (default 0.5 / 0.5).
- `--keep-classes`: ASPRS class filter, comma or space separated.
- `--height-mode`: `default`, `relative` or `absolute`.
- `--columns-mode`: `diag` (default), `top`, `all` or `skip`, with
  `--columns-top-n` (default 50) sizing the `top` ranking and
  `--columns-all-max` (default 500000; 0 removes the cap) capping `all`.
- `--group-intervals` is on by default and writes `area.npz` grouped;
  `--no-group-intervals` renders the raw store instead.  Give one or the
  other, never both.  `--group-gap METERS` restricts the merge to vertical
  gaps of at most that many metres; left out, it merges across any gap.
- `--chunk-size` (default 5000000) is the points per streaming chunk.
  `--no-chunks` reads each tile whole instead, and `--no-clip` keeps whole
  intersecting tiles rather than clipping them to the box.

WARNING: `--delete-laz` is destructive and is deliberately kept out of the
block above.  Appended to that command it removes each source tile from
`--laz-dir` as soon as the tile is voxelized, so a second run over the same box
has to fetch the corpus again.

`area_raw.npz` is written before grouping, and the default
`--intermediates auto` removes it again after a successful run; use
`--keep-area-raw` to re-run grouping later with a different `--group-gap`.
Both of those flags live in section 2b.

Output files produced, directly under `--output-dir`:

  `stats.txt`           point, column and interval counts, the vertical
                        extent, and the per-class totals

  `area_max_height.png`, `area_max_points_class.png`,
  `area_orthophoto_class.png`, `area_n_intervals.png`
                        the four 2-D maps, one per rendering mode

  `columns/`            the column diagnostics: `diagnostics/` always, plus
                        `per_column/` under `--columns-mode top` or `all`,
                        and nothing at all under `skip`

  `area.npz`            grouped store (consecutive same-class intervals
                        merged via `--group-intervals`, which is on by
                        default)

  `area_manifest.json`  the sidecar beside `area.npz`, recording the grid
                        and the grouping the store was built with.  Both it
                        and `area.npz` are skipped under `--no-save-store`

  `stages.json`         one record per output stage: its status, whether it
                        was retried, and the command that reproduces it

  `stages/`             the stage working directory: `params.json`, and for
                        every stage that ran a `<stage>.result.json` and a
                        `<stage>.faultlog`.  The faultlog is opened before its
                        stage starts, so a stage that succeeds leaves an empty
                        one; a faultlog with content is the record of a stage
                        that died.  A clean default run of the four reduction
                        stages therefore leaves four empty faultlogs, four
                        result files and `params.json`

  `area_raw.npz`        ungrouped store saved *before* grouping (only written
                        when `--group-intervals` is on).  Use this if you
                        want to re-run grouping with a different `--group-gap`
                        later.  NOTE: under the default `--intermediates auto`
                        it is DELETED again on a fully successful run; pass
                        `--keep-area-raw` (or `--intermediates keep`) to
                        retain it.

  `store_raw/`          mmap-able per-stage store copy the output stages
                        attach to, kept for `--resume-from-store` when
                        `--keep-raw-store` is on and removed otherwise

`stages.json`, `stages/` and `store_raw/` come from the isolated-stage path,
which is the default; `--no-isolate-stages` runs the stages in process and
writes none of the three.

Default `--output-dir`: `outputs/RunN/area_output/` (auto-incrementing), the
same auto-numbering the single-tile command uses.

The rest of the `area` flag surface - the 3-D viewers, sharding, resuming,
process isolation, kept intermediates and the download wrappers - is
section 2b.

## 2b. Area voxelization: the advanced flags [ADVANCED / EDGE CASE]

Every flag below belongs to the same `python -m voxelizer.area_cli area`
command as section 2.  They are kept apart because none of them is needed
for a run that fits in RAM, and because each group is documented in full
somewhere else in this file.  Group headings name that place.

Estimate, then stop - section 3:

    --preflight-only               estimate and exit, no processing
    --max-rss-mb N                 abort if RSS exceeds the budget

Re-run stages from a cached store - section 4:

    --resume-from-store PATH       skip voxelization, use the cached store
    --only-stage STAGE             repeatable: restrict the run to the named
                                   stage(s), out of stats, persist_npz, maps,
                                   col_diag, viz3d_roi, viz3d_full and
                                   viz3d_stream

Out-of-core shard mode - section 9 (merge 9b, diagnostics 9c):

    --shard                        out-of-core shard mode
    --merge-shards                 out-of-core streaming merge, which adds
                                   store_raw/, area.npz and the merged stages
    --merge-band-intervals 40000000   merge RAM/speed dial
    --delete-shards                remove shards/ after success
    --resume-shards                reuse the finished shards of an
                                   interrupted run (validated)
    --isolate-tiles                child process per tile: a native decoder
                                   crash costs ONE tile
    --retry-lazrs                  one second-opinion decode after a child
                                   crash (needs the optional lazrs backend)

Stores and intermediates kept on disk:

    --keep-raw-store               persist store_raw/ for resume
    --keep-area-raw                persist area_raw.npz (pre-grouping)
    --intermediates auto           "keep" | "delete" | "auto"
    --no-save-store                skip area.npz + area_manifest.json

Per-stage process isolation:

    --isolate-stages               the default; --no-isolate-stages runs the
                                   stages in process instead
    --stage-timeout 0              per-stage wall limit; 0 = off (default)

The 3-D viewers - sections 5, 5b and 6:

    --viz3d                        render the box viewer(s) from the area
                                   store, with --max-boxes 5000000,
                                   --roi-size 200.0, --roi-cx X, --roi-cy Y,
                                   and --no-full or --no-roi to drop one page
                                   (both at once is refused)
    --viz3d-stream                 render the streaming viewer instead, with
                                   --tile-m 64, --max-instances 4000000 and
                                   --inline-threshold-mb 64

`--viz3d` and `--viz3d-stream` are alternatives: given both, a run renders
the streaming viewer only and drops `--viz3d`, which is what the `single` verb
of section 1 does too.

Fetch the tiles as part of the run - section 11:

    --stream                       stream and voxelize (needs --json)
    --download                     download the tiles first
    --json INVENTORY.json          the inventory --download and --stream read
    --tile-pitch 500               acquisition grid pitch in metres
    --workers N                    parallel download threads
    --limit 0                      cap on the tiles taken (0 = no cap)

Common stage combinations for `--only-stage` (repeat the flag per stage):

    --only-stage stats --only-stage persist_npz
    --only-stage maps --only-stage col_diag --only-stage stats
    --only-stage viz3d_roi --only-stage viz3d_full --viz3d

The first voxelizes and saves the .npz only, the second re-renders the 2-D
maps, the column diagnostics and the statistics, and the third re-renders the
3-D HTML.

CAVEAT: in a normal (non-resume) run a stage must ALSO be enabled by its
own flag. Naming only stages that are disabled (e.g. --only-stage
col_diag together with --columns-mode skip) is REFUSED with an error
listing the stages that are enabled, instead of silently doing nothing.
--viz3d is inferred automatically from viz3d_* stage names on both the
normal and the --resume-from-store paths.

## 3. Pre-flight check (estimate only) [OPTIONAL]

Print how many tiles intersect the bbox, estimated peak RAM, and exit.

    python -m voxelizer.area_cli area    \
        --xmin 1831000  --ymin 5176500   \
        --xmax 1832000  --ymax 5177500   \
        --laz-dir inputs/laz             \
        --keep-classes 2,3,4,5,6         \
        --preflight-only

The estimate carries a 30% overhead factor (allocator fragmentation,
transient temporaries, margin of error); if it exceeds your RAM, use
`--shard` - the old `--fit-ram-mb` downsample-to-fit option was
removed (it is either brute force through RAM or sharding).

Its two shape numbers, occupancy and intervals per column, are calibrated
from the campaign of 2026-08-08 (ten 2 km x 2 km areas of Grand Lyon at
four grids, 40 stores, 3.55 billion points): occupancy 0.951 / 0.947 /
0.937 at 1.0 / 0.5 / 0.25 m, intervals per column 2.116 / 1.776 / 1.480 at
the same grids, with no dependence on `--cell-z`.  Both are fitted as
power laws in `cell_xy`.  The print shows a calibrated central figure,
within -4% to +9% of eight runs outside the fit, and a planning figure
1.14x to 1.30x above those same measured stores, which is the one to size
a machine by.  In local mode the LAZ headers supply a point total, which
caps intervals (an interval holds at least one point) and raises the
estimate for deliveries denser than the calibration corpus; a streamed
inventory JSON carries no point count and gets neither.

`--preflight-only` output describes a single process holding the whole
merged store, so on a `--shard --merge-shards` run at a fine grid it is a
ceiling and not a forecast.  The estimate and the measured peak RSS of six
whole-metropolis runs sit side by side in
RESULTS.md of the archived campaign of 2026-08-08 (outside this delivery) (run E5_preflight_vs_rss), which is the
measurement behind that sentence; it fixes the direction of the error and
not its size, because those runs never hold the store the figure describes,
four of the six peaks are censored by the machine's physical RAM ceiling and
two of the runs did not finish.

## 4. Resume from store (re-run 2-D / stats) [OPTIONAL]

Skip voxelization.  Read cached grid from a previous `--keep-raw-store` run.
All geometry params are auto-loaded from `store_raw/run_params.json`.

    python -m voxelizer.area_cli area                          \
        --output-dir outputs/Run1                              \
        --resume-from-store outputs/Run1/store_raw             \
        --only-stage maps --only-stage col_diag --only-stage stats

Or re-run everything (2-D + 3-D) at once:

    python -m voxelizer.area_cli area                          \
        --output-dir outputs/Run1                              \
        --resume-from-store outputs/Run1/store_raw             \
        --viz3d                                                \
        --only-stage maps --only-stage col_diag --only-stage stats --only-stage viz3d_roi --only-stage viz3d_full

## 5. 3-D HTML from .npz (no voxelization) [OPTIONAL]

Render interactive 3-D HTML viewer(s) straight from a saved `area.npz`.
Independent of `store_raw/` - only needs the .npz.

    python -m voxelizer.viz3d_cli from-store outputs/Run1/area.npz \
        --output-dir outputs/Run1  --label area \
        --max-boxes 500000  --roi-size 200.0 \
        --roi-cx 1831500.0  --roi-cy 5177000.0 \
        --grid

`--grid` also writes the `.vxg` grid caches beside the pages.

Both the full-area page and the ROI page are written by default.  No flag
switches a single page on; the only two switches are `--no-full` and
`--no-roi`, each suppressing the page it names, and giving both at once is
refused because nothing would then be rendered.

## 5b. 3-D HTML from one LAZ tile (viewers only) [OPTIONAL]

Voxelize a single LAZ file and render only the two Three.js pages, skipping
the 2-D maps, stats and column diagnostics that `python -m voxelizer single`
(section 1) also writes.  Same renderer as section 5, except that the grid
comes from the LAZ instead of a saved store.

    python -m voxelizer.viz3d_cli single PATH/TO/TILE.laz \
        --output-dir outputs/Run1/viz3d \
        --cell-xy 0.5   --cell-z 0.5 \
        --max-boxes 5000000 \
        --roi-size 200.0 \
        --roi-cx 1831500.0   --roi-cy 5177000.0 \
        --grid \
        --delete-laz

- `--output-dir` is required; `-o` is the short form.
- `--cell-xy` / `--cell-z`: voxel size (default 0.5 / 0.5).
- `--max-boxes`: thinning budget for the full view (default 5000000).
- `--roi-size`: ROI side in metres (default 200).
- `--roi-cx` / `--roi-cy`: ROI centre; the default is the centre of the
  occupied area.
- `--no-full` or `--no-roi` skips one page; both at once is refused.
- `--grid` also writes the `.vxg` grid caches.
- `--delete-laz` deletes the source after success.

Writes `<output-dir>/<tile_stem>/<tile_stem>_full.html`, auto-thinned by
striding columns until it fits `--max-boxes`, and
`<tile_stem>_roi.html`, rendered at full detail.  `--grid` adds a `.vxg`
cache beside each page, the same lossless quantized encoding the HTML
embeds, which `load_grid()` reads back without touching the LAZ again.

## 6. Streaming 3-D viewer [OPTIONAL]

Render a LOD-streaming Three.js viewer that handles 200M+ instance boxes.
Writes one `.html` (with optional sidecar `.bin` or base64-inlined blob).

    python -m voxelizer.viz3d_cli stream outputs/Run1/area.npz     \
        --out outputs/Run1/area_stream.html                         \
        --label area                  --tile-m 64                  \
        --max-instances 4000000       --inline-threshold-mb 64     \
        --keep-classes 2,3,4,5,6                                    \
        --region XMIN YMIN XMAX YMAX

`--region` takes a metric bounding box and, with `--keep-classes`, is how the
payload is cut down.  The parser also accepts `--stride` and `--max-boxes` for
compatibility with the box exporters of sections 5 and 5b, then ignores both
and prints a warning saying so: this exporter always writes every interval at
full detail, and `--max-instances` bounds the GPU working set at view time
instead.

## 7. Reconstruct LAS/LAZ from .npz (and back) [OPTIONAL]

Round-trip a ColumnStore back to a point cloud. Three modes:

    density        one point per original LiDAR point (via `count`), all at
                   the interval centre. The default; keeps class + counts,
                   loses the interval extent on a re-voxelization.
    one_per_voxel  one point per interval voxel. Keeps class + extent,
                   loses the counts.
    exact          one per voxel PLUS `count - height` padding points.
                   The only bit-exact round trip: re-voxelizing on the same
                   grid reproduces the six store arrays byte for byte, and
                   re-saving reproduces the .npz byte-identically too (the
                   packaged writer is deterministic; the archive round-trip
                   test pins the SHA-256 equality).

    python -m voxelizer.reconstruct to-laz area.npz output.las
    python -m voxelizer.reconstruct to-laz shard.npz shard.laz \
        --mode exact \
        --verify \
        --epsg 3946 \
        --no-grid-vlr
    python -m voxelizer.reconstruct to-laz area.npz output.las \
        --one-per-voxel \
        --z-base

- `--mode`: `density` (the default), `one_per_voxel` or `exact`.
- `--verify`: rebuild from the file just written and assert it matches.
- `--epsg`: the CRS written into the header (default 3946).
- `--no-grid-vlr`: omit the IARBRE grid VLR, which then makes `--origin`,
  `--cell-xy` and `--cell-z` necessary on the way back.
- `--one-per-voxel`: the legacy alias for `--mode one_per_voxel`.
- `--z-base`: use the interval base for z instead of its centre.

    python -m voxelizer.reconstruct to-npz shard.laz rebuilt.npz \
        --origin X Y Z --cell-xy 0.5 --cell-z 0.5
    python -m voxelizer.reconstruct verify shard.npz shard.laz

Those three `to-npz` options are needed only for a file that carries no grid
VLR.  The pre-subcommand spelling still works:

    python -m voxelizer.reconstruct area.npz output.laz

Exported files carry a CRS (io_laz.read_laz REJECTS files without one) and an
`IARBRE` VLR holding the grid origin + cell sizes, so `to-npz` needs no
sidecar. Class codes above 31 promote the file to LAS 1.4 / point format 6.

## 7b. Archive a run's shards as exact LAZ [ADVANCED / EDGE CASE]

`shards/*.npz` is a numpy zip only this repo can read; the same information as
`.laz` opens in any GIS tool. Size is 0.3-1.7x the `.npz` depending on how fine
the grid is - a `.npz` costs ~4 B per INTERVAL, the `.laz` ~1 B per POINT, and
interval count explodes as the grid refines. Measured on a 61M-point tile:
1.65x at 1 m, 1.17x at 0.5 m, 0.65x (i.e. SMALLER) at 0.25 m xy / 0.1 m z.

    python -m voxelizer.archive_cli pack RUN_DIR \
        --workers 8 \
        --replace \
        --epsg 3946
    python -m voxelizer.archive_cli unpack RUN_DIR \
        --out DIR \
        --workers 8

Every option is optional.  `--workers` (default 1) spreads the independent
shards over that many processes, `--replace` deletes each source `.npz` once
its `.laz` has verified, and `--epsg` overrides the CRS written into the
archive files (default: the run's recorded EPSG).  `--no-verify` skips the
per-shard rebuild-and-compare check: about 3x faster, strictly worse, and
refused together with `--replace`, since verification is what makes replacing
safe.  On `unpack`, `--out` defaults to `<run>/shards`.

Only the RAW per-tile shards can be archived: `area.npz` is the grouped store
and grouping is not reliably invertible, so it is regenerated from the shards
(the grouping settings are recorded in shards/manifest.json for exactly this).
Coarsening an exact export later is faithful; refining it is not.
See ReadMEs/laz_roundtrip_design.md.

## 8. 3D Tiles export [OPTIONAL]

Convert an `area.npz` to 3D Tiles (`tileset.json` + per-tile `.glb`
files using `EXT_mesh_gpu_instancing`).

    python -m voxelizer.tileset_cli from-store outputs/Run1/area.npz \
        --out-dir outputs/Run1/tiles \
        --tile-m 100 \
        --flat \
        --vertical-crs EPSG:5720 \
        --keep-classes 2,3,4,5,6 \
        --region XMIN YMIN XMAX YMAX \
        --crs EPSG:3946

Only `--out-dir` is required.  `--tile-m` is the spatial tile size in metres
(default 100), `--flat` writes the flat layout instead of the LOD pyramid,
`--vertical-crs` is the datum of the input altitudes (default EPSG:5720),
`--keep-classes` filters to fewer classes, `--region` exports a metric
sub-box, and `--crs` is the horizontal CRS (default EPSG:3946).  Two more
height flags belong to the same pair and are described below: `--no-geoid`
skips the automatic geoid lift and `--height-offset 49.7` replaces it with an
explicit lift, so neither is combined with the other.

Two things happen by default, without asking for them.

LOD: the exporter builds a quadtree LOD pyramid - interior nodes hold
2x-coarsened boxes with a halving `geometricError` and `REPLACE`
refinement, full-detail leaves at the bottom - so a viewer streams a
coarse overview first and refines on zoom. `--flat` restores the legacy
root -> leaves layout (every tile full detail, no overview).

Georeferencing: `root.transform` is a real ECEF matrix built from the
store origin, so Cesium places the model on the globe instead of at the
earth's centre. Input altitudes are read as NGF-IGN69 orthometric heights
(`--vertical-crs EPSG:5720`) and lifted to ellipsoidal with the IGN RAF
geoid grid through pyproj - about +49.7 m near Lyon, the grid fetched from
the PROJ CDN on first use, and a warning rather than a silent
mis-placement if it cannot be resolved. `--no-geoid` treats the
altitudes as already ellipsoidal, leaving the model roughly 50 m low
against real terrain; `--height-offset M` adds fixed metres instead of
consulting the grid (`--height-offset 49.7` reproduces the Lyon lift
offline). All four flags exist on `from-payload` too. Mechanics:
ReadMEs/Voxelizer-Documentation.md section 13.

`--tile-m` defaults to `100` (metres); smaller values produce more,
finer `.glb` tiles. NOTE: `--tile-m 0` does NOT mean "whole area in
one tile" (an old claim here): observed behaviour is one tile per
occupied column - hundreds of tiny .glb files. For a single tile, pass a
value at least as large as the area extent.

Every level of the pyramid is written streaming (leaves in Morton
Z-order, each interior node written as its last child arrives), so peak
memory follows tree depth, not area - `--tile-m 50` or `--tile-m 25`
peaks like the default 100 and lets a client cull more finely.

If the conversion crashes (e.g. out of memory / too many records):
  - `--keep-classes` - drop unneeded classes first
  - `--region XMIN YMIN XMAX YMAX` - export only a spatial sub-area

If you already have `.idx.json` + `.bin` from a prior streaming run:

    python -m voxelizer.tileset_cli from-payload area_stream.idx.json \
        area_stream.bin \
        --out-dir outputs/Run1/tiles \
        --flat \
        --vertical-crs EPSG:5720 \
        --crs EPSG:3946

The `.bin` argument is optional (it is found beside the `.idx.json` when it
is left out), and so is every flag except `--out-dir`.  `--no-geoid` and
`--height-offset` work here exactly as they do on `from-store`.

Viewing the export: the delivery kit is tilesexport/ at the repository
root. AssistingRuns/serve_run.py there serves a run directory with the
CORS and no-cache headers browsers need and hosts the bundled CesiumJS
page beside it -

    cd REPO_ROOT/tilesexport/AssistingRuns
    python serve_run.py ../Run1        # then open
                                       # http://localhost:8765/viewer.html

No Cesium ion account is needed. The Unreal path is tilesexport/UEViz/
(see its PIPELINE.md and README.md).

## 9. Sharded area (out-of-core) [ADVANCED / EDGE CASE]

For areas that exceed available RAM.  Each tile is voxelized independently
to a `.npz` shard; maps and stats are folded from shards.

    python -m voxelizer.area_cli area \
        --xmin 1831000  --ymin 5176500 \
        --xmax 1832000  --ymax 5177500 \
        --laz-dir inputs/laz  --output-dir outputs/Run1 \
        --cell-xy 0.5  --cell-z 0.5 \
        --keep-classes 2,3,4,5,6 \
        --shard \
        --merge-shards \
        --resume-shards \
        --isolate-tiles \
        --retry-lazrs \
        --intermediates auto

- `--shard` activates shard mode and `--merge-shards` adds the optional
  streaming merge, which produces `store_raw/`, `area.npz` and the merged
  stages.
- `--resume-shards` continues an interrupted run by reusing every matching
  shard already on disk.
- `--isolate-tiles` voxelizes each tile in a child process, recording the
  failures in `shards/failed_tiles.json`, and `--retry-lazrs` grants one
  lazrs second opinion after a crash.
- `--intermediates auto` cleans the derived caches up if everything went well.

WARNING: `--delete-shards` and `--delete-laz` are destructive and are
deliberately kept out of the block above.  `--delete-shards` removes the
`shards/` cache once everything that reads it has run, which also ends any
later re-rendering, resuming or archiving from those shards; `--delete-laz`
removes each source tile from `--laz-dir` as it is voxelized, so a second run
over the same box has to fetch the corpus again.  Append either one only when
you mean it.

A shard-only run (no `--merge-shards`) computes everything it reports
from the shards themselves: statistics and the whole of `columns/` fold
shard by shard through `voxelizer/shard_diagnostics.py` (section 9c),
byte-identical
to the merged-store output, and the four maps are mosaicked at a bounded
pixel budget. Add `--merge-shards` when you want the artifacts that only
exist as one store (`area.npz`, `area_raw.npz`, `store_raw/`, the
streaming viewer). The merge is out of core (`merge_streaming`, section 9b):
`store_raw/` is assembled on disk one key band at a time, so peak RAM is
set by `--merge-band-intervals` (default 40,000,000 intervals per band,
about 1.4 GB), independent of the merged store's size.

The merged-store phase runs through the same per-stage process isolation
as the non-shard path, so `--no-save-store`, `--intermediates`,
`--keep-raw-store` and `--resume-from-store` all work in shard mode
too. `maps` never re-runs there: the bounded mosaics written by the
per-tile fold are the canonical shard-mode maps.

Crash resilience: `--resume-shards` reuses every shard already
on disk (lattice-validated; `shards/run_config.json` refuses resuming over a
run with different parameters), `--isolate-tiles` contains native LAZ-decoder
crashes to a single tile via the `voxelizer.shard_worker` child (recorded in
`shards/failed_tiles.json`; the run continues), and `--retry-lazrs` grants
one second-opinion decode. Recommended for metropolis-scale runs:
`--shard --resume-shards --isolate-tiles`. Works identically on Windows and
in the Docker services (shards persist through the ./outputs and
./EntireLyonOutputs bind mounts).

Also note: `--only-stage` in shard mode selects which stages run on the
merged store: `persist_npz`, `col_diag`, `stats`, `viz3d_roi`,
`viz3d_full`, `viz3d_stream`. A shard-only run refuses, up front and by
name, the flags only a merged store can serve: `--keep-raw-store`,
`--keep-area-raw`, `--only-stage`, `--stage-timeout`,
`--viz3d-stream`.

## 9b. Out-of-core shard merge, run on its own [ADVANCED / EDGE CASE]

Assemble a `shards/` directory into one raw store directory on disk, band by
band, so peak RAM is one band rather than the whole merged store.  This is
the code `--merge-shards` calls inside a sharded run (section 9), exposed as
its own command so a merge can be redone, planned, or run on a different
machine without re-voxelizing anything.

    python -m voxelizer.merge_streaming \
        --shards-dir outputs/Run1/shards \
        --out-store-dir outputs/Run1/store_raw \
        --group-intervals \
        --group-gap METRES \
        --band-intervals 40000000 \
        --overwrite

- `--group-intervals` is OFF here by default, so a bare run reproduces the
  RAW merged store; `--no-group-intervals` states that default explicitly.
  Give one or the other, never both.
- `--group-gap METRES` applies with grouping; its default is any gap.
- `--band-intervals` is the RAM knob: intervals per band, default 40000000,
  about 1.4 GB.
- `--overwrite` replaces an existing `--out-store-dir`.
- `--plan-only` prints the band plan and the expected peak RAM from the
  manifest, then exits without merging.

Writes `--out-store-dir` in the `ColumnStore.save_dir` layout: one `.npy`
per array plus `meta.json`.  The arrays are filled inside `<out>.partial/`
and the directory is renamed into place only after `meta.json` is written,
so an interrupted merge leaves a `.partial` no consumer can mistake for a
finished store.  `--plan-only` writes nothing at all; it reads
`shards/manifest.json` only.

Note the defaults differ from the pipeline's on purpose: `--group-intervals`
is OFF here, so a bare run reproduces the RAW merged store, while the
sharded pipeline groups by default.

## 9c. Column diagnostics and stats from shards [ADVANCED / EDGE CASE]

Compute the whole of `columns/` and `stats.txt` directly from a `shards/`
directory, with no merged store: tiles partition the grid, so every
reduction behind those artifacts is a fold over disjoint parts already on
disk.  This is what a shard-only run uses (section 9), exposed so the
diagnostics can be re-rendered, or rendered at other settings, from shards
that are already there.

    python -m voxelizer.shard_diagnostics \
        --shards-dir outputs/Run1/shards \
        --out-dir outputs/Run1 \
        --columns-mode diag \
        --columns-top-n 50 \
        --columns-all-max 500000 \
        --batch-intervals 8000000 \
        --tile-label area

- `--columns-mode`: `diag` (the default), `top`, `all` or `skip`;
  `--columns-top-n` (default 50) sizes the ranking and `--columns-all-max`
  caps `all` (0 = no cap).
- `--stats` writes `stats.txt` and is on by default; `--no-stats` turns it
  off.  Give one or the other, never both.
- `--group-intervals` / `--no-group-intervals` and `--group-gap METRES`
  override the manifest, which is what both default to.
- `--batch-intervals` is a RAM knob: intervals per reduction batch, default
  8000000.
- `--tile-label` is the label printed in the figure titles.

Writes `<out-dir>/columns/` unless `--columns-mode skip`, and
`<out-dir>/stats.txt` unless `--no-stats`; asking for neither is refused.
The grouping settings default to the manifest's provenance block, so the
sweep reduces exactly the intervals the merged store would hold, and the run
stops up front if the manifest's per-shard extents overlap, because a column
straddling two shards would break the fold.

## 10. Python API (in-script usage) [OPTIONAL]

All entry points are importable directly:

    from voxelizer import (
        # Data
        ColumnStore, Column, Interval,
        # Class codes / colors
        CLASS_NAMES, CLASS_COLORS,
        # Voxelization
        voxelize_laz, voxelize_laz_chunked, voxelize,
        # Area pipeline
        process_area, process_area_streamed, select_area_tiles,
        run_area_sharded,
        # Per-tile pipeline
        process_single_tile, MODES,
        # I/O
        read_laz, read_laz_chunks, read_laz_header,
        # Exports
        export_tileset_from_store, convert_to_3d_tiles, export_tiled_from_store,
        store_to_las, store_to_laz, store_to_points, store_from_laz,
        EXPORT_MODES, stores_equal, verify_exact, verify_roundtrip,
        # LAZ archiving of a sharded run
        pack_shards, unpack_shards,
        # 2-D visualization
        plot_tile_map, plot_column, render_tile_map,
        # 3-D visualization (requires pyvista)
        export_html, export_ply, show_pyvista,
        export_grid, load_grid, render_area_3d,
        # Ground index
        compute_ground_indices, fill_ground_holes, NODATA,
        # Decoder / denoise / absorb / resolve
        classify_voxel, classify_gap,
        MEASURED_AIR, OPAQUE_INTERIOR, SUBSURFACE,
        min_points_filter, morphological_filter, absorb_interior, resolve,
        # Ray tracing
        ColumnGridDDA, DDAConfig, RayHit,
        # Diagnostics / stages
        write_column_diagnostics, STAGES,
        # Downloads
        download_orthos,
        # Pre-flight
        estimate_area_run, estimate_store_bytes, suggest_cell_xy,
        RamBudgetExceeded,
        # Utils
        next_run_output_dir,
        # Modules, not functions: the entry points and the launcher writer
        area_cli, download_laz, serve_voxel_html, serve_tiles, launchers,
    )
    from voxelizer.data_structures import ColumnStore

    store = ColumnStore.load("area.npz")
    store_to_las(store, "output.las")

That list is the whole of `voxelizer.__all__`, 67 names.  Five of them bind a
MODULE rather than a callable: `area_cli`, `download_laz`, `serve_voxel_html`,
`serve_tiles` and `launchers`.  The first four are entry points, and binding a
`main()` to those names at package-import time shadowed the module itself and
made runpy warn on `python -m voxelizer.<module>`; `launchers` is the writer of
the generated `.cmd` files.  Call them as `download_laz.download_laz(...)`,
`serve_voxel_html.main()`, `serve_tiles.main()`, `area_cli.main()` and
`launchers.write_stream_launcher(...)`.  Their neighbours in the list,
`download_orthos` and `next_run_output_dir`, are ordinary functions.

## 11. Tile downloaders [CORE]

Download Grand Lyon 2023 inventory tiles by bbox:

    python -m voxelizer.download_laz \
        --xmin-start 1831000 --xmin-end 1831500 \
        --ymin-start 5176500 --ymin-end 5177000 \
        --laz-dir inputs/laz \
        --workers 4

    python -m voxelizer.download_orthos \
        --xmin-start 1831000 --xmin-end 1831500 \
        --ymin-start 5176500 --ymin-end 5177000 \
        --ortho-dir inputs/ortho

The bounds select tiles by their ORIGIN, on the 500 m acquisition grid: a tile
is taken when its `x_min` falls in `[--xmin-start, --xmin-end]` and its `y_min`
in `[--ymin-start, --ymin-end]`.  The window above therefore holds four tiles,
the four covering the area example of section 2.

The orthophoto downloader takes the same bounding box, `--json`, `--workers`,
`--limit` and `--dry-run`, and writes to `--ortho-dir` instead of
`--laz-dir`.  Add `--json INVENTORY.json` to either command to point it at
another inventory, `--limit N` to stop after N tiles and `--dry-run` to see
the selection without fetching anything.

`--json` is optional: the shipped inventory is found automatically,
first under `inputs/quickhelpers/` beside the `voxelizer` package,
then under `quickhelpers/` at the repository root.
`--limit` caps the tile count (useful for testing).  `--dry-run` only
prints what would be downloaded.  The area CLI's `--download` and
`--stream` modes are wrappers around this.

Of the two, only `download_laz` feeds the pipeline: nothing else in the package
reads what `download_orthos` fetches, so the orthophoto tiles are reference
imagery to look at beside a run rather than an input to one.  Both carry the
`[CORE]` tag here because fetching tiles is the only way to get data at all.

## 12. Post-processing a saved store [OPTIONAL]

Semantic passes over an `area.npz` (or any saved store), applied in a
fixed order; each pass is opt-in:

    python -m voxelizer.postprocess_cli INPUT.npz OUTPUT.npz \
        --min-points 2 \
        --morph 2 --morph-classes A,B,C \
        --absorb --absorb-min-neighbour 0.75 --absorb-max-noise 2 \
        --resolve --tie-margin 1 --tie-rel 0.0 \
        --group --group-gap METERS \
        --stats

- `--min-points N` drops intervals with `<= N` points; a bare `--min-points`
  means 4.  Omitted, the pass does not run.
- `--morph [MIN_NEIGH]` is the 8-neighbour morphological filter; bare, it
  means 2.  `--morph-classes A,B,C` restricts it to those classes.
- `--absorb` reclassifies vegetation sandwiched in a building envelope.
  `--absorb-min-neighbour` (0.75) is the minimum building fraction among the
  penetrated 8-neighbours, `--absorb-max-noise` (2) the height in voxels
  below which a sandwiched run is absorbed unconditionally.
- `--resolve` collapses cross-class overlaps, removing the losing side's
  points.  `--tie-margin` (1) and `--tie-rel` (0.0) set the near-tie band,
  `--no-prefer-taller` drops the taller-run tie-break,
  `--no-demote-uncertain` lets unclassified, noise and artefact classes win
  near-ties, and `--class-priority A,B,C` overrides that with an explicit
  order.
- `--group` merges same-class intervals afterwards, bounded by
  `--group-gap METERS` if that is given.
- `--stats` prints the per-class point movement, and `--dry-run` reports
  without writing anything.

`--absorb` conserves points exactly (vegetation relabelled to building);
`--min-points`, `--morph` and `--resolve` remove points by design and
`--stats` prints how many per class.

## 13. Serving finished outputs [OPTIONAL]

One verb reads a directory and starts the right server:

    python -m voxelizer serve OUTPUT_DIR [--kind stream|tiles] \
        [--port 0] [--bind ADDR] [--no-open] [--open-viewer cesium|itowns] \
        [--open-page NAME]

`--open-page NAME` applies to a streaming directory and picks which page is
opened; left out it takes the directory's own `*_stream.html`, preferring
`area_stream.html`.  `--open-viewer` is the tileset equivalent and defaults to
`cesium`.

`serve_voxel_html` (streaming viewer payloads, HTTP Range/206, section 16)
and `serve_tiles` (3D Tiles + glTF content types, section 16b) can also be
started directly. Every run that writes a viewer also writes a
double-clickable launcher beside it: `view_stream.cmd`, `view_cesium.cmd`,
`view_itowns.cmd`, `launch_unreal.cmd`. Launchers carry no absolute
paths and pick a free port; closing the console window stops the server.

## 14. Environment variables [ADVANCED / EDGE CASE]

    VOXELIZER_BIND          Server bind default for both servers (a
                            container sets 0.0.0.0; the desktop default is
                            loopback). An explicit --bind always wins.
    VOXELIZER_NO_BROWSER    1 = never open a browser (launchers and tests).
    VOXELIZER_LAZ_BACKEND   LAZ decoder for THIS process: laszip (pinned
                            default) or lazrs (single-thread, used by the
                            --retry-lazrs isolated retry). lazrs_parallel
                            is refused on purpose.
    VOXELIZER_RUN_ORDER     Run-forming cell sort: class_first (default,
                            runs maximal per class) or height_first (the
                            compatibility order every pre-switch store was
                            built with). Per-voxel content is identical
                            under both. Leave unset unless reproducing
                            older output.

## 15. Live run diagnostics [ADVANCED / EDGE CASE]

`voxel_runner_diagnos.py` is a run-time monitor that wraps any command
and records CPU, RAM, threads,
disk I/O and process count on a background thread, plus detailed crash info:

    python voxelizer/voxel_runner_diagnos.py -- \
        python -m voxelizer single tile.laz -o outputs/Run5

    python voxelizer/voxel_runner_diagnos.py --serve -- \
        python -m voxelizer.area_cli area ...

    python voxelizer/voxel_runner_diagnos.py --view outputs/Run5/diagnosis

`--serve` opens a live browser dashboard, on `--port` (default 8770) and
`--host` (default `$VOXELIZER_BIND` when that is set, else 127.0.0.1), and
`--no-open` starts it without opening a browser; `--view` attaches to a
previous run.  Output lands in `<run-dir>/diagnosis/`.  `--run-dir` names that
directory explicitly, for a wrapped command whose `-o`/`--output-dir` cannot be
read off the command line; `--interval` sets the sampling period in seconds
(default 0.25) and `--log-every` the period between console lines (default 1).

## 16. 3-D viewer HTTP server [OPTIONAL]

The streaming viewer's `.bin` sidecar needs Range-request support.  The
built-in server provides exactly that and auto-detects the most recent
`*_stream.html` under `outputs/`:

    python -m voxelizer.serve_voxel_html [DIR] [--port 8000] [--bind ADDR] \
        [--no-cache] [--open] [--open-page NAME] [--no-open]

`DIR` defaults to the most recently modified run folder holding a
`*_stream.html`.  `--port 0` asks the operating system for a free port.
`--bind` defaults to 127.0.0.1, or to `$VOXELIZER_BIND` when that is set
(section 14).  `--open` opens a browser, `--open-page NAME` picks which page
under the served directory it opens, and `--no-open` overrides both.
`--no-cache` sends `Cache-Control: no-store` on the payloads as well, which
defeats the browser-side tile cache and is meant for debugging.

## 16b. 3D Tiles HTTP server [OPTIONAL]

A 3D Tiles export needs the glTF content types and the CORS headers a
browser insists on, plus a viewer page.  This server supplies both, and
serves the directory a `tileset_cli` export wrote (section 8):

    python -m voxelizer.serve_tiles outputs/Run1/tiles \
        --port 0 \
        --bind 127.0.0.1 \
        --open-viewer cesium

The directory argument is required and is refused unless it holds a
`tileset.json`.  `--port 0`, the default, asks the operating system for a
free port and prints the url it got.  `--bind` defaults to `127.0.0.1`, or
to `$VOXELIZER_BIND` when that is set in the environment (section 14); an
explicit `--bind` always wins.  `--open-viewer cesium` or
`--open-viewer itowns` opens that page once the port is known, and
`--no-open` never opens a browser whatever `--open-viewer` says.  The
server prints the tileset url and both viewer urls, and runs until Ctrl+C.

## 17. Ray queries, transmittance and direct sun hours [ADVANCED / EDGE CASE]

NOTE: this layer is LIBRARY-ONLY. There is no CLI subcommand for it yet;
it is driven from Python or from the scripts under Experiments/.

Modules:
  voxelizer/ray_trace.py      full 3-D Amanatides-Woo DDA (reference walker)
  voxelizer/ray_columns.py    CeilingDDA - same answers, exact early-out for
                              upward rays; use this one for shadow work
  voxelizer/transmittance.py  class-aware Beer-Lambert transmittance in [0,1]
  voxelizer/solar.py          NOAA solar position + grid-convergence correction
  voxelizer/sun_hours.py      direct sun hours per column

Minimal example:

    from datetime import datetime, timezone
    from voxelizer.data_structures import ColumnStore
    from voxelizer.sun_hours import compute_sun_hours
    from voxelizer.transmittance import Extinction

    store = ColumnStore.load('outputs/Run1/area_output/area.npz')
    res = compute_sun_hours(
        store, lat_deg=45.7647, lon_deg=4.8386,
        date_utc=datetime(2026, 6, 21, tzinfo=timezone.utc),
        ext=Extinction.provisional_canopy(0.225),
        label='class-aware', step_minutes=15)
    print(res.summary())

Run the same thing twice - once with Extinction.opaque_only() and once with
finite vegetation coefficients - and the DIFFERENCE is the contribution of the
volumetric material model. Either map alone is not the result.

Experiment scripts (reproduce the reported figures). These live in
Experiments/:
    python Experiments/derive_extinction.py   # fit k from the corpus
    python Experiments/run_sun_hours.py       # opaque vs class-aware

CAVEATS:
  * Extinction coefficients are RESOLUTION-DEPENDENT (they are derived from
    canopy thickness measured in voxels). Use the value fitted at the same
    cell size as the store being queried. The current class-5 values are the
    per-pulse medians 0.3256 at 0.5 m and 0.2076 at 1.0 m, recorded in
    Experiments/campaign_2026-08-08/E10_per_pulse_refit_scopeA/. The 0.225 in
    the example above is the earlier return-count fit at 1.0 m, kept because
    a lot of recorded output was produced with it; return-count values are an
    upper bound on k, so a new figure should use the per-pulse one.
  * Direct beam only: no diffuse sky, no reflection, no atmospheric
    attenuation, no cloud. This is a geometric quantity, not irradiance.
  * For metropolis-scale passes, run in blocks under process isolation so a
    native fault costs one block rather than the run (see HowToUse.md).

## 18. GUI (Tkinter) [OPTIONAL]

Launch the interactive Tkinter-based area launcher:

    python -m voxelizer.gui_area

Seven tabs (Input / Voxel Grid / Files & Outputs / 3-D Visualiser /
Export & Serve / Diagnostics / Advanced); every checkbox has a hover
tooltip. The
`Continue...` button lights up after a stopped/crashed/partial run and
wraps `--resume-shards` (unfinished tiles) or `--resume-from-store`
+ `--only-stage` (re-run output stages); `Resume previous run...`
does the same for any older output directory.

## 19. Verification suite [OPTIONAL]

Re-prove the documented claims against the real LAZ corpus and the
production stores.  Eleven standalone experiments; each prints pass/fail
per claim and writes its measurements to `code_verification/out/`:

    python code_verification/run_all.py          # run all eleven
    python code_verification/exp05_ray_equivalence_real.py   # or any single one

See `code_verification/README.md` for what each experiment covers.
