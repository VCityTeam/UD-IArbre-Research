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

## Team 
- Nikolaos Vynios
- John Samuel
- Gilles Gesquière

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
its own heap under numpy load - see the problem log).

```
pip install -r VoxelisingPython/requirements.txt
cd VoxelisingPython
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

Everything else is optional. [`ExecutionSteps.md`](VoxelisingPython/ExecutionSteps.md)
lists every command-line entry point in the package and tags each one `[CORE]`,
`[OPTIONAL]` or `[ADVANCED / EDGE CASE]`; three of its 25 sections carry the
core tag, the two commands above and the tile downloaders of section 11.
Seventeen modules of the package carry a `__main__` guard; fifteen of them have
a section of their own, and the two left out are the internal child processes
`voxelizer.shard_worker` and `voxelizer.stage_runner`, which the area CLI spawns
and which nobody runs by hand. Sharded runs for areas beyond RAM,
LAS/LAZ round trips, 3D Tiles export, the streaming viewer, the GUI and the
verification suite are all in the optional and advanced sections. The full
walkthrough is
[`VoxelisingPython/HowToUse.md`](VoxelisingPython/HowToUse.md).

## For the code review (start here)

- [`ARCHITECTURE.md`](ARCHITECTURE.md) (French): file and module organisation in five
  tiers, the classes and their interactions, the execution flow from the LAZ tiles
  to the outputs, and the functions and exact commands needed to rerun the pipeline,
  the experiments and the tests.
- [`VoxelisingPython/ReadMEs/12_workflow_schema.md`](VoxelisingPython/ReadMEs/12_workflow_schema.md)
  (with the rendered `12_workflow_schema.svg`): what calls what, in order, with what
  data, for the two commands that matter.
- Generated API documentation (Doxygen, with call graphs and linked sources):
  the site is generated locally, it is not versioned. From `VoxelisingPython/docs/`,
  run `make_docs.cmd` (Windows) or `make_docs.sh` (Linux), then open
  `docs/doxygen/html/index.html`.
- Diagrams: `VoxelisingPython/ReadMEs/01_*.svg` to `10_*.svg` and
  `12_workflow_schema.svg`, in particular `10_classes_fr.svg` and the
  workflow schema.
- Tests, recorded experiments and the re-verification suite are not in this
  directory: `VoxelisingPython/tests/`, `VoxelisingPython/Experiments/` and
  `VoxelisingPython/code_verification/` are kept in the VCity monorepo
  (`Projects/IArbre/Stage-voxelisation`), where the suite gives 671 passed and
  2 skipped.
- [`VoxelisingPython/ReadMEs/02_visualization_formats.md`](VoxelisingPython/ReadMEs/02_visualization_formats.md):
  why the kept duplications stay (three 3-D exporters, two Three.js templates,
  the GUI, the validation trees, the downloaders).

## What is in this repository

```
VoxelisingPython/            The deliverable code
  voxelizer/                 The Python package (49 modules): LAZ reading,
                             voxelization, area pipeline, sharding,
                             out-of-core shard merge and from-shard
                             diagnostics, stage isolation, decoder, ground
                             index, denoising, ray tracing, solar geometry,
                             sun hours, 2.5-D surface comparison, 2-D/3-D
                             rendering, streaming viewer and its HTTP
                             server, 3D Tiles export and its server, exact
                             LAZ round trip and shard archiving,
                             post-processing CLI, GUI
  scripts/                   Launcher scripts (serve_run, serve_tiles for
                             CesiumJS/iTowns, run_area) for the common flows
  ReadMEs/                   Reader-facing documentation: the voxelizer guide,
                             workflow notes, and the numbered architecture SVGs
  HowToUse.md                Narrative tutorial (GUI and CLI, with examples)
  ExecutionSteps.md          Flat CLI reference covering every entry point,
                             each section tagged CORE / OPTIONAL / ADVANCED
  notes.md                   Scratch notes kept with the code
  requirements.txt           Pinned dependencies (Python 3.13+)
  inputs/                    quickhelpers/ holds the two Grand Lyon inventory
                             JSONs the downloaders read; laz/ is where tiles
                             land (empty here - the corpus is not tracked)
  Dockerfile, docker-compose.yml, docker/, dockerruncpu.cmd, dockerdownload.cmd
                             Containerised CPU pipeline (8 GB memory cap);
                             dockerdownload.cmd fetches tiles into inputs/laz
  run_area.bat               Double-click launcher for the GUI (Windows)
  run_viewer.bat / .sh       Serve and open the streaming 3-D viewer. A
                             finished run also carries its own generated
                             view_*.cmd / launch_unreal.cmd launchers
  Diagrams/                  Pipeline / dataflow diagrams, plus a plain-language
                             overview (pipeline_overview_simple.md)
  outputs/                   Where local runs land (empty here - run outputs are
                             not tracked; the Quickstart regenerates one)
  EntireLyonOutputs/         Where whole-metropolis run outputs persist on the
                             host - docker-compose bind-mounts this directory
                             into the container (empty here - runs are not
                             tracked)
tilesexport/                 The 3D Tiles delivery end of the pipeline
  AssistingRuns/             Local visualisation kit: HTTP server with the
                             bundled CesiumJS viewer page, plus docs 00-04
                             (export internals, serving, troubleshooting,
                             migration to other Cesium clients)
  UEViz/                     Unreal Engine 5.8 visualisation: pipeline and
                             experiment docs, editor-driving tools, and the
                             IarbreVoxels project source (the Cesium for
                             Unreal plugin and the exported tilesets are
                             not carried - see the READMEs there)
Presentations/
  Pres1/                     Mid-term presentation, English (PDF)
  Soutenance/                The defence material, six files:
    Nikolaos_internship-report_EN.pdf
    Nikolaos_internship-report_FR.pdf
                             The internship report, English and French editions
    ProblemSolving.md        Every problem hit and how it was solved
    DESIGN_DECISIONS.md      Why each choice was made and what it costs
    BIBLIOGRAPHY_AND_REFERENCES.md
                             The source of record for every citation. These
                             three are the companion documents the report
                             builds on (see below)
    soutenance_FR_Vynios.pdf The defence slides, French (59 pages)
notes.md                     Top-level scratch notes
```

## Going further

Once the Quickstart above works, these are the next commands by task. Add
`--preflight-only` to an area command first to see predicted RAM, or `--shard`
for areas beyond RAM.

The GUI (both modes, pre-flight dialog, resume support): `run_area.bat`
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
[`VoxelisingPython/ExecutionSteps.md`](VoxelisingPython/ExecutionSteps.md).

## The data

The corpus is the Grand Lyon 2023 airborne LiDAR: **2,842 LAZ tiles,
~51.9 billion points**, LAS 1.2 point format 1, ASPRS classes, EPSG:3946.
The tiles are open data and are not stored in this repository;
`python -m voxelizer.download_laz` fetches them by bounding box using the
inventory JSON in `VoxelisingPython/inputs/quickhelpers/`.

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

## The report and its companion documents

The internship report lives in `Presentations/Soutenance/` (PDF).
It builds on three standalone companion documents in the same directory,
which are the record of the work behind it:

| Document | What it holds |
|---|---|
| `ProblemSolving.md` | Every significant problem hit during the internship and how it was solved - data traps, scale walls, crash forensics, environment traps, and the correctness sweeps |
| `DESIGN_DECISIONS.md` | The why behind every debatable design choice: alternatives considered, what was picked, and what each choice costs |
| `BIBLIOGRAPHY_AND_REFERENCES.md` | The source of record for every reference: all sources consulted, the cited subset, and where each is used in the report |

## Team

- Nikolaos Vynios (intern)
- John Samuel (supervision)
- Gilles Gesquière (supervision)
