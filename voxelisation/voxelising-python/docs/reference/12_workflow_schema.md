# Workflow schema

One diagram for the two commands that matter, plus the read side that is often
mistaken for part of them. The boxes are a function-level graph of one run: the
functions these commands actually call, plus the child-process entry points
(`shard_worker.main`, `stage_runner.main`). The functions of the six essential
modules are a subset of them, 25 of the 49 function boxes, the other 24
belonging to 19 further modules plus one repository script. Beside the function
boxes the drawing carries 14 artifact cylinders and 2 input nodes. This departs
from the standard's rule that the boxes are the six essential modules, and it
does so on purpose: six boxes cannot show what calls what inside the flow.

The six are `data_structures`, `voxelize`, `decoder`, `sharding`,
`merge_streaming` and `area_cli`, the same six ARCHITECTURE section 1 names.
Their 25 boxes carry a thick gold border, so the spine is visible without
reading the module prefix off 49 captions.

Two module counts, because they are two different numbers. Modules drawn: 25
package modules own a box, the six plus the 19 others; three more
(`visualizer3d`, `ray_trace`, `solar`) are named inside a caption without
owning a box of their own. Modules entered: a default `area` run, meaning no
`--download` and no `--shard`, runs a function of 16 package modules, and four
of those sixteen are named nowhere in the drawing. They are `cli_common` (the
shared argparse clusters both parsers build on), `run_utils` (`make_provenance`
on every run, `next_run_output_dir` when `--output-dir` is omitted),
`classes_config` (the class table `ColumnStore.stats` reports against) and
`viz_common` (the class colour table the maps share with the column
diagnostics). The other twelve are `area_cli`, `preflight`, `area`, `io_laz`,
`voxelize`, `data_structures`, `store_streaming`, `area_outputs`,
`stage_runner`, `pipeline` (`_format_stats` and `MODES`, reused by the area
stages), `visualization` and `column_diagnostics`.

Solid arrows are calls on the default path, labelled with the data that crosses
them. A thick arrow is a data hand-off between two boxes that do not call each
other: their common caller calls both and passes the value the label names, and
six arrows are of that kind. A dashed arrow is a branch taken only under the
flag on its label. An arrow leaving a cylinder is a read of that file rather
than a call, and an arrow ending in a cross is a deletion. Cylinders are
artifacts written to disk; a dashed cylinder is an intermediate that a clean
run deletes. Parallelograms are the input data. Functions are cited by
qualified name, `module.function`, `module.Class.method` or `module.Class`, and
never by source line: every line number the earlier revision carried had
already rotted. Three boxes are not one name: `M13` and `C21` each hold the
functions of one call chain, and `R6` is a repository script path.

Rendering source is `12_workflow_schema.mmd`, rendered to
`12_workflow_schema.svg`.

```
npx -y @mermaid-js/mermaid-cli -i 12_workflow_schema.mmd -o 12_workflow_schema.svg -b white
```

The `.mmd` opens with an `init` block and carries one invisible link,
`P1 ~~~ P2`. Both are layout only. Without them dagre puts the two panels side
by side and the render is 10205 px wide; with them the panels stack and it is
6229 px wide by 6723 tall, narrower than the 6853 px the previous revision
shipped while carrying more boxes, more arrows and two input nodes. That
invisible link is the one edge in the file with no label; it draws no line and
no arrow head. All 91 real arrows carry a label.

## Panel A: `python -m voxelizer single TILE.laz`

One LAZ file in, per-tile outputs out, plus the tile's own store under
`--shards` / `--keep-raw-store`. No merge, no decoder. Those two branches mean
the panel carries two artifact cylinders beyond the per-tile outputs.

| Step | Call | Data crossing the arrow |
|---|---|---|
| 0 | `TILE.laz` -> `io_laz.read_laz` | the input node of the panel: the one tile named on the command line, opened by laspy |
| 1 | `__main__.main` -> `pipeline.process_single_tile` | the tile path, the parsed options (cell sizes, `--keep-classes`) |
| 2 | `pipeline.process_single_tile` -> `voxelize.voxelize_laz` | tile path, cell sizes, `keep_classes` |
| 3 | `voxelize.voxelize_laz` -> `io_laz.read_laz` | point arrays (x, y, z, class) |
| 4 | `voxelize.voxelize_laz` -> `voxelize.voxelize` | the point arrays |
| 5 | `voxelize.voxelize` -> `voxelize._cells_from_points`, then `voxelize._store_from_cells` -> `ColumnStore.from_intervals` | `voxelize` calls both halves and passes the quantized cells between them, which is why that edge is drawn as a hand-off and not as a call; `_store_from_cells` does call `from_intervals` |
| 6 | `pipeline.process_single_tile` -> `ColumnStore.stats` and `ColumnStore.column_summaries` | the store |
| 7 | `pipeline.process_single_tile` -> `visualization.plot_tile_map` (once per mode) and `column_diagnostics.write_column_diagnostics` (fed the store) | the summaries reach `plot_tile_map` from `process_single_tile`, not from `column_summaries`, which is the second hand-off arrow of the panel; `plot_tile_map` then draws the `uint8` raster `render_tile_map` returns on an Axes and `pipeline` saves it with `fig.savefig` |
| 8 | optional, dashed, from `__main__.main` on the store it already holds: `--viz3d` -> `viz3d_cli._run_single` -> `visualizer3d.render_store_3d`; `--viz3d-stream` -> `tiled_exporter.export_tiled_from_store` | the store. The two flags are exclusive and the streaming viewer wins: the code is `if args.viz3d_stream: ... elif args.viz3d:` |
| 9 | optional, dashed: `--shards` -> `sharding.save_single_tile_shard`, which calls `ColumnStore.save` and writes `shards/manifest.json`; `--keep-raw-store` -> `pipeline.process_single_tile` -> `ColumnStore.save_dir` (via its `save_store_to`) | the store, again. The shard is the raw (ungrouped) store plus the manifest the shard tooling reads; the raw store is the memory-mappable directory plus a `run_params.json` that `area_cli --resume-from-store` later re-enters. One run writes one shard, and this verb has no intermediates sweep, so neither is ever deleted |

Artifacts: `<tile>/stats.txt`; four `<stem>_*.png` maps
(`max_height`, `max_points_class`, `orthophoto_class`, `n_intervals`);
`<tile>/columns/diagnostics/*.png` and, in `top` or `all` mode,
`<tile>/columns/per_column/*.png`. Under `--viz3d`, `<stem>_full.html` and
`<stem>_roi.html`; under `--viz3d-stream`, `<stem>_stream.html` with its `.bin`
and `.idx.json` sidecars. Under `--shards`, `shards/<tile_stem>.npz` +
`shards/manifest.json`; under `--keep-raw-store`, `store_raw/` + `store_raw/run_params.json`.
Steps 8 and 9 are drawn in the panel, dashed.

## Panel B: `python -m voxelizer.area_cli area`

An emprise in, a merged store and its outputs out. The input node carries both
halves of what a run is given: the LAZ tiles under `--laz-dir`, and the bbox
flags. It feeds `area.select_area_tiles`, which reads one header per candidate
tile through `io_laz.read_laz_header`, and `voxelize.voxelize_laz_chunked`,
which is where the points themselves are read through `io_laz.read_laz_chunks`;
under `--shard` the same chunked read happens in `sharding.run_area_sharded`.

Two paths split in `area_cli.main` on `--shard` and never rejoin: in-memory
(`area_cli.run_area`, which drives `area.process_area`) and sharded
(`sharding.run_area_sharded`). The shard path rejoins the same stage machinery
only when `--merge-shards` is given.

`area_cli.main` parses and refuses bad flag combinations; the run itself is
`area_cli.run_area`, which picks the tile loop, writes `area_raw.npz`, applies
the grouping and chooses the output writer. Every arrow of the build below
therefore leaves `run_area`, not `main`. Four branches are the exception and
are drawn from `main` itself, because `main` takes them before `run_area` is
reached: `--preflight-only`, `--download`, `--shard` and `--resume-from-store`.
`main` also calls `area_cli._cleanup_intermediates` at both of its exits, after
the deliverables are written, and that call is drawn too.

`--download` fetches the tiles of the origin window before anything is read.
It requires `--json INVENTORY`, and `area_cli._maybe_download` calls
`download_laz.download_laz`, which writes the tiles into `--laz-dir`: in the
drawing its arrow lands on the input node, because that is what the branch
changes.

`--resume-from-store DIR` skips the whole build. `area_cli.main` reads
`DIR/run_params.json` for the bbox and every output option, then calls
`area_outputs._write_outputs_isolated` directly with `store=None` and
`raw_store_dir=DIR`, so no LAZ is read, nothing is voxelized or merged, no
`area_raw.npz` is written and no grouping is applied: the store in `DIR` is
already the grouped one a previous `--isolate-stages --keep-raw-store` run
left behind. The writer skips its own `save_dir`, refuses a directory without
a `meta.json`, and re-enters the ordinary stage machinery from there, so the
deliverables are the same set the default path produces. `main` exits on the
stage manifest without ever calling `run_area`.

### In-memory path

| Step | Call | Data crossing the arrow |
|---|---|---|
| 1 | `area_cli.main` -> `area_cli.run_area` -> `area.process_area` (or `area.process_area_streamed` under `--stream`) | bbox, grid, class filter; either loop returns the merged store to `run_area`, which is what reaches step 6. The `--stream` edge into the saver is a hand-off for that reason: `process_area_streamed` writes nothing itself |
| 1b | `area.process_area_streamed` -> `voxelize.voxelize_laz_chunked` and -> `ColumnStore.merge` | the streamed loop is not a shortcut past the build: it downloads one tile into `templaz/`, voxelizes it with the same chunked call, deletes the temporary file at once, then merges the per-tile store into the area store with the same `area.merge(tile, inplace=True)`, and checks the RSS budget after each tile exactly as `process_area` does. Both calls are drawn from the streamed box. What differs is only where the tile comes from and that it does not survive the loop |
| 2 | `area.process_area` -> `area.select_area_tiles` | bbox in; back, the kept tile paths and the area-wide z floor that becomes the shared origin's vertical datum |
| 3 | `area.process_area` -> `voxelize.voxelize_laz_chunked` | each tile, in chunks |
| 4 | per-tile store -> `ColumnStore.merge` (`area.merge(tile, inplace=True)`, once per tile) | a hand-off, not a call: `process_area` receives the tile store from the chunked voxelizer and passes it to the merge. Both stores sit on the shared origin, and over clipped, grid-aligned tiles the merge is a concatenation plus one gather |
| 5 | `area.process_area` -> `preflight.check_rss_budget` | RSS budget, after each tile |
| 6 | `area_cli.run_area` -> `store_streaming.save_store_npz_atomic` | `area_raw.npz` before grouping, and only when grouping and store-saving are both on |
| 7 | `area_cli.run_area` -> `ColumnStore.grouped` | `--group-gap`, whose argparse default is `None` (no gap limit); 1.5 m is the value the recorded runs used |
| 8 | `ColumnStore.grouped` -> `area_outputs._write_outputs_isolated` (the default) or `area_outputs._write_outputs` under `--no-isolate-stages` | a hand-off again: `run_area` groups, then chooses the writer. Both branches leave the grouping box, because `--no-isolate-stages` changes the writer and not the grouping; the only branch that skips grouping is `--no-group-intervals`, which is drawn straight from `run_area` to the writer. The isolated writer spawns `stage_runner.main` children |

### Sharded path

| Step | Call | Data crossing the arrow |
|---|---|---|
| 1 | `area_cli.main` -> `sharding.run_area_sharded` | `--shard` |
| 2 | `sharding.run_area_sharded` -> `area.select_area_tiles`, then per tile `voxelize.voxelize_laz_chunked` in this process | the default path: no child process is involved |
| 3 | per-tile store -> `sharding._atomic_shard_save` | the third hand-off of the drawing, and the same shape as step 4 of the in-memory path: `run_area_sharded` receives the tile store and passes it to the writer, which stages `shards/<tile>.npz` through a temporary file plus `os.replace` |
| 4 | conditional, dashed: `--isolate-tiles` -> `sharding._voxelize_tile_in_child` -> `shard_worker.main` | one subprocess per tile; the child voxelizes and writes its own shard (its own temporary file plus `os.replace`, not `_atomic_shard_save`), and the parent reloads it through `sharding._try_load_shard` |
| 5 | `--resume-shards` reuses validated shards (`sharding._try_load_shard`) | existing `shards/*.npz` |
| 6 | `sharding.run_area_sharded` -> `shard_diagnostics.sweep_shards` and `write_column_diagnostics_from_shards`, when no merge is asked for | `columns/` and the exact `stats.txt`, folded over the shards. Both are fed by `shard_diagnostics.open_shard_set`, which is where `--group-gap` reaches this path: the sweep sees grouped shard slices |
| 7 | `--merge-shards` -> `sharding._merge_all_shards` -> `merge_streaming.merge_shards_streaming` | the shard files, and the grouping flags `_merge_all_shards` forwards |
| 8 | `merge_streaming.plan_bands` -> band by band, each band assembled with `ColumnStore.merge_many` | at most one band in RAM. Before each band is merged, every shard's slice of that band goes through `ColumnStore.grouped`, which is why the grouping box has an incoming arrow from the streaming merge as well as from `run_area` |
| 9 | merged store -> `store_raw/` | six `.npy` arrays plus `meta.json`. The directory is named `store_raw` but its contents are the grouped store unless `--no-group-intervals` was given: grouping happens inside the streaming merge, band slice by band slice, and the containerised gate run of the sharded command logs the merge as `grouped (gap=3 cells)` |
| 10 | back into `area_outputs._write_outputs_isolated` (`include_maps=False`) | the same stage machinery as the in-memory path |

A `--shard` run without `--merge-shards` is not output-less, and two of the
things it writes are written by every `--shard` run, merge or no merge:
`stats.txt` from the per-tile fold and the four `area_<mode>.png` mosaics
scattered from the raw per-tile summaries, both produced before the merge
branch is even reached. What is gated on the absence of a merge is `columns/`
from the shard sweep, which also rewrites `stats.txt` with the exact
column-height median, and, under `--viz3d`, the two box viewers written
straight from the shards: `area_full.html` through
`sharding.render_full_from_shards`, and `area_roi.html` through
`sharding.load_region_from_shards` followed by `area_outputs.render_area_3d`.
Both read the shards as written, that is the raw ungrouped intervals. With a
merge those two filenames are written instead by the merged store's own viewer
stages, from the grouped store. What a shard-only run cannot write is anything
that IS the merged store: `area.npz`, `area_raw.npz`, `store_raw/` and the
streaming viewer.

The in-memory merge and the sharded one are different functions.
`ColumnStore.merge` is the tile-by-tile in-memory merge inside
`area.process_area`; `ColumnStore.merge_many` is called only by
`merge_streaming.merge_shards_streaming`, once per key band, and stays the
primitive the band decomposition is tested against.

### What the output folder holds when the run is over

Deliverables: `area.npz`, `area_manifest.json`, `stats.txt`, `stages.json`, the
four `area_<mode>.png`, `columns/`, and `stages/`, which holds `params.json`
plus one `<stage>.result.json` and one `<stage>.faultlog` per stage. The parent
writes `stages.json` and `stages/params.json`; each child writes its own
`result.json` and its own fault log. In shard mode add `shards/<tile>.npz`,
`shards/manifest.json`, `shards/run_config.json` and, when a tile fails,
`shards/failed_tiles.json`.

Two of the drawn artifacts are intermediates and a clean run deletes them,
which is why they are drawn as dashed cylinders with a deletion arrow from
`area_cli._cleanup_intermediates`. Under the default `--intermediates auto`,
and only when `stages.json` reports every stage OK, the sweep removes
`store_raw/` unless `--keep-raw-store`, `area_raw.npz` unless
`--keep-area-raw`, and `templaz/`, which has no box. `--intermediates keep`
removes nothing, and `auto` after a failed stage keeps everything and prints
the exact `--resume-from-store` command. Deliverables and `stages/` are never
touched. The containerised gate run of the quickstart `area` command ends on
`intermediates: auto - reclaimed 0.09 GB` and leaves an output folder with no
`area_raw.npz` and no `store_raw/` in it.

`store_raw/` is written on every isolated-stage run that is handed a store:
`_write_outputs_isolated` calls `ColumnStore.save_dir` before any stage starts,
because the stage children attach to it by memory map. On the two paths that
pass `store=None`, `--resume-from-store` and the sharded merge, it is not
written there because it already exists, the merge having written it directly.
`--keep-raw-store` does not create it; it only stops the retention sweep from
deleting it.

Viewer artifacts differ by flag and the drawing now says so rather than merging
them. `--viz3d-stream` writes `area_stream.html`, plus `area_stream.bin` and
`area_stream.idx.json` unless the payload is small enough to be inlined in the
page, plus the `view_stream.cmd` launcher both writers leave beside it.
`--viz3d` writes `area_full.html` and `area_roi.html` instead. The two flags
are exclusive and the streaming one wins.

### Branches that are not drawn

The drawn Panel B is the default path plus the flags on the dashed edges. The
following flags change the path a run takes and are deliberately left undrawn,
because drawing them would double the panel for cases the recorded runs did not
use. This list is complete for path-changing flags.

`--no-chunks` replaces the chunked read with a whole-file `io_laz.read_laz`
plus `voxelize.voxelize` in all four tile loops (`area.process_area`,
`area.process_area_streamed`, `sharding.run_area_sharded` and
`shard_worker.main`), the same pair Panel A draws.
`--no-save-store` drops `area_raw.npz` and the `persist_npz` stage, so no
`area.npz` is written either.
`--columns-mode skip` drops the `col_diag` stage and, in shard mode, the whole
shard sweep.
`--no-full` and `--no-roi` each drop one of the two box viewers.
`--only-stage` restricts the stage list to the named stages.
`--delete-laz` and `--delete-shards` remove the sources and the shard directory
at the end of the run.
`--retry-lazrs` gives the isolated tile child a second decode attempt with a
different backend.
`--keep-area-raw` in shard mode is drawn as one dashed arrow into the saver,
and the pass it names is bigger than that arrow: `run_area_sharded` runs a
second, ungrouped `_merge_all_shards` into a scratch `store_raw_ungrouped/`
directory, compresses it to `area_raw.npz` and removes the scratch directory.

The remaining flags change a parameter rather than a path and have no place in
a call graph: `--cell-xy`, `--cell-z`, `--chunk-size`, `--no-clip`,
`--keep-classes`, `--height-mode`, the value of `--group-gap`, `--max-rss-mb`,
`--tile-pitch`, `--workers`, `--limit`, `--merge-band-intervals`,
`--stage-timeout`, `--columns-top-n`, `--columns-all-max`, `--max-boxes`,
`--roi-size`, `--roi-cx`, `--roi-cy`, `--tile-m`, `--max-instances` and
`--inline-threshold-mb`.

## The read side

`decoder` and `ground_index` are never on the construction path. They read a
finished store and give every empty voxel one of three meanings: measured air,
opaque interior, or subsurface. Everything physical sits above them and calls
down into them, which is the direction the panel draws:
`sun_hours.compute_sun_hours` (with `solar.day_arc` and `solar.sun_vector_grid`)
calls `ray_columns.CeilingDDA`, which subclasses `ray_trace.ColumnGridDDA`, and
`transmittance.transmittance`; both of those call `decoder.classify_voxel_at`,
which reaches `ground_index.compute_ground_indices` through the lazy
`ColumnStore.ground_idx` property. This layer is library only; it has no CLI
subcommand. It is driven from `Experiments/run_sun_hours.py`, which loads a
finished `area.npz` through `ColumnStore.load`, and re-proved by
`code_verification/exp05_ray_equivalence_real.py` and
`exp11_sun_hours_reproduction.py`.

The separation is the point of drawing it apart: `decoder` appears in neither
`single` nor the area build, and `merge_streaming` enters an area run only
under `--shard --merge-shards` (it also has a standalone `main`, run as
`python -m voxelizer.merge_streaming`). A reader who sees them in the module
table might assume they are build steps. They are not.
