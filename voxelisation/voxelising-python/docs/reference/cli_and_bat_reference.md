# Running the Voxelizer - Bat Files & CLI Reference

## Quick Start (double-click a bat)

| Bat file | What it does |
| --- | --- |
| `scripts/run_area.bat` | Opens the area voxelizer Tkinter GUI for coordinate-driven multi-tile runs |
| `scripts/run_viewer.bat` | Runs `voxelizer.serve_voxel_html --open` on the latest run under `outputs/`, or on a directory passed as an argument, and opens the streaming 3-D viewer |

A finished run also carries its own generated launchers - `view_stream.cmd`
beside a `*_stream.html`, and `view_cesium.cmd` / `view_itowns.cmd` beside a
`tileset.json` - so a specific result opens with
a double-click on the result itself.

**Note:** for big sharded CLI runs, three crash-resilience flags exist: `--resume-shards` (continue an interrupted run from its finished shards), `--isolate-tiles` (contain native decoder crashes to one tile via the `shard_worker` child; failures recorded in `shards/failed_tiles.json`) and `--retry-lazrs` (one second-opinion decode with the single-thread lazrs backend). They are tabulated under "`area_cli`" below.

**Note:** There is no separate single-tile bat file. The single-tile workflow is
`python -m voxelizer single ...` on the command line, or the *Single file (drag &
drop)* mode inside `run_area.bat`'s Tkinter dialog.

## Settings Dialog (`run_area.bat`)

Double-clicking `run_area.bat` opens a Tkinter dialog. A **Mode** selector at
the top picks between *Single file (drag & drop)* and *Area by coordinates*;
everything else is grouped into seven tabs - Input, Voxel Grid, Files &
Outputs, 3-D Visualiser, Export & Serve, Diagnostics, Advanced - which show
the fields relevant to the selected mode. Hover any control for a tooltip
explaining what it does.

### Tab: Input

File / bbox / tile folder / download / streaming.

| Field | Default | Description |
| --- | --- | --- |
| **LAZ file** (file mode) | *(empty)* | Type a path, click Browse, or drag-and-drop a `.laz`/`.las` file |
| **X min / Y min** (area mode) | `1831000` / `5175000` | RGF93/CC46 (EPSG:3946) bounding box, lower corner |
| **X max / Y max** (area mode) | `1832000` / `5176000` | RGF93/CC46 (EPSG:3946) bounding box, upper corner |
| **Tile folder** | `inputs/laz` | Directory containing IGN LiDAR HD tiles; the dialog reports how many intersect the box |
| **Download tiles from Grand Lyon** | off | Fetch the intersecting tiles from the inventory JSON before the run |
| **Streaming mode** | off | Download-voxelize-discard instead of reading a local folder (needs the inventory JSON) |

### Tab: Voxel Grid

| Field | Default | Description |
| --- | --- | --- |
| **cell_xy** | `0.5` | Horizontal voxel size in metres |
| **cell_z** | `0.5` | Vertical voxel size in metres |
| **height** | `default` | `default` / `relative` / `absolute` - contrast reference of the max-height map only |
| **Stream in chunks** | on | Read the LAZ in chunks instead of whole-file |
| **chunk size** | `5000000` | Points per chunk when streaming in chunks |
| **Clip to exact bounding box** | on | Drop points outside the requested rectangle (area mode) |

### Tab: Files & Outputs

Every keep/delete decision: source LAZ, voxel-grid cache, per-stage store,
pre-grouping store, column diagnostics.

| Field | Default | Description |
| --- | --- | --- |
| **Generate per-column diagnostics (columns/ folder)** | on | Unchecking it is the same as `--columns-mode skip` |
| *(mode dropdown beside it)* | `diag` | `diag` / `top` / `all` |
| **top N** | `50` | Only used when the mode is `top` |
| **Delete source LAZ file(s) after processing** | off | Only meaningful with download / streaming |
| **Keep voxel grid cache (area.npz / shards/) for reuse** | on | Keep the persisted store |
| **Keep per-stage store (store_raw/) for --resume-from-store** | off | Lets output stages be re-run without re-voxelizing |
| **Enable grouping (merge consecutive same-class intervals)** | on | The default post-pass over the store |
| **Grouping gap (m)** | *(empty)* | Optional maximum gap grouping may bridge |
| **Keep pre-grouping store (area_raw.npz) for re-grouping later** | off | Also persist the ungrouped store |
| **Intermediates policy** | `auto` | What to do with intermediate files |
| **Merge shards after run (area.npz; columns/ needs no merge)** | on | In shard mode, merge the shards at the end into `area.npz`. Peak RAM during that phase is one merge band, not the merged store. Unticking it makes the run shard-only: `stats.txt`, the mosaic maps, the full and ROI 3-D views and `columns/` are still written from the shards, but `area.npz`, `area_raw.npz`, `store_raw/` and the streaming viewer are not, and the 3-D tab withdraws its `streamable` mode |

`stats.txt` and the four 2-D maps are always written and are not optional.

### Tab: 3-D Visualiser

**Enable 3-D visualiser (Three.js HTML)** is off by default; ticking it
reveals the export options - mode (`singleton` default), `max boxes`
`5000000`, `ROI size (m)` `200` with an optional `ROI cx` / `ROI cy` centre,
`render full view` and `render ROI view` (both on), `max GPU instances`
`4000000`, `inline threshold (MB)` `64`, and `tile size (m)` `64`.

`singleton` writes one self-contained HTML file per view, auto-thinned to
`max boxes`. `streamable` writes the streaming viewer (`*_stream.html` plus
a Range-fetched `.bin` sidecar), which carries every interval and bounds the
GPU working set at view time instead of decimating the payload; the last
four fields apply to it only. A shard-only run (no merge) restricts the
dropdown to `singleton`, because the streaming payload is walked from one
store's key order and a set of shards has none.

### Tab: Export & Serve

What to do with a run that has already finished. **Export to 3-D Tiles**
takes a run directory (it finds `area.npz` inside it, falling back to
`area_raw.npz`) or a store `.npz` directly, and writes `tileset.json`, the
`tile_*.glb` files and the generated `view_*.cmd` launchers into a chosen
directory, defaulting to a `tiles/` folder beside the store. Its options are
`tile size (m)` `100`, `LOD pyramid` (on; off emits `--flat`), `geoid
heights` (on; off emits `--no-geoid` and sits about 50 m low at Lyon) and an
optional `keep classes` list.

**View in a browser** starts a local server on a chosen directory: *View
stream* runs `voxelizer.serve_voxel_html` on a folder holding `*_stream.html`
(preferring `area_stream.html`), and *View tileset* runs
`voxelizer.serve_tiles` on a folder holding `tileset.json`, opening the
bundled `cesium` or `itowns` page. A streaming page that kept its `.bin`
sidecar needs an HTTP origin, since it fetches byte ranges of that file; a
small export whose payload was inlined into the page opens from the file
system too. Both exporters already write the same launchers beside their
artifacts, so these buttons are that double-click without leaving the window.

### Tab: Diagnostics

| Field | Default | Description |
| --- | --- | --- |
| **Live diagnostics** | off | Wrap the job with `voxel_runner_diagnos.py` so CPU/RAM ticks stream into the log |
| **Open HTML dashboard (--serve)** | off | Additionally open the live browser dashboard |

### Tab: Advanced

| Field | Default | Description |
| --- | --- | --- |
| **Isolate output stages** | on | `--isolate-stages`. Each stage (stats, `area.npz`, maps, columns, 3-D) runs in its own child process attached to a memory-mapped on-disk store, so a native crash costs that stage only |
| **Stage timeout (s)** | *(empty)* | `--stage-timeout`. Left blank there is no limit |
| **Isolate each tile in a child process (survive native decoder crashes)** | on | `--isolate-tiles`, shard mode only |
| **Retry a crashed tile once with the lazrs decoder** | off | `--retry-lazrs`, shard mode only; requires the tile isolation above |
| **Merge band intervals** | *(empty)* | `--merge-band-intervals`, shard mode only. Left blank the merge uses its own default of 40,000,000 intervals per band |

The last three apply when *Shard beyond RAM* is chosen at the pre-flight
dialog, and when resuming a sharded run.

A pre-flight cost estimation dialog always runs first for area-by-coordinate
runs. It shows the predicted tile count, area and memory cost, takes a RAM
budget in megabytes, and offers three buttons: **Cancel (refuse run)**,
**Continue anyway (RAM watchdog)** and **Shard beyond RAM**. Closing the
window counts as Cancel.

## Running from cmd / PowerShell

All commands below assume you are in the `voxelising-python\` directory and have
activated the virtualenv (or use the `.venv\Scripts\python.exe` path directly).

### Environment setup (one-time)

```cmd
cd voxelising-python
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Single tile

```cmd
.venv\Scripts\python.exe -m voxelizer single PATH_TO_TILE.laz --output-dir outputs\Run1\single
```

### An area by coordinates

```cmd
.venv\Scripts\python.exe -m voxelizer.area_cli area --xmin 1831000 --ymin 5176500 --xmax 1832000 --ymax 5177500 --laz-dir inputs\laz
```

Add `--preflight-only` first to see the predicted tile count and RAM, and
`--shard` for areas beyond RAM. Left out, `--output-dir` defaults to
`outputs\RunN\single\` for the single-tile run and `outputs\RunN\area_output\`
for the area run, with an auto-incrementing run number in both.

## CLI Options

`python -m voxelizer` has two subcommands: `single` (process one LAZ tile)
and `serve` (serve a finished output directory and open it in a browser,
documented further down).

### `single` subcommand

```cmd
python -m voxelizer single LAZ_FILE [--output-dir DIR] [OPTIONS]
```

| Positional/Option | Default | Description |
| --- | --- | --- |
| `LAZ_FILE` | required | Path to a `.laz` or `.las` file |
| `--output-dir`, `-o` | `outputs/RunN/single/` | Run directory; outputs go to `<DIR>/<tile_stem>/`. The run number auto-increments |
| `--cell-xy F` | `0.5` | Horizontal voxel size in metres |
| `--cell-z F` | `0.5` | Vertical voxel size in metres |
| `--columns-mode` | `diag` | Column output mode: `all` / `top` / `diag` / `skip` (see below) |
| `--columns-top-n N` | `50` | How many columns to keep when the mode is `top` |
| `--height-mode` | `default` | Vertical reference of the max-height map only: `default` auto-contrasts the tops, `relative` shows height above each column's own lowest occupied voxel (nDSM/CHM-style), `absolute` shows true altitude from the tile floor (DSM) |
| `--delete-laz` | off | Delete the source file after successful voxelization |
| `--viz3d` | off | Also render the interactive Three.js HTML viewers into `<out>/<tile_stem>/` |
| `--viz3d-stream` | off | Render the streaming HTML viewer instead, which handles 200M+ boxes |
| `--max-boxes N` | `5000000` | Box budget for the full 3-D view, auto-thinned by striding above it. Must be positive; for an uncapped export use the streaming viewer |
| `--roi-size F` | `200` | Side length in metres of the region-of-interest view |
| `--roi-cx` / `--roi-cy` | area centre | ROI centre in RGF93/CC46 (EPSG:3946) metres |
| `--no-full` / `--no-roi` | both views on | Skip the full-area or the ROI 3-D view |
| `--tile-m F` | `64` | Streaming viewer tile size in metres. `--viz3d-stream` only |
| `--max-instances N` | `4000000` | Hard GPU cap for the streaming viewer. `--viz3d-stream` only |
| `--inline-threshold-mb N` | `64` | Base64-embed the `.bin` when it is smaller than this. `--viz3d-stream` only |

## `--columns-mode` explained

Controls what lands in the `columns\` subfolder of each tile's output.

| Mode | 4 diagnostic PNGs | Per-column PNGs | Speed | Use case |
| --- | --- | --- | --- | --- |
| `diag` | Yes | No | Fast | **Default.** Quick overview of column structure. |
| `top` | Yes | Top N by interval count | Medium | Detailed inspection of the most complex columns. |
| `all` | Yes | One per occupied column | **Slow** | Full column-level analysis. |
| `skip` | No | No | Fastest | Only the 4 tile-level PNGs + stats.txt. |

`all` was built for single-tile stores. A dense 500 m tile at the default
0.5 m cell has roughly 800k occupied columns, and the measured cost on the
reused-figure path is 75 ms and 27 KB per PNG, so the mode is capped:
`--columns-all-max` (area runs) defaults to the 500,000 columns of
`column_diagnostics._MODE_ALL_MAX_COLUMNS` and refuses above it, quoting the
estimated hours and gigabytes so the choice is informed. `--columns-all-max 0`
disables the cap. Output fans out into `blk_XXXX_YYYY/` subdirectories
automatically (100 columns per block, so at most 10,000 files each), because
a single directory of millions of files cripples NTFS enumeration.

The 4 diagnostic PNGs (when not `skip`) are:

- `columns_samples.png` - one representative column per category (ground only, ground+building, ground+tree, etc.)
- `columns_top_complex.png` - the columns with the most intervals
  (`--columns-top-n`, clamped to 24 panels)
- `columns_by_class.png` - one representative column per class code present
- `histogram_intervals.png` - distribution of intervals per column (linear + log-log)

## Output Structure

### Single tile (`python -m voxelizer single ...`)

```
outputs\Run1\single\
    <tile_stem>\
        <tile_stem>_max_height.png
        <tile_stem>_max_points_class.png
        <tile_stem>_orthophoto_class.png
        <tile_stem>_n_intervals.png
        stats.txt
        columns\                    (unless --columns-mode=skip)
            diagnostics\
                columns_samples.png
                columns_top_complex.png
                columns_by_class.png
                histogram_intervals.png
            per_column\             (only in 'all' or 'top' mode)
                col_ix0000_iy0324.png
                ...
```

## Examples

### Default run (diagnostic columns only)

```cmd
.venv\Scripts\python.exe -m voxelizer single tile.laz -o outputs\Run1\single
```

### With top-100 column PNGs

```cmd
.venv\Scripts\python.exe -m voxelizer single tile.laz -o outputs\Run1\single --columns-mode top --columns-top-n 100
```

### Fine-grained voxels, skip column diagnostics

```cmd
.venv\Scripts\python.exe -m voxelizer single tile.laz -o outputs\Run1\single --cell-xy 0.5 --cell-z 0.2 --columns-mode skip
```

---

## `area_cli` - the whole-area entry point

```cmd
python -m voxelizer.area_cli area --xmin X --ymin Y --xmax X --ymax Y --laz-dir DIR [OPTIONS]
```

`--laz-dir` is required unless `--resume-from-store` is used. Coordinates are
RGF93/CC46 (EPSG:3946). `--preflight-only` prints the tile and RAM estimate
and exits without processing anything.

### Reading the tiles

| Option | Default | Description |
| --- | --- | --- |
| `--chunk-size N` | `5000000` | Points per chunk while a tile is read in streaming mode, the `DEFAULT_CHUNK_SIZE` of `voxelize.py` |
| `--no-chunks` | off | Read each tile whole with `laspy.read` instead of streaming it chunk by chunk |
| `--no-clip` | off | Keep whole intersecting tiles instead of clipping them to the bounding box |

### Fetching the tiles as part of the run

| Option | Default | Description |
| --- | --- | --- |
| `--download` | off | Fetch the tiles covering the box into `--laz-dir` first, then voxelize them. Needs the inventory JSON and a network |
| `--stream` | off | Download each tile, voxelize it and discard it one at a time, replacing the two-phase download-then-voxelize flow. Requires `--json` |
| `--json PATH` | - | The Grand Lyon inventory JSON that `--download` and `--stream` read |
| `--tile-pitch M` | `500` | Tile size in metres of the acquisition grid: the origin window `--download` fetches, and the inventory tile selection under `--stream` |
| `--workers N` | `min(32, cpu+4)` | Parallel download threads for `--download` |
| `--limit N` | `0` (no cap) | Cap on the tiles fetched by `--download` or processed by `--stream`, useful for a test run |

### Sharding and merging

| Option | Default | Description |
| --- | --- | --- |
| `--shard` | off | Voxelize each tile into its own `shards/*.npz` and free it, so peak memory is one tile. `stats.txt`, the four mosaic maps, the full and ROI 3-D views and everything under `columns/` are computed from the shards themselves by `shard_diagnostics`, with no merged store |
| `--resume-shards` | off | Reuse every matching shard already in `<output-dir>/shards/` instead of re-voxelizing its tile; lattice metadata is validated per shard and `run_config.json` guards against parameter drift |
| `--isolate-tiles` | off | Voxelize each tile in a `shard_worker` child, so a native decoder crash costs one tile, recorded in `shards/failed_tiles.json` |
| `--retry-lazrs` | off | With `--isolate-tiles`: retry a crashed tile once with the single-thread lazrs backend before recording it as failed |
| `--merge-shards` | off | After the per-tile loop, merge every shard into `<out>/store_raw/` and write `area.npz` and the stages that read it. The merge is out of core: `store_raw/` is assembled one key band at a time and the stages attach to it by memory map, so peak RAM follows the band budget rather than the merged store |
| `--merge-band-intervals N` | `40000000` | That budget, in intervals per band - about 1.4 GB peak. Halve it on a small machine; raise it to re-read the compressed shards fewer times, since a shard is read once per band its column range touches |
| `--delete-shards` | off | Delete the `shards/` cache at the end, once everything that reads it has run. Reclaims disk and disables re-rendering, resuming and archiving |

A shard-only run refuses, before the first tile is read, any flag that needs
the merged store: `--keep-raw-store`, `--keep-area-raw`, `--only-stage`,
`--stage-timeout` and `--viz3d-stream`. Adding `--merge-shards` builds the
store those flags act on; dropping the flag leaves the from-shards outputs,
which are written either way.

### Stage isolation

| Option | Default | Description |
| --- | --- | --- |
| `--isolate-stages` / `--no-isolate-stages` | on | Run each output stage in its own child process attached to a memory-mapped on-disk store, so a native crash in one stage costs that stage only - the run retries it once and writes every other output. Also frees the parent's in-heap store during rendering. Applies to non-shard runs; a `--shard` run always isolates its merged phase |
| `--stage-timeout SECONDS` | `0` (no limit) | Wall-clock limit per isolated stage, after which the child is killed and retried once. Off by default because a fixed limit cannot tell a big stage from a stuck one, because the value that separates them scales with the store. The stage logs and faultlogs are the hang evidence instead |
| `--only-stage STAGE` | all | Restrict the run to the named stage(s), repeatable. Stages: `stats`, `persist_npz`, `maps`, `col_diag`, `viz3d_roi`, `viz3d_full`, `viz3d_stream` |
| `--resume-from-store DIR` | - | Skip voxelization and run the output stages from an existing `store_raw/`. Geometry and options come from its `run_params.json`, so the bbox flags are not needed |
| `--max-rss-mb N` | off | RAM watchdog: abort the tile loop cleanly, writing partial outputs, if the process's RSS crosses the budget |

### What is kept on disk

| Option | Default | Description |
| --- | --- | --- |
| `--intermediates {keep,delete,auto}` | `auto` | Policy for `store_raw/`, `area_raw.npz` and `templaz/`. `auto` deletes them on full success and, if any stage failed, keeps everything and prints the exact `--resume-from-store` command. Deliverables and `stages/` diagnostics are never touched |
| `--keep-raw-store` | off | Keep `<out>/store_raw/` after the run. Required to use `--resume-from-store` later |
| `--keep-area-raw` | off | Keep `<out>/area_raw.npz`, the pre-grouping store written when `--group-intervals` is on, so grouping can be re-run with a different `--group-gap` without re-voxelizing. In `--shard` mode this triggers a second out-of-core merge pass so `area_raw.npz` is produced alongside `area.npz`; peak RAM stays one band either way, but the ungrouped store is the largest artefact a run produces and this asks for it twice on disk |
| `--no-save-store` | off | Do not write `area.npz` + `area_manifest.json` at all |

Both explicit flags override the `--intermediates` umbrella for their own
artefact. Declining `--keep-area-raw` loses no data: the ungrouped form lives
in the per-tile shards, and can be rebuilt later over a smaller sub-area with
`--resume-shards --no-group-intervals`.

### Grouping

`--group-intervals` (on by default; `--no-group-intervals` renders the raw
store) merges consecutive same-class intervals per column before any output
is drawn, which collapses sparse-return fragmentation into a smaller store,
fewer 3-D boxes, and complexity diagnostics that count structures rather than
return sparsity. `--group-gap METRES` restricts the merge to vertical gaps up
to that many metres; the default merges across any gap.

---

## `viz3d_cli` - 3-D viewers on their own

```cmd
python -m voxelizer.viz3d_cli single     LAZ_FILE -o DIR [OPTIONS]
python -m voxelizer.viz3d_cli from-store STORE.npz -o DIR [OPTIONS]
python -m voxelizer.viz3d_cli stream     STORE.npz --out PAGE.html [OPTIONS]
```

`single` voxelizes one tile and renders from it; `from-store` renders from a
saved store such as a run's `area.npz`, with no voxelization; `stream` writes
the streaming viewer. `--grid` (on `single` and `from-store`) additionally
writes a `.vxg` grid cache, the same lossless quantized encoding the HTML
embeds, which `load_grid()` reads back without re-reading the LAZ.

`stream` accepts `--stride` and `--max-boxes` for compatibility and ignores
both, printing a warning: it always writes every interval at full detail.
Shrink the payload with `--region XMIN YMIN XMAX YMAX` or `--keep-classes`
instead. `--max-instances` (default 4M, about 0.3 GB of GPU memory at ~76 B
per instance) is a working-set budget, not a payload cut: the page keeps the
tiles nearest the camera resident at full detail up to that budget and draws
the rest as per-tile impostor silhouettes. `--tile-m` (default 64) sets how
finely it can cull and evict, and `--inline-threshold-mb` (default 64)
base64-embeds the `.bin` into the page and deletes the sidecar when it fits.

---

## `postprocess_cli` - semantic passes, store in, store out

```cmd
python -m voxelizer.postprocess_cli IN OUT.npz [PASSES]
```

`IN` is a `.npz` or a raw `store_raw/` directory attached by memory map;
`OUT` is written atomically. The passes always run in the order
min-points -> morph -> absorb -> resolve -> group, whichever order the flags
are given in.

| Option | Default when bare | Description |
| --- | --- | --- |
| `--min-points [N]` | `4` | Drop intervals with `<= N` points |
| `--morph [MIN_NEIGH]` | `2` | Drop intervals with fewer than `MIN_NEIGH` same-class neighbours among the 8 columns around them; `--morph-classes A,B,C` restricts it |
| `--absorb` | - | Reclassify vegetation runs sandwiched by building inside a building-dominant neighbourhood. `--absorb-min-neighbour` (0.75) is the minimum building fraction among the penetrated 8-neighbours; `--absorb-max-noise` (2) is the height in voxels below which a sandwiched run is absorbed unconditionally |
| `--resolve` | - | Collapse cross-class overlaps so every voxel carries one class. `--tie-margin` (1) and `--tie-rel` (0.0) set the near-tie band, `--no-prefer-taller` drops the taller-run tie-break, `--no-demote-uncertain` lets unclassified/noise/artefact classes win near-ties, `--class-priority A,B,C` overrides that with an explicit order |
| `--group` | - | Merge vertically-consecutive same-class intervals afterwards; `--group-gap M` bounds the gap it may bridge |
| `--stats` | - | Print the full per-class point table for every pass, not only the classes whose totals moved |
| `--dry-run` | - | Run the passes and report, write nothing |

Omitting a pass's flag means that pass does not run. Every store-driven tool
downstream - the 2-D maps, the 3-D viewers, 3D Tiles, reconstruction - reads
the result unchanged.

---

## `tileset_cli` - 3D Tiles export

```cmd
python -m voxelizer.tileset_cli from-store   STORE.npz -o OUT_DIR [OPTIONS]
python -m voxelizer.tileset_cli from-payload PAYLOAD   [OPTIONS]
```

`from-store` goes `.npz` -> tiled payload -> tileset in one call;
`from-payload` converts an existing `.idx.json` + `.bin` pair. The output is
an OGC 3D Tiles 1.1 tileset: one `tileset.json` plus one `tile_*.glb` per
node, full-detail leaves beneath a quadtree of coarsened interior levels.

| Option | Default | Description |
| --- | --- | --- |
| `--tile-m F` | `100` | Leaf tile size in metres; the LOD pyramid groups leaves 2x2 per level |
| `--flat` | off | Legacy flat root-to-leaves layout instead of the LOD pyramid |
| `--vertical-crs` | `EPSG:5720` | Vertical CRS of the input altitudes (NGF-IGN69). The geoid grid is applied through pyproj, which uses the PROJ CDN on first use |
| `--no-geoid` | off | Treat the altitudes as already ellipsoidal; the model then sits ~50 m low against real terrain at Lyon |
| `--height-offset F` | off | Explicit metres to add to the origin height instead of the geoid grid (about 49.7 for Lyon) |
| `--keep-classes` | every class | Comma/space list of ASPRS classes to export |
| `--crs` | `EPSG:3946` | Native CRS recorded in the tileset extras |
| `--region XMIN YMIN XMAX YMAX` | whole store | Metric subset, to reduce the payload |

Both converters also write the `view_cesium.cmd` and `view_itowns.cmd`
launchers beside `tileset.json`.

---

## `serve` - put a finished directory in front of a browser

```cmd
python -m voxelizer serve DIR [--kind stream|tiles] [--port N] [--bind ADDR]
                               [--no-open] [--open-viewer cesium|itowns]
                               [--open-page NAME]
```

`DIR` is a run's output directory holding a `*_stream.html`, or a 3-D Tiles
directory holding `tileset.json`. The kind is read off the directory and only
needs stating when it holds both. Left unset, `--port` takes the chosen
server's own default: 8000 for the streaming viewer, an OS-chosen free port
for a tileset. `--port 0` always asks the operating system for a free one.
`--open-page` picks which streaming page to open, defaulting to
`area_stream.html` when present.

The same servers are what the generated `.cmd` launchers start:
`view_stream.cmd` beside a `*_stream.html`, `view_cesium.cmd` and
`view_itowns.cmd` beside a `tileset.json`. For the three server launchers the
console window that opens is the
server's lifetime, and closing it stops the server; each file says so in
its own header.

---

## Environment variables

| Variable | Values | Effect |
| --- | --- | --- |
| `VOXELIZER_BIND` | an interface address | Overrides the `--bind` default of both HTTP servers, shared through `cli_common.default_bind`. The default is loopback (`127.0.0.1`): what is being served is a directory of the machine's own files. A container publishes its ports deliberately and must listen on every interface for the published port to reach anything, which is what this is for |
| `VOXELIZER_NO_BROWSER` | `1` | Serve without opening a browser - the same as `--no-open` |
| `VOXELIZER_LAZ_BACKEND` | `laszip` (or unset), `lazrs` | Selects the LAZ decoder for this process. `laszip` (the C++ LASzip via the `laszip` package) is the pinned default for every read; `lazrs` is single-threaded and is reached only through the `--retry-lazrs` isolated retry. `lazrs_parallel` is refused on purpose - it corrupts memory on large IGN tiles. If the lazrs wheel is missing the retry falls back to laszip with a warning rather than failing |
| `VOXELIZER_RUN_ORDER` | `class_first` (default), `height_first` | Sets the sort order the vertical RLE walks, so it decides run structure: `class_first` sorts `(ix, iy, cls, iz)` and makes runs maximal per `(ix, iy, cls)`; `height_first` sorts `(ix, iy, iz, cls)` and splits a same-class run wherever another class interleaves in the same voxels (0.19 % of intervals on a 16.6 M-interval urban merge). Both are deterministic and give the same per-voxel classes and per-class point counts. `class_first` is the production default; `height_first` remains fully supported as the compatibility order, which is what every store and archive written before the switch records in its provenance - the archive tools re-voxelize under the recorded order, and a shard resume under a different order is refused |

---

## `reconstruct` - ColumnStore <-> LAS/LAZ

```cmd
python -m voxelizer.reconstruct to-laz NPZ OUTPUT [OPTIONS]
python -m voxelizer.reconstruct to-npz LAZ OUTPUT [OPTIONS]
python -m voxelizer.reconstruct verify NPZ LAZ
```

The pre-subcommand spelling `python -m voxelizer.reconstruct NPZ OUTPUT` is
still accepted and means `to-laz`.

### `to-laz`

| Option | Default | Description |
| --- | --- | --- |
| `--mode` | `density` | `density` (one point per original LiDAR point via `count`), `one_per_voxel`, or `exact` |
| `--one-per-voxel` | off | Legacy alias for `--mode one_per_voxel` |
| `--z-base` | off | Use the interval base for z instead of the centre (rejected by `--mode exact`) |
| `--epsg N` | `3946` | CRS written into the header. Files without a CRS are rejected by `io_laz.read_laz` |
| `--no-grid-vlr` | off | Omit the `IARBRE` grid VLR; the rebuild then needs `--origin/--cell-xy/--cell-z` |
| `--verify` | off | After writing, rebuild from the file and assert it matches. Exit 1 on mismatch |

Only `--mode exact` is a **bit-exact** round trip. `density` loses the
interval extent (all `count` copies sit at one z); `one_per_voxel` loses the
point counts.

### `to-npz`

| Option | Default | Description |
| --- | --- | --- |
| `--origin X Y Z` | from the VLR | Grid origin. Required if the file has no `IARBRE` VLR |
| `--cell-xy F` | from the VLR | Horizontal cell size |
| `--cell-z F` | from the VLR | Vertical cell size |

Rebuilding on a *derived* origin lands the grid half a cell off, because
`voxelize` takes its origin from `min(x)` and a reconstructed cloud's minimum
is a voxel centre. Hence the VLR.

---

## `archive_cli` - exact shard archive

```cmd
python -m voxelizer.archive_cli pack   RUN_DIR [OPTIONS]
python -m voxelizer.archive_cli unpack RUN_DIR [OPTIONS]
```

`pack` converts every `shards/*.npz` to `shards_laz/*.laz` (0.3-1.7x the size
depending on grid fineness - see `laz_roundtrip_design.md` section 10 - and readable by
any GIS tool); `unpack` rebuilds them bit-identically.

### `pack`

| Option | Default | Description |
| --- | --- | --- |
| `RUN_DIR` | - | Run directory, or its `shards/` directory |
| `--workers N` | `1` | Parallel shard workers (shards are independent) |
| `--no-verify` | off | Skip the per-shard rebuild-and-compare. ~3x faster, strictly worse |
| `--replace` | off | Delete each source `.npz` once its `.laz` verifies. **Refused with `--no-verify`** |
| `--epsg N` | run's recorded EPSG, else `3946` | CRS written into the archive files |

### `unpack`

| Option | Default | Description |
| --- | --- | --- |
| `ARCHIVE_DIR` | - | Archive directory, or the run directory above it |
| `--out DIR` | `<run>/shards` | Where to write the rebuilt `.npz` files |
| `--workers N` | `1` | Parallel workers |

Both exit non-zero if any shard fails. `unpack` also restores
`shards/manifest.json`, without which the run's merge and `--resume-shards`
paths have nothing to iterate.

**Only the raw per-tile shards can be archived.** `area.npz` is the *grouped*
store and grouping is not reliably invertible, so `pack` refuses anything that
is not `shards/`; regenerate `area.npz` from the restored shards instead. An
exact export can later be re-voxelized to a **coarser** grid faithfully, but
never to a finer one. See `laz_roundtrip_design.md`.
