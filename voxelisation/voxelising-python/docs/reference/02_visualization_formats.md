# The three 3-D export paths: what each one is for

The review flagged "3 separate implementations of view the voxel output in
3D": `visualizer3d.py`, `tiled_exporter.py`, `tileset_exporter.py`. They are
three output formats, not three copies of one program, and they now share the
code they used to duplicate. This document says which is which, what each
costs, and what the consolidation changed.

A fourth module, `viz_common.py`, was extracted in this pass. It holds only
what the three genuinely share.

## The three formats

| Module | Writes | Consumed by | Wired through |
|---|---|---|---|
| `visualizer3d.py` | one self-contained `.html` (Three.js `InstancedMesh`, voxel data base64-inlined), a binary `.ply`, a live PyVista window, a `.vxg` grid cache | a browser with no server; CloudCompare or any PLY reader | default `--viz3d` on `__main__` and `area_cli`; the `viz3d_roi` and `viz3d_full` stages via `area_outputs.render_area_3d`; `sharding.render_full_from_shards` |
| `tiled_exporter.py` | `.bin` payload (32-byte records, tile-contiguous) plus `.idx.json` and a streaming `.html` view-dependent viewer | the streaming viewer, and `tileset_exporter` | `--viz3d-stream`; the `viz3d_stream` stage |
| `tileset_exporter.py` | `tileset.json` and per-tile `.glb` using `EXT_mesh_gpu_instancing`, as a quadtree LOD pyramid | CesiumJS, iTowns, Cesium for Unreal | `python -m voxelizer.tileset_cli from-store` |

Each exists because the format above it cannot do its job: a base64 page
cannot hold a metropolis, a `.bin` payload is not a standard, and a 3D Tiles
tileset cannot be double-clicked open.

### Sizes and limits

| | `visualizer3d` | `tiled_exporter` | `tileset_exporter` |
|---|---|---|---|
| Output unit | one file per viewer | one payload + index | `tileset.json` + N `.glb` |
| Scaling bound | `--max-boxes` (hard cap 200 000 000), then thinning | band-at-a-time writer; peak RAM is one tile row, not the payload | streaming per level; peak memory follows tree depth, not area |
| Detail | capped, or strided when over budget | full, never thinned | full at the leaves, 2x-coarsened above |
| Network needed at view time | first load only (Three.js from a CDN) | yes: Range-request HTTP server | yes: a tileset host |
| Colour | `instanceColor` per instance | `rgb` in each 32-byte record | glTF materials, one per class |

## What was duplicated, and what the extraction did

Before this pass, one definition of each shared fact existed several times:

| Shared fact | Copies before | Copies now |
|---|---|---|
| 256x3 class colour table | 5 (`visualization._class_lut`, `visualizer3d._collect_voxel_geometry`, `_float_geometry_from_grid` and `_decode_grid_payload`, `tiled_exporter._class_lut`) | 1 (`viz_common.class_color_lut`) |
| Grey fallback for an unknown code | 3 literals (`visualization.plot_column`, `tileset_exporter._tile_glb` and `_coarsen_records`) | 1 constant (`viz_common.CLASS_LUT_UNKNOWN_RGB`) |
| 32-byte record dtype | 2 (`tiled_exporter._REC`, `tileset_exporter._REC_DTYPE`) | 1 (`viz_common.REC_DTYPE`) |
| Unit-box geometry | 3 (inline in `visualizer3d.show_pyvista` and `visualizer3d.export_ply`, `tileset_exporter._make_unit_box`) | 2 builders, both in `viz_common`: `unit_box_corners`/`unit_box_tris` for the double-sided meshes, `unit_box_gltf` for the culling-safe glTF mesh |
| Embedded Three.js page | 2 templates, 712 and 791 lines | 2 templates, unchanged (see below) |

The unit box deliberately stays two builders, not one. The in-process meshes
wound clockwise-inward and are rendered double-sided; the glTF mesh winds
counter-clockwise-outward because back-face culling under `doubleSided: false`
discards a wrongly wound quad. One shared winding would break one of them, so
`viz_common` keeps both and documents why.

### What deliberately did not change

The two Three.js templates are still two. They render different things: the
`visualizer3d` page fetches one base64 blob, while the `tiled_exporter` page
manages tile residency, Range fetches, a camera probe and a keep-set. Merging
them into one parameterised template was considered and rejected. Measured on
the two templates (712 and 791 lines), the distinct-line Jaccard similarity
over their non-blank, whitespace-stripped lines is 0.063, and the longest run
of consecutive matching lines is 34 under that same convention, 35 when blank
lines are kept. The shared part is the scene setup and the search highlighter,
and the differing part is the whole streaming residency manager.
The conclusion (keep both) holds on those figures. A single template
with two modes would be harder to read than the two pages it replaced, and the
two pages are pinned by `tests/test_viewer_templates.py`, which parses both
templates as JavaScript and adds five tests, one per defect that reached a
generated viewer.

## Verified equivalents

The extraction was proved equivalent, not assumed:

- `class_color_lut()` reproduces every `CLASS_COLORS` entry and the grey
  fallback at 128 for unknown codes.
- `REC_DTYPE` is 32 bytes with fields `('xyz','siz','rgb','cls')`, identical
  to both former definitions; the `assert _REC_BYTES == 32` pin is kept in
  both exporters.
- `unit_box_gltf()` returns positions, normals and flat indices identical
  (shape, dtype and every element) to the former `_make_unit_box()`.
- `unit_box_corners()` and `unit_box_tris()` are identical to the two inline
  arrays in `visualizer3d`.
- The shipped guards are the module-level `assert _REC_BYTES == 32` in both
  exporters and the tests that decode payloads through the shared dtype:
  `tests/test_tileset_streaming.py` reads every tile back at the
  `_REC_BYTES` stride with `_REC_DTYPE`, `tests/test_tileset_lod.py` builds
  its records from `_REC_DTYPE`, and `tests/test_tileset_glb_geometry.py`
  checks the unit-box shapes and winding. The diagram generator
  `docs/reference/gen_export_paths.py` also reads these values out of the source
  and asserts them before drawing, but it is a maintainer tool kept out of
  publication by `voxelising-python/.gitignore` (with `gen_module_tiers.py`
  and `gen_module_diagram.py`), so it is not a guard a reviewer can run.
- An end-to-end smoke run (16 columns, two classes) wrote a 1 024-byte payload
  (32 records x 32 bytes), a `tileset.json` with five `.glb`, a 32 KB HTML page
  and a 9 KB PLY, all through the shared code.

## Dispositions considered

The review's phrasing left the action open. The four options, with costs:

| Option | What changes | Gained | Lost | Verdict |
|---|---|---|---|---|
| A. Extract a shared core | `viz_common.py`; three call sites rewired | 6 LUTs -> 1, 2 dtypes -> 1, 3 unit boxes -> 2 named builders | nothing user-visible | **done** |
| B. Drop the PyVista arm | remove `show_pyvista`, `export_grid`, the `.vxg` cache, the `pyvista` requirement line | one fewer heavy optional dependency | the REPL-inspection path and the grid cache | open; small win, no CLI path lost |
| C. Remove `visualizer3d` | route `--viz3d` to the streaming path | one viewer fewer | 17 `__init__` exports, the two `viz3d_roi`/`viz3d_full` stages, four tests, five stress scripts, the no-server single-file viewer, four diagrams | rejected: removes delivered features |
| D. Document only | nothing in code | zero risk | the duplication stands | superseded by A |

Option A is what this pass did. Option B remains available if the `pyvista`
dependency is ever unwanted; it touches only `visualizer3d.py`, the
`requirements.txt` line and `tests/test_grid_codec.py`.

## Two other things the review named, and why they stay

The review also listed the graphical interface and the overlapping test trees
as cleanup targets. Neither is a viewer, so neither belongs above, but the
reasons are recorded here so the decision is not mistaken for an oversight.

**The graphical interface** (`gui_area.py`, 2 570 lines) wraps the two
commands the Quickstart documents. It is not a second implementation of
anything: it builds the same argument lists and runs them as a child process
(`python -m voxelizer.area_cli area ...`), so the pipeline it drives is the
one command line drives. It is launched by `scripts/run_area.bat`, `scripts/run_area.sh`
and the Docker GUI service, it is documented in `how-to-use.md` and the report,
and 40 tests (`tests/test_gui_area.py`) cover its pure helpers and its
command construction. It
is the entry point a non-programmer on the team uses. Two unreachable helpers
inside it were removed; the interface itself is a delivered feature, and
removing it would delete a working surface rather than simplify the code.

**The six test and experiment trees** - kept, like the whole test
workspace, in the private VCity monorepo (`Projects/IArbre/Stage-voxelisation`),
not in this delivery - do different jobs and are kept apart on
purpose:

| Tree | What it holds | Why it is separate |
|---|---|---|
| `tests/` | the unit, regression and CLI-contract suite, run by `pytest` | the only tree that must stay green on every change |
| `code_verification/` | eleven experiments that re-prove the report's numbers against the real corpus | re-verification on production data, not a second unit suite |
| `tests/stress_test_scripts/` | bounded-memory harnesses for scale scenarios | they cap RSS and run for minutes, so they are not pytest cases |
| `tests/diagtests/` | the stage-isolation debug helper, the frozen corpus-statistics protocol (`genstats/`) and the inventory JSONs it reads | one-off diagnostic helpers plus a frozen protocol whose committed outputs the report cites; neither is a pytest case |
| `Experiments/` | the study protocols and their recorded artifacts - metrics, logs and summary tables for the encoder, extinction, sun-hours, ray-cost, sweep and stratified studies | each script freezes one study's tiles, seeds and settings, so a published figure keeps a fixed referent; its own README argues this per script |
| `tests/T160/` | the closed investigation of one corrupted LAZ tile: the three-decoder comparison, the hashes and the remediation checks | a historical incident record kept as evidence, with nothing in it to run |

The overlap between `tests/` and `code_verification/` is deliberate: the same
invariant is checked cheaply on synthetic data and again on real stores. What
the review called duplication is that second check.

**The downloaders.** Four fetch loops exist. `voxelizer/download_laz.py` and
`voxelizer/download_orthos.py` share the per-file fetch,
`_download_common.download_one`, one URL to one path with its timeout and
retries, along with the inventory resolver and reader, the bounding-box
validation, the tile selection and the default worker count. The multi-tile
orchestration around it is duplicated rather than shared: the two orchestration
functions are a name-swapped twin, 53 of their lines byte-identical, with one
identical run of 35 lines covering the dry-run listing, the job list, the
thread pool and the summary. Both module docstrings state this and ask that a
change to one be checked against the other.
`tests/diagtests/genstats/download_laz.py` and `download_orthos.py` are the
fetch step of the frozen corpus-statistics protocol whose committed
`per_file_stats.csv` and `LAZ_STATS.md` are cited by `Experiments/pick_areas.py`,
`Experiments/run_stratified_n10.py` and
`code_verification/exp01_laz_ground_truth.py`; they keep their own loop so the
recorded outputs stay reproducible from the exact scripts that produced them.
`tests/verify_laz.py` keeps its own small loop so that a verification of the
corpus never depends on the code under test. Each of the three carries this
reason in its header docstring; none of them imports `_download_common` by
decision.

