"""
A Python voxelizer for the IA.rbre project.
@ingroup t0_socle

This file is the package facade. It belongs to no import level of its own,
because it re-exports all of them; it is filed under the first topic so that
the generated site gives the package overview a page.

Inspired by LiVoxGen (Megan Kress, 2015), rewritten from scratch
for the project's needs:
   - Native LAZ support (laspy + C++ LASzip via the laszip pip package), not the abandoned liblas
   - Uses IGN LiDAR HD semantic classes already present in the .laz files
   - No kdtree: direct integer-index voxelization via numpy (much faster)
   - Output is a column store: per (x,y) column, a list of (z, class) intervals
   - Pure Python, no C++ build step

Modules (listed in dependency order, foundation first)
------------------------------------------------------
# Level 0 - no internal imports at module import time (preflight and
# stage_runner defer higher-level imports into function bodies)
classes_config.py      ASPRS / IGN LiDAR class codes + display colors
_download_common.py    shared download infrastructure (download_one, select_tiles, ...)
cli_common.py          shared argparse flag clusters (cell / viz3d geom)
io_laz.py              LAZ/LAS reader (sole laspy READ path; reconstruct.py
                       writes LAS/LAZ through laspy directly)
preflight.py           pre-flight cost estimation and RAM guarding
run_utils.py           auto-incrementing run directory utilities
solar.py               NOAA/Meeus solar position + projected-grid sun vectors
stage_runner.py        per-stage child process isolation for area runs
voxel_runner_diagnos.py  standalone run-time diagnostics (CPU/RAM/disk)

# Level 1 - import only Level 0 at import time (data_structures'
# ground_idx property lazily imports ground_index, a Level 2 module)
data_structures.py     Interval + Column dataclasses; ColumnStore (flat arrays)
viz_common.py          geometry/colour/record helpers shared by two of the three 3-D
                       exporters (unit box, class LUT, 32-byte record layout)
tileset_exporter.py    3D Tiles exporter (.glb + tileset.json)
download_laz.py        Grand Lyon LiDAR tile downloader
download_orthos.py     Grand Lyon orthophoto tile downloader

# Level 2 - depend on Levels 0-1
store_streaming.py     out-of-core reductions over one (mapped) ColumnStore,
                       written as folds so they also compose over a shard set
merge_streaming.py     out-of-core shard merge: the merged store assembled on
                       disk, one key band at a time
voxelize.py            numpy voxelization + run-length compression
ground_index.py        ground-index computation (NODATA, hole filling, ...)
visualization.py       optional matplotlib helpers (top-down tile maps)
visualizer3d.py        3-D voxel grid visualizers (HTML/PyVista/PLY)
tiled_exporter.py      tiled streaming exporter + view-dependent Three.js viewer
absorb.py              interior absorption filtering
reconstruct.py         store <-> LAS/LAZ reconstruction (exact round trip)
resolve.py             overlap resolution (cross-class interval tie-breaking)

# Level 3 - depend on Levels 0-2
decoder.py             voxel classification (measured-air, opaque-interior, subsurface)
pipeline.py            the per-tile ("single") run + shared stats helpers
area.py                coordinate-driven whole-area voxelization
column_diagnostics.py  per-column visual diagnostics
shard_diagnostics.py   the same diagnostics and statistics computed from a
                       SHARD SET, with no merged store (matplotlib and the
                       figure writers are imported inside its functions)
sharding.py            out-of-core area pipeline (shard-beyond-RAM)
viz3d_cli.py           CLI entry point for 3-D HTML visualization

# Level 4 - the analysis tier. Grouped by role, not by uniform depth: these
# are what the ray-casting objective is built from, and they form a chain
# among themselves rather than a flat layer.
denoise.py             noise filtering (min-points, morphological). Imports
                       ONLY data_structures - shallower than the rest of this
                       group, and listed here because it is a post-processing
                       step rather than because anything below needs it.
ray_trace.py           DDA voxel ray tracing (data_structures + decoder)
ray_columns.py         ceiling-terminated ray walker (CeilingDDA); subclasses
                       ray_trace.ColumnGridDDA
transmittance.py       class-aware Beer-Lambert transmittance along rays
                       (classes_config + data_structures + decoder)
sun_hours.py           direct-sun-hours maps (opaque vs class-aware); the top
                       of the chain - ray_columns + transmittance + solar +
                       data_structures
surface_model.py       the 2.5-D surface and raster arms of the objective (v)
                       comparison. Imports only classes_config and
                       data_structures - listed here by role, like denoise.

# CLI / GUI - top-level consumers
area_cli.py            CLI for the area voxelizer (re-exports render_area_3d)
shard_worker.py        per-TILE child process isolation for sharded runs
                       (voxelizes one tile to an atomically-written shard
                       .npz; exit-code contract in its own docstring)
gui_area.py            Tkinter dialog for single-file and area modes
__main__.py            ``python -m voxelizer single|serve ...`` entry point
tileset_cli.py         3-D Tiles export CLI (``from-store`` -> tileset.json + .glb)
postprocess_cli.py     store-to-store post-processing CLI (denoise / absorb /
                       resolve / group; feeds tileset_cli and every other
                       store-driven output)
serve_voxel_html.py    static-file server for streaming voxel viewers (HTTP Range)
serve_tiles.py         static-file server for 3-D Tiles output (glTF types,
                       bundled Cesium/iTowns pages)
archive_cli.py         exact shards/*.npz <-> shards_laz/*.laz archive
                       (``pack`` / ``unpack``; raw shards only - see its
                       docstring for why area.npz is refused)
launchers.py           generated ``view_*.cmd`` / ``launch_unreal.cmd`` files
                       written beside a run's viewers. Imports nothing from
                       the package (it emits text), so it sits at Level 0 by
                       dependency and is listed here by role: its importers
                       are stage_runner, tileset_exporter and area_outputs,
                       each inside a function body.

Not a member of any tier, a dependency of the CLI one:

area_outputs.py        shared output writers + per-stage isolation, imported
                       by BOTH area_cli and sharding, and by stage_runner.
                       It imports NEITHER of those two top-level consumers,
                       which is what keeps the layering acyclic - they would
                       otherwise have to import each other. It does reach
                       DOWN: data_structures at module level, and
                       column_diagnostics, launchers, pipeline,
                       store_streaming, tiled_exporter, visualization and
                       visualizer3d inside function bodies.

The most common entry points are re-exported here so callers can simply
``from voxelizer import voxelize_laz, ...``.

Every module that is itself a ``python -m voxelizer.<module>`` entry point is
kept OUT of that eager set: area_cli, archive_cli, download_laz,
download_orthos, reconstruct, stage_runner, tileset_cli and viz3d_cli are
imported on first attribute access by the package ``__getattr__`` below.
Importing the package therefore leaves them absent from sys.modules, which is
what stops runpy warning when one of them is run with ``python -m``. The names
they provide (STAGES, store_to_laz, pack_shards, render_area_3d, ...) stay in
``__all__`` and resolve exactly as before, on first use.

Author: Nikolaos Vynios, VCity LIRIS, 2026.
"""

from __future__ import annotations

import importlib

# -- Level 0: foundation modules --------------------------------------------
from .classes_config import CLASS_COLORS, CLASS_NAMES
from .io_laz import read_laz, read_laz_chunks, read_laz_header
from .preflight import (RamBudgetExceeded, estimate_area_run,
                        estimate_store_bytes, suggest_cell_xy)
from .run_utils import next_run_output_dir

# -- Level 1: data layer ----------------------------------------------------
from .data_structures import Column, ColumnStore, Interval
from .tileset_exporter import convert_to_3d_tiles

# -- Level 2: core algorithms -----------------------------------------------
from .voxelize import voxelize, voxelize_laz, voxelize_laz_chunked
from .ground_index import compute_ground_indices, fill_ground_holes, NODATA
from .visualization import plot_column, plot_tile_map, render_tile_map
from .tiled_exporter import export_tiled_from_store
from .absorb import absorb_interior
# reconstruct is NOT imported here: it carries a ``python -m`` entry point of
# its own, and importing it at package import time gave that entry point the
# runpy warning. Its eight public names (EXPORT_MODES, store_from_laz,
# store_to_las, store_to_laz, store_to_points, stores_equal, verify_exact,
# verify_roundtrip) are resolved on first use by __getattr__ below.
from .resolve import resolve

# -- Level 3: pipeline & area -----------------------------------------------
from .decoder import classify_gap, classify_voxel, MEASURED_AIR, OPAQUE_INTERIOR, SUBSURFACE
from .pipeline import MODES, process_single_tile
from .area import process_area, process_area_streamed, select_area_tiles
from .column_diagnostics import write_column_diagnostics
from .sharding import run_area_sharded
# stage_runner is a Level 0 module - it imports nothing from the package at
# module import time, deferring every stage's imports into the stage function
# - and STAGES is named down here only because it is the Level 3 output
# tier's table of contents. The listing above places the module by dependency;
# this note places the name by what it names. The module itself is NOT
# imported here: ``python -m voxelizer.stage_runner`` is what area_outputs
# launches for every stage, so STAGES is resolved on first use by __getattr__
# below.

# -- Level 4: advanced processing -------------------------------------------
from .denoise import min_points_filter, morphological_filter
from .ray_trace import ColumnGridDDA, DDAConfig, RayHit

# -- Downloads ----------------------------------------------------------------
# Neither downloader is imported here: `python -m voxelizer.download_laz` and
# `python -m voxelizer.download_orthos` are both entry points, and importing
# the submodules at package import time made runpy warn about them. Both names
# are resolved by __getattr__ below, and they resolve to different things,
# which is what the two modules already meant: `download_laz` is the MODULE
# (its function is `download_laz.download_laz`), while `download_orthos` is the
# FUNCTION, as the eager `from .download_orthos import download_orthos` that
# used to sit here bound it.

"""
visualizer3d imports no heavy optional dependency at module load time -
pyvista is imported inside show_pyvista() itself, which raises its own
clear ImportError. The guard below is defensive: if visualizer3d ever
fails to import, the rest of the package still works and the 3-D entry
points become stubs that explain what is missing.
"""
def _missing_pyvista(*_a, **_kw):
    """Stub bound to the 3-D entry points when ``visualizer3d`` fails to import; accepts any arguments and raises ``ImportError`` naming pyvista as the missing dependency."""
    raise ImportError(
        "voxelizer.visualizer3d requires pyvista. "
        "Install it with:  pip install pyvista"
    )

try:
    from .visualizer3d import export_html, export_ply, show_pyvista
except ImportError:  # visualizer3d import failed -> stub with a clear error
    export_html = export_ply = show_pyvista = _missing_pyvista  # type: ignore[assignment]

try:
    from .visualizer3d import export_grid, load_grid
except ImportError:
    export_grid = load_grid = _missing_pyvista  # type: ignore[assignment]

# -- CLI / GUI ----------------------------------------------------------------
# Nothing from this tier is imported eagerly. area_cli, serve_voxel_html,
# serve_tiles and launchers are named in __all__ without being imported - see
# the note beside them there - and viz3d_cli, tileset_cli and archive_cli are
# entry points too, so the names they used to contribute (_run_single,
# _run_streaming, _run_from_store from viz3d_cli, export_tileset_from_store
# from tileset_cli, pack_shards and unpack_shards from archive_cli) are
# resolved on first use by __getattr__ below. render_area_3d, which used to be
# imported from area_cli here, is resolved the same way, so
# `voxelizer.render_area_3d` still works without `import voxelizer` pulling
# area_cli into sys.modules.


# Where the names that are NOT bound above come from: public name ->
# (submodule, attribute). An attribute of None means the name IS the module.
# Every entry is a name of an entry-point module, or a name one of those
# modules provides; keeping them out of the eager imports is what keeps
# `python -m voxelizer.<module>` free of the runpy sys.modules warning.
# The three private viz3d_cli helpers are listed because they were package
# attributes before viz3d_cli became lazy and callers may still read them.
_LAZY_NAMES = {
    "render_area_3d": ("area_cli", "render_area_3d"),
    "area_cli": ("area_cli", None),
    "STAGES": ("stage_runner", "STAGES"),
    "EXPORT_MODES": ("reconstruct", "EXPORT_MODES"),
    "store_from_laz": ("reconstruct", "store_from_laz"),
    "store_to_las": ("reconstruct", "store_to_las"),
    "store_to_laz": ("reconstruct", "store_to_laz"),
    "store_to_points": ("reconstruct", "store_to_points"),
    "stores_equal": ("reconstruct", "stores_equal"),
    "verify_exact": ("reconstruct", "verify_exact"),
    "verify_roundtrip": ("reconstruct", "verify_roundtrip"),
    "_run_single": ("viz3d_cli", "_run_single"),
    "_run_streaming": ("viz3d_cli", "_run_streaming"),
    "_run_from_store": ("viz3d_cli", "_run_from_store"),
    "export_tileset_from_store": ("tileset_cli", "_run_from_store"),
    "pack_shards": ("archive_cli", "pack"),
    "unpack_shards": ("archive_cli", "unpack"),
    "download_laz": ("download_laz", None),
    "download_orthos": ("download_orthos", "download_orthos"),
    "serve_voxel_html": ("serve_voxel_html", None),
    "serve_tiles": ("serve_tiles", None),
    "launchers": ("launchers", None),
}


def __getattr__(name: str):
    """Lazy package attribute: resolve ``name`` through ``importlib.import_module`` on first access, using the ``_LAZY_NAMES`` table above, or - for any other name of ``__all__`` - as the submodule of that name; the resolved object is cached in the package namespace, and an unknown name raises ``AttributeError``."""
    try:
        module_name, attribute = _LAZY_NAMES[name]
    except KeyError:
        if name not in __all__:
            raise AttributeError(
                f"module {__name__!r} has no attribute {name!r}") from None
        module_name, attribute = name, None
    module = importlib.import_module(f".{module_name}", __name__)
    value = module if attribute is None else getattr(module, attribute)
    globals()[name] = value
    return value

__all__ = [
    # Level 0: foundation
    "CLASS_NAMES",
    "CLASS_COLORS",
    "read_laz",
    "read_laz_chunks",
    "read_laz_header",
    "RamBudgetExceeded",
    "estimate_area_run",
    "estimate_store_bytes",
    "suggest_cell_xy",
    "next_run_output_dir",
    # Level 1: data layer
    "Interval",
    "Column",
    "ColumnStore",
    "convert_to_3d_tiles",
    # Level 2: core algorithms
    "voxelize",
    "voxelize_laz",
    "voxelize_laz_chunked",
    "compute_ground_indices",
    "fill_ground_holes",
    "NODATA",
    "plot_column",
    "plot_tile_map",
    "render_tile_map",
    "export_tiled_from_store",
    "absorb_interior",
    "EXPORT_MODES",
    "store_from_laz",
    "store_to_las",
    "store_to_laz",
    "store_to_points",
    "stores_equal",
    "verify_exact",
    "verify_roundtrip",
    "resolve",
    # Level 3: pipeline & area
    "classify_gap",
    "classify_voxel",
    "MEASURED_AIR",
    "OPAQUE_INTERIOR",
    "SUBSURFACE",
    "process_single_tile",
    "MODES",
    "process_area",
    "process_area_streamed",
    "select_area_tiles",
    "write_column_diagnostics",
    "STAGES",
    "run_area_sharded",
    # Level 4: advanced
    "min_points_filter",
    "morphological_filter",
    "ColumnGridDDA",
    "DDAConfig",
    "RayHit",
    # Downloads
    # download_laz is the MODULE and download_orthos the FUNCTION (see the
    # note beside the Downloads imports above). Both are resolved by the
    # package __getattr__, which is also what binds them under
    # `from voxelizer import *`.
    "download_laz",
    "download_orthos",
    # 3-D visualization (optional, pyvista)
    "export_html",
    "export_ply",
    "show_pyvista",
    "export_grid",
    "load_grid",
    # CLI / GUI
    # The function, resolved lazily by the package __getattr__ above.
    "render_area_3d",
    # The MODULE, like the three below it: `python -m voxelizer.area_cli` is
    # what every area run and the GUI's subprocess execute, and importing it
    # at package import time gave each of them the same runpy warning.
    "area_cli",
    # The MODULE, not a function. This used to be `from .serve_voxel_html
    # import main as serve_voxel_html`, which bound the package attribute to
    # that module's main() and shadowed the module itself: `from voxelizer
    # import serve_voxel_html` handed back a zero-argument function, and
    # because importing the package pulled the submodule into sys.modules,
    # every `python -m voxelizer.serve_voxel_html` - which is what the
    # generated view_stream.cmd launchers run - opened with a runpy
    # RuntimeWarning about the module being found in sys.modules before its
    # own execution. Naming it here without importing it above keeps
    # `from voxelizer import serve_voxel_html` (and `import *`) working while
    # both problems go away; use `serve_voxel_html.main()` for the entry
    # point. The package __getattr__ now imports it on first access, so
    # `voxelizer.serve_voxel_html` resolves as well.
    "serve_voxel_html",
    # The same treatment, and for the same reason, for the other two modules
    # that are entry points rather than collections of functions:
    # `serve_tiles` is launched as `python -m voxelizer.serve_tiles`, and
    # `launchers` writes the .cmd files that do the launching. Both are
    # MODULES here. Neither is an attribute of the package after a plain
    # `import voxelizer` - nothing imports them at module level - but
    # `from voxelizer import serve_tiles` already worked through the
    # interpreter's submodule fallback. The package __getattr__ now imports
    # them on first access as well, and naming them here is what makes
    # `from voxelizer import *` bind them too.
    "serve_tiles",
    "launchers",
    "export_tileset_from_store",
    "pack_shards",
    "unpack_shards",
]
