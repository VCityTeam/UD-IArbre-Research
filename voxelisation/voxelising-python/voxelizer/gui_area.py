"""
@ingroup t4_entrees


Tkinter dialog for the voxelizer with two modes:

  * Single file (drag & drop / browse) -> whole-file ``laspy.read`` path
    (``pipeline.process_single_tile``), unchanged from the classic workflow.
  * Area by coordinates -> enter an RGF93/CC46 (EPSG:3946) bounding box and a tile folder;
    every intersecting tile is *streamed* (``chunk_iterator``) onto one shared
    grid with a single vertical datum and clipped to the exact rectangle
    (``area_cli.run_area`` -> ``area.process_area``).

Both modes write the same deliverables as the single-tile run - stats.txt,
the four 2-D maps and the ``columns/`` diagnostics (mode selectable below).

RAM safety:

  * Every area run FIRST shows a pre-flight dialog with a header/inventory-
    only cost estimate (tiles, km2, predicted store + peak RAM) and a RAM
    budget box (megabytes). Three choices: Cancel / Continue anyway (with an
    RSS watchdog at the budget) / Shard beyond RAM (per-tile .npz shards +
    bounded mosaic maps + columns/, none of which needs a merged store; the
    optional merge step - on by default - assembles one on disk, band by
    band, to write area.npz).
  * Area jobs run as a SUBPROCESS (``python -m <pkg>.area_cli area ...``), so
    the GUI never shares an address space with a memory-heavy job: the window
    stays responsive, and Stop terminates the child cleanly. The RSS watchdog
    inside the child aborts at the budget and still writes partial outputs.
  * Two diagnostics tickboxes: "Live diagnostics" wraps the job with
    ``voxel_runner_diagnos.py`` so its CPU/RAM ticks stream into this log
    (and the console); "Open HTML dashboard" additionally passes --serve so
    the live browser dashboard opens.

A "3-D visualiser" checkbox activates the Three.js HTML export and reveals
*all* of its CLI options (--max-boxes, --roi-size/--roi-cx/--roi-cy, and the
--no-full / --no-roi switches).
In area mode the viewers are rendered straight from the merged area store; in
file mode they come from ``viz3d_cli`` on the dropped tile.

The "Export & Serve" tab covers what happens AFTER a run: exporting a finished
store to 3-D Tiles (``tileset_cli from-store``, from a ``.npz`` OR a raw
``store_raw/`` directory - the store-form selector picks which when a run holds
both) and putting either kind of output in front of a browser
(``serve_voxel_html`` for a streaming viewer, ``serve_tiles`` for a tileset).
Both exporters already write their own double-clickable ``.cmd`` launchers
beside the artifacts, so those buttons are for ad-hoc viewing from the window
that is already open. A served directory keeps a child process alive until it
is stopped, so servers are tracked separately from the job and terminated when
the window closes.

The "Store Tools" tab is the other after-run surface: the store-level CLIs that
take a finished store (a ``.npz`` or a raw ``store_raw/`` directory) or a
``shards/`` set and do one thing to it - ``postprocess_cli`` (denoise, absorb,
resolve, group), ``reconstruct`` (store <-> LAS/LAZ), ``archive_cli`` (pack /
unpack shards as exact LAZ), ``merge_streaming`` (out-of-core shard merge),
``shard_diagnostics`` (columns and stats straight from shards) and
``viz3d_cli from-store``/``stream``. Every one runs through the same job child
as a run, so Stop and the log pane work identically.

Layout / usability notes:

  * The dialog is a ``ttk.Notebook`` with eight tabs (Input, Voxel Grid, Files &
    Outputs, 3-D Visualiser, Export & Serve, Store Tools, Diagnostics,
    Advanced) instead of one long scroll of checkboxes, so related settings are
    visually grouped and rarely-touched options are a click away rather than
    always in view. Store Tools holds the store-level CLIs (post-process,
    reconstruct, archive, merge, shard diagnostics, 3-D from a store), each in
    its own collapsible section; none of them voxelizes.
  * Long explanations live in hover tooltips rather than in the checkbox
    labels themselves, so labels stay short.
  * Bounding-box and cell-size fields validate live (red field + a status
    line) instead of only failing when Run is clicked.
  * A one-line running summary and a tile-folder tile-count are always
    visible, so the current configuration is legible without re-reading
    every field.
  * The log pane has a scrollbar and colours warnings/errors.
  * The "Files & Outputs" tab consolidates every keep/delete decision this
    tool can make (source LAZ, voxel-grid cache, per-stage store, raw
    ungrouped store, column diagnostics) in one place, including a
    previously GUI-less flag (``--keep-raw-store``). stats.txt and the four
    2-D maps are always written in a normal run (no dedicated skip flag,
    though the stage-selection switch ``--only-stage``, meant for resumes,
    can restrict a run to fewer stages) and are shown as disabled/checked
    rows for transparency rather than omitted silently.
  * Widgets whose combination the CLI would refuse are coupled here instead,
    so a refusal never reaches the user as an exit code. "Merge shards" off
    means a shard-only run: the column diagnostics stay available - they are
    computed from the shards themselves - and the dialog says so rather than
    switching them off. The CLI's own refusal (``--shard`` without
    ``--merge-shards`` while asking for the merged store or one of its
    exports) stays where it is: it guards the command line, this guards the
    dialog.
"""

from __future__ import annotations

import argparse
import os
import queue
import socket
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore

    _DND_OK = True
except Exception:
    _DND_OK = False

from .run_utils import next_run_output_dir


def _find_diagnos_script() -> Path | None:
    """Locate voxel_runner_diagnos.py (next to this module, repo root, or cwd)."""
    here = Path(__file__).resolve().parent
    for cand in (here / "voxel_runner_diagnos.py",
                 here.parent / "voxel_runner_diagnos.py",
                 Path.cwd() / "voxel_runner_diagnos.py"):
        if cand.is_file():
            return cand
    return None


def _clean_dropped_path(raw: str) -> str:
    """Normalise a tkinterdnd2 drop payload to a plain path: strip whitespace
    and unwrap the ``{...}`` braces Tk puts around paths containing spaces."""
    raw = raw.strip()
    if raw.startswith("{") and "}" in raw:
        return raw[1:raw.index("}")]
    return raw


# ---------------------------------------------------------------------------
# Pure helpers: widget coupling and command construction.
#
# These carry the rules the dialog has to get right, and none of them needs a
# display to run - which is the point. A rule that lives inside a callback
# closure can only be checked by driving the GUI; here it is a function with
# an argument list, and the tests read it directly.
# ---------------------------------------------------------------------------
# A shard-only run computes the per-column diagnostics from the shards
# themselves (voxelizer.shard_diagnostics), so the merge no longer constrains
# the columns mode: every mode is available either way, and this note says
# where the figures will come from. What the CLI still refuses without
# --merge-shards is the merged store and its exports (area.npz, area_raw.npz,
# store_raw/, the streaming viewer) - none of which this pair controls.
COLUMNS_FROM_SHARDS_NOTE = ("Shard-only run: the per-column diagnostics and "
                            "the statistics are computed from the shards "
                            "themselves - no merged store is built.")


def couple_columns_to_merge(merge_shards: bool,
                            columns_mode: str) -> tuple[str, str]:
    """Return the columns mode that will run, and the note to show for it.

    The mode passes through unchanged - a shard-only run writes columns/ as
    readily as a merged one. The note is empty exactly when there is nothing
    to explain (merging on, or the diagnostics off), so a caller can still use
    it as the "show the explanation" flag as well as its text.
    """
    if merge_shards or columns_mode == "skip":
        return columns_mode, ""
    return columns_mode, COLUMNS_FROM_SHARDS_NOTE


# The streaming viewer is the one 3-D view a shard-only run cannot produce:
# tiled_exporter walks ONE store's key order, and a set of shards has no single
# key order to walk. area_cli refuses --viz3d-stream without --merge-shards
# before any work starts, so the dialog must not be able to assemble that pair.
# Unlike the columns pair above this is a real constraint, so the mode is
# changed rather than merely annotated.
STREAM_NEEDS_MERGE_NOTE = ("Shard-only run: the streaming viewer is exported "
                           "from the merged store, so the 3-D mode falls back "
                           "to singleton (the full and ROI views are built "
                           "from the shards).")


def couple_stream_to_merge(merge_shards: bool,
                           viz3d_mode: str) -> tuple[str, str]:
    """Return the 3-D viewer mode that will run, and the note to show for it.

    ``streamable`` survives a merging run and becomes ``singleton`` in a
    shard-only one. The note is empty exactly when nothing was changed, so a
    caller can use it as the "show the explanation" flag as well as its text.
    """
    if merge_shards or viz3d_mode != "streamable":
        return viz3d_mode, ""
    return "singleton", STREAM_NEEDS_MERGE_NOTE


def resolve_store_path(path, *, kind: str = "auto") -> Path | None:
    """The store meant by *path*, or None if there is none.

    A user picks the thing they can see - usually a run's output directory -
    rather than the file inside it, so a directory is resolved to the store
    the pipeline writes there. *kind* chooses which form is acceptable:

      * ``"auto"`` (the default): a directory resolves in this order -
        ``area.npz``, then the pre-grouping ``area_raw.npz``, then a raw
        ``store_raw/`` directory (when it holds a ``meta.json``), then a
        ``shards/`` folder holding exactly ONE ``.npz`` (the ``single
        --shards`` output). Several shards is a set to merge, not a store, and
        returns None.
      * ``"npz"``: only a compressed ``.npz`` (the file itself, or
        ``area.npz``/``area_raw.npz``/the lone shard inside a directory). Never
        a ``store_raw/`` directory.
      * ``"dir"``: only a raw ``store_raw/`` directory (the directory itself,
        or one found inside a run directory). Never an ``.npz``.

    ``"npz"`` and ``"dir"`` are the explicit overrides for when a run holds
    both forms and the auto order would pick the other one.
    """
    if not path:
        return None
    p = Path(str(path).strip().strip('"').strip("'"))

    def _npz_in(d: Path) -> Path | None:
        """The compressed store inside directory *d*, or None: area.npz, then
        area_raw.npz."""
        for name in ("area.npz", "area_raw.npz"):
            cand = d / name
            if cand.is_file():
                return cand
        return None

    def _lone_shard_in(d: Path) -> Path | None:
        """The only shard of a single-tile ``--shards`` folder, or None when
        *d* holds no ``shards/`` or more than one shard."""
        shards = d / "shards"
        if shards.is_dir():
            npzs = sorted(shards.glob("*.npz"))
            if len(npzs) == 1:
                return npzs[0]
        return None

    def _dir_in(d: Path) -> Path | None:
        """The raw store directory inside *d*, or None: d/store_raw, else d
        itself when it IS a complete raw store."""
        cand = d / "store_raw"
        if (cand / "meta.json").is_file():
            return cand
        if (d / "meta.json").is_file():
            return d
        return None

    # An explicit file is taken as given, whatever its kind asked for: the
    # user pointed at one file, not at a directory to search.
    if p.is_file():
        if kind == "dir":
            return None
        return p
    if p.is_dir():
        if kind == "npz":
            return _npz_in(p) or _lone_shard_in(p)
        if kind == "dir":
            return _dir_in(p)
        # auto: area.npz -> area_raw.npz -> store_raw/ -> lone shard.
        return _npz_in(p) or _dir_in(p) or _lone_shard_in(p)
    return None


def build_tileset_cmd(python: str, store_npz, out_dir, *, tile_m: float,
                      lod: bool = True, keep_classes: str = "",
                      geoid: bool = True, crs: str | None = None,
                      region=None, height_offset=None,
                      vertical_crs: str | None = None) -> list[str]:
    """``python -m voxelizer.tileset_cli from-store ...`` as an argv list.

    Mirrors the CLI's own surface, which deliberately has no ``--max-instances``:
    that number is a viewer GPU working-set budget for the streaming HTML
    viewer, and the intermediate payload it would size is deleted by this
    export path, so it could never change a tileset. The LOD pyramid is the
    default there and here; the checkbox emits ``--flat`` when cleared.

    ``crs`` and ``region`` are the CLI's ``--crs`` and ``--region`` (the latter
    a ``(xmin, ymin, xmax, ymax)`` metric subset); ``height_offset`` is
    ``--height-offset`` and ``vertical_crs`` is ``--vertical-crs``. All are
    omitted when not given, so the CLI keeps its own defaults.
    """
    cmd = [python, "-u", "-m", f"{__package__ or 'voxelizer'}.tileset_cli",
           "from-store", str(store_npz), "--out-dir", str(out_dir),
           "--tile-m", str(tile_m)]
    if not lod:
        cmd.append("--flat")
    if keep_classes.strip():
        cmd += ["--keep-classes", keep_classes.strip()]
    if crs:
        cmd += ["--crs", str(crs)]
    if region:
        cmd += ["--region"] + [str(v) for v in region]
    if vertical_crs:
        cmd += ["--vertical-crs", str(vertical_crs)]
    if not geoid:
        cmd.append("--no-geoid")
    if height_offset not in (None, ""):
        cmd += ["--height-offset", str(height_offset)]
    return cmd


def build_tileset_from_payload_cmd(python: str, idx_json, out_dir, *,
                                   bin_path=None, crs: str | None = None,
                                   lod: bool = True, geoid: bool = True,
                                   height_offset=None,
                                   vertical_crs: str | None = None) -> list[str]:
    """``python -m voxelizer.tileset_cli from-payload ...`` as an argv list.

    Converts an already-written ``.idx.json`` + ``.bin`` streaming payload
    (``area_stream.idx.json`` next to ``area_stream.bin``, or a single tile's
    ``<stem>_stream.idx.json``) to a 3-D Tiles folder without touching a store.
    ``bin_path`` is left to the CLI's own sibling-file default when None.
    """
    cmd = [python, "-u", "-m", f"{__package__ or 'voxelizer'}.tileset_cli",
           "from-payload", str(idx_json), "--out-dir", str(out_dir)]
    if bin_path:
        cmd.insert(cmd.index("--out-dir"), str(bin_path))
    if not lod:
        cmd.append("--flat")
    if crs:
        cmd += ["--crs", str(crs)]
    if vertical_crs:
        cmd += ["--vertical-crs", str(vertical_crs)]
    if not geoid:
        cmd.append("--no-geoid")
    if height_offset not in (None, ""):
        cmd += ["--height-offset", str(height_offset)]
    return cmd


def build_serve_stream_cmd(python: str, serve_dir, *,
                           page: str | None = None, port=None,
                           bind: str | None = None,
                           no_open: bool = False) -> list[str]:
    """``python -m voxelizer.serve_voxel_html <dir> --port <n> --open``.

    On a desktop, port 0 so several viewers can be open at once, exactly as
    the generated ``view_stream.cmd`` does it; the server prints the url it
    was given. Inside the Docker image the calculus flips: docker-compose
    publishes exactly one fixed port (8000) for this server, so an OS-chosen
    port would be unreachable from the host. ``VOXELIZER_BIND`` is set only
    by the image (Dockerfile ``ENV``), so it doubles as the container marker:
    when it is set, ask for 8000 if that port is currently free - probed with
    a bind attempt on the same address the server will use - and fall back to
    0 (reachable via ``docker exec``, at least) when a first viewer already
    holds it.

    ``port`` and ``bind`` override both defaults when given (a string ``"0"``
    still asks the OS); ``no_open`` emits ``--no-open`` instead of ``--open``.
    """
    if port not in (None, ""):
        port = str(port)
    else:
        port = "0"
        bind_default = os.environ.get("VOXELIZER_BIND", "").strip()
        if bind_default:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    probe.bind((bind_default, 8000))
                port = "8000"
            except OSError:
                port = "0"
    cmd = [python, "-u", "-m", f"{__package__ or 'voxelizer'}.serve_voxel_html",
           str(serve_dir), "--port", port]
    if bind:
        cmd += ["--bind", str(bind)]
    cmd.append("--no-open" if no_open else "--open")
    if page:
        cmd += ["--open-page", page]
    return cmd


def build_serve_tiles_cmd(python: str, serve_dir, viewer, *,
                          port=None, bind: str | None = None,
                          no_open: bool = False) -> list[str]:
    """``python -m voxelizer.serve_tiles <dir> --open-viewer {cesium,itowns}``.

    ``port`` and ``bind`` are omitted when not given, so the server keeps its
    own default (an OS-chosen free port; localhost or ``$VOXELIZER_BIND``).
    ``no_open`` emits ``--no-open``; the server still serves either way.
    """
    cmd = [python, "-u", "-m", f"{__package__ or 'voxelizer'}.serve_tiles",
           str(serve_dir)]
    if viewer:
        cmd += ["--open-viewer", str(viewer)]
    if port not in (None, ""):
        cmd += ["--port", str(port)]
    if bind:
        cmd += ["--bind", str(bind)]
    if no_open:
        cmd.append("--no-open")
    return cmd


# ---------------------------------------------------------------------------
# Store-tool command builders (the Store Tools tab). Pure functions like the
# exporters above, so every argv is unit-testable without a Tk window.
# ---------------------------------------------------------------------------
def build_postprocess_cmd(python: str, input_store, output_npz, *,
                          min_points=None, morph=None, morph_classes="",
                          absorb=False, absorb_min_neighbour=None,
                          absorb_max_noise=None, resolve=False,
                          tie_margin=None, tie_rel=None, prefer_taller=True,
                          demote_uncertain=True, class_priority="",
                          group=False, group_gap=None, stats=False,
                          dry_run=False) -> list[str]:
    """``python -m voxelizer.postprocess_cli IN OUT.npz [passes]``.

    ``min_points`` / ``morph`` are the CLI's bare-or-valued flags: ``None``
    omits them, ``""`` (empty string) emits the bare flag (its default value),
    and a number emits that value. The CLI refuses an empty plan, so at least
    one pass must be present; that is enforced by the caller, not here.
    """
    cmd = [python, "-u", "-m", f"{__package__ or 'voxelizer'}.postprocess_cli",
           str(input_store), str(output_npz)]

    def _opt_val(flag, val):
        """Bare flag when *val* is '', flag+value when it is a number, nothing
        when it is None."""
        if val is None:
            return []
        if val == "":
            return [flag]
        return [flag, str(val)]

    cmd += _opt_val("--min-points", min_points)
    cmd += _opt_val("--morph", morph)
    if morph_classes.strip():
        cmd += ["--morph-classes", morph_classes.strip()]
    if absorb:
        cmd.append("--absorb")
    if absorb_min_neighbour not in (None, ""):
        cmd += ["--absorb-min-neighbour", str(absorb_min_neighbour)]
    if absorb_max_noise not in (None, ""):
        cmd += ["--absorb-max-noise", str(absorb_max_noise)]
    if resolve:
        cmd.append("--resolve")
    if tie_margin not in (None, ""):
        cmd += ["--tie-margin", str(tie_margin)]
    if tie_rel not in (None, ""):
        cmd += ["--tie-rel", str(tie_rel)]
    if not prefer_taller:
        cmd.append("--no-prefer-taller")
    if not demote_uncertain:
        cmd.append("--no-demote-uncertain")
    if class_priority.strip():
        cmd += ["--class-priority", class_priority.strip()]
    if group:
        cmd.append("--group")
        if group_gap not in (None, ""):
            cmd += ["--group-gap", str(group_gap)]
    if stats:
        cmd.append("--stats")
    if dry_run:
        cmd.append("--dry-run")
    return cmd


def build_reconstruct_cmd(python: str, verb: str, *,
                          npz=None, laz=None, output=None, mode=None,
                          one_per_voxel=False, z_base=False, epsg=None,
                          grid_vlr=True, verify=False, origin=None,
                          cell_xy=None, cell_z=None) -> list[str]:
    """``python -m voxelizer.reconstruct {to-laz,to-npz,verify}``.

    ``to-laz``: ``npz`` + ``output`` (a .las/.laz) with ``--mode``,
    ``--one-per-voxel``, ``--z-base``, ``--epsg``, ``--no-grid-vlr``,
    ``--verify``. ``to-npz``: ``laz`` + ``output`` (a .npz) with
    ``--origin X Y Z``, ``--cell-xy``, ``--cell-z`` (needed when the file has
    no IARBRE grid VLR). ``verify``: ``npz`` + ``laz``. ``epsg`` is omitted
    when None so the CLI keeps its own default.
    """
    cmd = [python, "-u", "-m", f"{__package__ or 'voxelizer'}.reconstruct", verb]
    if verb == "to-laz":
        cmd.append(str(npz))
        cmd.append(str(output))
        if mode:
            cmd += ["--mode", str(mode)]
        if one_per_voxel:
            cmd.append("--one-per-voxel")
        if z_base:
            cmd.append("--z-base")
        if epsg not in (None, ""):
            cmd += ["--epsg", str(epsg)]
        if not grid_vlr:
            cmd.append("--no-grid-vlr")
        if verify:
            cmd.append("--verify")
    elif verb == "to-npz":
        cmd.append(str(laz))
        cmd.append(str(output))
        if origin:
            cmd += ["--origin"] + [str(v) for v in origin]
        if cell_xy not in (None, ""):
            cmd += ["--cell-xy", str(cell_xy)]
        if cell_z not in (None, ""):
            cmd += ["--cell-z", str(cell_z)]
    elif verb == "verify":
        cmd.append(str(npz))
        cmd.append(str(laz))
    else:
        raise ValueError(f"unknown reconstruct verb {verb!r}")
    return cmd


def build_archive_cmd(python: str, verb: str, *, run_dir=None, archive_dir=None,
                      workers=None, no_verify=False, replace=False, epsg=None,
                      out=None) -> list[str]:
    """``python -m voxelizer.archive_cli {pack,unpack}``.

    ``pack``: ``run_dir`` + ``--workers`` + ``--no-verify``/``--replace`` +
    ``--epsg``. ``replace`` and ``no_verify`` are mutually refused by the CLI,
    so when both are set ``--replace`` is dropped here (verification is what
    makes it safe). ``unpack``: ``archive_dir`` + ``--out`` + ``--workers``.
    """
    cmd = [python, "-u", "-m", f"{__package__ or 'voxelizer'}.archive_cli", verb]
    if verb == "pack":
        cmd.append(str(run_dir))
        if workers not in (None, ""):
            cmd += ["--workers", str(workers)]
        if replace and not no_verify:
            cmd.append("--replace")
        if no_verify:
            cmd.append("--no-verify")
        if epsg not in (None, ""):
            cmd += ["--epsg", str(epsg)]
    elif verb == "unpack":
        cmd.append(str(archive_dir))
        if out:
            cmd += ["--out", str(out)]
        if workers not in (None, ""):
            cmd += ["--workers", str(workers)]
    else:
        raise ValueError(f"unknown archive verb {verb!r}")
    return cmd


def build_merge_cmd(python: str, shards_dir, out_store_dir, *,
                    group_intervals=False, group_gap=None, band_intervals=None,
                    overwrite=False, plan_only=False) -> list[str]:
    """``python -m voxelizer.merge_streaming --shards-dir ... --out-store-dir ...``."""
    cmd = [python, "-u", "-m", f"{__package__ or 'voxelizer'}.merge_streaming",
           "--shards-dir", str(shards_dir),
           "--out-store-dir", str(out_store_dir)]
    cmd.append("--group-intervals" if group_intervals else "--no-group-intervals")
    if group_intervals and group_gap not in (None, ""):
        cmd += ["--group-gap", str(group_gap)]
    if band_intervals not in (None, ""):
        cmd += ["--band-intervals", str(band_intervals)]
    if overwrite:
        cmd.append("--overwrite")
    if plan_only:
        cmd.append("--plan-only")
    return cmd


def build_shard_diag_cmd(python: str, shards_dir, out_dir, *,
                         columns_mode="diag", columns_top_n=None,
                         columns_all_max=None, stats=True,
                         group_intervals=None, group_gap=None,
                         batch_intervals=None, tile_label="area") -> list[str]:
    """``python -m voxelizer.shard_diagnostics --shards-dir ... --out-dir ...``.

    ``group_intervals`` is the CLI's tri-state: ``None`` omits the flag (the
    manifest's provenance decides), ``True``/``False`` emit the explicit
    switch. The CLI refuses a run that asks for neither figures nor stats, so
    the caller must guarantee at least one.
    """
    cmd = [python, "-u", "-m", f"{__package__ or 'voxelizer'}.shard_diagnostics",
           "--shards-dir", str(shards_dir), "--out-dir", str(out_dir),
           "--columns-mode", str(columns_mode)]
    if columns_top_n not in (None, ""):
        cmd += ["--columns-top-n", str(columns_top_n)]
    if columns_mode == "all" and columns_all_max not in (None, ""):
        cmd += ["--columns-all-max", str(columns_all_max)]
    cmd.append("--stats" if stats else "--no-stats")
    if group_intervals is True:
        cmd.append("--group-intervals")
    elif group_intervals is False:
        cmd.append("--no-group-intervals")
    if group_gap not in (None, ""):
        cmd += ["--group-gap", str(group_gap)]
    if batch_intervals not in (None, ""):
        cmd += ["--batch-intervals", str(batch_intervals)]
    if tile_label and tile_label != "area":
        cmd += ["--tile-label", str(tile_label)]
    return cmd


def build_viz3d_from_store_cmd(python: str, store_file, out_dir, *,
                               label="", max_boxes=None, roi_size=None,
                               roi_cx=None, roi_cy=None, do_full=True,
                               do_roi=True, grid=False) -> list[str]:
    """``python -m voxelizer.viz3d_cli from-store STORE -o DIR [geom opts]``.

    ``store_file`` is a ``.npz`` or a raw store directory (``viz3d_cli`` loads
    either). Geometry options are omitted when blank, so the CLI keeps its
    defaults; ``--no-full`` / ``--no-roi`` are emitted when a view is
    unchecked.
    """
    cmd = [python, "-u", "-m", f"{__package__ or 'voxelizer'}.viz3d_cli",
           "from-store", str(store_file), "--output-dir", str(out_dir)]
    if label.strip():
        cmd += ["--label", label.strip()]
    if max_boxes not in (None, ""):
        cmd += ["--max-boxes", str(max_boxes)]
    if roi_size not in (None, ""):
        cmd += ["--roi-size", str(roi_size)]
    if roi_cx not in (None, ""):
        cmd += ["--roi-cx", str(roi_cx)]
    if roi_cy not in (None, ""):
        cmd += ["--roi-cy", str(roi_cy)]
    if not do_full:
        cmd.append("--no-full")
    if not do_roi:
        cmd.append("--no-roi")
    if grid:
        cmd.append("--grid")
    return cmd


def build_viz3d_stream_cmd(python: str, store_file, out_html, *,
                           label="", region=None, keep_classes="",
                           max_instances=None, inline_threshold_mb=None,
                           tile_m=None) -> list[str]:
    """``python -m voxelizer.viz3d_cli stream STORE --out PAGE.html [opts]``.

    Deliberately omits ``--stride`` and ``--max-boxes``: the streaming
    exporter accepts them only to warn that it ignores them (it always writes
    every interval; the GPU working set is bounded at view time), so exposing
    them would only invite a no-op. ``store_file`` is a ``.npz`` or a raw
    store directory.
    """
    cmd = [python, "-u", "-m", f"{__package__ or 'voxelizer'}.viz3d_cli",
           "stream", str(store_file), "--out", str(out_html)]
    if label.strip():
        cmd += ["--label", label.strip()]
    if region:
        cmd += ["--region"] + [str(v) for v in region]
    if keep_classes.strip():
        cmd += ["--keep-classes", keep_classes.strip()]
    if max_instances not in (None, ""):
        cmd += ["--max-instances", str(max_instances)]
    if inline_threshold_mb not in (None, ""):
        cmd += ["--inline-threshold-mb", str(inline_threshold_mb)]
    if tile_m not in (None, ""):
        cmd += ["--tile-m", str(tile_m)]
    return cmd


# ---------------------------------------------------------------------------
# Work functions (delegated to the tested core; safe to unit-test directly)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Small reusable widgets: tooltip + inline-validated entry helpers
# ---------------------------------------------------------------------------
class _ToolTip:
    """Minimal hover tooltip: a borderless Toplevel shown near the widget.

    Keeps checkbox/label text short in the main layout while still surfacing
    the full explanation on hover, instead of baking paragraphs into labels.
    """

    def __init__(self, widget, text: str):
        """Remember *widget* and *text* and bind Enter/Leave/ButtonPress on the
        widget so the tip appears on hover and vanishes on leave or click."""
        self.widget = widget
        self.text = text
        self.tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _show(self, _evt=None):
        """Create the tooltip Toplevel just below the widget (no-op if one is
        already up or the text is empty)."""
        if self.tip is not None or not self.text:
            return
        x = self.widget.winfo_rootx() + 4
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        try:
            self.tip.wm_attributes("-topmost", True)
        except Exception:  # noqa: BLE001
            pass
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, justify="left", wraplength=380,
                 background="#ffffe0", relief="solid", borderwidth=1,
                 font=("Segoe UI", 8), padx=6, pady=4).pack()

    def _hide(self, _evt=None):
        """Destroy the tooltip Toplevel if one is showing."""
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


def _tip(widget, text: str):
    """Attach a hover tooltip to *widget* and return it (for chaining)."""
    if text:
        _ToolTip(widget, text)
    return widget


class _CollapsibleSection:
    """A titled section whose body shows or hides at a click.

    The Store Tools tab packs six independent tools into one tab; showing all
    their widgets at once would be an unreadable wall. Each tool gets one of
    these: a clickable header row with a disclosure triangle, and a body frame
    packed or unpacked underneath.

    The body is a plain ``ttk.Frame`` the caller fills and returns via
    ``.body``; toggling uses ``pack``/``pack_forget`` so it composes with the
    ``pack``-based layout the rest of the dialog uses. The header is a
    ``ttk.Label`` rather than a button so it reads as a heading; it is bound to
    ``<Button-1>`` and its triangle glyph tracks the state.
    """

    def __init__(self, parent, title: str, *, expanded: bool = False):
        """Build the header + an empty body under *parent*; start expanded or
        collapsed and reflect that in the triangle glyph."""
        self._expanded = expanded
        self._outer = ttk.Frame(parent)
        self._outer.pack(fill="x", pady=(6, 0))
        self.header = ttk.Frame(self._outer)
        self.header.pack(fill="x")
        self._label = ttk.Label(
            self.header, text=f"{'\u25be' if expanded else '\u25b8'}  {title}",
            foreground="#1f4e79", cursor="hand2")
        self._label.pack(side="left")
        self._title = title
        self.body = ttk.Frame(self._outer, padding=(16, 4, 0, 0))
        for w in (self.header, self._label):
            w.bind("<Button-1>", self._toggle)
        if expanded:
            self.body.pack(fill="x")

    def _toggle(self, _evt=None):
        """Flip the body: pack it (expanded) or unpack it (collapsed), updating
        the triangle glyph to match."""
        self._expanded = not self._expanded
        self._label.configure(
            text=f"{'\u25be' if self._expanded else '\u25b8'}  {self._title}")
        if self._expanded:
            self.body.pack(fill="x")
        else:
            self.body.pack_forget()


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
def build_app(root) -> dict:
    """Build the whole dialog onto *root* and return a handle to it.

    Split out of main() so the window can be constructed without
    entering the event loop: a test makes a withdrawn root, calls this, and
    inspects the notebook and the command builders. The returned mapping is
    that seam and nothing more - the widgets remain closure-local, as they
    were.
    """
    root.title("IA.rbre voxelizer - file or area")
    root.minsize(860, 620)
    root.geometry("900x760")
    root.resizable(True, True)  # was fixed-size; long runs need a bigger log

    style = ttk.Style(root)
    # Functional-only styling (not decorative): a visibly different field
    # colour for invalid numeric input, used by the live validators below.
    style.configure("Invalid.TEntry", fieldbackground="#ffdede")
    style.map("Invalid.TEntry", fieldbackground=[("!disabled", "#ffdede")])

    # ------------------------------------------------------------------
    # Mode selector - always visible above the tabs; it changes which
    # Input-tab panel is shown AND which Files & Outputs rows apply.
    # ------------------------------------------------------------------
    mode_var = tk.StringVar(value="file")
    mode_frame = ttk.Frame(root, padding=(12, 12, 12, 0))
    mode_frame.pack(fill="x")
    ttk.Label(mode_frame, text="Mode:").pack(side="left")
    ttk.Radiobutton(mode_frame, text="Single file (drag & drop)",
                    variable=mode_var, value="file").pack(side="left", padx=(8, 0))
    ttk.Radiobutton(mode_frame, text="Area by coordinates",
                    variable=mode_var, value="area").pack(side="left", padx=(8, 0))

    # ------------------------------------------------------------------
    # Notebook: groups everything else into seven tabs instead of one long
    # stack, and keeps rarely-touched settings (Diagnostics, Advanced) out
    # of the way until asked for.
    # ------------------------------------------------------------------
    nb = ttk.Notebook(root)
    nb.pack(fill="x", padx=12, pady=(8, 0))

    tab_input = ttk.Frame(nb, padding=10)
    tab_grid = ttk.Frame(nb, padding=10)
    tab_files = ttk.Frame(nb, padding=10)
    tab_viz = ttk.Frame(nb, padding=10)
    tab_export = ttk.Frame(nb, padding=10)
    tab_diag = ttk.Frame(nb, padding=10)
    tab_adv = ttk.Frame(nb, padding=10)
    tab_tools = ttk.Frame(nb, padding=10)
    nb.add(tab_input, text="Input")
    nb.add(tab_grid, text="Voxel Grid")
    nb.add(tab_files, text="Files & Outputs")
    nb.add(tab_viz, text="3-D Visualiser")
    nb.add(tab_export, text="Export & Serve")
    nb.add(tab_tools, text="Store Tools")
    nb.add(tab_diag, text="Diagnostics")
    nb.add(tab_adv, text="Advanced")

    # ==================================================================
    # TAB: Input
    # ==================================================================
    # -- File mode widgets --
    file_frame = ttk.Frame(tab_input)
    ttk.Label(file_frame, text="LAZ file:").pack(side="left")
    path_var = tk.StringVar()
    entry = ttk.Entry(file_frame, textvariable=path_var, width=52)
    entry.pack(side="left", padx=(6, 0), fill="x", expand=True)

    def _browse_file():
        """Open a LAZ/LAS file chooser (starting in inputs/laz when it exists)
        and put the chosen path into the file-mode entry."""
        cand = Path("inputs") / "laz"
        f = filedialog.askopenfilename(
            initialdir=str(cand) if cand.is_dir() else str(Path.cwd()),
            title="Choose a LAZ/LAS file",
            filetypes=[("LAZ/LAS files", "*.laz *.las"), ("All files", "*.*")])
        if f:
            path_var.set(f)

    ttk.Button(file_frame, text="Browse...", command=_browse_file).pack(side="left", padx=(6, 0))
    if _DND_OK:
        try:
            entry.drop_target_register(DND_FILES)
            entry.dnd_bind("<<Drop>>",
                           lambda e: path_var.set(_clean_dropped_path(e.data)))
        except tk.TclError:
            # tkinterdnd2 imports, but the tkdnd Tcl package is not loaded on
            # THIS root - which is the case for a plain tk.Tk(), the root a
            # test builds on. Drag & drop was always optional (see _DND_OK);
            # Browse... does the same job, so the dialog is built either way
            # rather than failing to open at all.
            pass
    _tip(entry, "Path to one .laz/.las tile. Drag a file onto this box, or "
                "use Browse...")

    # -- Area mode widgets --
    area_frame = ttk.Frame(tab_input)
    xmin_var, ymin_var = tk.StringVar(value="1831000"), tk.StringVar(value="5175000")
    xmax_var, ymax_var = tk.StringVar(value="1832000"), tk.StringVar(value="5176000")
    bbox_entries: dict[str, ttk.Entry] = {}
    bbox_vars = {"xmin": xmin_var, "ymin": ymin_var, "xmax": xmax_var, "ymax": ymax_var}
    _bbox_tips = {
        "xmin": "Western edge (easting), metres, RGF93/CC46 (EPSG:3946).",
        "xmax": "Eastern edge (easting), metres. Must be >= xmin.",
        "ymin": "Southern edge (northing), metres. Must be <= ymax.",
        "ymax": "Northern edge (northing), metres.",
    }
    for col, name in enumerate(["xmin", "ymin", "xmax", "ymax"]):
        var = bbox_vars[name]
        ttk.Label(area_frame, text=name).grid(row=0, column=col * 2, padx=(0 if col == 0 else 8, 2))
        e = ttk.Entry(area_frame, textvariable=var, width=12)
        e.grid(row=0, column=col * 2 + 1)
        _tip(e, _bbox_tips[name])
        bbox_entries[name] = e
    ttk.Label(area_frame, text="RGF93/CC46 (EPSG:3946) metres - the same "
                              "projection used by the Grand Lyon 2023 LAZ "
                              "tiles").grid(row=1, column=0, columnspan=8, sticky="w", pady=(2, 0))
    dir_row = ttk.Frame(area_frame)
    dir_row.grid(row=2, column=0, columnspan=8, sticky="we", pady=(6, 0))
    ttk.Label(dir_row, text="Tile folder:").pack(side="left")
    lazdir_var = tk.StringVar(value=str(Path("inputs") / "laz"))
    lazdir_entry = ttk.Entry(dir_row, textvariable=lazdir_var, width=40)
    lazdir_entry.pack(side="left", padx=(6, 0), fill="x", expand=True)
    ttk.Button(dir_row, text="Browse...",
               command=lambda: lazdir_var.set(filedialog.askdirectory() or lazdir_var.get())
               ).pack(side="left", padx=(6, 0))
    tile_count_var = tk.StringVar(value="")
    ttk.Label(dir_row, textvariable=tile_count_var, foreground="#555"
              ).pack(side="left", padx=(8, 0))
    _tip(lazdir_entry, "Folder containing the .laz tiles this bounding box "
                       "will be read from (local mirror of the Grand Lyon "
                       "2023 inventory).")

    def _update_tile_count(*_):
        """Refresh the tile-count label next to the tile folder entry: blank
        for no folder, a note when the folder is missing, else the number of
        ``*.laz`` files found in it."""
        d = lazdir_var.get().strip()
        if not d:
            tile_count_var.set("")
            return
        p = Path(d)
        if not p.is_dir():
            tile_count_var.set("(folder not found)")
            return
        try:
            n = sum(1 for _ in p.glob("*.laz"))
        except OSError:
            tile_count_var.set("")
            return
        tile_count_var.set(f"{n:,} .laz tile(s) found")

    lazdir_var.trace_add("write", _update_tile_count)

    # -- Download section (area mode only) --
    dl_frame = ttk.Frame(tab_input)
    download_var = tk.BooleanVar(value=False)
    dl_cb = ttk.Checkbutton(dl_frame, text="Download tiles from Grand Lyon",
                            variable=download_var)
    dl_cb.pack(side="left")
    _tip(dl_cb, "Before processing, fetch any tile inside the bbox that is "
                "missing from the tile folder, using the inventory JSON "
                "below.")
    json_frame = ttk.Frame(dl_frame)
    ttk.Label(json_frame, text="JSON:").pack(side="left")
    json_var = tk.StringVar()
    ttk.Entry(json_frame, textvariable=json_var, width=40).pack(side="left", padx=(4, 0), fill="x", expand=True)

    def _browse_json():
        """Open a file chooser for the inventory JSON and store the pick in
        the JSON entry."""
        f = filedialog.askopenfilename(
            initialdir=str(Path("inputs") / "quickhelpers"),
            title="Choose inventory JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if f:
            json_var.set(f)

    ttk.Button(json_frame, text="Browse...", command=_browse_json).pack(side="left", padx=(4, 0))

    # Download tuning - only meaningful with the download/stream fetch, so it
    # rides inside the same frame and shows only while download is ticked.
    dl_tune_frame = ttk.Frame(dl_frame)
    ttk.Label(dl_tune_frame, text="pitch").pack(side="left")
    tile_pitch_var = tk.StringVar(value="")
    tile_pitch_entry = ttk.Entry(dl_tune_frame, textvariable=tile_pitch_var,
                                 width=6)
    tile_pitch_entry.pack(side="left", padx=(4, 10))
    _tip(tile_pitch_entry,
         "Optional --tile-pitch: acquisition tile size in metres (blank = the "
         "500 m default). Only used to pick which inventory tiles fall in the "
         "box, for --download and --stream alike.")
    ttk.Label(dl_tune_frame, text="workers").pack(side="left")
    dl_workers_var = tk.StringVar(value="")
    dl_workers_entry = ttk.Entry(dl_tune_frame, textvariable=dl_workers_var,
                                 width=5)
    dl_workers_entry.pack(side="left", padx=(4, 10))
    _tip(dl_workers_entry,
         "Optional --workers: parallel downloaders (blank = the CLI default). "
         "Only affects the tile fetch, never the voxelization.")
    ttk.Label(dl_tune_frame, text="limit").pack(side="left")
    dl_limit_var = tk.StringVar(value="")
    dl_limit_entry = ttk.Entry(dl_tune_frame, textvariable=dl_limit_var,
                               width=6)
    dl_limit_entry.pack(side="left", padx=(4, 0))
    _tip(dl_limit_entry,
         "Optional --limit: fetch at most this many tiles (blank = no limit). "
         "Handy for a quick try on a big box.")

    def _toggle_download(*_):
        """Show the inventory-JSON and tuning rows only while 'Download
        tiles' is ticked."""
        if download_var.get():
            json_frame.pack(side="left", padx=(12, 0), fill="x", expand=True)
            dl_tune_frame.pack(side="left", padx=(12, 0))
        else:
            json_frame.pack_forget()
            dl_tune_frame.pack_forget()

    download_var.trace_add("write", _toggle_download)

    # -- Streaming mode checkbox (always visible in area mode) --
    stream_row = ttk.Frame(tab_input)
    stream_var = tk.BooleanVar(value=False)
    stream_cb = ttk.Checkbutton(stream_row, text="Streaming mode",
                                variable=stream_var)
    stream_cb.pack(side="left")
    _tip(stream_cb, "Download & voxelize each tile on the fly, one at a "
                    "time, without keeping a permanent local .laz copy. "
                    "Trades disk space for needing the inventory JSON and "
                    "a network connection during the whole run.")
    # JSON selector - shown only when streaming is checked.
    stream_json_row = ttk.Frame(tab_input)
    ttk.Label(stream_json_row, text="JSON:").pack(side="left")
    ttk.Entry(stream_json_row, textvariable=json_var, width=40).pack(
        side="left", padx=(4, 0), fill="x", expand=True)
    ttk.Button(stream_json_row, text="Browse...",
               command=_browse_json).pack(side="left", padx=(4, 0))

    # ==================================================================
    # TAB: Voxel Grid
    # ==================================================================
    ttk.Label(tab_grid, text="cell_xy").grid(row=0, column=0, sticky="w")
    cell_xy_var = tk.StringVar(value="0.5")
    cell_xy_entry = ttk.Entry(tab_grid, textvariable=cell_xy_var, width=8)
    cell_xy_entry.grid(row=0, column=1, padx=(2, 12))
    _tip(cell_xy_entry, "Horizontal voxel size in metres (each column is a "
                        "cell_xy x cell_xy square). Smaller = finer detail, "
                        "more columns, more RAM/time.")
    ttk.Label(tab_grid, text="cell_z").grid(row=0, column=2, sticky="w")
    cell_z_var = tk.StringVar(value="0.5")
    cell_z_entry = ttk.Entry(tab_grid, textvariable=cell_z_var, width=8)
    cell_z_entry.grid(row=0, column=3, padx=(2, 12))
    _tip(cell_z_entry, "Vertical voxel size in metres - the finest Z "
                       "resolution class boundaries snap to. When grouping "
                       "is on (Files & Outputs tab), consecutive same-class "
                       "steps merge into one interval, so a single interval "
                       "usually spans many cell_z steps, not just one.")
    ttk.Label(tab_grid, text="height").grid(row=0, column=4, sticky="w")
    height_var = tk.StringVar(value="default")
    height_dd = ttk.Combobox(tab_grid, textvariable=height_var,
                             values=["default", "relative", "absolute"],
                             state="readonly", width=9)
    height_dd.grid(row=0, column=5, padx=(2, 0))
    _tip(height_dd, "Contrast reference for the 'max height' 2-D map ONLY - "
                    "it does not change voxel geometry, stored Z, the .npz, "
                    "the 3-D viewer, or the other three maps. "
                    "default: auto-contrast between the lowest and highest "
                    "column tops. relative: height above each column's own "
                    "lowest point (canopy-height / nDSM style; terrain slope "
                    "removed, so equal-height trees read alike regardless of "
                    "ground). absolute: top scaled from the tile's true floor "
                    "(DSM style; keeps real terrain + object altitude).")

    ttk.Label(tab_grid, text="keep classes").grid(row=0, column=6, sticky="w",
                                                  padx=(18, 0))
    keep_classes_var = tk.StringVar(value="")
    keep_classes_entry = ttk.Entry(tab_grid, textvariable=keep_classes_var,
                                   width=14)
    keep_classes_entry.grid(row=0, column=7, padx=(2, 0))
    _tip(keep_classes_entry,
         "Optional --keep-classes: comma- or space-separated ASPRS codes to "
         "keep; every other class is dropped before voxelization. Blank keeps "
         "every class (the default). Applies in both File and Area mode.")

    # Chunking
    use_chunks_var = tk.BooleanVar(value=True)
    chunks_cb = ttk.Checkbutton(tab_grid, text="Stream in chunks",
                                variable=use_chunks_var)
    chunks_cb.grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))
    _tip(chunks_cb, "Read each tile in bounded batches instead of loading "
                    "it whole. Keeps peak RAM low; turning this off reads "
                    "every tile fully into memory at once.")
    chunks_frame = ttk.Frame(tab_grid)
    chunks_frame.grid(row=1, column=2, columnspan=4, sticky="w", pady=(10, 0))
    ttk.Label(chunks_frame, text="chunk size:").pack(side="left")
    chunk_var = tk.StringVar(value="5000000")
    ttk.Entry(chunks_frame, textvariable=chunk_var, width=10).pack(side="left", padx=(4, 2))
    ttk.Label(chunks_frame, text="points").pack(side="left")

    def _toggle_chunks(*_):
        """Show the chunk-size row only while 'Stream in chunks' is ticked."""
        if use_chunks_var.get():
            chunks_frame.grid(row=1, column=2, columnspan=4, sticky="w", pady=(10, 0))
        else:
            chunks_frame.grid_remove()

    use_chunks_var.trace_add("write", _toggle_chunks)

    clip_var = tk.BooleanVar(value=True)
    clip_cb = ttk.Checkbutton(tab_grid, text="Clip to exact bounding box",
                              variable=clip_var)
    clip_cb.grid(row=2, column=0, columnspan=4, sticky="w", pady=(10, 0))
    _tip(clip_cb, "Drop points outside the requested rectangle. Turning "
                  "this off keeps whole tiles even where they overhang the "
                  "box, so the output extends slightly past what you asked "
                  "for.")

    # ==================================================================
    # TAB: Files & Outputs
    # ==================================================================
    ttk.Label(tab_files, text="Choose which intermediate and cache files "
                              "survive after the run. stats.txt and the "
                              "four 2-D maps are always written - there is "
                              "no flag to skip them.",
              justify="left", wraplength=560, foreground="#444"
              ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

    delete_laz_var = tk.BooleanVar(value=False)
    delete_laz_cb = ttk.Checkbutton(tab_files, text="Delete source LAZ file(s) after processing",
                                    variable=delete_laz_var)
    delete_laz_cb.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 4))
    _tip(delete_laz_cb, "Deletes each tile's source .laz right after that "
                        "tile is voxelized - per-tile, not gated on the "
                        "whole run finishing, so stopping or crashing "
                        "partway through still leaves the already-processed "
                        "tiles' files deleted. In shard mode this ALSO does "
                        "a final sweep removing EVERY remaining .laz/.las in "
                        "the tile folder, not just this run's tiles - be "
                        "careful if that folder holds tiles for other runs "
                        "too. Applies in both File and Area mode.")

    columns_enabled_var = tk.BooleanVar(value=True)
    columns_row = ttk.Frame(tab_files)
    columns_row.grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 4))
    columns_cb = ttk.Checkbutton(columns_row, text="Generate per-column diagnostics (columns/ folder)",
                                 variable=columns_enabled_var)
    columns_cb.pack(side="left")
    _tip(columns_cb, "Diagnostic figures for the column structure (sample "
                     "columns, most-complex columns, interval-count "
                     "histogram, one representative column per class) and, "
                     "for 'top'/'all', one PNG per column. Unchecking this "
                     "is the same as --columns-mode skip: no columns/ folder "
                     "is written.")
    columns_mode_var = tk.StringVar(value="diag")
    columns_detail_dd = ttk.Combobox(columns_row, textvariable=columns_mode_var,
                                     values=["diag", "top", "all"],
                                     state="readonly", width=7)
    columns_detail_dd.pack(side="left", padx=(10, 6))
    _tip(columns_detail_dd, "diag: 4 diagnostic PNGs only (sample columns, "
                            "most-complex overview (up to 24 panels), interval-count histogram, "
                            "one representative column per class) - no "
                            "per-column folder. top: + PNGs for the N most "
                            "complex columns. all: + a PNG for every "
                            "occupied column (slow, large area runs only).")
    top_n_var = tk.StringVar(value="50")
    topn_label = ttk.Label(columns_row, text="top N")
    topn_spin = ttk.Spinbox(columns_row, textvariable=top_n_var, from_=1, to=100000, width=8)
    all_max_var = tk.StringVar(value="")
    allmax_label = ttk.Label(columns_row, text="all cap")
    allmax_spin = ttk.Spinbox(columns_row, textvariable=all_max_var, from_=0, to=100000000, width=10)
    _tip(allmax_spin,
         "Optional --columns-all-max: cap on the number of per-column PNGs in "
         "'all' mode (blank = the 500000 default; 0 = genuinely uncapped). Only "
         "used when the detail mode is 'all'.")
    _last_columns_detail = {"v": "diag"}

    def _sync_topn(*_):
        """Show the 'top N' spinbox only in 'top' mode and the 'all cap'
        spinbox only in 'all' mode, both gated on column diagnostics being
        enabled."""
        mode = columns_mode_var.get() if columns_enabled_var.get() else "skip"
        if mode == "top":
            topn_label.pack(side="left")
            topn_spin.pack(side="left", padx=(2, 0))
        else:
            topn_label.pack_forget()
            topn_spin.pack_forget()
        if mode == "all":
            allmax_label.pack(side="left", padx=(10, 0))
            allmax_spin.pack(side="left", padx=(2, 0))
        else:
            allmax_label.pack_forget()
            allmax_spin.pack_forget()

    def _sync_columns_enabled(*_):
        """Couple the columns checkbox to the detail dropdown: enabling
        restores the last real detail mode and re-enables the dropdown,
        disabling remembers the current mode, forces 'skip' and greys the
        dropdown out. Ends by re-syncing the top-N widgets."""
        if columns_enabled_var.get():
            columns_mode_var.set(_last_columns_detail["v"])
            columns_detail_dd.configure(state="readonly")
        else:
            if columns_mode_var.get() != "skip":
                _last_columns_detail["v"] = columns_mode_var.get()
            columns_mode_var.set("skip")
            columns_detail_dd.configure(state="disabled")
        _sync_topn()

    columns_enabled_var.trace_add("write", _sync_columns_enabled)
    columns_mode_var.trace_add("write", _sync_topn)

    # -- File-mode-only file controls --
    files_file_frame = ttk.Frame(tab_files)

    shards_var = tk.BooleanVar(value=False)
    shards_cb = ttk.Checkbutton(
        files_file_frame,
        text="Save the tile's store as a shard (shards/)",
        variable=shards_var)
    shards_cb.pack(anchor="w", pady=(2, 0))
    _tip(shards_cb, "File mode only (--shards). Writes the tile's voxel store "
                    "as shards/<tile_stem>.npz plus shards/manifest.json, the "
                    "same format an area run writes, so the single tile is "
                    "readable by merge_streaming, shard_diagnostics and "
                    "archive_cli. One run writes one shard: keep a fresh "
                    "output dir per tile.")

    keep_raw_store_single_var = tk.BooleanVar(value=False)
    keep_raw_cb_single = ttk.Checkbutton(
        files_file_frame,
        text="Keep the raw store (store_raw/) for --resume-from-store",
        variable=keep_raw_store_single_var)
    keep_raw_cb_single.pack(anchor="w", pady=(6, 0))
    _tip(keep_raw_cb_single,
         "File mode only (--keep-raw-store). Writes the tile's grid as the raw "
         "memory-mappable directory <out>/store_raw/ plus its run_params.json, "
         "so area_cli --resume-from-store (and this dialog's Continue...) can "
         "re-run the output stages - or turn the tile into area.npz - later "
         "without re-reading the LAZ.")

    # -- Area-mode-only file/cache controls --
    files_area_frame = ttk.Frame(tab_files)

    keep_npz_var = tk.BooleanVar(value=True)
    keep_npz_cb = ttk.Checkbutton(
        files_area_frame,
        text="Keep voxel grid cache (area.npz / shards/) for reuse",
        variable=keep_npz_var)
    keep_npz_cb.pack(anchor="w", pady=(2, 0))
    _tip(keep_npz_cb, "Keeps the built voxel grid on disk so 2-D maps or "
                      "the 3-D viewer can be re-rendered later without "
                      "re-voxelizing every tile. Unchecking passes "
                      "--no-save-store, so in a plain area run area.npz is "
                      "never written at all (not written then deleted); in "
                      "shard mode it also removes the shards/ cache after "
                      "the merge and deliverables are done.")

    keep_raw_store_var = tk.BooleanVar(value=False)
    keep_raw_cb = ttk.Checkbutton(
        files_area_frame,
        text="Keep per-stage store (store_raw/) for --resume-from-store",
        variable=keep_raw_store_var)
    keep_raw_cb.pack(anchor="w", pady=(6, 0))
    _tip(keep_raw_cb, "store_raw/ is the memory-mapped copy each output "
                      "stage (stats, maps, columns, 3-D) reads from when "
                      "'Isolate output stages' is on. Deleted after a fully "
                      "successful run unless you tick this OR set "
                      "'Intermediates policy' to keep. Tick this if you "
                      "want to re-run just the output stages later via "
                      "Continue.../--resume-from-store, without re-voxelizing.")

    group_var = tk.BooleanVar(value=True)
    group_cb = ttk.Checkbutton(
        files_area_frame,
        text="Enable grouping (merge consecutive same-class intervals)",
        variable=group_var)
    group_cb.pack(anchor="w", pady=(6, 0))
    _tip(group_cb, "Merges vertically-consecutive intervals of the same "
                   "class into one run before visualising (smaller store, "
                   "fewer 3-D boxes). When on, a pre-merge copy is written "
                   "to area_raw.npz during the run; keep it with the "
                   "'Keep pre-grouping store' box below. "
                   "--no-group-intervals renders the raw store directly "
                   "instead (and no area_raw.npz is produced).")

    group_gap_row = ttk.Frame(files_area_frame)
    group_gap_row.pack(anchor="w", fill="x", pady=(6, 0))
    ttk.Label(group_gap_row, text="Grouping gap (m):").pack(side="left")
    group_gap_var = tk.StringVar(value="")
    group_gap_entry = ttk.Entry(group_gap_row, textvariable=group_gap_var,
                                width=8)
    group_gap_entry.pack(side="left", padx=(6, 0))
    _tip(group_gap_entry,
         "Optional --group-gap: only merge vertically-consecutive same-class "
         "intervals across empty gaps up to this many metres. Blank = merge "
         "across any gap (the default). Smaller values keep genuinely "
         "separate objects apart (e.g. ground under a distinct canopy). Only "
         "used when grouping is on; disabled otherwise.")

    keep_area_raw_var = tk.BooleanVar(value=False)
    keep_area_raw_cb = ttk.Checkbutton(
        files_area_frame,
        text="Keep pre-grouping store (area_raw.npz) for re-grouping later",
        variable=keep_area_raw_var)
    keep_area_raw_cb.pack(anchor="w", pady=(6, 0))
    _tip(keep_area_raw_cb,
         "area_raw.npz is the ungrouped store written just before grouping. "
         "By default (Intermediates policy = auto) it is DELETED again on a "
         "successful run; tick this (--keep-area-raw) to retain it so you "
         "can re-run grouping later with a different gap without "
         "re-voxelizing. Only meaningful when grouping AND 'Keep voxel grid "
         "cache' are both on - it is disabled otherwise, because then no "
         "area_raw.npz is produced. In shard mode this runs a separate bounded "
         "raw merge so area_raw.npz sits alongside the grouped area.npz "
         "(peak RAM = max(raw, grouped), never their sum).\n\n"
         "CAVEAT: the ungrouped store is the LARGEST artefact a run "
         "produces, and this asks for it twice on disk - once as the "
         "scratch store directory the extra pass assembles, once as the "
         ".npz - plus a second full pass over the shards. It is a "
         "disk-and-time bill, not a RAM risk: the pass is out of core and "
         "peak RAM stays one merge band. Nothing is lost by declining it - "
         "the raw data already lives in the per-tile shards, and an "
         "ungrouped store can be rebuilt later over a smaller sub-area "
         "(Continue... / --resume-shards with grouping off).")

    def _sync_keep_area_raw(*_):
        """Enable 'Keep pre-grouping store' only while grouping and the voxel
        grid cache are both on, and the grouping-gap entry only while
        grouping is on."""
        # area_raw.npz is only written when grouping is on AND the store is
        # being saved; keeping it makes no sense (and can't work) otherwise.
        if group_var.get() and keep_npz_var.get():
            keep_area_raw_cb.configure(state="normal")
        else:
            keep_area_raw_cb.configure(state="disabled")
        # The gap only applies while grouping is on.
        group_gap_entry.configure(
            state="normal" if group_var.get() else "disabled")

    group_var.trace_add("write", _sync_keep_area_raw)
    keep_npz_var.trace_add("write", _sync_keep_area_raw)

    interm_row = ttk.Frame(files_area_frame)
    interm_row.pack(anchor="w", fill="x", pady=(6, 0))
    ttk.Label(interm_row, text="Intermediates policy:").pack(side="left")
    interm_var = tk.StringVar(value="auto")
    interm_dd = ttk.Combobox(interm_row, textvariable=interm_var, state="readonly",
                             width=8, values=("auto", "keep", "delete"))
    interm_dd.pack(side="left", padx=(6, 0))
    _tip(interm_dd, "Bulk policy for derived caches at end of run: "
                    "store_raw/ (per-stage mmap store), area_raw.npz "
                    "(pre-grouping store), and templaz/ (streaming-mode "
                    "temp tile downloads). "
                    "auto (default) = delete them on full success; keep "
                    "everything and print a resume command if any stage "
                    "failed. keep = never delete. delete = always delete, "
                    "even after a failure. 'Keep per-stage store', 'Keep "
                    "pre-grouping store' above and 'Delete source LAZ' "
                    "override this for their own artefact.")

    merge_shards_var = tk.BooleanVar(value=True)
    merge_cb = ttk.Checkbutton(
        files_area_frame,
        text="Merge shards after run (area.npz; columns/ needs no merge)",
        variable=merge_shards_var)
    merge_cb.pack(anchor="w", pady=(6, 0))
    _tip(merge_cb, "Only relevant if you pick 'Shard beyond RAM' in the "
                   "pre-flight dialog when you click Run: after the "
                   "per-tile shard loop, load every shard and merge into "
                   "one store to write area.npz. Peak RAM during this phase "
                   "is one merge band, not the merged store. Unticking it "
                   "makes the run shard-only: stats.txt, the mosaic maps, "
                   "the full and ROI 3-D views and columns/ are still "
                   "written - they are computed from the shards - but "
                   "area.npz, area_raw.npz, store_raw/ and the streaming "
                   "viewer are not. The CLI refuses those flags rather than "
                   "quietly merging anyway, so the 3-D tab withdraws its "
                   "'streamable' mode while this is unticked.")

    merge_note_var = tk.StringVar(value="")
    merge_note_lbl = ttk.Label(files_area_frame, textvariable=merge_note_var,
                               foreground="#a86a00", wraplength=560,
                               justify="left")
    merge_note_lbl.pack(anchor="w", padx=(20, 0))

    # The pair is no longer a constraint, only an explanation: the columns
    # controls stay live with merging off, and the note says where the figures
    # will be computed - from the shards, at the cost of one extra pass over
    # them, instead of from a merged store nobody asked to build.
    def _sync_merge_columns(*_):
        """Refresh the note under 'Merge shards' via couple_columns_to_merge:
        it explains where the column figures come from when merging is off
        in area mode, and is blank otherwise (file mode never shards)."""
        # Only area runs can shard, so the file-mode dialog never shows the
        # note: a file run has no shards to compute anything from.
        merging = bool(merge_shards_var.get()) or mode_var.get() != "area"
        _, note = couple_columns_to_merge(merging, columns_mode_var.get())
        merge_note_var.set(note)

    merge_shards_var.trace_add("write", _sync_merge_columns)
    mode_var.trace_add("write", _sync_merge_columns)
    # The note also has to follow the columns controls themselves: switching
    # the diagnostics off is what makes it irrelevant.
    columns_enabled_var.trace_add("write", _sync_merge_columns)
    columns_mode_var.trace_add("write", _sync_merge_columns)

    files_area_frame.grid(row=3, column=0, columnspan=2, sticky="w")

    # -- Always-written deliverables, shown for transparency (not editable) --
    always_row = ttk.Frame(tab_files)
    always_row.grid(row=4, column=0, columnspan=2, sticky="w", pady=(14, 0))
    ttk.Label(always_row, text="Always written (not optional):").pack(anchor="w")
    _always_stats = tk.BooleanVar(value=True)
    _always_maps = tk.BooleanVar(value=True)
    ttk.Checkbutton(always_row, text="stats.txt (run summary)",
                    variable=_always_stats, state="disabled").pack(anchor="w", pady=(2, 0))
    ttk.Checkbutton(always_row,
                    text="4 2-D maps (max height, dominant class, topmost "
                         "interval's class, interval count)",
                    variable=_always_maps, state="disabled").pack(anchor="w")

    # ==================================================================
    # TAB: 3-D Visualiser
    # ==================================================================
    viz_row = ttk.Frame(tab_viz)
    viz_row.pack(fill="x")
    viz3d_var = tk.BooleanVar(value=False)
    viz3d_cb = ttk.Checkbutton(viz_row, text="Enable 3-D visualiser (Three.js HTML)",
                               variable=viz3d_var)
    viz3d_cb.pack(side="left")
    _tip(viz3d_cb, "Exports an interactive HTML/Three.js viewer of the "
                   "voxel grid alongside the usual 2-D/columns output.")
    viz3d_mode_var = tk.StringVar(value="singleton")
    viz3d_mode_dd = ttk.Combobox(viz_row, textvariable=viz3d_mode_var,
                                  values=["singleton", "streamable"],
                                  state="readonly", width=12)
    ttk.Label(viz_row, text="mode:").pack(side="left", padx=(12, 4))
    _tip(viz3d_mode_dd, "singleton: one self-contained HTML file. If the "
                        "box count would exceed 'max boxes' it auto-thins "
                        "by striding columns - the whole area still shows, "
                        "just sparser, nothing is cut off. streamable: "
                        "every voxel, tiled and streamed to the browser on "
                        "demand - no stride, no cap, but needs the small "
                        "HTTP server below for big payloads. streamable is "
                        "exported from the merged store, so it is withdrawn "
                        "when 'Merge shards after run' is unticked.")

    stream_note_var = tk.StringVar(value="")
    ttk.Label(tab_viz, textvariable=stream_note_var, foreground="#a86a00",
              wraplength=560, justify="left").pack(anchor="w", padx=(12, 0))

    # -- Singleton options (shown when mode=single) --
    viz_opts = ttk.Frame(tab_viz, padding=(12, 8, 0, 0))
    ttk.Label(viz_opts, text="max boxes").grid(row=0, column=0)
    max_boxes_var = tk.StringVar(value="5000000")
    ttk.Entry(viz_opts, textvariable=max_boxes_var, width=10).grid(row=0, column=1, padx=(2, 12))
    ttk.Label(viz_opts, text="ROI size (m)").grid(row=0, column=2)
    roi_size_var = tk.StringVar(value="200")
    ttk.Entry(viz_opts, textvariable=roi_size_var, width=8).grid(row=0, column=3, padx=(2, 12))
    ttk.Label(viz_opts, text="ROI cx").grid(row=1, column=0, pady=(4, 0))
    roi_cx_var = tk.StringVar(value="")
    ttk.Entry(viz_opts, textvariable=roi_cx_var, width=10).grid(row=1, column=1, padx=(2, 12), pady=(4, 0))
    ttk.Label(viz_opts, text="ROI cy").grid(row=1, column=2, pady=(4, 0))
    roi_cy_var = tk.StringVar(value="")
    ttk.Entry(viz_opts, textvariable=roi_cy_var, width=10).grid(row=1, column=3, padx=(2, 12), pady=(4, 0))
    ttk.Label(viz_opts, text="(blank centre = middle of tile/area)").grid(
        row=2, column=0, columnspan=4, sticky="w", pady=(2, 0))
    full_var = tk.BooleanVar(value=True)
    roi_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(viz_opts, text="render full view", variable=full_var).grid(
        row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))
    ttk.Checkbutton(viz_opts, text="render ROI view", variable=roi_var).grid(
        row=3, column=2, columnspan=2, sticky="w", pady=(4, 0))

    # -- Streaming options (shown when mode=streamable) --
    stream_opts = ttk.Frame(tab_viz, padding=(12, 8, 0, 0))
    ttk.Label(stream_opts, text="max GPU instances").grid(row=0, column=0)
    max_instances_var = tk.StringVar(value="4000000")
    ttk.Entry(stream_opts, textvariable=max_instances_var, width=10).grid(row=0, column=1, padx=(2, 12))
    ttk.Label(stream_opts, text="inline threshold (MB)").grid(row=0, column=2)
    inline_thresh_var = tk.StringVar(value="64")
    ttk.Entry(stream_opts, textvariable=inline_thresh_var, width=8).grid(row=0, column=3, padx=(2, 12))
    ttk.Label(stream_opts, text="tile size (m)").grid(row=1, column=0, pady=(4, 0))
    tile_m_var = tk.StringVar(value="64")
    ttk.Entry(stream_opts, textvariable=tile_m_var, width=10).grid(
        row=1, column=1, padx=(2, 12), pady=(4, 0))
    ttk.Label(stream_opts,
              text=("Streamable mode exports EVERY voxel - no stride, no cap. "
                    "'max GPU instances' is a working-set budget: the "
                    "viewer keeps the\ntiles nearest the camera resident and evicts "
                    "the rest as you move. Big payloads need\n"
                    "python -m voxelizer.serve_voxel_html <dir>  "
                    "(it answers HTTP Range)."),
              justify="left", foreground="#555").grid(
        row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))

    def _sync_viz(*_):
        """Show the mode dropdown and the matching option panel when the 3-D
        visualiser is ticked, hide all of them otherwise, then refresh the
        summary line."""
        if viz3d_var.get():
            viz3d_mode_dd.pack(side="left")
            _sync_viz_mode()
        else:
            viz3d_mode_dd.pack_forget()
            viz_opts.pack_forget()
            stream_opts.pack_forget()
        _update_summary()

    def _sync_viz_mode(*_):
        """Swap the singleton and streamable option panels under the viz row
        to match the selected 3-D mode; does nothing while 3-D is off."""
        if not viz3d_var.get():
            return
        is_stream = (viz3d_mode_var.get() == "streamable")
        if is_stream:
            viz_opts.pack_forget()
            stream_opts.pack(fill="x", after=viz_row)
        else:
            stream_opts.pack_forget()
            viz_opts.pack(fill="x", after=viz_row)

    viz3d_var.trace_add("write", _sync_viz)
    viz3d_mode_var.trace_add("write", _sync_viz_mode)

    # A shard-only run cannot export the streaming viewer, and area_cli
    # refuses the pair before any work starts, so the dropdown withdraws the
    # option instead of offering a command line that will be rejected. Only
    # area runs can shard, so file mode keeps both modes on offer.
    def _sync_merge_stream(*_):
        """Withdraw the 'streamable' 3-D mode from the dropdown when an area
        run has 'Merge shards' unticked, fall the selected mode back via
        couple_stream_to_merge if needed, and set the explanatory note."""
        shard_only = (mode_var.get() == "area"
                      and not bool(merge_shards_var.get()))
        viz3d_mode_dd.configure(
            values=["singleton"] if shard_only
            else ["singleton", "streamable"])
        mode, note = couple_stream_to_merge(not shard_only,
                                            viz3d_mode_var.get())
        if viz3d_mode_var.get() != mode:
            # Re-enters this callback through the trace below; that pass
            # settles on the same mode and clears the note, which is why the
            # note is set afterwards rather than before.
            viz3d_mode_var.set(mode)
        stream_note_var.set(note)

    merge_shards_var.trace_add("write", _sync_merge_stream)
    mode_var.trace_add("write", _sync_merge_stream)
    viz3d_mode_var.trace_add("write", _sync_merge_stream)

    # ==================================================================
    # TAB: Diagnostics
    # ==================================================================
    diag_var = tk.BooleanVar(value=False)
    dash_var = tk.BooleanVar(value=False)
    diag_cb = ttk.Checkbutton(tab_diag, text="Live diagnostics", variable=diag_var)
    diag_cb.pack(anchor="w")
    _tip(diag_cb, "Wraps the job with voxel_runner_diagnos.py so CPU/RAM "
                  "ticks stream into this window's log (and the terminal) "
                  "while it runs. Available in both File and Area mode.")
    dash_cb = ttk.Checkbutton(tab_diag, text="Open HTML dashboard (--serve)",
                              variable=dash_var, state="disabled")
    dash_cb.pack(anchor="w", pady=(6, 0))
    _tip(dash_cb, "Requires 'Live diagnostics'. Also opens a live browser "
                  "dashboard; the job process stays alive to serve it "
                  "until you click Stop.")

    def _sync_diag(*_):
        """Enable the HTML-dashboard checkbox only while 'Live diagnostics'
        is on; turning diagnostics off also unticks the dashboard."""
        if diag_var.get():
            dash_cb.configure(state="normal")
        else:
            dash_var.set(False)
            dash_cb.configure(state="disabled")

    diag_var.trace_add("write", _sync_diag)

    # ==================================================================
    # TAB: Advanced
    # ==================================================================
    isolate_var = tk.BooleanVar(value=True)
    isolate_cb = ttk.Checkbutton(
        tab_adv, text="Isolate output stages", variable=isolate_var)
    isolate_cb.pack(anchor="w")
    _tip(isolate_cb, "Each stage (stats, area.npz, maps, columns, 3-D) runs "
                     "in its own child process attached to a memory-mapped "
                     "on-disk store. A native crash then costs only that "
                     "stage - the run survives and every other output "
                     "still gets written. On by default. (This is "
                     "--isolate-stages: output rendering. Distinct from the "
                     "per-tile isolation below, which is about voxelizing.)")

    stage_to_row = ttk.Frame(tab_adv)
    stage_to_row.pack(anchor="w", fill="x", pady=(6, 0))
    ttk.Label(stage_to_row, text="Stage timeout (s):").pack(side="left")
    stage_timeout_var = tk.StringVar(value="")
    stage_timeout_entry = ttk.Entry(stage_to_row, textvariable=stage_timeout_var,
                                    width=10)
    stage_timeout_entry.pack(side="left", padx=(6, 0))
    ttk.Label(stage_to_row, text="blank = no limit", foreground="#555"
              ).pack(side="left", padx=(8, 0))
    _tip(stage_timeout_entry,
         "Optional --stage-timeout: wall-clock limit per isolated stage; the "
         "child is killed and retried once when it expires. Blank (the "
         "default) means no limit, deliberately: a fixed limit cannot tell a "
         "big stage from a stuck one, because the value that separates them "
         "scales with the store. The stage logs and "
         "faultlogs are the hang evidence; this is opt-in for the case where "
         "you already know what a stage should cost.")

    ttk.Separator(tab_adv, orient="horizontal").pack(fill="x", pady=(12, 8))
    ttk.Label(tab_adv,
              text="Shard-mode crash resilience - applies when you pick "
                   "'Shard beyond RAM' at the pre-flight dialog, and when "
                   "resuming a sharded run via Continue:",
              justify="left", wraplength=580, foreground="#444"
              ).pack(anchor="w")

    isolate_tiles_var = tk.BooleanVar(value=True)
    isolate_tiles_cb = ttk.Checkbutton(
        tab_adv,
        text="Isolate each tile in a child process (survive native decoder crashes)",
        variable=isolate_tiles_var)
    isolate_tiles_cb.pack(anchor="w", pady=(6, 0))
    _tip(isolate_tiles_cb,
         "Shard mode only (--isolate-tiles). Voxelize each tile in its own "
         "child process, so a native LAZ-decoder crash (access violation) "
         "costs just that one tile - recorded in shards/failed_tiles.json - "
         "instead of killing the whole run. Strongly recommended for "
         "metropolis-scale runs, and required for the failed-tile retry "
         "below to do anything.")

    retry_lazrs_var = tk.BooleanVar(value=False)
    retry_lazrs_cb = ttk.Checkbutton(
        tab_adv,
        text="Retry a crashed tile once with the lazrs decoder",
        variable=retry_lazrs_var)
    retry_lazrs_cb.pack(anchor="w", pady=(6, 0))
    _tip(retry_lazrs_cb,
         "Requires 'Isolate each tile' (--retry-lazrs). After a child crash, "
         "retry that tile once with the single-thread lazrs backend before "
         "recording it as failed. The first attempt always uses the pinned "
         "LASzip backend (the parallel lazrs backend is refused outright), "
         "so this is a second opinion from a different decoder "
         "implementation, not from a single-threaded one.")

    band_row = ttk.Frame(tab_adv)
    band_row.pack(anchor="w", fill="x", pady=(8, 0))
    ttk.Label(band_row, text="Merge band intervals:").pack(side="left")
    band_var = tk.StringVar(value="")
    band_entry = ttk.Entry(band_row, textvariable=band_var, width=12)
    band_entry.pack(side="left", padx=(6, 0))
    ttk.Label(band_row, text="blank = library default", foreground="#555"
              ).pack(side="left", padx=(8, 0))
    _tip(band_entry,
         "Optional --merge-band-intervals: the RAM/speed dial for the shard "
         "merge. The merge is out of core - store_raw/ is assembled one band "
         "of keys at a time - and this is how many intervals a band holds. "
         "Blank uses the library default (about 1.4 GB of peak). Halve it on "
         "a small machine, since each band is assembled whole in RAM; raise "
         "it to re-read the compressed shards fewer times, because a shard is "
         "read once per band its column range touches.")

    ttk.Separator(tab_adv, orient="horizontal").pack(fill="x", pady=(12, 8))
    ttk.Label(tab_adv,
              text="Run only selected output stages (--only-stage). Unticked = "
                   "every enabled stage runs. A stage must be enabled by its "
                   "own flag to be selectable, so persist_npz needs store "
                   "saving on and col_diag needs column diagnostics not "
                   "skipped:",
              justify="left", wraplength=580, foreground="#444"
              ).pack(anchor="w")

    only_stage_vars = {}
    for _stage, _label in (("stats", "stats.txt"),
                           ("persist_npz", "area.npz"),
                           ("maps", "2-D maps"),
                           ("col_diag", "column diagnostics"),
                           ("viz3d_roi", "3-D ROI view"),
                           ("viz3d_full", "3-D full view"),
                           ("viz3d_stream", "streaming 3-D viewer")):
        _v = tk.BooleanVar(value=False)
        _cb = ttk.Checkbutton(tab_adv, text=f"only: {_label}",
                              variable=_v)
        _cb.pack(anchor="w", pady=(2, 0))
        only_stage_vars[_stage] = _v

    # ------------------------------------------------------------------
    # Live status: running summary + inline validation message, always
    # visible below the tabs regardless of which one is active.
    # ------------------------------------------------------------------
    status_frame = ttk.Frame(root, padding=(12, 6, 12, 0))
    status_frame.pack(fill="x")
    summary_var = tk.StringVar(value="")
    ttk.Label(status_frame, textvariable=summary_var, foreground="#333"
              ).pack(anchor="w")
    validation_var = tk.StringVar(value="")
    ttk.Label(status_frame, textvariable=validation_var, foreground="#b00020"
              ).pack(anchor="w")

    def _update_summary(*_):
        """Rebuild the one-line status summary: the chosen file or the bbox
        size in km (plus streaming/auto-download), the cell sizes when they
        parse, and whether 3-D and diagnostics are on."""
        if mode_var.get() == "file":
            name = Path(path_var.get()).name if path_var.get().strip() else "(no file chosen)"
            base = f"File: {name}"
        else:
            try:
                w = (float(xmax_var.get()) - float(xmin_var.get())) / 1000
                h = (float(ymax_var.get()) - float(ymin_var.get())) / 1000
                base = f"Area: {w:.2f} x {h:.2f} km"
            except ValueError:
                base = "Area: (invalid bounding box)"
            if stream_var.get():
                base += " - streaming"
            elif download_var.get():
                base += " - auto-download"
        try:
            cxy = float(cell_xy_var.get())
            cz = float(cell_z_var.get())
            base += f" - cell {cxy:g}/{cz:g} m"
        except ValueError:
            pass
        if viz3d_var.get():
            base += " - 3-D on"
        if diag_var.get():
            base += " - diagnostics on"
        summary_var.set(base)

    # ------------------------------------------------------------------
    # Live validation: bbox + cell-size fields turn red and a one-line
    # message appears here as soon as the value is unusable, instead of
    # only when Run is clicked.
    # ------------------------------------------------------------------
    def _float_ok(s: str) -> bool:
        """True if *s* parses as a float."""
        try:
            float(s)
            return True
        except ValueError:
            return False

    def _mark(entry: ttk.Entry, ok: bool):
        """Switch *entry* between the normal and the red 'Invalid' style."""
        entry.configure(style="TEntry" if ok else "Invalid.TEntry")

    def _validate_all(*_):
        """Live validation of the cell sizes (positive numbers) and, in area
        mode, the bounding box (numbers with min <= max): marks the offending
        entries red, shows the first problem on the status line, then
        refreshes the summary."""
        msgs: list[str] = []

        cxy_ok = _float_ok(cell_xy_var.get()) and float(cell_xy_var.get()) > 0
        cz_ok = _float_ok(cell_z_var.get()) and float(cell_z_var.get()) > 0
        _mark(cell_xy_entry, cxy_ok)
        _mark(cell_z_entry, cz_ok)
        if not cxy_ok:
            msgs.append("cell_xy must be a positive number")
        if not cz_ok:
            msgs.append("cell_z must be a positive number")

        if mode_var.get() == "area":
            vals = {}
            ok_all = True
            for name, e in bbox_entries.items():
                ok = _float_ok(bbox_vars[name].get())
                _mark(e, ok)
                ok_all = ok_all and ok
                if ok:
                    vals[name] = float(bbox_vars[name].get())
            if ok_all:
                bad_order = vals["xmin"] > vals["xmax"] or vals["ymin"] > vals["ymax"]
                if bad_order:
                    msgs.append("bounding box: min must be <= max")
                    for e in bbox_entries.values():
                        _mark(e, False)
            else:
                msgs.append("bounding box coordinates must be numbers")

        validation_var.set(msgs[0] if msgs else "")
        _update_summary()

    for var in (cell_xy_var, cell_z_var, xmin_var, ymin_var, xmax_var, ymax_var):
        var.trace_add("write", _validate_all)
    for var in (path_var, stream_var, download_var, viz3d_var, diag_var):
        var.trace_add("write", _update_summary)

    # ------------------------------------------------------------------
    # Output dir
    # ------------------------------------------------------------------
    out_frame = ttk.Frame(root, padding=(12, 8, 12, 0))
    out_frame.pack(fill="x")
    ttk.Label(out_frame, text="Output dir:").pack(side="left")
    out_var = tk.StringVar(value=str(next_run_output_dir(Path("outputs"), "area_output")))
    ttk.Entry(out_frame, textvariable=out_var, width=48).pack(side="left", padx=(6, 0), fill="x", expand=True)
    ttk.Button(out_frame, text="Browse...",
               command=lambda: out_var.set(filedialog.askdirectory() or out_var.get())
               ).pack(side="left", padx=(6, 0))
    resume_prev_btn = ttk.Button(out_frame, text="Resume previous run...",
                                 command=lambda: _resume_previous())
    resume_prev_btn.pack(side="left", padx=(12, 0))
    _tip(resume_prev_btn,
         "Point at any earlier run's output folder to pick it back up: "
         "resume its unfinished tiles (shard mode) or re-run individual "
         "output stages from a kept store_raw/, without re-entering the "
         "bounding box or re-voxelizing what already finished.")

    # ------------------------------------------------------------------
    # Log (scrollable, colour-tagged by severity) + Run/Stop/Close
    # ------------------------------------------------------------------
    log_frame = ttk.Frame(root, padding=(12, 8, 12, 0))
    log_frame.pack(fill="both", expand=True)
    log_widget = tk.Text(log_frame, height=14, width=82, state="disabled", wrap="word")
    log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=log_widget.yview)
    log_widget.configure(yscrollcommand=log_scroll.set)
    log_widget.pack(side="left", fill="both", expand=True)
    log_scroll.pack(side="right", fill="y")
    log_widget.tag_configure("err", foreground="#b00020")
    log_widget.tag_configure("warn", foreground="#a86a00")
    log_widget.tag_configure("ok", foreground="#0a7d29")
    log_q: "queue.Queue[str]" = queue.Queue()

    def log(msg):
        """Queue *msg* for the log pane (thread-safe; drained by _drain)."""
        log_q.put(str(msg))

    def _tag_for(line: str) -> str | None:
        """Pick the colour tag for a log line: 'err' for ERROR/TRACEBACK,
        'warn' for WARNING, 'ok' for '(OK)', else None."""
        upper = line.upper()
        if "ERROR" in upper or "TRACEBACK" in upper:
            return "err"
        if "WARNING" in upper:
            return "warn"
        if "(OK)" in line:
            return "ok"
        return None

    def _drain():
        """Move every queued line into the log Text widget (colour-tagged,
        scrolled to the end) and reschedule itself every 120 ms."""
        while not log_q.empty():
            line = log_q.get_nowait()
            log_widget.configure(state="normal")
            tag = _tag_for(line)
            if tag:
                log_widget.insert("end", line + "\n", tag)
            else:
                log_widget.insert("end", line + "\n")
            log_widget.see("end")
            log_widget.configure(state="disabled")
        root.after(120, _drain)

    root.after(120, _drain)

    # ------------------------------------------------------------------
    # Mode-driven visibility (Input tab panels + Files & Outputs rows +
    # Diagnostics availability, which is shared by both modes).
    # ------------------------------------------------------------------
    def _show_mode(*_):
        """Swap the Input tab between the file and area panels, show the
        streaming/download/tile-folder rows that apply to the area sub-mode,
        show or hide the area-only Files & Outputs rows, then re-validate
        and refresh the summary."""
        is_area = mode_var.get() == "area"
        is_stream = is_area and stream_var.get()
        (area_frame if is_area else file_frame).pack(fill="x")
        (file_frame if is_area else area_frame).pack_forget()
        if is_area:
            stream_row.pack(fill="x")
            if is_stream:
                stream_json_row.pack(fill="x")
                dl_frame.pack_forget()
                dir_row.grid_remove()
            else:
                stream_json_row.pack_forget()
                dir_row.grid()
                dl_frame.pack(fill="x")
            files_area_frame.grid()
            files_file_frame.grid_remove()
        else:
            stream_row.pack_forget()
            stream_json_row.pack_forget()
            dl_frame.pack_forget()
            files_area_frame.grid_remove()
            files_file_frame.grid()
        _validate_all()
        _update_summary()

    mode_var.trace_add("write", _show_mode)
    stream_var.trace_add("write", _show_mode)

    def _collect_viz_opts():
        """Return (do_full/do_roi/roi centres/etc.) parsed from the viz widgets."""

        def _opt_float(s):
            """Float from an entry string, or None when it is blank."""
            s = s.strip()
            return None if s == "" else float(s)

        mode = viz3d_mode_var.get()
        return dict(
            mode=mode,
            max_boxes=int(max_boxes_var.get()),
            roi_size=float(roi_size_var.get()),
            roi_cx=_opt_float(roi_cx_var.get()),
            roi_cy=_opt_float(roi_cy_var.get()),
            do_full=bool(full_var.get()),
            do_roi=bool(roi_var.get()),
            max_instances=int(max_instances_var.get()),
            tile_m=float(tile_m_var.get()),
            inline_threshold_mb=int(inline_thresh_var.get()),
        )

    # ------------------------------------------------------------------
    # Pre-flight dialog: ALWAYS shown before an area run.
    # ------------------------------------------------------------------
    def _preflight_dialog(est_text: str, default_budget_mb: int):
        """Modal cost dialog. Returns (choice, budget_mb); choice is one of
        'cancel' / 'continue' / 'shard'."""
        result = {"choice": "cancel", "budget": float(default_budget_mb)}
        dlg = tk.Toplevel(root)
        dlg.title("Pre-flight check - estimated resource cost")
        dlg.transient(root)
        dlg.grab_set()
        dlg.resizable(False, False)

        body = ttk.Frame(dlg, padding=12)
        body.pack(fill="both", expand=True)
        txt = tk.Text(body, height=est_text.count("\n") + 2, width=76,
                      font=("Courier New", 9))
        txt.insert("1.0", est_text)
        txt.configure(state="disabled")
        txt.pack(fill="x")

        brow = ttk.Frame(body)
        brow.pack(fill="x", pady=(10, 0))
        ttk.Label(brow, text="RAM budget (MEGABYTES):").pack(side="left")
        budget_var = tk.StringVar(value=str(int(default_budget_mb)))
        ttk.Entry(brow, textvariable=budget_var, width=10).pack(
            side="left", padx=(6, 0))
        ttk.Label(brow, text="used by the RSS watchdog and the "
                             "sharder").pack(side="left", padx=(8, 0))

        def _choose(choice):
            """Button handler of the pre-flight dialog: for 'continue' and
            'shard' validate the RAM budget (positive number, error box and
            stay open otherwise) and record it, then record *choice* and
            close the dialog."""
            if choice != "cancel":
                try:
                    b = float(budget_var.get())
                    if b <= 0:
                        raise ValueError
                except ValueError:
                    messagebox.showerror("Invalid budget",
                                         "RAM budget must be a positive "
                                         "number of megabytes.", parent=dlg)
                    return
                result["budget"] = b
            result["choice"] = choice
            dlg.destroy()

        btns_ = ttk.Frame(body)
        btns_.pack(fill="x", pady=(12, 0))
        ttk.Button(btns_, text="Cancel (refuse run)",
                   command=lambda: _choose("cancel")).pack(side="left")
        ttk.Button(btns_, text="Continue anyway (RAM watchdog)",
                   command=lambda: _choose("continue")).pack(side="left", padx=(8, 0))
        ttk.Button(btns_, text="Shard beyond RAM",
                   command=lambda: _choose("shard")).pack(side="left", padx=(8, 0))
        dlg.protocol("WM_DELETE_WINDOW", lambda: _choose("cancel"))
        root.wait_window(dlg)
        return result["choice"], result["budget"]

    # ------------------------------------------------------------------
    # Subprocess launcher: the GUI never shares an address space with a
    # heavy area job, so it can never be dragged into pagefile thrash.
    # ------------------------------------------------------------------
    proc_holder: dict = {"p": None}
    # Output dir of the most recently launched job, so that when it ends we can
    # inspect that directory for resumable state (store_raw/ or shards/).
    run_ctx: dict = {"out_dir": None}
    # The run dir + detection result the Continue button currently acts on.
    continue_ctx: dict = {"out_dir": None, "info": None}

    def _job_done(code):
        """Main-thread epilogue of a job child: clear the process slot,
        re-enable Run and 'Resume previous', disable Stop, log the exit code
        (None means the child never started) and re-probe the run's output
        dir for resumable state so the Continue button reflects it."""
        proc_holder["p"] = None
        run_btn.configure(state="normal")
        stop_btn.configure(state="disabled")
        resume_prev_btn.configure(state="normal")
        if code is not None:
            log(f"[job] exited with code {code}"
                + (" (OK)" if code == 0 else " - see log above"))
        # After any run ends (success, failure or Stop), see whether its output
        # dir left something to continue from, and light up the Continue button.
        if run_ctx["out_dir"]:
            _refresh_continue_state(Path(run_ctx["out_dir"]))

    def _launch_subprocess(cmd: list[str]):
        """Run *cmd* as the single job child: log the command line, flip the
        Run/Stop/Continue/Resume buttons into the running state and start a
        daemon thread that pipes the child's output into the log."""
        log("[job] " + " ".join(str(c) for c in cmd))

        def pump():
            """Worker thread: start the child (logging a failure and
            finishing via _job_done(None) if it cannot start), publish it in
            proc_holder, forward each output line to the log queue and the
            console, then wait and hand the return code to _job_done on the
            main thread."""
            try:
                p = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, encoding="utf-8", errors="replace")
            except Exception as exc:  # noqa: BLE001
                log(f"ERROR: could not start job: {type(exc).__name__}: {exc}")
                root.after(0, _job_done, None)
                return
            proc_holder["p"] = p
            try:
                for line in p.stdout:
                    text = line.rstrip("\n")
                    log_q.put(text)
                    # Also echo to the real console (the window scripts/run_area.bat
                    # opened) so the diagnostics ticks appear in the terminal
                    # too, not only in this log. sys.stdout is None under
                    # pythonw (no console) - guard for that.
                    if sys.stdout is not None:
                        try:
                            print(text, flush=True)
                        except Exception:  # noqa: BLE001
                            pass
            finally:
                p.wait()
                root.after(0, _job_done, p.returncode)

        run_btn.configure(state="disabled")
        stop_btn.configure(state="normal")
        continue_btn.configure(state="disabled")
        resume_prev_btn.configure(state="disabled")
        threading.Thread(target=pump, daemon=True).start()

    def _on_stop():
        """Stop button: terminate the live job child, if any, and start a
        watchdog thread that kills it should it not exit within 8 s."""
        p = proc_holder["p"]
        if p is None or p.poll() is not None:
            return
        log("[job] terminate requested ...")
        try:
            p.terminate()
        except Exception:  # noqa: BLE001
            pass

        def _killer():
            """Wait up to 8 s for the terminated child; kill it and log if
            it is still alive."""
            try:
                p.wait(timeout=8)
            except Exception:  # noqa: BLE001
                try:
                    p.kill()
                    log("[job] killed (did not exit within 8 s).")
                except Exception:  # noqa: BLE001
                    pass

        threading.Thread(target=_killer, daemon=True).start()

    # ------------------------------------------------------------------
    # Viewer servers.
    #
    # A server is not a job: it never finishes, and it must not disable Run
    # while it is up - two viewers and a running export are a perfectly
    # ordinary state. So servers get their own list rather than the single
    # proc_holder slot, and their own Stop. Every child in this list is
    # terminated when the window closes, so none outlives the GUI: a server
    # that does is an orphan holding a port, and the only cure is a task
    # manager.
    # ------------------------------------------------------------------
    servers: list = []

    def _start_server(cmd: list[str], label: str):
        """Start a viewer server child from *cmd* on a daemon thread; it is
        tracked in ``servers`` (not in the job slot) and its output reaches
        the log prefixed with *label*."""
        log(f"[serve] {' '.join(str(c) for c in cmd)}")

        def pump():
            """Worker thread: start the server child (logging a failure and
            giving up otherwise), add it to ``servers`` and refresh the Stop
            servers button, relay its output to the log, then on exit remove
            it from the list, log the return code and refresh the button
            again."""
            try:
                p = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, encoding="utf-8", errors="replace")
            except Exception as exc:  # noqa: BLE001
                log(f"ERROR: could not start {label}: "
                    f"{type(exc).__name__}: {exc}")
                return
            servers.append(p)
            root.after(0, _sync_server_buttons)
            try:
                for line in p.stdout:
                    log_q.put(f"[{label}] " + line.rstrip("\n"))
            finally:
                p.wait()
                if p in servers:
                    servers.remove(p)
                log(f"[serve] {label} stopped (code {p.returncode}).")
                root.after(0, _sync_server_buttons)

        threading.Thread(target=pump, daemon=True).start()

    def _stop_servers(*, quiet: bool = False):
        """Terminate every live viewer server. Safe to call with none up."""
        live = [p for p in list(servers) if p.poll() is None]
        if not live:
            return
        if not quiet:
            log(f"[serve] stopping {len(live)} server(s) ...")
        for p in live:
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass
        for p in live:
            try:
                p.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    p.kill()
                except Exception:  # noqa: BLE001
                    pass

    def _sync_server_buttons(*_):
        """Enable the 'Stop servers' button iff at least one viewer server is
        still running; tolerates being called before the button exists."""
        live = any(p.poll() is None for p in list(servers))
        try:
            stop_srv_btn.configure(state="normal" if live else "disabled")
        except Exception:  # noqa: BLE001
            pass  # called before the button exists (first construction pass)

    def _on_close():
        """The single shutdown path: no viewer server outlives the window.

        The job child is deliberately left alone - a metropolis run is hours
        of work and killing it because a window closed would be a surprise -
        but a server is pure overhead once nobody is looking at it, and an
        orphaned one keeps its port until the machine is rebooted or the
        process hunted down by hand.
        """
        _stop_servers(quiet=True)
        root.destroy()

    # ==================================================================
    # TAB: Export & Serve  (built here, after the process machinery it
    # drives; the notebook already holds the frame in its proper place)
    # ==================================================================
    ttk.Label(tab_export,
              text="What to do with a finished run: export its store to 3-D "
                   "Tiles, and put either kind of output in front of a "
                   "browser. Both exporters already write double-clickable "
                   ".cmd launchers beside their artifacts - these buttons are "
                   "the same thing without leaving the window.",
              justify="left", wraplength=580, foreground="#444"
              ).pack(anchor="w", pady=(0, 8))

    # -- 3-D Tiles export ---------------------------------------------
    ts_frame = ttk.LabelFrame(tab_export, text="Export to 3-D Tiles "
                                               "(tileset.json + .glb)",
                              padding=8)
    ts_frame.pack(fill="x")

    ts_src_row = ttk.Frame(ts_frame)
    ts_src_row.pack(fill="x")
    ttk.Label(ts_src_row, text="Run dir or .npz:").pack(side="left")
    ts_src_var = tk.StringVar(value=out_var.get())
    ts_src_entry = ttk.Entry(ts_src_row, textvariable=ts_src_var, width=44)
    ts_src_entry.pack(side="left", padx=(6, 0), fill="x", expand=True)
    _tip(ts_src_entry,
         "A run's output directory, or the path of a store file/directory "
         "directly. Inside a directory the form picked below decides what is "
         "exported: auto takes area.npz, then area_raw.npz, then store_raw/, "
         "then a lone single-tile shard. The resolved path is shown underneath, "
         "so there is no guessing about which store is being exported.")

    # Which on-disk form a DIRECTORY resolves to. A run can hold several at
    # once (area.npz + store_raw/), and the two export different box counts,
    # so the choice is explicit rather than implicit.
    ts_kind_row = ttk.Frame(ts_frame)
    ts_kind_row.pack(fill="x", pady=(4, 0))
    ttk.Label(ts_kind_row, text="store form:").pack(side="left")
    ts_kind_var = tk.StringVar(value="auto")
    ts_kind_dd = ttk.Combobox(
        ts_kind_row, textvariable=ts_kind_var,
        values=["auto", "npz", "dir"], state="readonly", width=6)
    ts_kind_dd.pack(side="left", padx=(6, 8))
    _tip(ts_kind_dd,
         "auto: area.npz, else area_raw.npz, else store_raw/, else a lone "
         "shard. npz: only a compressed .npz (never store_raw/). dir: only the "
         "raw store_raw/ directory, attached by memory map (never an .npz). "
         "Use npz or dir when a run holds both forms and auto would pick the "
         "other one.")

    def _browse_ts_src():
        """Directory chooser for the tileset source; stores the pick in the
        'Run dir or .npz' entry."""
        d = filedialog.askdirectory(title="Choose a run output directory")
        if d:
            ts_src_var.set(d)

    def _browse_ts_npz():
        """File chooser for a store .npz; stores the pick in the 'Run dir or
        .npz' entry."""
        f = filedialog.askopenfilename(
            title="Choose a store .npz",
            filetypes=[("Voxel store", "*.npz"), ("All files", "*.*")])
        if f:
            ts_src_var.set(f)

    ttk.Button(ts_src_row, text="Dir...", command=_browse_ts_src,
               width=7).pack(side="left", padx=(6, 0))
    ttk.Button(ts_src_row, text=".npz...", command=_browse_ts_npz,
               width=8).pack(side="left", padx=(4, 0))
    ttk.Button(ts_src_row, text="Use output dir",
               command=lambda: ts_src_var.set(out_var.get())
               ).pack(side="left", padx=(4, 0))

    ts_resolved_var = tk.StringVar(value="")
    ttk.Label(ts_frame, textvariable=ts_resolved_var, foreground="#555"
              ).pack(anchor="w", pady=(2, 0))

    ts_out_row = ttk.Frame(ts_frame)
    ts_out_row.pack(fill="x", pady=(6, 0))
    ttk.Label(ts_out_row, text="Tileset out dir:").pack(side="left")
    ts_out_var = tk.StringVar(value="")
    ts_out_entry = ttk.Entry(ts_out_row, textvariable=ts_out_var, width=44)
    ts_out_entry.pack(side="left", padx=(6, 0), fill="x", expand=True)
    ttk.Button(ts_out_row, text="Browse...",
               command=lambda: ts_out_var.set(
                   filedialog.askdirectory() or ts_out_var.get())
               ).pack(side="left", padx=(6, 0))
    _tip(ts_out_entry,
         "Where tileset.json, the tile_*.glb and the generated view_*.cmd "
         "launchers are written. Left blank it defaults to a tiles/ folder "
         "beside the store.")

    ts_opts = ttk.Frame(ts_frame)
    ts_opts.pack(fill="x", pady=(6, 0))
    ttk.Label(ts_opts, text="tile size (m)").pack(side="left")
    ts_tile_m_var = tk.StringVar(value="100")
    ts_tile_entry = ttk.Entry(ts_opts, textvariable=ts_tile_m_var, width=7)
    ts_tile_entry.pack(side="left", padx=(4, 12))
    _tip(ts_tile_entry,
         "--tile-m: the spatial size of a leaf tile in metres (default 100). "
         "The LOD pyramid groups these leaves 2x2 per level, so this sets the "
         "finest granularity the viewer can stream and evict.")
    ts_lod_var = tk.BooleanVar(value=True)
    ts_lod_cb = ttk.Checkbutton(ts_opts, text="LOD pyramid",
                                variable=ts_lod_var)
    ts_lod_cb.pack(side="left")
    _tip(ts_lod_cb,
         "On (the default): coarse interior levels above full-detail leaves, "
         "so a viewer draws a whole city without loading every leaf. Off "
         "emits --flat, the legacy root -> leaves layout, in which the client "
         "must fetch every leaf in view.")
    ts_geoid_var = tk.BooleanVar(value=True)
    ts_geoid_cb = ttk.Checkbutton(ts_opts, text="geoid heights",
                                  variable=ts_geoid_var)
    ts_geoid_cb.pack(side="left", padx=(12, 0))
    _tip(ts_geoid_cb,
         "On (the default): input altitudes are NGF-IGN69 (EPSG:5720) and the "
         "geoid grid is applied through pyproj, which needs network access on "
         "first use and places the model on real terrain. Off emits "
         "--no-geoid and treats the altitudes as already ellipsoidal, which "
         "sits about 50 m low at Lyon.")

    ts_keep_row = ttk.Frame(ts_frame)
    ts_keep_row.pack(fill="x", pady=(6, 0))
    ttk.Label(ts_keep_row, text="keep classes:").pack(side="left")
    ts_keep_var = tk.StringVar(value="")
    ts_keep_entry = ttk.Entry(ts_keep_row, textvariable=ts_keep_var, width=20)
    ts_keep_entry.pack(side="left", padx=(6, 0))
    ttk.Label(ts_keep_row, text="blank = every class", foreground="#555"
              ).pack(side="left", padx=(8, 0))
    _tip(ts_keep_entry,
         "--keep-classes: comma-separated ASPRS codes to export, e.g. "
         "2,3,4,5,6 for ground, the three vegetation strata and buildings. "
         "Blank exports every class in the store.")

    # -- Region / CRS / height datum ----------------------------------
    ts_more_row = ttk.Frame(ts_frame)
    ts_more_row.pack(fill="x", pady=(6, 0))
    ttk.Label(ts_more_row, text="region x/y:").pack(side="left")
    ts_region_vars = [tk.StringVar(value="") for _ in range(4)]
    for _v in ts_region_vars:
        ttk.Entry(ts_more_row, textvariable=_v, width=9).pack(side="left",
                                                              padx=(2, 0))
    _tip(ts_more_row,
         "Optional --region: a metric bbox (xmin ymin xmax ymax) to export only "
         "part of the store. Leave all four blank for the whole store.")
    _lbl_crs = ttk.Label(ts_more_row, text="crs:")
    _lbl_crs.pack(side="left", padx=(12, 2))
    ts_crs_var = tk.StringVar(value="")
    ts_crs_entry = ttk.Entry(ts_more_row, textvariable=ts_crs_var, width=10)
    ts_crs_entry.pack(side="left")
    _tip(ts_crs_entry,
         "Optional --crs: native CRS recorded in the tileset extras "
         "(default EPSG:3946). Blank keeps the default.")
    _lbl_hofs = ttk.Label(ts_more_row, text="height offset (m):")
    _lbl_hofs.pack(side="left", padx=(12, 2))
    ts_hofs_var = tk.StringVar(value="")
    ts_hofs_entry = ttk.Entry(ts_more_row, textvariable=ts_hofs_var, width=8)
    ts_hofs_entry.pack(side="left")
    _tip(ts_hofs_entry,
         "Optional --height-offset: metres added to the origin height instead "
         "of the geoid grid (e.g. 49.7 at Lyon). Blank uses the geoid grid when "
         "'geoid heights' is on.")
    _lbl_vcrs = ttk.Label(ts_more_row, text="vertical crs:")
    _lbl_vcrs.pack(side="left", padx=(12, 2))
    ts_vcrs_var = tk.StringVar(value="")
    ts_vcrs_entry = ttk.Entry(ts_more_row, textvariable=ts_vcrs_var, width=10)
    ts_vcrs_entry.pack(side="left")
    _tip(ts_vcrs_entry,
         "Optional --vertical-crs: the vertical datum of the input altitudes "
         "(default EPSG:5720 = NGF-IGN69). Blank keeps the default.")

    ttk.Label(ts_frame,
              text="No 'max instances' here, deliberately: that number is the "
                   "streaming HTML viewer's GPU working-set budget, and this "
                   "path deletes the intermediate payload it would size, so "
                   "it cannot change a tileset. Detail is set by tile size "
                   "and the LOD pyramid.",
              justify="left", wraplength=560, foreground="#555"
              ).pack(anchor="w", pady=(6, 0))

    def _sync_ts_source(*_):
        """Refresh the 'resolved store' label under the tileset source entry
        with the store resolve_store_path finds there (honouring the chosen
        form), or a not-found note."""
        npz = resolve_store_path(ts_src_var.get(), kind=ts_kind_var.get())
        if npz is None:
            ts_resolved_var.set("(no store found there in that form - try "
                                "store form: auto)")
        else:
            marker = "raw dir" if Path(npz).is_dir() else "npz"
            ts_resolved_var.set(f"store ({marker}): {npz}")

    ts_src_var.trace_add("write", _sync_ts_source)
    ts_kind_var.trace_add("write", _sync_ts_source)

    def _on_export_tileset():
        """'Export tileset' button: resolve the source store and validate the
        tile size (message boxes on failure), default the output dir to
        ``<store dir>/tiles``, build the tileset_cli command and launch it as
        the job child."""
        npz = resolve_store_path(ts_src_var.get(), kind=ts_kind_var.get())
        if npz is None:
            messagebox.showwarning(
                "No store",
                "Point at a run output directory holding area.npz, "
                "area_raw.npz, store_raw/ or a single-tile shards/ folder with "
                "one .npz, or at a store .npz / store_raw/ directly. If the "
                "folder holds several forms, pick one in 'store form'.")
            return
        try:
            tile_m = float(ts_tile_m_var.get())
            if tile_m <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid value",
                                 "Tile size must be a positive number of "
                                 "metres.")
            return
        region = [s.strip() for s in (v.get() for v in ts_region_vars)]
        region_val = None
        if any(region):
            try:
                region_val = tuple(float(s) for s in region)
            except ValueError:
                messagebox.showerror("Invalid value",
                                     "Region needs all four of xmin ymin xmax "
                                     "ymax, or all four blank.")
                return
        out_dir = ts_out_var.get().strip() or str(
            (npz if Path(npz).is_dir() else npz.parent) / "tiles")
        cmd = build_tileset_cmd(sys.executable, npz, out_dir,
                                tile_m=tile_m, lod=bool(ts_lod_var.get()),
                                keep_classes=ts_keep_var.get(),
                                geoid=bool(ts_geoid_var.get()),
                                crs=ts_crs_var.get().strip() or None,
                                region=region_val,
                                height_offset=ts_hofs_var.get().strip(),
                                vertical_crs=ts_vcrs_var.get().strip() or None)
        log(f"[tileset] exporting {npz} -> {out_dir}")
        _launch_subprocess(cmd)

    ttk.Button(ts_frame, text="Export tileset",
               command=_on_export_tileset).pack(anchor="w", pady=(8, 0))

    # -- 3-D Tiles from an existing streaming payload ------------------
    tp_frame = ttk.LabelFrame(tab_export,
                              text="Export to 3-D Tiles from a streaming "
                                   "payload (.idx.json + .bin)",
                              padding=8)
    tp_frame.pack(fill="x", pady=(10, 0))
    tp_src_row = ttk.Frame(tp_frame)
    tp_src_row.pack(fill="x")
    ttk.Label(tp_src_row, text=".idx.json:").pack(side="left")
    tp_idx_var = tk.StringVar(value="")
    ttk.Entry(tp_src_row, textvariable=tp_idx_var, width=42).pack(
        side="left", padx=(6, 0), fill="x", expand=True)
    ttk.Button(tp_src_row, text="Browse...",
               command=lambda: tp_idx_var.set(
                   filedialog.askopenfilename(
                       title="Choose the streaming payload .idx.json",
                       filetypes=[("Index JSON", "*.idx.json"),
                                  ("JSON files", "*.json"),
                                  ("All files", "*.*")]) or tp_idx_var.get())
               ).pack(side="left", padx=(6, 0))
    _tip(tp_src_row,
         "The *.idx.json a --viz3d-stream export wrote beside its .bin "
         "(area_stream.idx.json, or a single tile's <stem>_stream.idx.json). "
         "The .bin is found automatically beside it when the .bin box is "
         "blank.")
    tp_bin_row = ttk.Frame(tp_frame)
    tp_bin_row.pack(fill="x", pady=(6, 0))
    ttk.Label(tp_bin_row, text=".bin:").pack(side="left")
    tp_bin_var = tk.StringVar(value="")
    ttk.Entry(tp_bin_row, textvariable=tp_bin_var, width=42).pack(
        side="left", padx=(6, 0), fill="x", expand=True)
    ttk.Label(tp_bin_row, text="blank = beside the .idx.json",
              foreground="#555").pack(side="left", padx=(6, 0))
    tp_crs_row = ttk.Frame(tp_frame)
    tp_crs_row.pack(fill="x", pady=(6, 0))
    ttk.Label(tp_crs_row, text="crs:").pack(side="left")
    tp_crs_var = tk.StringVar(value="")
    ttk.Entry(tp_crs_row, textvariable=tp_crs_var, width=10).pack(side="left",
                                                                 padx=(4, 12))
    tp_geoid_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(tp_crs_row, text="geoid heights",
                    variable=tp_geoid_var).pack(side="left")
    ttk.Label(tp_crs_row, text="tile size (m)").pack(side="left", padx=(12, 2))
    tp_tile_m_var = tk.StringVar(value="100")
    ttk.Entry(tp_crs_row, textvariable=tp_tile_m_var, width=6).pack(side="left")

    tp_out_row = ttk.Frame(tp_frame)
    tp_out_row.pack(fill="x", pady=(6, 0))
    ttk.Label(tp_out_row, text="Tileset out dir:").pack(side="left")
    tp_out_var = tk.StringVar(value="")
    ttk.Entry(tp_out_row, textvariable=tp_out_var, width=42).pack(
        side="left", padx=(6, 0), fill="x", expand=True)
    ttk.Label(tp_out_row, text="blank = tiles/ beside the .idx.json",
              foreground="#555").pack(side="left", padx=(6, 0))

    def _on_export_payload():
        """'Export from payload' button: require the .idx.json (warning box
        otherwise), default the tiles dir beside it, and launch
        ``tileset_cli from-payload`` as the job child."""
        idx = tp_idx_var.get().strip().strip('"').strip("'")
        if not idx or not Path(idx).is_file():
            messagebox.showwarning(
                "No payload",
                "Choose the *.idx.json written by a --viz3d-stream export.")
            return
        idx_p = Path(idx)
        out_dir = tp_out_var.get().strip() or str(idx_p.parent / "tiles")
        cmd = build_tileset_from_payload_cmd(
            sys.executable, idx_p, out_dir,
            bin_path=(tp_bin_var.get().strip() or None),
            crs=tp_crs_var.get().strip() or None,
            lod=bool(ts_lod_var.get()),
            geoid=bool(tp_geoid_var.get()))
        log(f"[tileset] exporting payload {idx} -> {out_dir}")
        _launch_subprocess(cmd)

    ttk.Button(tp_frame, text="Export from payload",
               command=_on_export_payload).pack(anchor="w", pady=(8, 0))


    # -- Serve ---------------------------------------------------------
    srv_frame = ttk.LabelFrame(tab_export, text="View in a browser",
                               padding=8)
    srv_frame.pack(fill="x", pady=(10, 0))

    srv_dir_row = ttk.Frame(srv_frame)
    srv_dir_row.pack(fill="x")
    ttk.Label(srv_dir_row, text="Directory:").pack(side="left")
    srv_dir_var = tk.StringVar(value=out_var.get())
    srv_dir_entry = ttk.Entry(srv_dir_row, textvariable=srv_dir_var, width=44)
    srv_dir_entry.pack(side="left", padx=(6, 0), fill="x", expand=True)
    ttk.Button(srv_dir_row, text="Browse...",
               command=lambda: srv_dir_var.set(
                   filedialog.askdirectory() or srv_dir_var.get())
               ).pack(side="left", padx=(6, 0))
    ttk.Button(srv_dir_row, text="Use output dir",
               command=lambda: srv_dir_var.set(out_var.get())
               ).pack(side="left", padx=(4, 0))
    _tip(srv_dir_entry,
         "The folder to serve: a run's output folder for 'View stream' (it "
         "holds area_stream.html and the .bin sidecar), or the tileset folder "
         "for 'View tileset'.")

    srv_btn_row = ttk.Frame(srv_frame)
    srv_btn_row.pack(fill="x", pady=(8, 0))

    srv_opts_row = ttk.Frame(srv_frame)
    srv_opts_row.pack(fill="x", pady=(6, 0))
    ttk.Label(srv_opts_row, text="port:").pack(side="left")
    srv_port_var = tk.StringVar(value="")
    srv_port_entry = ttk.Entry(srv_opts_row, textvariable=srv_port_var, width=7)
    srv_port_entry.pack(side="left", padx=(4, 12))
    _tip(srv_port_entry,
         "Optional --port. Blank lets each server keep its own default (the "
         "streaming viewer picks an OS-chosen free port so several can run; the "
         "tileset server picks a free port too). Set a number to pin it, or 0 "
         "to explicitly ask the OS for one.")
    ttk.Label(srv_opts_row, text="bind:").pack(side="left")
    srv_bind_var = tk.StringVar(value="")
    srv_bind_entry = ttk.Entry(srv_opts_row, textvariable=srv_bind_var, width=12)
    srv_bind_entry.pack(side="left", padx=(4, 12))
    _tip(srv_bind_entry,
         "Optional --bind: interface to listen on. Blank keeps the server "
         "default (localhost, or $VOXELIZER_BIND when set - which is how the "
         "Docker image exposes 8000).")
    srv_noopen_var = tk.BooleanVar(value=False)
    srv_noopen_cb = ttk.Checkbutton(srv_opts_row, text="don't open a browser",
                                    variable=srv_noopen_var)
    srv_noopen_cb.pack(side="left")
    _tip(srv_noopen_cb,
         "Emit --no-open: serve without launching a browser (the url is still "
         "printed in the log below). Useful when the browser is elsewhere.")

    def _serve_dir_or_warn():
        """The directory named in the serve entry (quotes stripped) as a
        Path, or None after a warning box when it is not a directory."""
        d = Path(srv_dir_var.get().strip().strip('"').strip("'"))
        if not d.is_dir():
            messagebox.showwarning("No directory",
                                   f"Not a directory:\n{d}")
            return None
        return d

    def _on_view_stream():
        """'View stream' button: find a ``*_stream.html`` in the serve
        directory (preferring area_stream.html, warning box if none) and
        start serve_voxel_html on it as a tracked server."""
        d = _serve_dir_or_warn()
        if d is None:
            return
        pages = sorted(p.name for p in d.glob("*_stream.html") if p.is_file())
        if not pages:
            messagebox.showwarning(
                "No streaming viewer",
                f"No *_stream.html in\n{d}\n\nRun with the 3-D visualiser in "
                f"'streamable' mode to produce one.")
            return
        page = "area_stream.html" if "area_stream.html" in pages else pages[0]
        _start_server(build_serve_stream_cmd(
            sys.executable, d, page=page,
            port=srv_port_var.get().strip() or None,
            bind=srv_bind_var.get().strip() or None,
            no_open=bool(srv_noopen_var.get())), "stream")

    def _on_view_tileset():
        """'View tileset' button: require a tileset.json in the serve
        directory (warning box otherwise) and start serve_tiles on it with
        the selected viewer as a tracked server."""
        d = _serve_dir_or_warn()
        if d is None:
            return
        if not (d / "tileset.json").is_file():
            messagebox.showwarning(
                "No tileset",
                f"No tileset.json in\n{d}\n\nExport one above first, or point "
                f"at the folder the export wrote.")
            return
        _start_server(
            build_serve_tiles_cmd(
                sys.executable, d, srv_viewer_var.get(),
                port=srv_port_var.get().strip() or None,
                bind=srv_bind_var.get().strip() or None,
                no_open=bool(srv_noopen_var.get())),
            f"tiles/{srv_viewer_var.get()}")

    view_stream_btn = ttk.Button(srv_btn_row, text="View stream",
                                 command=_on_view_stream)
    view_stream_btn.pack(side="left")
    _tip(view_stream_btn,
         "Starts voxelizer.serve_voxel_html on the directory above and opens "
         "its *_stream.html. A page that kept a .bin sidecar cannot be opened "
         "from the file system: it fetches byte ranges of that sidecar, which "
         "needs an HTTP origin. (A small export whose payload was inlined "
         "into the page opens either way.) The port is chosen by the "
         "operating system, so several viewers can be up at once.")
    view_tiles_btn = ttk.Button(srv_btn_row, text="View tileset",
                                command=_on_view_tileset)
    view_tiles_btn.pack(side="left", padx=(8, 0))
    _tip(view_tiles_btn,
         "Starts voxelizer.serve_tiles on the directory above and opens the "
         "bundled viewer page. The viewer library itself comes from a CDN, so "
         "the page needs network access on first load; the tiles are local.")
    ttk.Label(srv_btn_row, text="viewer:").pack(side="left", padx=(12, 4))
    srv_viewer_var = tk.StringVar(value="cesium")
    srv_viewer_dd = ttk.Combobox(srv_btn_row, textvariable=srv_viewer_var,
                                 values=["cesium", "itowns"],
                                 state="readonly", width=8)
    srv_viewer_dd.pack(side="left")
    _tip(srv_viewer_dd, "Which bundled 3-D Tiles viewer page 'View tileset' "
                        "opens. Both are served either way, so the other one "
                        "is one url away.")
    stop_srv_btn = ttk.Button(srv_btn_row, text="Stop server(s)",
                              state="disabled",
                              command=lambda: _stop_servers())
    stop_srv_btn.pack(side="left", padx=(12, 0))
    _tip(stop_srv_btn,
         "Terminates every viewer server this window started. They are also "
         "stopped when the window closes, so none is ever left holding a port "
         "after the GUI is gone.")

    ttk.Label(srv_frame,
              text="Servers keep running until stopped; each prints the url "
                   "it was given into the log below. Closing this window "
                   "stops them all.",
              justify="left", wraplength=560, foreground="#555"
              ).pack(anchor="w", pady=(6, 0))

    # ==================================================================
    # TAB: Store Tools
    #
    # The store-level CLIs, each in its own collapsible section. None of
    # these voxelizes: every one takes a finished store (a .npz or a raw
    # store_raw/ directory) or a shards/ set and does one thing to it. They
    # reuse the same job child and the same tracked servers as the run tabs,
    # so a long post-process or a served viewer behaves exactly like a run.
    # ==================================================================
    ttk.Label(tab_tools,
              text="Work on a finished store or shard set. A 'store' here is "
                   "either a .npz (area.npz, a shard, a post-processed file) "
                   "or a raw store_raw/ directory, attached by memory map. "
                   "Nothing on this tab voxelizes: point each tool at what an "
                   "earlier run already wrote. Sections start collapsed - "
                   "click a heading to open it.",
              justify="left", wraplength=580, foreground="#444"
              ).pack(anchor="w", pady=(0, 8))

    def _store_kind_dd(parent, var, *, label="store form:"):
        """A small 'store form' combobox (auto / npz / dir) bound to *var*;
        returns the combobox for tooltips."""
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(2, 2))
        ttk.Label(row, text=label).pack(side="left")
        dd = ttk.Combobox(row, textvariable=var,
                          values=["auto", "npz", "dir"], state="readonly",
                          width=6)
        dd.pack(side="left", padx=(6, 8))
        _tip(dd, "auto: area.npz, else area_raw.npz, else store_raw/, else a "
                 "lone shard. npz: only a compressed .npz. dir: only the raw "
                 "store_raw/ directory (memory-mapped).")
        return dd

    def _store_row(parent, var, *, label="store:", tip="", with_kind=None):
        """A 'store' entry + Dir/.npz/Use-output-dir buttons. When *with_kind*
        is a StringVar, the buttons filter by that form via resolve_store_path.
        Returns the entry (for a tooltip)."""
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(2, 0))
        ttk.Label(row, text=label).pack(side="left")
        ent = ttk.Entry(row, textvariable=var, width=44)
        ent.pack(side="left", padx=(6, 0), fill="x", expand=True)
        if tip:
            _tip(ent, tip)

        def _browse_dir():
            d = filedialog.askdirectory(title="Choose a run output directory")
            if d:
                var.set(d)

        def _browse_npz():
            f = filedialog.askopenfilename(
                title="Choose a store .npz",
                filetypes=[("Voxel store", "*.npz"), ("All files", "*.*")])
            if f:
                var.set(f)

        ttk.Button(row, text="Dir...", command=_browse_dir, width=6).pack(
            side="left", padx=(6, 0))
        ttk.Button(row, text=".npz...", command=_browse_npz, width=7).pack(
            side="left", padx=(4, 0))
        ttk.Button(row, text="Use output dir",
                   command=lambda: var.set(out_var.get())).pack(
            side="left", padx=(4, 0))
        return ent

    def _launch_tool(cmd, *, out_dir=None, missing=""):
        """Build-time helper: log the tool command and launch it as the job
        child, refusals already checked by the caller."""
        log("[tool] " + " ".join(str(c) for c in cmd))
        _launch_subprocess(cmd)

    # ---- Post-process --------------------------------------------------
    sec_post = _CollapsibleSection(tab_tools, "Post-process a store "
                                              "(denoise / absorb / resolve / group)",
                                   expanded=True)
    bp = sec_post.body
    pp_in_var = tk.StringVar(value="")
    pp_kind_var = tk.StringVar(value="auto")
    _store_row(bp, pp_in_var, label="input store:",
               tip="The store to post-process: a .npz or a store_raw/ "
                   "directory (memory-mapped).",
               with_kind=pp_kind_var)
    _store_kind_dd(bp, pp_kind_var)
    pp_out_var = tk.StringVar(value="")
    pp_out_row = ttk.Frame(bp)
    pp_out_row.pack(fill="x", pady=(4, 0))
    ttk.Label(pp_out_row, text="output .npz:").pack(side="left")
    ttk.Entry(pp_out_row, textvariable=pp_out_var, width=44).pack(
        side="left", padx=(6, 0), fill="x", expand=True)
    _tip(pp_out_row.winfo_children()[-1],
         "Where the post-processed store is written (.npz appended if "
         "missing). It is a NEW store; the input is never modified.")
    pp_denoise_row = ttk.Frame(bp)
    pp_denoise_row.pack(fill="x", pady=(6, 0))
    pp_min_points_var = tk.BooleanVar(value=False)
    mp_cb = ttk.Checkbutton(pp_denoise_row, text="min-points",
                            variable=pp_min_points_var)
    mp_cb.pack(side="left")
    mp_val = tk.StringVar(value="4")
    ttk.Entry(pp_denoise_row, textvariable=mp_val, width=6).pack(
        side="left", padx=(4, 12))
    _tip(mp_cb, "Drop intervals with <= N points (bare = 4).")
    pp_morph_var = tk.BooleanVar(value=False)
    morph_cb = ttk.Checkbutton(pp_denoise_row, text="morph",
                               variable=pp_morph_var)
    morph_cb.pack(side="left")
    morph_val = tk.StringVar(value="2")
    ttk.Entry(pp_denoise_row, textvariable=morph_val, width=6).pack(
        side="left", padx=(4, 12))
    _tip(morph_cb, "Morphological support filter: drop intervals with fewer "
                   "than N same-class neighbours among the 8 surrounding "
                   "columns (bare = 2).")
    pp_morph_classes_var = tk.StringVar(value="")
    ttk.Label(pp_denoise_row, text="morph classes:").pack(side="left")
    ttk.Entry(pp_denoise_row, textvariable=pp_morph_classes_var,
              width=12).pack(side="left", padx=(4, 0))
    _tip(pp_denoise_row.winfo_children()[-1],
         "Restrict --morph to these ASPRS classes (blank = every class).")

    pp_pass_row = ttk.Frame(bp)
    pp_pass_row.pack(fill="x", pady=(6, 0))
    pp_absorb_var = tk.BooleanVar(value=False)
    abs_cb = ttk.Checkbutton(pp_pass_row, text="absorb", variable=pp_absorb_var)
    abs_cb.pack(side="left")
    _tip(abs_cb, "Reclassify vegetation runs sandwiched by building to building.")
    pp_resolve_var = tk.BooleanVar(value=False)
    res_cb = ttk.Checkbutton(pp_pass_row, text="resolve", variable=pp_resolve_var)
    res_cb.pack(side="left", padx=(12, 0))
    _tip(res_cb, "Collapse cross-class overlaps so every voxel carries one class.")
    pp_group_var = tk.BooleanVar(value=False)
    grp_cb = ttk.Checkbutton(pp_pass_row, text="group", variable=pp_group_var)
    grp_cb.pack(side="left", padx=(12, 0))
    _tip(grp_cb, "Merge vertically-consecutive same-class intervals after the "
                 "passes above.")
    pp_group_gap_var = tk.StringVar(value="")
    ttk.Label(pp_pass_row, text="group gap (m):").pack(side="left", padx=(12, 0))
    ttk.Entry(pp_pass_row, textvariable=pp_group_gap_var, width=7).pack(
        side="left", padx=(4, 0))
    _tip(pp_pass_row.winfo_children()[-1],
         "With group: only merge across empty gaps of at most this many metres "
         "(blank = any gap).")

    pp_adv = _CollapsibleSection(bp, "resolve / absorb tuning", expanded=False)
    av = pp_adv.body
    av_row1 = ttk.Frame(av)
    av_row1.pack(fill="x")
    ttk.Label(av_row1, text="tie margin:").pack(side="left")
    pp_tie_margin_var = tk.StringVar(value="")
    ttk.Entry(av_row1, textvariable=pp_tie_margin_var, width=6).pack(
        side="left", padx=(4, 12))
    _tip(av_row1.winfo_children()[-1],
         "Counts within this absolute margin of the best are tied (blank = the "
         "library default, 1).")
    ttk.Label(av_row1, text="tie rel:").pack(side="left")
    pp_tie_rel_var = tk.StringVar(value="")
    ttk.Entry(av_row1, textvariable=pp_tie_rel_var, width=6).pack(
        side="left", padx=(4, 12))
    ttk.Label(av_row1, text="class priority:").pack(side="left")
    pp_class_prio_var = tk.StringVar(value="")
    ttk.Entry(av_row1, textvariable=pp_class_prio_var, width=14).pack(
        side="left", padx=(4, 0))
    _tip(av_row1.winfo_children()[-1],
         "Explicit near-tie preference order (earlier wins), e.g. 2,3,4,5,6.")
    av_row2 = ttk.Frame(av)
    av_row2.pack(fill="x", pady=(4, 0))
    ttk.Label(av_row2, text="absorb min neighbour:").pack(side="left")
    pp_abs_min_var = tk.StringVar(value="")
    ttk.Entry(av_row2, textvariable=pp_abs_min_var, width=6).pack(
        side="left", padx=(4, 12))
    ttk.Label(av_row2, text="absorb max noise:").pack(side="left")
    pp_abs_noise_var = tk.StringVar(value="")
    ttk.Entry(av_row2, textvariable=pp_abs_noise_var, width=6).pack(
        side="left", padx=(4, 12))
    pp_prefer_taller_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(av_row2, text="prefer taller",
                    variable=pp_prefer_taller_var).pack(side="left", padx=(0, 12))
    pp_demote_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(av_row2, text="demote uncertain",
                    variable=pp_demote_var).pack(side="left")
    pp_stats_var = tk.BooleanVar(value=False)
    pp_dry_var = tk.BooleanVar(value=False)
    av_row3 = ttk.Frame(av)
    av_row3.pack(fill="x", pady=(4, 0))
    ttk.Checkbutton(av_row3, text="--stats (full per-class table)",
                    variable=pp_stats_var).pack(side="left")
    ttk.Checkbutton(av_row3, text="--dry-run (write nothing)",
                    variable=pp_dry_var).pack(side="left", padx=(12, 0))

    def _on_postprocess():
        """Validate an input store and at least one pass (the CLI refuses an
        empty plan), then launch postprocess_cli as the job child."""
        inp = resolve_store_path(pp_in_var.get(), kind=pp_kind_var.get())
        if inp is None:
            messagebox.showwarning("No store",
                                   "Choose an input store (a .npz or a "
                                   "store_raw/ directory).")
            return
        out = pp_out_var.get().strip()
        if not out:
            out = str(Path(inp).with_name(Path(inp).name + "_clean.npz"))
        passes = []
        if pp_min_points_var.get():
            passes.append("min-points")
        if pp_morph_var.get():
            passes.append("morph")
        if pp_absorb_var.get():
            passes.append("absorb")
        if pp_resolve_var.get():
            passes.append("resolve")
        if pp_group_var.get():
            passes.append("group")
        if not passes:
            messagebox.showwarning(
                "No pass selected",
                "Pick at least one pass (min-points, morph, absorb, resolve, "
                "group) - the CLI refuses an empty plan.")
            return
        cmd = build_postprocess_cmd(
            sys.executable, inp, out,
            min_points=(mp_val.get().strip() if pp_min_points_var.get() else None),
            morph=(morph_val.get().strip() if pp_morph_var.get() else None),
            morph_classes=pp_morph_classes_var.get(),
            absorb=bool(pp_absorb_var.get()),
            absorb_min_neighbour=pp_abs_min_var.get().strip(),
            absorb_max_noise=pp_abs_noise_var.get().strip(),
            resolve=bool(pp_resolve_var.get()),
            tie_margin=pp_tie_margin_var.get().strip(),
            tie_rel=pp_tie_rel_var.get().strip(),
            prefer_taller=bool(pp_prefer_taller_var.get()),
            demote_uncertain=bool(pp_demote_var.get()),
            class_priority=pp_class_prio_var.get(),
            group=bool(pp_group_var.get()),
            group_gap=pp_group_gap_var.get().strip(),
            stats=bool(pp_stats_var.get()),
            dry_run=bool(pp_dry_var.get()))
        _launch_tool(cmd)

    ttk.Button(bp, text="Post-process store",
               command=_on_postprocess).pack(anchor="w", pady=(8, 0))

    # ---- Reconstruct ---------------------------------------------------
    sec_rec = _CollapsibleSection(tab_tools, "Reconstruct LAS/LAZ "
                                             "(store <-> point cloud)")
    br = sec_rec.body
    rec_verb_var = tk.StringVar(value="to-laz")
    rec_verb_row = ttk.Frame(br)
    rec_verb_row.pack(fill="x")
    ttk.Label(rec_verb_row, text="verb:").pack(side="left")
    ttk.Combobox(rec_verb_row, textvariable=rec_verb_var,
                 values=["to-laz", "to-npz", "verify"], state="readonly",
                 width=10).pack(side="left", padx=(6, 0))
    _tip(rec_verb_row.winfo_children()[-1],
         "to-laz: .npz -> .las/.laz ('exact' is the only bit-exact round trip). "
         "to-npz: .las/.laz -> .npz. verify: check a .npz rebuilds a .laz "
         "exactly.")
    rec_npz_var = tk.StringVar(value="")
    _store_row(br, rec_npz_var, label="store .npz:",
               tip="The .npz used by to-laz and verify.")
    rec_laz_var = tk.StringVar(value="")
    rec_laz_row = ttk.Frame(br)
    rec_laz_row.pack(fill="x", pady=(4, 0))
    ttk.Label(rec_laz_row, text="LAS/LAZ file:").pack(side="left")
    ttk.Entry(rec_laz_row, textvariable=rec_laz_var, width=44).pack(
        side="left", padx=(6, 0), fill="x", expand=True)

    def _browse_laz_any():
        f = filedialog.askopenfilename(
            title="Choose a LAS/LAZ file",
            filetypes=[("LAZ/LAS files", "*.laz *.las"), ("All files", "*.*")])
        if f:
            rec_laz_var.set(f)

    ttk.Button(rec_laz_row, text="Browse...", command=_browse_laz_any,
               width=9).pack(side="left", padx=(6, 0))
    _tip(rec_laz_row.winfo_children()[-1],
         "The LAS/LAZ file used by to-npz (input) and verify (the file to "
         "compare against).")
    rec_out_var = tk.StringVar(value="")
    rec_out_row = ttk.Frame(br)
    rec_out_row.pack(fill="x", pady=(4, 0))
    ttk.Label(rec_out_row, text="output file:").pack(side="left")
    ttk.Entry(rec_out_row, textvariable=rec_out_var, width=44).pack(
        side="left", padx=(6, 0), fill="x", expand=True)
    _tip(rec_out_row.winfo_children()[-1],
         "to-laz writes a .las/.laz here; to-npz writes a .npz here. Ignored by "
         "verify.")
    rec_opt_row = ttk.Frame(br)
    rec_opt_row.pack(fill="x", pady=(6, 0))
    ttk.Label(rec_opt_row, text="mode:").pack(side="left")
    rec_mode_var = tk.StringVar(value="")
    ttk.Combobox(rec_opt_row, textvariable=rec_mode_var,
                 values=["", "density", "one_per_voxel", "exact"],
                 state="readonly", width=14).pack(side="left", padx=(4, 12))
    _tip(rec_opt_row.winfo_children()[-1],
         "Export mode for to-laz. 'exact' is the only bit-exact round trip; "
         "blank keeps the CLI default (density).")
    rec_epsg_var = tk.StringVar(value="")
    ttk.Label(rec_opt_row, text="epsg:").pack(side="left")
    ttk.Entry(rec_opt_row, textvariable=rec_epsg_var, width=7).pack(
        side="left", padx=(4, 12))
    ttk.Label(rec_opt_row, text="origin X Y Z:").pack(side="left")
    rec_origin_vars = [tk.StringVar(value="") for _ in range(3)]
    for _v in rec_origin_vars:
        ttk.Entry(rec_opt_row, textvariable=_v, width=8).pack(side="left",
                                                              padx=(2, 0))
    _tip(rec_opt_row.winfo_children()[-1],
         "to-npz only: the grid origin, required when the file has no IARBRE "
         "grid VLR (blank = read it from the file).")
    rec_verify_var = tk.BooleanVar(value=False)
    rec_grid_vlr_var = tk.BooleanVar(value=True)
    rec_zbase_var = tk.BooleanVar(value=False)
    rec_opv_var = tk.BooleanVar(value=False)
    rec_row2 = ttk.Frame(br)
    rec_row2.pack(fill="x", pady=(4, 0))
    ttk.Checkbutton(rec_row2, text="verify after to-laz",
                    variable=rec_verify_var).pack(side="left")
    ttk.Checkbutton(rec_row2, text="IARBRE grid VLR",
                    variable=rec_grid_vlr_var).pack(side="left", padx=(12, 0))
    ttk.Checkbutton(rec_row2, text="z from interval base",
                    variable=rec_zbase_var).pack(side="left", padx=(12, 0))
    ttk.Checkbutton(rec_row2, text="one per voxel",
                    variable=rec_opv_var).pack(side="left", padx=(12, 0))

    def _on_reconstruct():
        """Validate the files the chosen verb needs, then launch reconstruct."""
        verb = rec_verb_var.get()
        npz = rec_npz_var.get().strip().strip('"').strip("'")
        laz = rec_laz_var.get().strip().strip('"').strip("'")
        out = rec_out_var.get().strip().strip('"').strip("'")
        if verb in ("to-laz", "verify") and not npz:
            messagebox.showwarning("No store", "Choose the store .npz.")
            return
        if verb == "to-laz" and not out:
            messagebox.showwarning("No output",
                                   "Choose the output .las/.laz path.")
            return
        if verb == "to-npz" and not laz:
            messagebox.showwarning("No input", "Choose the input LAS/LAZ file.")
            return
        if verb == "to-npz" and not out:
            messagebox.showwarning("No output", "Choose the output .npz path.")
            return
        if verb == "verify" and not laz:
            messagebox.showwarning("No file", "Choose the LAS/LAZ to verify "
                                              "against.")
            return
        origin = [v.get().strip() for v in rec_origin_vars]
        origin_val = None
        if any(origin):
            if not all(origin):
                messagebox.showerror("Invalid origin", "Give all three of X Y Z, "
                                                       "or leave all blank.")
                return
            origin_val = tuple(float(o) for o in origin)
        cmd = build_reconstruct_cmd(
            sys.executable, verb, npz=npz or None, laz=laz or None,
            output=out or None, mode=rec_mode_var.get() or None,
            one_per_voxel=bool(rec_opv_var.get()),
            z_base=bool(rec_zbase_var.get()),
            epsg=rec_epsg_var.get().strip() or None,
            grid_vlr=bool(rec_grid_vlr_var.get()),
            verify=bool(rec_verify_var.get()),
            origin=origin_val)
        _launch_tool(cmd)

    ttk.Button(br, text="Run reconstruct",
               command=_on_reconstruct).pack(anchor="w", pady=(8, 0))

    # ---- Archive -------------------------------------------------------
    sec_arc = _CollapsibleSection(tab_tools, "Archive shards "
                                             "(shards/*.npz <-> exact LAZ)")
    ba = sec_arc.body
    arc_verb_var = tk.StringVar(value="pack")
    arc_verb_row = ttk.Frame(ba)
    arc_verb_row.pack(fill="x")
    ttk.Label(arc_verb_row, text="verb:").pack(side="left")
    ttk.Combobox(arc_verb_row, textvariable=arc_verb_var,
                 values=["pack", "unpack"], state="readonly",
                 width=8).pack(side="left", padx=(6, 0))
    _tip(arc_verb_row.winfo_children()[-1],
         "pack: shards/*.npz -> shards_laz/*.laz (exact, verified per shard). "
         "unpack: shards_laz/*.laz -> shards/*.npz.")
    arc_dir_var = tk.StringVar(value="")
    _store_dir_row = ttk.Frame(ba)
    _store_dir_row.pack(fill="x", pady=(4, 0))
    ttk.Label(_store_dir_row, text="run / archive dir:").pack(side="left")
    ttk.Entry(_store_dir_row, textvariable=arc_dir_var, width=44).pack(
        side="left", padx=(6, 0), fill="x", expand=True)

    def _browse_arc_dir():
        d = filedialog.askdirectory(title="Choose a run directory")
        if d:
            arc_dir_var.set(d)

    ttk.Button(_store_dir_row, text="Dir...", command=_browse_arc_dir,
               width=6).pack(side="left", padx=(6, 0))
    ttk.Button(_store_dir_row, text="Use output dir",
               command=lambda: arc_dir_var.set(out_var.get())).pack(
        side="left", padx=(4, 0))
    _tip(_store_dir_row.winfo_children()[-1],
         "pack: the run directory (or its shards/ directory). unpack: the "
         "archive directory (or the run directory above it).")
    arc_worker_row = ttk.Frame(ba)
    arc_worker_row.pack(fill="x", pady=(4, 0))
    ttk.Label(arc_worker_row, text="workers:").pack(side="left")
    arc_workers_var = tk.StringVar(value="")
    ttk.Entry(arc_worker_row, textvariable=arc_workers_var, width=6).pack(
        side="left", padx=(4, 12))
    ttk.Label(arc_worker_row, text="epsg:").pack(side="left")
    arc_epsg_var = tk.StringVar(value="")
    ttk.Entry(arc_worker_row, textvariable=arc_epsg_var, width=7).pack(
        side="left", padx=(4, 12))
    ttk.Label(arc_worker_row, text="unpack out:").pack(side="left")
    arc_out_var = tk.StringVar(value="")
    ttk.Entry(arc_worker_row, textvariable=arc_out_var, width=24).pack(
        side="left", padx=(4, 0))
    arc_replace_var = tk.BooleanVar(value=False)
    arc_noverify_var = tk.BooleanVar(value=False)
    arc_row3 = ttk.Frame(ba)
    arc_row3.pack(fill="x", pady=(4, 0))
    arc_rep_cb = ttk.Checkbutton(arc_row3, text="--replace (DELETE source .npz "
                                                "once verified)",
                                 variable=arc_replace_var)
    arc_rep_cb.pack(side="left")
    _tip(arc_rep_cb, "pack only. Removes each source .npz after its .laz has "
                     "verified. Refused with --no-verify (verification is what "
                     "makes it safe).")
    arc_nv_cb = ttk.Checkbutton(arc_row3, text="--no-verify (faster, unsafe "
                                               "with --replace)",
                                variable=arc_noverify_var)
    arc_nv_cb.pack(side="left", padx=(12, 0))
    _tip(arc_nv_cb, "pack only. Skips the per-shard rebuild-and-compare check.")

    def _on_archive():
        """Validate the directory, then launch archive_cli (pack/unpack)."""
        d = arc_dir_var.get().strip().strip('"').strip("'")
        if not d or not Path(d).exists():
            messagebox.showwarning("No directory",
                                   "Choose the run / archive directory.")
            return
        verb = arc_verb_var.get()
        cmd = build_archive_cmd(
            sys.executable, verb,
            run_dir=d if verb == "pack" else None,
            archive_dir=d if verb == "unpack" else None,
            workers=arc_workers_var.get().strip(),
            no_verify=bool(arc_noverify_var.get()),
            replace=bool(arc_replace_var.get()),
            epsg=arc_epsg_var.get().strip(),
            out=arc_out_var.get().strip() or None)
        _launch_tool(cmd)

    ttk.Button(ba, text="Run archive",
               command=_on_archive).pack(anchor="w", pady=(8, 0))

    # ---- Merge shards --------------------------------------------------
    sec_merge = _CollapsibleSection(tab_tools, "Merge shards into a store "
                                               "(out of core)")
    bm = sec_merge.body
    merge_shards_var_tool = tk.StringVar(value="")
    _store_dir_row2 = ttk.Frame(bm)
    _store_dir_row2.pack(fill="x")
    ttk.Label(_store_dir_row2, text="shards dir:").pack(side="left")
    ttk.Entry(_store_dir_row2, textvariable=merge_shards_var_tool, width=44).pack(
        side="left", padx=(6, 0), fill="x", expand=True)

    def _browse_merge_shards():
        d = filedialog.askdirectory(title="Choose a shards/ directory")
        if d:
            merge_shards_var_tool.set(d)

    ttk.Button(_store_dir_row2, text="Dir...", command=_browse_merge_shards,
               width=6).pack(side="left", padx=(6, 0))
    ttk.Button(_store_dir_row2, text="Use output dir/shards",
               command=lambda: merge_shards_var_tool.set(
                   str(Path(out_var.get()) / "shards"))).pack(
        side="left", padx=(4, 0))
    merge_out_var = tk.StringVar(value="")
    merge_out_row = ttk.Frame(bm)
    merge_out_row.pack(fill="x", pady=(4, 0))
    ttk.Label(merge_out_row, text="out store dir:").pack(side="left")
    ttk.Entry(merge_out_row, textvariable=merge_out_var, width=44).pack(
        side="left", padx=(6, 0), fill="x", expand=True)
    merge_group_var = tk.BooleanVar(value=False)
    merge_band_var2 = tk.StringVar(value="")
    merge_group_gap_var = tk.StringVar(value="")
    merge_ow_var = tk.BooleanVar(value=False)
    merge_plan_var = tk.BooleanVar(value=False)
    merge_opt = ttk.Frame(bm)
    merge_opt.pack(fill="x", pady=(4, 0))
    ttk.Checkbutton(merge_opt, text="group intervals",
                    variable=merge_group_var).pack(side="left")
    ttk.Label(merge_opt, text="group gap (m):").pack(side="left", padx=(12, 0))
    ttk.Entry(merge_opt, textvariable=merge_group_gap_var, width=6).pack(
        side="left", padx=(4, 12))
    ttk.Label(merge_opt, text="band intervals:").pack(side="left")
    ttk.Entry(merge_opt, textvariable=merge_band_var2, width=12).pack(
        side="left", padx=(4, 12))
    ttk.Checkbutton(merge_opt, text="overwrite",
                    variable=merge_ow_var).pack(side="left", padx=(0, 12))
    ttk.Checkbutton(merge_opt, text="plan only",
                    variable=merge_plan_var).pack(side="left")

    def _on_merge():
        """Validate the shards dir and out store dir, then launch
        merge_streaming (plan-only needs no out dir, but the flag is required
        by the CLI, so one is always supplied)."""
        sd = merge_shards_var_tool.get().strip().strip('"').strip("'")
        if not sd or not Path(sd).is_dir():
            messagebox.showwarning("No shards dir",
                                   "Choose the shards/ directory to merge.")
            return
        od = merge_out_var.get().strip().strip('"').strip("'")
        if not od:
            od = str(Path(sd).parent / "store_raw")
        cmd = build_merge_cmd(
            sys.executable, sd, od,
            group_intervals=bool(merge_group_var.get()),
            group_gap=merge_group_gap_var.get().strip(),
            band_intervals=merge_band_var2.get().strip(),
            overwrite=bool(merge_ow_var.get()),
            plan_only=bool(merge_plan_var.get()))
        _launch_tool(cmd)

    ttk.Button(bm, text="Run merge",
               command=_on_merge).pack(anchor="w", pady=(8, 0))

    # ---- Shard diagnostics ---------------------------------------------
    sec_diag = _CollapsibleSection(tab_tools, "Shard diagnostics "
                                              "(columns/ + stats from shards)")
    bd = sec_diag.body
    sdiag_shards_var = tk.StringVar(value="")
    _store_dir_row3 = ttk.Frame(bd)
    _store_dir_row3.pack(fill="x")
    ttk.Label(_store_dir_row3, text="shards dir:").pack(side="left")
    ttk.Entry(_store_dir_row3, textvariable=sdiag_shards_var, width=44).pack(
        side="left", padx=(6, 0), fill="x", expand=True)

    def _browse_sdiag():
        d = filedialog.askdirectory(title="Choose a shards/ directory")
        if d:
            sdiag_shards_var.set(d)

    ttk.Button(_store_dir_row3, text="Dir...", command=_browse_sdiag,
               width=6).pack(side="left", padx=(6, 0))
    ttk.Button(_store_dir_row3, text="Use output dir/shards",
               command=lambda: sdiag_shards_var.set(
                   str(Path(out_var.get()) / "shards"))).pack(
        side="left", padx=(4, 0))
    sdiag_out_var = tk.StringVar(value="")
    sdiag_out_row = ttk.Frame(bd)
    sdiag_out_row.pack(fill="x", pady=(4, 0))
    ttk.Label(sdiag_out_row, text="out dir:").pack(side="left")
    ttk.Entry(sdiag_out_row, textvariable=sdiag_out_var, width=44).pack(
        side="left", padx=(6, 0), fill="x", expand=True)
    sdiag_mode_var = tk.StringVar(value="diag")
    sdiag_topn_var = tk.StringVar(value="")
    sdiag_group_var = tk.StringVar(value="")  # "", "on", "off"
    sdiag_row = ttk.Frame(bd)
    sdiag_row.pack(fill="x", pady=(4, 0))
    ttk.Label(sdiag_row, text="columns mode:").pack(side="left")
    ttk.Combobox(sdiag_row, textvariable=sdiag_mode_var,
                 values=["diag", "top", "all", "skip"], state="readonly",
                 width=7).pack(side="left", padx=(4, 12))
    ttk.Label(sdiag_row, text="top N:").pack(side="left")
    ttk.Entry(sdiag_row, textvariable=sdiag_topn_var, width=6).pack(
        side="left", padx=(4, 12))
    ttk.Label(sdiag_row, text="grouping:").pack(side="left")
    ttk.Combobox(sdiag_row, textvariable=sdiag_group_var,
                 values=["", "on", "off"], state="readonly",
                 width=5).pack(side="left", padx=(4, 0))
    _tip(sdiag_row.winfo_children()[-1],
         "Blank = the manifest's recorded grouping; on/off force it.")
    sdiag_stats_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(sdiag_row, text="stats.txt",
                    variable=sdiag_stats_var).pack(side="left", padx=(12, 0))

    def _on_shard_diag():
        """Validate the dirs and that at least one of figures/stats is asked
        for (the CLI refuses a request for neither), then launch."""
        sd = sdiag_shards_var.get().strip().strip('"').strip("'")
        if not sd or not Path(sd).is_dir():
            messagebox.showwarning("No shards dir",
                                   "Choose the shards/ directory to diagnose.")
            return
        mode = sdiag_mode_var.get()
        if mode == "skip" and not sdiag_stats_var.get():
            messagebox.showwarning(
                "Nothing requested",
                "columns mode 'skip' with stats off asks for nothing - enable "
                "one.")
            return
        od = sdiag_out_var.get().strip().strip('"').strip("'")
        if not od:
            od = str(Path(sd).parent / "shard_diag")
        grouping = {"on": True, "off": False}.get(sdiag_group_var.get(), None)
        cmd = build_shard_diag_cmd(
            sys.executable, sd, od,
            columns_mode=mode,
            columns_top_n=sdiag_topn_var.get().strip(),
            stats=bool(sdiag_stats_var.get()),
            group_intervals=grouping)
        _launch_tool(cmd)

    ttk.Button(bd, text="Run shard diagnostics",
               command=_on_shard_diag).pack(anchor="w", pady=(8, 0))

    # ---- 3-D from a store ----------------------------------------------
    sec_v3 = _CollapsibleSection(tab_tools, "3-D viewers from a store "
                                            "(from-store / stream)")
    bv = sec_v3.body
    v3_store_var = tk.StringVar(value="")
    v3_kind_var = tk.StringVar(value="auto")
    _store_row(bv, v3_store_var, label="store:",
               tip="The store to render from: a .npz or a store_raw/ directory.",
               with_kind=v3_kind_var)
    _store_kind_dd(bv, v3_kind_var)
    v3_verb_var = tk.StringVar(value="from-store")
    v3_verb_row = ttk.Frame(bv)
    v3_verb_row.pack(fill="x", pady=(4, 0))
    ttk.Label(v3_verb_row, text="verb:").pack(side="left")
    ttk.Combobox(v3_verb_row, textvariable=v3_verb_var,
                 values=["from-store", "stream"], state="readonly",
                 width=11).pack(side="left", padx=(6, 12))
    v3_out_var = tk.StringVar(value="")
    ttk.Label(v3_verb_row, text="out (dir / .html):").pack(side="left")
    ttk.Entry(v3_verb_row, textvariable=v3_out_var, width=24).pack(
        side="left", padx=(4, 0))
    v3_opts = ttk.Frame(bv)
    v3_opts.pack(fill="x", pady=(4, 0))
    ttk.Label(v3_opts, text="label:").pack(side="left")
    v3_label_var = tk.StringVar(value="")
    ttk.Entry(v3_opts, textvariable=v3_label_var, width=10).pack(
        side="left", padx=(4, 12))
    ttk.Label(v3_opts, text="max boxes:").pack(side="left")
    v3_maxboxes_var = tk.StringVar(value="")
    ttk.Entry(v3_opts, textvariable=v3_maxboxes_var, width=10).pack(
        side="left", padx=(4, 12))
    ttk.Label(v3_opts, text="roi size (m):").pack(side="left")
    v3_roi_size_var = tk.StringVar(value="")
    ttk.Entry(v3_opts, textvariable=v3_roi_size_var, width=7).pack(
        side="left", padx=(4, 0))
    v3_row2 = ttk.Frame(bv)
    v3_row2.pack(fill="x", pady=(4, 0))
    v3_full_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(v3_row2, text="full view", variable=v3_full_var).pack(
        side="left")
    v3_roi_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(v3_row2, text="ROI view", variable=v3_roi_var).pack(
        side="left", padx=(12, 0))
    v3_grid_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(v3_row2, text=".vxg grid cache",
                    variable=v3_grid_var).pack(side="left", padx=(12, 0))
    v3_row3 = ttk.Frame(bv)
    v3_row3.pack(fill="x", pady=(4, 0))
    ttk.Label(v3_row3, text="region x/y:").pack(side="left")
    v3_region_vars = [tk.StringVar(value="") for _ in range(4)]
    for _v in v3_region_vars:
        ttk.Entry(v3_row3, textvariable=_v, width=9).pack(side="left",
                                                          padx=(2, 0))
    _tip(v3_row3.winfo_children()[-1],
         "stream only: a metric bbox to render a subset (blank = whole store).")
    ttk.Label(v3_row3, text="keep classes:").pack(side="left", padx=(12, 2))
    v3_keep_var = tk.StringVar(value="")
    ttk.Entry(v3_row3, textvariable=v3_keep_var, width=12).pack(side="left")
    v3_row4 = ttk.Frame(bv)
    v3_row4.pack(fill="x", pady=(4, 0))
    ttk.Label(v3_row4, text="max instances:").pack(side="left")
    v3_maxinst_var = tk.StringVar(value="")
    ttk.Entry(v3_row4, textvariable=v3_maxinst_var, width=10).pack(
        side="left", padx=(4, 12))
    ttk.Label(v3_row4, text="inline MB:").pack(side="left")
    v3_inline_var = tk.StringVar(value="")
    ttk.Entry(v3_row4, textvariable=v3_inline_var, width=6).pack(
        side="left", padx=(4, 12))
    ttk.Label(v3_row4, text="tile m:").pack(side="left")
    v3_tilem_var = tk.StringVar(value="")
    ttk.Entry(v3_row4, textvariable=v3_tilem_var, width=6).pack(
        side="left", padx=(4, 0))
    ttk.Label(bv, text="stream ignores --stride / --max-boxes (it always writes "
                       "every interval); use region or keep classes to shrink "
                       "the payload.",
              justify="left", wraplength=560, foreground="#555").pack(
        anchor="w", pady=(4, 0))

    def _on_viz3d_store():
        """Validate the store, then launch viz3d_cli from-store or stream."""
        st = resolve_store_path(v3_store_var.get(), kind=v3_kind_var.get())
        if st is None:
            messagebox.showwarning("No store",
                                   "Choose a store (.npz or store_raw/).")
            return
        verb = v3_verb_var.get()
        if verb == "from-store":
            if not (v3_full_var.get() or v3_roi_var.get()):
                messagebox.showwarning("Nothing to render",
                                       "Tick full and/or ROI - both off means "
                                       "no viewer.")
                return
            out = v3_out_var.get().strip() or str(
                (Path(st) if Path(st).is_dir() else Path(st).parent) / "viz3d")
            cmd = build_viz3d_from_store_cmd(
                sys.executable, st, out,
                label=v3_label_var.get(),
                max_boxes=v3_maxboxes_var.get().strip(),
                roi_size=v3_roi_size_var.get().strip(),
                do_full=bool(v3_full_var.get()), do_roi=bool(v3_roi_var.get()),
                grid=bool(v3_grid_var.get()))
        else:
            out = v3_out_var.get().strip()
            if not out:
                base = (Path(st) if Path(st).is_dir() else Path(st))
                out = str(base / "stream.html")
            region = [v.get().strip() for v in v3_region_vars]
            region_val = tuple(float(s) for s in region) if all(region) else None
            cmd = build_viz3d_stream_cmd(
                sys.executable, st, out,
                label=v3_label_var.get(), region=region_val,
                keep_classes=v3_keep_var.get(),
                max_instances=v3_maxinst_var.get().strip(),
                inline_threshold_mb=v3_inline_var.get().strip(),
                tile_m=v3_tilem_var.get().strip())
        _launch_tool(cmd)

    ttk.Button(bv, text="Render 3-D",
               command=_on_viz3d_store).pack(anchor="w", pady=(8, 0))

    def _wrap_with_diagnostics(cmd: list[str]) -> list[str]:
        """If the diagnostics tickbox is on, prepend the voxel_runner_diagnos
        wrapper (adding --serve for the live HTML dashboard when that tickbox
        is on too). Returns the command unchanged when diagnostics is off or
        the wrapper script cannot be located."""
        if not diag_var.get():
            return cmd
        script = _find_diagnos_script()
        if script is None:
            log("WARNING: voxel_runner_diagnos.py not found (looked next to "
                "this module, at the repo root and in the working directory) "
                "- running WITHOUT diagnostics.")
            return cmd
        wrap = [sys.executable, "-u", str(script)]
        if dash_var.get():
            wrap.append("--serve")
        if dash_var.get():
            log(f"[diag] wrapping the job with {script.name} + live HTML "
                "dashboard; CPU/RAM ticks appear here and in the terminal, "
                "the dashboard opens in your browser, and the job process "
                "stays alive to serve it until you click Stop.")
        else:
            log(f"[diag] wrapping the job with {script.name}; CPU/RAM ticks "
                "appear here and in the terminal.")
        return wrap + ["--"] + cmd

    def _build_single_cmd(p, *, out_dir, cxy, cz, hm, cmode, ctop, delete_laz,
                          viz3d, viz_opts, is_stream=False,
                          keep_classes=None, shards=False, keep_raw_store=False,
                          columns_all_max=None):
        """Single-tile run as a child process (`python -m voxelizer single`),
        so the diagnostics runner has a real process tree to watch and 3-D +
        diagnostics compose. The runner auto-detects <out_dir> as the run dir
        from --output-dir, so its diagnosis/ folder lands next to the tile's
        output folder."""
        pkg = __package__ or "voxelizer"
        cmd = [sys.executable, "-u", "-m", pkg, "single", str(p),
               "--output-dir", out_dir,
               "--cell-xy", str(cxy), "--cell-z", str(cz),
               "--columns-mode", cmode, "--columns-top-n", str(ctop),
               "--height-mode", hm]
        if columns_all_max is not None:
            cmd += ["--columns-all-max", str(columns_all_max)]
        if keep_classes:
            cmd += ["--keep-classes", str(keep_classes)]
        if delete_laz:
            cmd.append("--delete-laz")
        if shards:
            cmd.append("--shards")
        if keep_raw_store:
            cmd.append("--keep-raw-store")
        if viz3d and is_stream:
            cmd += ["--viz3d-stream", "--max-instances",
                    str(viz_opts["max_instances"]),
                    "--inline-threshold-mb",
                    str(viz_opts["inline_threshold_mb"]),
                    "--tile-m", str(viz_opts["tile_m"])]
        elif viz3d:
            cmd += ["--viz3d", "--max-boxes", str(viz_opts["max_boxes"]),
                    "--roi-size", str(viz_opts["roi_size"])]
            if viz_opts["roi_cx"] is not None:
                cmd += ["--roi-cx", str(viz_opts["roi_cx"])]
            if viz_opts["roi_cy"] is not None:
                cmd += ["--roi-cy", str(viz_opts["roi_cy"])]
            if not viz_opts["do_full"]:
                cmd.append("--no-full")
            if not viz_opts["do_roi"]:
                cmd.append("--no-roi")
        return _wrap_with_diagnostics(cmd)

    def _build_area_cmd(bbox, *, out_dir, cxy, cz, cs, hm, cmode, ctop,
                        viz3d, viz_opts, is_stream, choice, budget_mb,
                        keep_npz=True, isolate_stages=True,
                        group_intervals=True, group_gap=None,
                        intermediates="auto",
                        merge_shards=True, keep_raw_store=False,
                        keep_area_raw=False, merge_band_intervals=None,
                        stage_timeout=None, keep_classes=None,
                        columns_all_max=None, tile_pitch=None, workers=None,
                        limit=None, only_stages=None):
        """Assemble the ``python -m voxelizer.area_cli area ...`` command
        line for an area run from the bbox, the explicit keyword settings
        and the remaining widget values (chunking, clip, delete-LAZ,
        stream/download JSON, per-tile isolation), translating the
        pre-flight *choice* into the RAM-strategy flags and dropping the
        retention flags a shard-only run would be refused for. Returns the
        list wrapped by _wrap_with_diagnostics."""
        pkg = __package__ or "voxelizer"
        # Belt and braces behind the widget coupling, and the only place that
        # knows the pre-flight choice: the widgets gate on "merge unticked in
        # area mode", which is right whenever the run turns out to shard, while
        # here `choice` says whether it actually did. Two rules apply.
        #
        # The columns mode is NOT one of them any more - every mode runs either
        # way - so the call below is a no-op kept as the single place the rule
        # would live again if it ever came back.
        #
        # The 3-D mode is: --viz3d-stream without --merge-shards is refused by
        # area_cli before any work starts, so a shard-only run falls back to
        # the singleton viewer here rather than assembling a command line that
        # would be rejected. See _sync_merge_stream for the widget half.
        if choice == "shard":
            cmode, _ = couple_columns_to_merge(merge_shards, cmode)
            if viz3d and viz_opts.get("mode"):
                vmode, _ = couple_stream_to_merge(merge_shards,
                                                  viz_opts["mode"])
                if vmode != viz_opts["mode"]:
                    viz_opts = {**viz_opts, "mode": vmode}
        cmd = [sys.executable, "-u", "-m", f"{pkg}.area_cli", "area",
               "--xmin", str(bbox[0]), "--ymin", str(bbox[1]),
               "--xmax", str(bbox[2]), "--ymax", str(bbox[3]),
               "--laz-dir", lazdir_var.get().strip() or "inputs/laz",
               "--output-dir", out_dir,
               "--cell-xy", str(cxy), "--cell-z", str(cz),
               "--chunk-size", str(cs),
               "--columns-mode", cmode, "--columns-top-n", str(ctop),
               "--height-mode", hm]
        if columns_all_max is not None:
            cmd += ["--columns-all-max", str(columns_all_max)]
        if keep_classes:
            cmd += ["--keep-classes", str(keep_classes)]
        if not use_chunks_var.get():
            cmd.append("--no-chunks")
        if not clip_var.get():
            cmd.append("--no-clip")
        if delete_laz_var.get():
            cmd.append("--delete-laz")
        if is_stream:
            cmd += ["--stream", "--json", json_var.get().strip()]
        elif download_var.get():
            cmd += ["--download", "--json", json_var.get().strip()]
            # Download tuning only means anything with the fetch, so it rides
            # on the same branch rather than being emitted always.
            if tile_pitch not in (None, ""):
                cmd += ["--tile-pitch", str(tile_pitch)]
            if workers not in (None, ""):
                cmd += ["--workers", str(workers)]
            if limit not in (None, ""):
                cmd += ["--limit", str(limit)]
        if viz3d and viz_opts.get("mode") == "streamable":
            cmd += ["--viz3d-stream", "--max-instances",
                    str(viz_opts["max_instances"]),
                    "--inline-threshold-mb",
                    str(viz_opts["inline_threshold_mb"]),
                    "--tile-m", str(viz_opts["tile_m"])]
        elif viz3d:
            cmd += ["--viz3d", "--max-boxes", str(viz_opts["max_boxes"]),
                    "--roi-size", str(viz_opts["roi_size"])]
            if viz_opts["roi_cx"] is not None:
                cmd += ["--roi-cx", str(viz_opts["roi_cx"])]
            if viz_opts["roi_cy"] is not None:
                cmd += ["--roi-cy", str(viz_opts["roi_cy"])]
            if not viz_opts["do_full"]:
                cmd.append("--no-full")
            if not viz_opts["do_roi"]:
                cmd.append("--no-roi")
        # Dialog outcome -> RAM strategy flags.
        b = str(int(budget_mb))
        if choice == "continue":
            cmd += ["--max-rss-mb", b]
        elif choice == "shard":
            cmd += ["--shard", "--max-rss-mb", b]
            if merge_shards:
                cmd.append("--merge-shards")
                # Only the merge reads this dial, so it is emitted only where
                # it means something rather than always.
                if merge_band_intervals not in (None, ""):
                    cmd += ["--merge-band-intervals", str(merge_band_intervals)]
            # Per-tile crash isolation (Advanced tab). retry-lazrs only means
            # anything alongside isolate-tiles, so it is gated on it.
            if isolate_tiles_var.get():
                cmd.append("--isolate-tiles")
                if retry_lazrs_var.get():
                    cmd.append("--retry-lazrs")
        # Keep vs reclaim the reusable voxel grid on disk. One checkbox, two
        # mechanisms depending on the RAM strategy: non-shard runs skip
        # area.npz; shard runs drop the shards/ cache after everything has read
        # it. Each flag is a no-op in the mode it does not apply to.
        if not keep_npz:
            cmd.append("--no-save-store")
            cmd.append("--delete-shards")
        # Both retention flags name artefacts only the merge produces, and a
        # shard-only run is refused for asking. Nothing is lost by leaving them
        # out there: there would be nothing to keep.
        merged_store_run = choice != "shard" or merge_shards
        if keep_raw_store and merged_store_run:
            cmd.append("--keep-raw-store")
        # area_raw.npz retention: only produced by grouping + save-store, so
        # emitting this is harmless (a no-op) when those are off.
        if keep_area_raw and merged_store_run:
            cmd.append("--keep-area-raw")
        # Per-stage isolation: CLI defaults to True, so only emit the flag
        # when the user explicitly unchecked the tickbox. Selecting stages OR
        # setting a stage timeout FORCES isolation: the CLI refuses
        # --only-stage / --stage-timeout under --no-isolate-stages on a plain
        # area run (a shard run isolates its merged phase regardless), so
        # neither control may emit the refusal the dialog exists to avoid.
        if (not isolate_stages and not only_stages
                and stage_timeout in (None, "")):
            cmd.append("--no-isolate-stages")
        # Blank means the CLI default, which is 0 - no limit. Emitting it only
        # when set keeps "I did not fill that box in" and "I asked for no
        # limit" the same command line.
        elif stage_timeout not in (None, ""):
            cmd += ["--stage-timeout", str(stage_timeout)]
        if not group_intervals:
            cmd.append("--no-group-intervals")
        elif group_gap not in (None, ""):
            cmd += ["--group-gap", str(group_gap)]
        if only_stages:
            cmd += ["--only-stage"] + [str(s) for s in only_stages]
        if intermediates != "auto":
            cmd += ["--intermediates", intermediates]
        # Diagnostics wrapper (tickboxes) - shared with the single-file path.
        return _wrap_with_diagnostics(cmd)

    # ------------------------------------------------------------------
    # Resume / Continue machinery.
    #
    # An interrupted or crashed area run leaves one (or both) of two
    # resumable artefacts on disk, which the CLI already knows how to pick
    # up - the GUI just never surfaced them:
    #   * <out>/store_raw/run_params.json  -> re-run output stages only
    #     (`--resume-from-store`), geometry read back from the JSON.
    #   * <out>/shards/run_config.json     -> re-voxelize only the tiles
    #     without a valid shard (`--shard --resume-shards`); the lattice /
    #     bbox / class filter are read back so the resume cannot drift from
    #     the original run.
    # ------------------------------------------------------------------
    _RESUME_STAGES = [
        ("stats", "stats.txt"),
        ("persist_npz", "area.npz"),
        ("maps", "2-D maps"),
        ("col_diag", "column diagnostics"),
        ("viz3d_roi", "3-D ROI view"),
        ("viz3d_full", "3-D full view"),
    ]

    def _detect_resumable(out_dir) -> dict:
        """Inspect *out_dir* for resumable state. Never raises."""
        import json
        info = {"store_raw": None, "run_params": None,
                "shards": None, "shard_cfg": None, "failed_tiles": None}
        out_dir = Path(out_dir)
        rp = out_dir / "store_raw" / "run_params.json"
        if rp.is_file():
            try:
                info["run_params"] = json.loads(rp.read_text(encoding="utf-8"))
                info["store_raw"] = out_dir / "store_raw"
            except Exception:  # noqa: BLE001
                pass
        sc = out_dir / "shards" / "run_config.json"
        if sc.is_file():
            try:
                info["shard_cfg"] = json.loads(sc.read_text(encoding="utf-8"))
                info["shards"] = out_dir / "shards"
            except Exception:  # noqa: BLE001
                pass
        ft = out_dir / "shards" / "failed_tiles.json"
        if ft.is_file():
            try:
                info["failed_tiles"] = json.loads(ft.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass
        return info

    def _refresh_continue_state(out_dir):
        """Light up (or grey out) the Continue button for *out_dir*."""
        info = _detect_resumable(out_dir)
        if info["store_raw"] or info["shards"]:
            continue_ctx["out_dir"] = Path(out_dir)
            continue_ctx["info"] = info
            continue_btn.configure(state="normal")
            bits = []
            if info["shards"]:
                bits.append("unfinished tiles (shard mode)")
            if info["failed_tiles"]:
                bits.append(f"{len(info['failed_tiles'])} failed tile(s)")
            if info["store_raw"]:
                bits.append("output stages (store_raw)")
            log("[resume] this run left resumable state: " + "; ".join(bits)
                + " - click Continue... to pick it up.")
        else:
            continue_ctx["out_dir"] = None
            continue_ctx["info"] = None
            continue_btn.configure(state="disabled")

    def _do_resume_stages(out_dir, store_raw, stages: list[str]):
        """Launch an ``area --resume-from-store`` child that re-runs only
        *stages* from the kept store_raw, honouring the Advanced-tab stage
        timeout and the diagnostics wrapper; records *out_dir* as the
        current run dir."""
        pkg = __package__ or "voxelizer"
        cmd = [sys.executable, "-u", "-m", f"{pkg}.area_cli", "area",
               "--resume-from-store", str(store_raw),
               "--output-dir", str(out_dir)]
        for s in stages:
            cmd += ["--only-stage", s]
        # A resume re-runs the isolated stages, so the Advanced-tab limit
        # applies here exactly as it does to a fresh run.
        if stage_timeout_var.get().strip():
            cmd += ["--stage-timeout", stage_timeout_var.get().strip()]
        cmd = _wrap_with_diagnostics(cmd)
        run_ctx["out_dir"] = str(out_dir)
        log(f"[resume] re-running stages {stages} from {store_raw}")
        _launch_subprocess(cmd)

    def _build_resume_shards_cmd(out_dir, cfg: dict) -> list[str]:
        """The `--shard --resume-shards` command line, as a list.

        Split out of the launcher so the flags this assembles - above all the
        merge/3-D pair, which is a --shard run by construction and so is
        always subject to the coupling - can be read without a subprocess.
        Raises ValueError when run_config.json cannot support a resume.
        """
        pkg = __package__ or "voxelizer"
        bbox = cfg.get("bbox")
        if not bbox or len(bbox) != 4:
            raise ValueError("shards/run_config.json has no usable bbox.")
        cmd = [sys.executable, "-u", "-m", f"{pkg}.area_cli", "area",
               "--xmin", str(bbox[0]), "--ymin", str(bbox[1]),
               "--xmax", str(bbox[2]), "--ymax", str(bbox[3]),
               "--laz-dir", lazdir_var.get().strip() or "inputs/laz",
               "--output-dir", str(out_dir),
               "--cell-xy", str(cfg.get("cell_xy")),
               "--cell-z", str(cfg.get("cell_z")),
               "--shard", "--resume-shards"]
        # Shard-lattice params come from run_config.json so the resume cannot
        # drift from the original run (the CLI would refuse a mismatch anyway).
        if cfg.get("clip") is False:
            cmd.append("--no-clip")
        kc = cfg.get("keep_classes")
        if kc:
            cmd += ["--keep-classes", ",".join(str(int(c)) for c in kc)]
        # Rendering / isolation / height come from the CURRENT GUI state, so
        # the user can, e.g., turn on 3-D or change columns detail on resume.
        # This is always a --shard run; the mode passes through the coupling
        # unchanged now that a shard-only run computes columns/ from the
        # shards, and the call stays here so the rule keeps one spelling.
        cmode, _note = couple_columns_to_merge(bool(merge_shards_var.get()),
                                               columns_mode_var.get())
        ctop = str(int(top_n_var.get())) if cmode == "top" else "50"
        cmd += ["--columns-mode", cmode, "--columns-top-n", ctop,
                "--height-mode", height_var.get(),
                "--chunk-size", str(chunk_var.get())]
        if not use_chunks_var.get():
            cmd.append("--no-chunks")
        if isolate_tiles_var.get():
            cmd.append("--isolate-tiles")
            if retry_lazrs_var.get():
                cmd.append("--retry-lazrs")
        if merge_shards_var.get():
            cmd.append("--merge-shards")
            if band_var.get().strip():
                cmd += ["--merge-band-intervals", band_var.get().strip()]
        # RAM strategy: a fresh shard run always carries --max-rss-mb from
        # the pre-flight dialog, but that dialog does not run on a resume, so
        # the watchdog gets the same default the dialog would pre-fill (60%
        # of physical RAM, 8192 MB when that cannot be read). Leaving the
        # flag off would mean a resumed run has no watchdog at all.
        from .preflight import total_ram_mb
        total = total_ram_mb()
        cmd += ["--max-rss-mb", str(int(total * 0.6) if total else 8192)]
        # Keep vs reclaim the reusable voxel grid, the same one-checkbox pair
        # the fresh-run builder emits: --no-save-store is the non-shard half
        # and a no-op here, --delete-shards drops the shards/ cache once
        # everything has read it.
        if not keep_npz_var.get():
            cmd.append("--no-save-store")
            cmd.append("--delete-shards")
        if stage_timeout_var.get().strip():
            cmd += ["--stage-timeout", stage_timeout_var.get().strip()]
        # Grouping is applied at merge time, so the current GUI grouping state
        # (not run_config.json) drives it on resume.
        if not group_var.get():
            cmd.append("--no-group-intervals")
        elif group_gap_var.get().strip():
            cmd += ["--group-gap", group_gap_var.get().strip()]
        # area_raw.npz is an artefact only the merge produces, and a shard-only
        # run is refused for asking (area_cli exits 2). Same gate the fresh-run
        # builder applies through `merged_store_run`; a resume is always a
        # --shard run, so here the merge tickbox alone decides. Nothing is lost
        # by leaving the flag out: there would be nothing to keep.
        if keep_area_raw_var.get() and merge_shards_var.get():
            cmd.append("--keep-area-raw")
        # store_raw/ retention rides the same merge gate: only the merge
        # produces it, and a shard-only run is refused for asking.
        if keep_raw_store_var.get() and merge_shards_var.get():
            cmd.append("--keep-raw-store")
        # The intermediates policy applies in shard mode too (the CLI cleans
        # up after a shard-only run as well), so the current GUI choice is
        # forwarded exactly as on a fresh run; "auto" is the CLI default.
        if interm_var.get() != "auto":
            cmd += ["--intermediates", interm_var.get()]
        if viz3d_var.get():
            vo = _collect_viz_opts()
            # A resume is a --shard run by construction, so the streaming
            # viewer needs --merge-shards here exactly as it does on a fresh
            # run; without it area_cli refuses the pair.
            vmode, _snote = couple_stream_to_merge(
                bool(merge_shards_var.get()), vo.get("mode", "singleton"))
            vo = {**vo, "mode": vmode}
            if vo.get("mode") == "streamable":
                cmd += ["--viz3d-stream", "--max-instances",
                        str(vo["max_instances"]), "--inline-threshold-mb",
                        str(vo["inline_threshold_mb"]), "--tile-m",
                        str(vo["tile_m"])]
            else:
                cmd += ["--viz3d", "--max-boxes", str(vo["max_boxes"]),
                        "--roi-size", str(vo["roi_size"])]
                if vo["roi_cx"] is not None:
                    cmd += ["--roi-cx", str(vo["roi_cx"])]
                if vo["roi_cy"] is not None:
                    cmd += ["--roi-cy", str(vo["roi_cy"])]
                if not vo["do_full"]:
                    cmd.append("--no-full")
                if not vo["do_roi"]:
                    cmd.append("--no-roi")
        return _wrap_with_diagnostics(cmd)

    def _do_resume_shards(out_dir, cfg: dict):
        """Launch the ``--resume-shards`` child for *out_dir* built from the
        saved shard config; shows an error box instead if the command cannot
        be built. Records *out_dir* as the current run dir."""
        try:
            cmd = _build_resume_shards_cmd(out_dir, cfg)
        except ValueError as exc:
            messagebox.showerror("Cannot resume", str(exc))
            return
        run_ctx["out_dir"] = str(out_dir)
        log(f"[resume] resuming shard run in {out_dir} (reusing valid shards)")
        _launch_subprocess(cmd)

    def _open_continue_dialog(out_dir, info: dict):
        """Modal 'Continue / resume run' dialog for *out_dir*: offers
        'Resume unfinished tiles' when a shards/ config is present and a
        stage-tickbox panel 'Re-run output stages' when store_raw is, or
        says nothing is resumable. Blocks until the dialog closes."""
        dlg = tk.Toplevel(root)
        dlg.title("Continue / resume run")
        dlg.transient(root)
        dlg.grab_set()
        dlg.resizable(False, False)
        body = ttk.Frame(dlg, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=f"Run directory:  {out_dir}",
                  justify="left").pack(anchor="w", pady=(0, 10))

        offered = False

        # -- Option A: resume unfinished tiles (shard mode) --
        if info["shards"] and info["shard_cfg"]:
            offered = True
            cfg = info["shard_cfg"]
            fa = ttk.LabelFrame(body, text="Resume unfinished tiles (shard mode)",
                                padding=8)
            fa.pack(fill="x", pady=(0, 10))
            nfail = len(info["failed_tiles"]) if info["failed_tiles"] else 0
            msg = (f"Re-scans shards/ and voxelizes only the tiles that do "
                   f"not yet have a valid shard, reusing the "
                   f"{cfg.get('cell_xy')}/{cfg.get('cell_z')} m lattice and "
                   f"bounding box from the original run.")
            if nfail:
                msg += (f" {nfail} tile(s) previously failed isolated decode "
                        f"and will be attempted again.")
            ttk.Label(fa, justify="left", wraplength=480, text=msg).pack(anchor="w")
            ttk.Button(fa, text="Resume unfinished tiles",
                       command=lambda: (dlg.destroy(),
                                        _do_resume_shards(out_dir, cfg))
                       ).pack(anchor="w", pady=(6, 0))

        # -- Option B: re-run output stages from store_raw --
        if info["store_raw"]:
            offered = True
            fb = ttk.LabelFrame(body, text="Re-run output stages (from store_raw)",
                                padding=8)
            fb.pack(fill="x", pady=(0, 10))
            ttk.Label(fb, justify="left", wraplength=480, text=(
                "Skips voxelization and re-runs only the chosen output stages "
                "straight from the preserved store_raw/. Geometry is read back "
                "from run_params.json - no need to re-enter the bounding box "
                "or cell size.")).pack(anchor="w")
            stage_vars: dict[str, tk.BooleanVar] = {}
            grid = ttk.Frame(fb)
            grid.pack(anchor="w", pady=(6, 0))
            for i, (sid, lbl) in enumerate(_RESUME_STAGES):
                v = tk.BooleanVar(value=sid in ("stats", "maps", "col_diag"))
                stage_vars[sid] = v
                ttk.Checkbutton(grid, text=lbl, variable=v).grid(
                    row=i // 3, column=i % 3, sticky="w", padx=(0, 14), pady=1)

            def _go_stages():
                """Collect the ticked stages (warning box if none), close
                the dialog and hand them to _do_resume_stages."""
                sel = [s for s, v in stage_vars.items() if v.get()]
                if not sel:
                    messagebox.showwarning("No stage",
                                           "Pick at least one stage to re-run.",
                                           parent=dlg)
                    return
                dlg.destroy()
                _do_resume_stages(out_dir, info["store_raw"], sel)

            ttk.Button(fb, text="Re-run selected stages",
                       command=_go_stages).pack(anchor="w", pady=(6, 0))

        if not offered:
            ttk.Label(body, text="Nothing resumable was found in this folder.",
                      foreground="#b00020").pack(anchor="w")
        ttk.Button(body, text="Cancel", command=dlg.destroy).pack(
            anchor="e", pady=(4, 0))
        root.wait_window(dlg)

    def _on_continue():
        """Continue button: open the resume dialog on the run dir and
        detection result recorded in continue_ctx, if any."""
        if not continue_ctx["out_dir"]:
            return
        _open_continue_dialog(continue_ctx["out_dir"], continue_ctx["info"])

    def _resume_previous():
        """'Resume previous run...' button: ask for an earlier run's output
        folder, probe it with _detect_resumable and open the resume dialog,
        or show an info box when it holds neither store_raw nor shards."""
        d = filedialog.askdirectory(
            title="Choose a previous run's output directory")
        if not d:
            return
        info = _detect_resumable(d)
        if not (info["store_raw"] or info["shards"]):
            messagebox.showinfo(
                "Nothing to resume",
                "No store_raw/run_params.json or shards/run_config.json was "
                "found in that folder.\n\nA run is resumable only if it kept "
                "its per-stage store ('Keep per-stage store' on the Files & "
                "Outputs tab) or ran in shard mode.")
            return
        _open_continue_dialog(Path(d), info)

    def _on_run():
        """Handle the Run button. Parse and check the shared fields (cell sizes,
        columns mode, 3-D options, chunk size), then in file mode builds and
        launches the single-tile command; in area mode it checks the bbox and
        JSON requirements, runs the pre-flight estimate and modal dialog, and
        launches the area command with the chosen RAM strategy unless the
        user cancelled. Every failure is reported through a message box."""
        try:
            cxy, cz = float(cell_xy_var.get()), float(cell_z_var.get())
            if cxy <= 0 or cz <= 0:
                raise ValueError("cell sizes must be positive")
            cmode = columns_mode_var.get()
            ctop = int(top_n_var.get()) if cmode == "top" else 50
            all_max = (all_max_var.get().strip() or None
                       if cmode == "all" else None)
            viz3d = bool(viz3d_var.get())
            viz_opts = _collect_viz_opts() if viz3d else {}
            if viz3d and viz_opts.get("mode") != "streamable" \
                    and not (viz_opts["do_full"] or viz_opts["do_roi"]):
                raise ValueError("3-D is on but both views are unchecked")
            cs = int(chunk_var.get())
        except ValueError as e:
            messagebox.showerror("Invalid value", str(e));
            return
        out_dir = out_var.get().strip()
        run_ctx["out_dir"] = out_dir  # so _job_done can probe it for resume state
        hm = height_var.get()
        if mode_var.get() == "file":
            p = path_var.get().strip().strip('"').strip("'")
            if not p:
                messagebox.showwarning("No file", "Choose or drop a LAZ file.");
                return
            dl = delete_laz_var.get()
            # File mode is now unified with area mode: one subprocess path
            # (`python -m voxelizer single ...`), so it is crash-isolated, Stop
            # works, and 3-D + live diagnostics compose. Diagnostics wrapping
            # is applied by _build_single_cmd only when the tickbox is on.
            is_stream = viz3d and viz_opts.get("mode") == "streamable"
            cmd = _build_single_cmd(p, out_dir=out_dir, cxy=cxy, cz=cz,
                                    hm=hm, cmode=cmode, ctop=ctop,
                                    delete_laz=dl, viz3d=viz3d,
                                    viz_opts=viz_opts, is_stream=is_stream,
                                    keep_classes=keep_classes_var.get().strip() or None,
                                    shards=shards_var.get(),
                                    keep_raw_store=keep_raw_store_single_var.get(),
                                    columns_all_max=all_max)
            _launch_subprocess(cmd)
            return
        else:
            try:
                bbox = (float(xmin_var.get()), float(ymin_var.get()),
                        float(xmax_var.get()), float(ymax_var.get()))
            except ValueError:
                messagebox.showerror("Invalid value", "Coordinates must be numbers.");
                return
            if bbox[0] > bbox[2] or bbox[1] > bbox[3]:
                messagebox.showerror("Invalid bbox", "min must be <= max.");
                return
            is_stream = stream_var.get()
            if is_stream:
                if not json_var.get().strip():
                    messagebox.showwarning("No JSON", "Streaming mode requires an inventory JSON file.")
                    return
            else:
                if download_var.get() and not json_var.get().strip():
                    messagebox.showwarning("No JSON", "Download is enabled but no inventory JSON file is selected.")
                    return
            # ---- ALWAYS pre-flight: estimate, then let the user decide ----
            from .preflight import (estimate_area_run, format_estimate,
                                    total_ram_mb)
            try:
                est = estimate_area_run(
                    bbox, cell_xy=cxy, cell_z=cz, stream=is_stream,
                    json_file=json_var.get().strip() or None,
                    laz_dir=lazdir_var.get().strip() or None,
                    clip=clip_var.get(), group_intervals=group_var.get())
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror(
                    "Pre-flight failed",
                    f"Could not estimate this run:\n{type(exc).__name__}: {exc}")
                return
            total = total_ram_mb()
            default_budget = int(total * 0.6) if total else 8192
            head = ""
            if total:
                head = f"This machine has {total:,.0f} MB of physical RAM.\n\n"
            choice, budget = _preflight_dialog(head + format_estimate(est),
                                               default_budget)
            if choice == "cancel":
                log("Run cancelled at pre-flight.")
                return
            log(f"Pre-flight: {est['n_tiles']} tile(s), "
                f"{est['covered_km2']:.2f} km2, est. peak "
                f"{est['est_peak_mb']:,.0f} MB; choice = {choice}, "
                f"budget = {budget:.0f} MB.")
            cmd = _build_area_cmd(bbox, out_dir=out_dir, cxy=cxy, cz=cz,
                                  cs=cs, hm=hm, cmode=cmode, ctop=ctop,
                                  viz3d=viz3d, viz_opts=viz_opts,
                                  is_stream=is_stream, choice=choice,
                                  budget_mb=budget, keep_npz=keep_npz_var.get(),
                                  isolate_stages=isolate_var.get(),
                                  group_intervals=group_var.get(),
                                  group_gap=group_gap_var.get().strip(),
                                  intermediates=interm_var.get(),
                                  merge_shards=merge_shards_var.get(),
                                  keep_raw_store=keep_raw_store_var.get(),
                                  keep_area_raw=keep_area_raw_var.get(),
                                  merge_band_intervals=band_var.get().strip(),
                                  stage_timeout=stage_timeout_var.get().strip(),
                                  keep_classes=keep_classes_var.get().strip() or None,
                                  columns_all_max=all_max,
                                  tile_pitch=tile_pitch_var.get().strip(),
                                  workers=dl_workers_var.get().strip(),
                                  limit=dl_limit_var.get().strip(),
                                  only_stages=[s for s, v in only_stage_vars.items()
                                               if v.get()] or None)
            _launch_subprocess(cmd)
            return

    btns = ttk.Frame(root, padding=(12, 8, 12, 12))
    btns.pack(fill="x")
    # One shutdown path for both ways out of the window, so a viewer server
    # cannot survive either of them.
    ttk.Button(btns, text="Close", command=_on_close).pack(side="right")
    root.protocol("WM_DELETE_WINDOW", _on_close)
    run_holder = ttk.Frame(btns);
    run_holder.pack(side="right", padx=6)
    run_btn = ttk.Button(run_holder, text="Run")
    run_btn.pack()
    run_btn.configure(command=_on_run)
    stop_btn = ttk.Button(btns, text="Stop", state="disabled", command=_on_stop)
    stop_btn.pack(side="right", padx=(0, 6))
    _tip(stop_btn,
         "Terminates the running job. Shards are written atomically (temp "
         "file + rename) and any half-written shard is swept on the next "
         "resume, so stopping never corrupts an already-finished shard or "
         "store. If the run hadn't reached a persisted shard/store_raw yet "
         "(e.g. mid-voxelization of a plain, non-sharded run), there is "
         "simply nothing to resume - Continue... only lights up when it "
         "finds something.")
    continue_btn = ttk.Button(btns, text="Continue...", state="disabled",
                              command=lambda: _on_continue())
    continue_btn.pack(side="right", padx=(0, 6))
    _tip(continue_btn,
         "Enabled after a run that left resumable state (a kept store_raw/ "
         "or a shards/ folder). Resume unfinished tiles, or re-run "
         "individual output stages, without re-voxelizing what already "
         "completed.")

    _show_mode()
    _sync_columns_enabled()
    _sync_topn()
    _sync_viz()
    _sync_keep_area_raw()
    _sync_merge_columns()
    _sync_merge_stream()
    _sync_ts_source()
    _sync_server_buttons()
    _update_tile_count()
    _validate_all()
    _update_summary()

    # The seam for tests and for any caller that wants to drive the dialog:
    # the command builders and the variables whose combinations carry rules,
    # nothing decorative. Everything else stays closure-local.
    return {
        "root": root,
        "notebook": nb,
        "tabs": [nb.tab(t, "text") for t in nb.tabs()],
        "vars": {
            "mode": mode_var,
            "columns_enabled": columns_enabled_var,
            "columns_mode": columns_mode_var,
            "columns_all_max": all_max_var,
            "keep_classes": keep_classes_var,
            "shards": shards_var,
            "keep_raw_store_single": keep_raw_store_single_var,
            "tile_pitch": tile_pitch_var,
            "workers": dl_workers_var,
            "limit": dl_limit_var,
            "download": download_var,
            "json": json_var,
            "only_stages": only_stage_vars,
            "merge_shards": merge_shards_var,
            "merge_note": merge_note_var,
            "keep_area_raw": keep_area_raw_var,
            "viz3d": viz3d_var,
            "viz3d_mode": viz3d_mode_var,
            "stream_note": stream_note_var,
            "merge_band_intervals": band_var,
            "stage_timeout": stage_timeout_var,
            "tileset_src": ts_src_var,
            "tileset_kind": ts_kind_var,
            "tileset_out": ts_out_var,
            "tileset_tile_m": ts_tile_m_var,
            "tileset_lod": ts_lod_var,
            "tileset_geoid": ts_geoid_var,
            "tileset_keep_classes": ts_keep_var,
            "tileset_region": ts_region_vars,
            "tileset_crs": ts_crs_var,
            "tileset_height_offset": ts_hofs_var,
            "tileset_vertical_crs": ts_vcrs_var,
            "tileset_payload_idx": tp_idx_var,
            "tileset_payload_bin": tp_bin_var,
            "tileset_payload_out": tp_out_var,
            "serve_dir": srv_dir_var,
            "serve_viewer": srv_viewer_var,
            "serve_port": srv_port_var,
            "serve_bind": srv_bind_var,
            "serve_no_open": srv_noopen_var,
            "output_dir": out_var,
        },
        "build_area_cmd": _build_area_cmd,
        "build_single_cmd": _build_single_cmd,
        "build_resume_shards_cmd": _build_resume_shards_cmd,
        # Store Tools builders are module-level pure functions; expose them on
        # the same seam so a caller can assemble a tool command without a
        # window, mirroring build_resume_shards_cmd.
        "tool_builders": {
            "postprocess": build_postprocess_cmd,
            "reconstruct": build_reconstruct_cmd,
            "archive": build_archive_cmd,
            "merge": build_merge_cmd,
            "shard_diagnostics": build_shard_diag_cmd,
            "viz3d_from_store": build_viz3d_from_store_cmd,
            "viz3d_stream": build_viz3d_stream_cmd,
        },
        "resolve_store_path": resolve_store_path,
        "servers": servers,
        "stop_servers": _stop_servers,
        "close": _on_close,
    }


def main() -> None:
    """Entry point: create the root window (drag-and-drop aware when
    tkinterdnd2 is available), build the dialog and run the Tk main loop.
    The dialog takes no options; the parser exists so that ``--help`` prints
    usage and exits, and an unknown flag is refused, before any window or
    output folder is created."""
    argparse.ArgumentParser(
        prog="python -m voxelizer.gui_area",
        description="Open the IA.rbre voxelizer dialog (single-file and "
                    "area modes); it takes no options.").parse_args()
    root = TkinterDnD.Tk() if _DND_OK else tk.Tk()
    build_app(root)
    root.mainloop()
    sys.exit(0)


if __name__ == "__main__":
    main()
