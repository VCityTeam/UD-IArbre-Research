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
store to 3-D Tiles (``tileset_cli from-store``) and putting either kind of
output in front of a browser (``serve_voxel_html`` for a streaming viewer,
``serve_tiles`` for a tileset). Both exporters already write their own
double-clickable ``.cmd`` launchers beside the artifacts, so those buttons are
for ad-hoc viewing from the window that is already open. A served directory
keeps a child process alive until it is stopped, so servers are tracked
separately from the job and terminated when the window closes.

Layout / usability notes:

  * The dialog is a ``ttk.Notebook`` with seven tabs (Input, Voxel Grid, Files &
    Outputs, 3-D Visualiser, Export & Serve, Diagnostics, Advanced) instead of
    one long scroll of checkboxes, so related settings are visually grouped and
    rarely-touched options are a click away rather than always in view.
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


def resolve_store_path(path) -> Path | None:
    """The ``.npz`` store meant by *path*, or None if there is none.

    A user picks the thing they can see - usually a run's output directory -
    rather than the file inside it, so a directory is resolved to the store
    the area pipeline writes there (``area.npz``, falling back to the
    pre-grouping ``area_raw.npz``). An explicit file is taken as given.
    """
    if not path:
        return None
    p = Path(str(path).strip().strip('"').strip("'"))
    if p.is_file():
        return p
    if p.is_dir():
        for name in ("area.npz", "area_raw.npz"):
            cand = p / name
            if cand.is_file():
                return cand
    return None


def build_tileset_cmd(python: str, store_npz, out_dir, *, tile_m: float,
                      lod: bool = True, keep_classes: str = "",
                      geoid: bool = True) -> list[str]:
    """``python -m voxelizer.tileset_cli from-store ...`` as an argv list.

    Mirrors the CLI's own surface, which deliberately has no ``--max-instances``:
    that number is a viewer GPU working-set budget for the streaming HTML
    viewer, and the intermediate payload it would size is deleted by this
    export path, so it could never change a tileset. The LOD pyramid is the
    default there and here; the checkbox emits ``--flat`` when cleared.
    """
    cmd = [python, "-u", "-m", f"{__package__ or 'voxelizer'}.tileset_cli",
           "from-store", str(store_npz), "--out-dir", str(out_dir),
           "--tile-m", str(tile_m)]
    if not lod:
        cmd.append("--flat")
    if keep_classes.strip():
        cmd += ["--keep-classes", keep_classes.strip()]
    if not geoid:
        cmd.append("--no-geoid")
    return cmd


def build_serve_stream_cmd(python: str, serve_dir, *,
                           page: str | None = None) -> list[str]:
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
    """
    port = "0"
    bind = os.environ.get("VOXELIZER_BIND", "").strip()
    if bind:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind((bind, 8000))
            port = "8000"
        except OSError:
            port = "0"
    cmd = [python, "-u", "-m", f"{__package__ or 'voxelizer'}.serve_voxel_html",
           str(serve_dir), "--port", port, "--open"]
    if page:
        cmd += ["--open-page", page]
    return cmd


def build_serve_tiles_cmd(python: str, serve_dir, viewer: str) -> list[str]:
    """``python -m voxelizer.serve_tiles <dir> --open-viewer {cesium,itowns}``."""
    return [python, "-u", "-m", f"{__package__ or 'voxelizer'}.serve_tiles",
            str(serve_dir), "--open-viewer", viewer]


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
    nb.add(tab_input, text="Input")
    nb.add(tab_grid, text="Voxel Grid")
    nb.add(tab_files, text="Files & Outputs")
    nb.add(tab_viz, text="3-D Visualiser")
    nb.add(tab_export, text="Export & Serve")
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

    def _toggle_download(*_):
        """Show the inventory-JSON row only while 'Download tiles' is ticked."""
        if download_var.get():
            json_frame.pack(side="left", padx=(12, 0), fill="x", expand=True)
        else:
            json_frame.pack_forget()

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
    _last_columns_detail = {"v": "diag"}

    def _sync_topn(*_):
        """Show the 'top N' label and spinbox only when column diagnostics
        are enabled and the detail mode is 'top'."""
        if columns_enabled_var.get() and columns_mode_var.get() == "top":
            topn_label.pack(side="left")
            topn_spin.pack(side="left", padx=(2, 0))
        else:
            topn_label.pack_forget()
            topn_spin.pack_forget()

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
        else:
            stream_row.pack_forget()
            stream_json_row.pack_forget()
            dl_frame.pack_forget()
            files_area_frame.grid_remove()
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
         "A run's output directory (area.npz is found inside it, falling back "
         "to the pre-grouping area_raw.npz) or the path of a store .npz "
         "directly. The resolved file is shown below, so there is no guessing "
         "about which store is being exported.")

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
        with the .npz resolve_store_path finds there, or a not-found note."""
        npz = resolve_store_path(ts_src_var.get())
        if npz is None:
            ts_resolved_var.set("(no area.npz / area_raw.npz found there yet)")
        else:
            ts_resolved_var.set(f"store: {npz}")

    ts_src_var.trace_add("write", _sync_ts_source)

    def _on_export_tileset():
        """'Export tileset' button: resolve the source store and validate the
        tile size (message boxes on failure), default the output dir to
        ``<store dir>/tiles``, build the tileset_cli command and launch it as
        the job child."""
        npz = resolve_store_path(ts_src_var.get())
        if npz is None:
            messagebox.showwarning(
                "No store",
                "Point at a run output directory containing area.npz (or "
                "area_raw.npz), or at a store .npz directly.")
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
        out_dir = ts_out_var.get().strip() or str(npz.parent / "tiles")
        cmd = build_tileset_cmd(sys.executable, npz, out_dir,
                                tile_m=tile_m, lod=bool(ts_lod_var.get()),
                                keep_classes=ts_keep_var.get(),
                                geoid=bool(ts_geoid_var.get()))
        log(f"[tileset] exporting {npz} -> {out_dir}")
        _launch_subprocess(cmd)

    ttk.Button(ts_frame, text="Export tileset",
               command=_on_export_tileset).pack(anchor="w", pady=(8, 0))

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
        _start_server(build_serve_stream_cmd(sys.executable, d, page=page),
                      "stream")

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
            build_serve_tiles_cmd(sys.executable, d, srv_viewer_var.get()),
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
                          viz3d, viz_opts, is_stream=False):
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
        if delete_laz:
            cmd.append("--delete-laz")
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
                        stage_timeout=None):
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
        # when the user explicitly unchecked the tickbox.
        if not isolate_stages:
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
                                    viz_opts=viz_opts, is_stream=is_stream)
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
                                  stage_timeout=stage_timeout_var.get().strip())
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
            "merge_shards": merge_shards_var,
            "merge_note": merge_note_var,
            "keep_area_raw": keep_area_raw_var,
            "viz3d": viz3d_var,
            "viz3d_mode": viz3d_mode_var,
            "stream_note": stream_note_var,
            "merge_band_intervals": band_var,
            "stage_timeout": stage_timeout_var,
            "tileset_src": ts_src_var,
            "tileset_out": ts_out_var,
            "tileset_tile_m": ts_tile_m_var,
            "tileset_lod": ts_lod_var,
            "tileset_geoid": ts_geoid_var,
            "tileset_keep_classes": ts_keep_var,
            "serve_dir": srv_dir_var,
            "serve_viewer": srv_viewer_var,
            "output_dir": out_var,
        },
        "build_area_cmd": _build_area_cmd,
        "build_single_cmd": _build_single_cmd,
        "build_resume_shards_cmd": _build_resume_shards_cmd,
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
