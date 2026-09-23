# Voxel-Based Representation for Volumetric Modeling of Territories

## Context

The IA.RBRE project explores an innovative paradigm for territorial representation based on the concept of information stored at the pixel level.
This methodological framework enables each rasterized element (pixel) to be associated with rich information combining environmental, morphological, 
and artificial intelligence–derived data. The objective is to move beyond traditional GIS approaches by designing a homogeneous large-scale information 
model that supports territorial analysis and simulation.

However, the intrinsic limitation of the pixel paradigm lies in its two-dimensional nature. Territories, and particularly their vegetation and built
components, are organized in space through volumetric structures that can only be partially represented in 2D. Phenomena such as shading, solar exposure, 
light propagation, and radiation interception by vegetation require a discrete and homogeneous 3D model capable of linking each portion of space to physical and environmental attributes.

## Internship Objectives

The objective of this internship is to extend the IA.RBRE paradigm toward a volumetric representation by investigating a voxel-based data structure 
(volumetric pixels), in which information is attached not to an elementary surface but to an elementary volume.

The work will draw inspiration from world-representation models used in simulation and gaming environments (such as Minecraft), 
where territories are decomposed into grid-oriented volumetric blocks.

The main objectives are:

* Design a voxelized data model suitable for representing different types of spatial entities, including vegetation, soil, buildings, and subsurface structures.
* Construct this model from existing datasets (LiDAR, DSM, CityGML, etc.).
* Classify and categorize voxels using these data sources.
* Experiment with shadow-casting algorithms (or volumetric ray casting) that account for interactions between voxels representing different territorial elements.
* Compare the results with existing 2D and 2.5D approaches (digital surface models, solar radiation rasters, etc.) in order to assess improvements in accuracy, realism, and interpretability.

## The deliverable

Master's internship deliverables for the **IA.rbre** project (LIRIS, Lyon):
a Python system that turns the Grand Lyon airborne LiDAR corpus into a
run-length-compressed **voxel column store**, and answers volumetric
questions against it - statistics, 2-D maps, interactive 3-D viewers,
standards-compliant 3D Tiles, volumetric ray casting, class-aware light
transmittance, per-column direct sun hours, and a bit-exact `ColumnStore` <-> `.laz`
round trip that lets a sharded run be archived as ordinary LAZ - at
metropolis scale on one machine.

## Quickstart

Requires **Python 3.13 or newer** (3.11 is explicitly unsupported, it corrupts
its own heap under numpy load - see the problem log,
[`docs/problem-solving.md`](docs/problem-solving.md)).

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r voxelising-python/requirements.txt
cd voxelising-python
python -m voxelizer single your_tile.laz --output-dir outputs/test1
```

Then open `outputs/test1/`. Under it there is a directory named after your tile,
holding:

- `stats.txt` - point counts, column counts, interval counts, extents, per-class
  totals;
- four 2-D maps, one per rendering mode (`pipeline.MODES`):
  `<tile>_max_height.png`, `<tile>_max_points_class.png`,
  `<tile>_orthophoto_class.png`, `<tile>_n_intervals.png`;
- `columns/diagnostics/` - figures about interval structure, plus
  `columns/per_column/` when `--columns-mode top` or `all` is used.

No LiDAR tile at hand? Fetch one by bounding box (EPSG:3946 metres):

```
python -m voxelizer.download_laz --xmin-start 1831000 --xmin-end 1831500 \
    --ymin-start 5176500 --ymin-end 5177000 --laz-dir inputs/laz --limit 1
```

`--limit 1` stops after the first tile, which is all the single-tile command
above needs. Without it that origin window fetches four tiles, and those four
are exactly the ones covering the box of the area example below.

For a whole area rather than one tile:

```
python -m voxelizer.area_cli area --help
python -m voxelizer.area_cli area --xmin 1831000 --ymin 5176500 \
    --xmax 1832000 --ymax 5177500 --laz-dir inputs/laz \
    --output-dir outputs/test-area --cell-xy 0.5 --cell-z 0.5
```

That writes `area.npz` (the merged store), `area_manifest.json`, `stats.txt`,
four `area_*.png` maps and `columns/` under the output directory, plus
`stages.json` and the `stages/` directory, the per-stage record a run writes
because `--isolate-stages` is on by default. `stages/` holds `params.json` and,
for every stage that ran, a `<stage>.result.json` and a `<stage>.faultlog`. A
faultlog is opened before its stage starts and stays empty when the stage
succeeds, so an empty one is the normal case and one with content is the record
of a stage that died.

### The three copy-paste commands

For a run that stays out of RAM, use one of the two area commands below instead
of the in-memory area command above. Run all three from `voxelising-python/`,
after the setup at the top. The first is the single-file case: one tile, named
by its path rather than by coordinates, and it keeps that tile's shard. The two
area commands keep the per-tile `shards/<tile>.npz` and write the merged
`area.npz` with `area_manifest.json`, `stats.txt`, the four `area_*.png` maps,
`columns/`, `stages.json` and the streaming 3-D viewer; the third additionally
downloads the tiles it needs as part of the run, and keeps `store_raw/` and
`area_raw.npz`. Append `--preflight-only` to an area command first to size the
machine.

1. One tile, saved as a shard, plus the streaming viewer:

```
python -m voxelizer single path/to/tile.laz --output-dir outputs/test-single \
    --cell-xy 0.5 --cell-z 0.5 --shards --viz3d-stream --tile-m 64
```

`--shards` writes the tile's voxel store as `shards/<tile_stem>.npz` with a
`shards/manifest.json`, the same shard format the area commands use, so a
single-file run is readable by the shard tooling (`merge_streaming`,
`shard_diagnostics`, `archive_cli`) exactly as one tile of an area run is. One
run writes one shard, so give each tile its own `--output-dir` (the default
auto-numbering already does). `--viz3d-stream` writes `<tile_stem>_stream.html`
with a sidecar `.bin` + `.idx.json` once the payload exceeds
`--inline-threshold-mb` (default 64; pass `--inline-threshold-mb 0` to always
keep the `.bin`).

2. The same shard output for a whole area, as the merged store:

```
python -m voxelizer.area_cli area --xmin 1831000 --ymin 5176500 \
    --xmax 1832000 --ymax 5177500 --laz-dir inputs/laz \
    --output-dir outputs/test-area --cell-xy 0.5 --cell-z 0.5 --group-gap 1.5 \
    --shard --merge-shards --resume-shards --isolate-tiles \
    --viz3d-stream --tile-m 64
```

`--shard` voxelizes each tile into its own `shards/<tile>.npz`,
`--merge-shards` folds them into `area.npz`, and `--viz3d-stream` writes
`area_stream.html` with its sidecar `area_stream.bin` + `area_stream.idx.json`.
`--viz3d-stream` is refused without `--merge-shards`.

3. The same area run, downloading the tiles first:

```
python -m voxelizer.area_cli area --xmin 1831000 --ymin 5176500 \
    --xmax 1832000 --ymax 5177500 --laz-dir inputs/laz \
    --output-dir outputs/test-area --cell-xy 0.5 --cell-z 0.5 --group-gap 1.5 \
    --shard --merge-shards --resume-shards --isolate-tiles --retry-lazrs \
    --download --json inputs/quickhelpers/nuage-de-points-lidar-2023-de-la-metropole-de-lyon.json \
    --viz3d-stream --tile-m 64 --keep-raw-store --keep-area-raw
```

Same artifacts as command 2, plus the tiles fetched by `--download` and, with
the two keep flags, the `store_raw/` memory-mapped copy and the pre-grouping
`area_raw.npz` that `--intermediates auto` deletes after a fully successful run.

Everything else is optional. [`execution-steps.md`](voxelising-python/execution-steps.md)
lists every command-line entry point in the package and tags each one `[CORE]`,
`[OPTIONAL]` or `[ADVANCED / EDGE CASE]`; three of its 25 sections carry the
core tag: the single-tile pipeline command 1 drives, the area pipeline
commands 2 and 3 drive, and the tile downloaders of section 11.
Seventeen modules of the package carry a `__main__` guard; fifteen of them have
a section of their own, and the two left out are the internal child processes
`voxelizer.shard_worker` and `voxelizer.stage_runner`, which the area CLI spawns
and which nobody runs by hand. LAS/LAZ round trips, 3D Tiles export, the GUI
and the verification suite are all in the optional and advanced sections. The
full walkthrough is
[`voxelising-python/how-to-use.md`](voxelising-python/how-to-use.md).

## For the code review (start here)

- [`architecture.md`](architecture.md) (French): the package layout, execution flow,
  data model, and commands that reproduce the documented workflow.
- [`voxelising-python/docs/reference/12_workflow_schema.md`](voxelising-python/docs/reference/12_workflow_schema.md)
  and its SVG: the call sequence and data exchanged by the two main commands.
- Generated API documentation: build it from `voxelising-python/docs/` with
  `make_docs.cmd` on Windows or `make_docs.sh` on Linux, then open
  `voxelising-python/docs/doxygen/html/index.html`.
- Architecture diagrams are in `voxelising-python/docs/reference/`.
- [`voxelising-python/docs/reference/02_visualization_formats.md`](voxelising-python/docs/reference/02_visualization_formats.md)
  explains the retained viewer and export formats.

Every guide shipped in the delivery is indexed below; nothing is left
reachable only by browsing the tree.

## Reference guides

Top-level documents: this README,
[`architecture.md`](architecture.md) (French, the reviewer's map),
[`voxelising-python/execution-steps.md`](voxelising-python/execution-steps.md)
(flat CLI reference, 25 tagged sections) and
[`voxelising-python/how-to-use.md`](voxelising-python/how-to-use.md)
(narrative tutorial). The companion documents behind the report live under
[`docs/`](docs/) (table at the end of this README).

Topic guides and diagrams, all under `voxelising-python/docs/reference/`:

| Guide | What it covers |
|---|---|
| [`voxelizer-documentation.md`](voxelising-python/docs/reference/voxelizer-documentation.md) | The long-form reference: data model, algorithm, every stage, exports, serving |
| [`cli_and_bat_reference.md`](voxelising-python/docs/reference/cli_and_bat_reference.md) | Every CLI verb and generated `.cmd` launcher, tabulated |
| [`12_workflow_schema.md`](voxelising-python/docs/reference/12_workflow_schema.md) (+ `.svg`) | What calls what, in order, for the two core commands |
| [`02_visualization_formats.md`](voxelising-python/docs/reference/02_visualization_formats.md) | Why each kept viewer/export format stays |
| [`visualizer3d_workflow.md`](voxelising-python/docs/reference/visualizer3d_workflow.md) | The streaming Three.js viewer end to end |
| [`single_vs_all_workflows.md`](voxelising-python/docs/reference/single_vs_all_workflows.md) | Single tile vs area vs sharded area, when to use which |
| [`columns_gridding_voxels.md`](voxelising-python/docs/reference/columns_gridding_voxels.md) | Columns, the grid, and how voxels derive from both |
| [`points_vs_intervals.md`](voxelising-python/docs/reference/points_vs_intervals.md) | Points vs run-length intervals, and what each buys |
| [`pipeline_py_explanation.md`](voxelising-python/docs/reference/pipeline_py_explanation.md) | Line-by-line walkthrough of the single-tile pipeline |
| [`laz_roundtrip_design.md`](voxelising-python/docs/reference/laz_roundtrip_design.md) | The exact LAZ round trip and shard archiving design |
| [`las_1_2_vs_1_4_classification.md`](voxelising-python/docs/reference/las_1_2_vs_1_4_classification.md) | LAS 1.2 vs 1.4 classification semantics |
| [`lidar-classification.md`](voxelising-python/docs/reference/lidar-classification.md) | The ASPRS class table the corpus uses |
| numbered `01`-`10` `.svg` (+ `.mmd` sources) | Architecture, data, algorithm, export and class diagrams; `06*` are the module dependency graphs |

Pipeline diagrams:
[`voxelising-python/diagrams/pipeline_dataflow_diagram.md`](voxelising-python/diagrams/pipeline_dataflow_diagram.md)
(eight Mermaid diagrams; the
[standalone page](voxelising-python/diagrams/pipeline_dataflow_diagram.html)
renders them) and the plain-language
[`pipeline_overview_simple.md`](voxelising-python/diagrams/pipeline_overview_simple.md).

Launcher scripts: [`voxelising-python/scripts/README.md`](voxelising-python/scripts/README.md).
Docker workflow: [`voxelising-python/docker/`](voxelising-python/docker)
(`Dockerfile`, `docker-compose.yml`, entrypoint, download/run wrappers).

## What is in this repository

```
voxelising-python/             The deliverable code and Python package
  voxelizer/                   LAZ reading, voxelization, area processing,
                               sharding, diagnostics, ray tracing, solar
                               analysis, visualization, and 3D Tiles export
  docs/                        Doxygen configuration and reference material
    reference/                 Guides, workflow notes, diagrams, and the
                               LiDAR classification reference
  diagrams/                    Pipeline and data-flow diagrams
  scripts/                     Cross-platform launcher scripts
  inputs/                      Download inventories and empty input mounts
  outputs/                     Empty runtime output mount
  entire-lyon-outputs/         Empty whole-metropolis output mount
  docker/                       Containerized CPU and GUI workflow:
                               Dockerfile, docker-compose.yml,
                               docker-entrypoint.sh, and the
                               dockerdownload / dockerruncpu wrappers
  execution-steps.md           Command-line reference
  how-to-use.md                Narrative tutorial
  requirements.txt             Pinned Python dependencies
docs/                          Companion design, problem-solving, and source docs
```

The public branch does not include the full test and experiment workspace. The
files in this branch are the deliverable package, its reproducibility guides,
and the empty runtime mount points needed by the documented commands.
## Going further

Once the Quickstart above works, these are the next commands by task. Add
`--preflight-only` to an area command first to see predicted RAM, or `--shard`
for areas beyond RAM.

The GUI (both modes, pre-flight dialog, resume support): `scripts/run_area.bat`
or `python -m voxelizer.gui_area`.

Clean a finished store with the semantic passes, then feed the result to
any store-driven output:

```
python -m voxelizer.postprocess_cli area.npz area_clean.npz --min-points 4 --morph --absorb --resolve
python -m voxelizer.tileset_cli from-store area_clean.npz -o tiles/
```

View a result in a browser. Every export writes double-clickable `.cmd`
launchers beside its artifacts; this is the same servers by hand:

```
python -m voxelizer serve <run>/area_output           # streaming 3-D viewer (a run exported with --viz3d-stream)
python -m voxelizer serve tiles/ --open-viewer cesium # the 3D Tiles output (tileset_cli from-store)
```

No run output is tracked in this repository. To view something, produce a run
yourself, then export a viewer from it (`--viz3d-stream` on `area_cli`, or
`tileset_cli from-store` on the resulting `area.npz`).

The flat reference is
[`voxelising-python/execution-steps.md`](voxelising-python/execution-steps.md).

## The data

The corpus is the Grand Lyon 2023 airborne LiDAR: **2,842 LAZ tiles,
~51.9 billion points**, LAS 1.2 point format 1, ASPRS classes, EPSG:3946.
The tiles are open data and are not stored in this repository;
`python -m voxelizer.download_laz` fetches them by bounding box using the
inventory JSON in `voxelising-python/inputs/quickhelpers/`.

## The design in one paragraph

Each (x, y) cell of a horizontal grid holds an ordered list of
`(z_start, z_end, class, count)` intervals - a run-length encoding of the
column above and below it. Columns are independent of their neighbours,
so the store builds one tile at a time, shards across a metropolis, and
merges by concatenation; vertical questions (heights, gaps, ray walks)
are answered against the compressed form directly. A decoder gives every
empty voxel one of three meanings - measured air, opaque interior,
subsurface - so a ray knows whether it is crossing space the beam
demonstrably passed through or space no beam ever reached, and an exact
ceiling bound makes upward escape rays cheap enough for
metropolis-scale sun-hours maps. Subsurface is the one state that is
inferred rather than measured: an airborne laser records nothing below
the first opaque surface, so everything under the measured ground is a
deduction from the ground index.

## Companion documentation

The public branch keeps the supporting documents under `docs/`:

| Document | What it contains |
|---|---|
| [`problem-solving.md`](docs/problem-solving.md) | Problems encountered and the resolutions that remain relevant to the delivered package. |
| [`design-decisions.md`](docs/design-decisions.md) | The main design choices and their trade-offs. |
| [`bibliography-and-references.md`](docs/bibliography-and-references.md) | Sources used by the documentation and implementation. |

## Team

- Nikolaos Vynios (intern)
- John Samuel (supervision)
- Gilles Gesquière (supervision)
