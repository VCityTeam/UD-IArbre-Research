"""
CLI entry point for the per-tile ("single") voxelizer workflow and for
viewing a finished run.
@ingroup t4_entrees


    python -m voxelizer single PATH --output-dir DIR [--cell-xy F] [--cell-z F]
        [--columns-mode {diag,top,all,skip}] [--columns-top-n N]
        [--height-mode {default,relative,absolute}] [--keep-classes LIST]
        [--delete-laz] [--shards] [--keep-raw-store]
        [--viz3d [--max-boxes N] [--roi-size M] [--roi-cx M] [--roi-cy M]
                 [--no-full] [--no-roi]]
        [--viz3d-stream [--tile-m 64] [--max-instances N]
                        [--inline-threshold-mb 64]]

        Voxelize one LAZ file. Writes DIR/<tile_stem>/*.png and
        DIR/<tile_stem>/stats.txt. DIR defaults to
        outputs/RunN/single/ with auto-incrementing Run number. With
        --shards, also writes the tile's store as DIR/shards/<tile_stem>.npz
        + DIR/shards/manifest.json, the same shard format an area run leaves
        in shards/ (see sharding.save_single_tile_shard). With
        --keep-raw-store, also writes the grid as the raw memory-mappable
        directory DIR/store_raw/ plus its run_params.json, the same artefact
        area_cli --resume-from-store re-enters.

    python -m voxelizer serve DIR [--kind {stream,tiles}] [--port N]
        [--bind HOST] [--no-open] [--open-viewer {cesium,itowns}]
        [--open-page NAME]

        Serve a finished output directory and open it in a browser. Which
        server that means is READ OFF THE DIRECTORY: a ``*_stream.html``
        makes it the Range-capable streaming server
        (voxelizer.serve_voxel_html), a ``tileset.json`` makes it the
        3-D Tiles server (voxelizer.serve_tiles). A directory holding
        both is genuinely ambiguous and is refused until ``--kind`` says
        which; a directory holding neither is refused with what was looked
        for. Both servers keep their own defaults for everything not passed
        here, so this verb adds a shortcut, never a second set of defaults.

With --viz3d it ALSO renders the interactive Three.js HTML viewer(s)
(``<stem>_full.html`` / ``<stem>_roi.html``) into the same ``<tile_stem>/`` folder,
delegating to viz3d_cli._run_single with every ROI/box option.
This is what lets the GUI run one unified subprocess path in which 3-D and
the live diagnostics compose. The heavier pyvista-based show_pyvista() is
still not wired here; use voxelizer.show_pyvista() directly for that.
"""

from __future__ import annotations
import argparse
import logging
import sys
from .cli_common import add_cell_args, add_viz3d_geom_args, parse_classes
from pathlib import Path

try:
    from .pipeline import process_single_tile
except ImportError:  # support running the file directly without a package install
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from voxelizer.pipeline import process_single_tile  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# `serve`: which server a finished output directory needs
#
# The two servers are not interchangeable - one answers HTTP Range for a
# `.bin` sidecar, the other sends glTF content types and carries the bundled
# viewer pages - and remembering which module goes with which directory is
# exactly the knowledge a user should not have to hold. Each output kind
# leaves an unmistakable marker file, so the directory can be asked instead.
# ---------------------------------------------------------------------------
STREAM_PAGE_GLOB = "*_stream.html"
TILESET_NAME = "tileset.json"

# Preferred page when a directory holds several streaming viewers: the area
# pipeline's own name, so `serve` opens what the run advertises rather than
# whichever name sorts first.
_PREFERRED_STREAM_PAGE = "area_stream.html"


def find_stream_page(serve_dir: Path) -> Path | None:
    """The streaming viewer page in *serve_dir*, or None.

    ``area_stream.html`` wins when present; otherwise the remaining
    ``*_stream.html`` are ordered by name so the answer is deterministic
    rather than filesystem-order dependent.
    """
    try:
        pages = sorted(p for p in serve_dir.glob(STREAM_PAGE_GLOB) if p.is_file())
    except OSError:
        return None
    if not pages:
        return None
    for p in pages:
        if p.name == _PREFERRED_STREAM_PAGE:
            return p
    return pages[0]


def sniff_serve_kind(serve_dir: Path) -> str:
    """Classify *serve_dir* as ``stream`` / ``tiles`` / ``both`` / ``neither``.

    ``both`` and ``neither`` are answers, not failures: the caller turns them
    into a request for ``--kind`` and into an explanation of what was looked
    for, which is more use than a guess would be.
    """
    has_stream = find_stream_page(serve_dir) is not None
    has_tiles = (serve_dir / TILESET_NAME).is_file()
    if has_stream and has_tiles:
        return "both"
    if has_stream:
        return "stream"
    if has_tiles:
        return "tiles"
    return "neither"


def build_serve_argv(kind: str, serve_dir: Path, *, port: int | None = None,
                     bind: str | None = None, no_open: bool = False,
                     open_viewer: str = "cesium",
                     open_page: str | None = None) -> list[str]:
    """The argv the chosen server's ``main`` is called with.

    ``port`` and ``bind`` are omitted when not given, so each server keeps its
    own documented default (8000 vs an OS-chosen free port; localhost or
    ``$VOXELIZER_BIND``) instead of this verb inventing a third.

    Opening a browser is the default here - a verb whose whole purpose is to
    look at a result should not need a second flag to show it - and
    ``--no-open`` is forwarded so the servers' own suppression (and
    ``VOXELIZER_NO_BROWSER``) still has the last word.
    """
    argv = [str(serve_dir)]
    if port is not None:
        argv += ["--port", str(port)]
    if bind is not None:
        argv += ["--bind", bind]
    if kind == "tiles":
        argv += ["--open-viewer", open_viewer]
    else:
        argv.append("--open")
        if open_page:
            argv += ["--open-page", open_page]
    if no_open:
        argv.append("--no-open")
    return argv


def _run_serve(args, parser: argparse.ArgumentParser) -> int:
    """Run the ``serve`` verb: pick the server kind (``--kind`` or sniffed
    off the directory, with ``parser.error`` for a missing directory, an
    ambiguous one holding both artifacts, or one holding neither), build the
    chosen server's argv and hand it to ``serve_tiles.main`` or
    ``serve_voxel_html.main``. Returns that server's exit code (0 for the
    streaming server)."""
    serve_dir = args.dir
    if not serve_dir.is_dir():
        parser.error(f"serve: not a directory: {serve_dir}")

    kind = args.kind or sniff_serve_kind(serve_dir)
    if kind == "both":
        parser.error(
            f"serve: {serve_dir} holds both a streaming viewer "
            f"({STREAM_PAGE_GLOB}) and a tileset ({TILESET_NAME}), which need "
            f"different servers. Pass --kind stream or --kind tiles.")
    if kind == "neither":
        parser.error(
            f"serve: nothing servable in {serve_dir} - no {STREAM_PAGE_GLOB} "
            f"(streaming viewer) and no {TILESET_NAME} (3-D Tiles). Point at "
            f"a run's output directory, or at the tileset directory a "
            f"tileset_cli export wrote.")

    page = args.open_page
    if kind == "stream" and page is None:
        found = find_stream_page(serve_dir)
        page = found.name if found is not None else None

    argv = build_serve_argv(kind, serve_dir, port=args.port, bind=args.bind,
                            no_open=args.no_open,
                            open_viewer=args.open_viewer, open_page=page)
    if kind == "tiles":
        from . import serve_tiles
        return int(serve_tiles.main(argv))
    # Imported here rather than at module level purely to keep this module's
    # import cheap. It is NOT what avoids the runpy warning: `python -m
    # voxelizer.serve_voxel_html` never imports this module at all - only the
    # package __init__ - so a module-level import here could not have put the
    # server into sys.modules ahead of runpy. What does avoid the warning is
    # __init__ naming the server in __all__ without importing it; see the note
    # there.
    from . import serve_voxel_html
    serve_voxel_html.main(argv)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """Build the ``python -m voxelizer`` parser with the ``single`` (one LAZ
    file, output dir, column/height options, ``--viz3d`` and
    ``--viz3d-stream`` with their geometry and streaming knobs) and ``serve``
    (directory, ``--kind``, ``--port``, ``--bind``, ``--no-open``,
    ``--open-viewer``, ``--open-page``) sub-commands."""
    p = argparse.ArgumentParser(
        prog="python -m voxelizer",
        description="IA.rbre LiDAR voxelizer: process one tile, or serve a "
                    "finished output directory.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # --- single ----------------------------------------------------------
    s = sub.add_parser("single", help="Process one LAZ tile.")
    add_viz3d_geom_args(s, max_boxes=5_000_000, roi_size=200.0)
    add_cell_args(s)
    s.add_argument("laz_file", type=Path, help="Path to a .laz or .las file.")
    s.add_argument(
        "--output-dir", "-o", type=Path, default=None,
        help="Run directory; tile outputs go to <out>/<tile_stem>/. "
             "Defaults to outputs/RunN/single/ with auto-incrementing Run number.",
    )
    s.add_argument("--columns-mode", choices=("all", "top", "diag", "skip"),
                   default="diag",
                   help="Per-column PNG output: 'all' = every occupied "
                        "column (slow - a dense 500 m tile at the default "
                        "0.5 m cell has roughly 800k occupied columns); "
                        "'top' = top N by "
                        "interval count; 'diag' = 4 diagnostic PNGs only "
                        "(samples, top complex, histogram, by-class); 'skip' = no "
                        "columns/ folder. Default 'diag'.")
    s.add_argument("--columns-top-n", type=int, default=50,
                   help="How many columns to keep when --columns-mode=top "
                        "(default 50).")
    s.add_argument("--columns-all-max", type=int, default=None,
                   help="Cap on the per-column PNGs in --columns-mode=all "
                        "(default 500000; 0 removes the cap). Ignored "
                        "otherwise.")
    s.add_argument("--height-mode", choices=("default", "relative", "absolute"),
                   default="default",
                   help="Vertical reference for the max_height map: "
                        "'default' auto-contrasts the tops; 'relative' shows "
                        "height above each column's own lowest occupied voxel "
                        "(nDSM/CHM-style); "
                        "'absolute' shows true altitude from the tile floor "
                        "(DSM). Default 'default'.")
    s.add_argument("--delete-laz", action="store_true",
                   help="Delete the source .laz file after successful voxelization.")
    s.add_argument("--keep-classes", type=str, default=None,
                   help="Comma/space list of ASPRS class codes to keep; all "
                        "others are dropped before voxelization. Default: keep "
                        "every class.")
    s.add_argument("--shards", action="store_true",
                   help="Also save the tile's voxel store as a shard: "
                        "<out>/shards/<tile_stem>.npz + shards/manifest.json, "
                        "the same format an area run writes. One run writes "
                        "one shard, so use a fresh --output-dir per tile.")
    s.add_argument("--keep-raw-store", action="store_true",
                   help="Also write the voxel grid as the raw memory-mappable "
                        "directory <out>/store_raw/ (six .npy arrays + "
                        "meta.json) with its run_params.json, the artefact "
                        "area_cli --resume-from-store re-enters. Not deleted: "
                        "this verb has no intermediates sweep.")

    # --- 3-D HTML viewer (all options mirror the area CLI / viz3d_cli) ----
    s.add_argument("--viz3d", action="store_true",
                   help="Also render interactive 3-D HTML viewer(s) (Three.js) "
                        "into <out>/<tile_stem>/.")
    s.add_argument("--viz3d-stream", action="store_true",
                   help="Render streaming HTML viewer (handles 200M+ boxes).")
    s.add_argument("--tile-m", type=float, default=64.0,
                   help="Streaming viewer tile size in metres (default 64). "
                        "Only used with --viz3d-stream.")
    s.add_argument("--max-instances", type=int, default=4_000_000,
                   help="Hard GPU cap for streaming viewer (default 4M). "
                        "Only used with --viz3d-stream.")
    s.add_argument("--inline-threshold-mb", type=int, default=64,
                   help="If .bin is below this size, base64-embed it. "
                        "Default 64 MB. Only used with --viz3d-stream.")

    # --- serve -----------------------------------------------------------
    v = sub.add_parser(
        "serve",
        help="Serve a finished output directory and open it in a browser.")
    v.add_argument("dir", type=Path,
                   help="A run's output directory (holding *_stream.html) or "
                        "a 3-D Tiles directory (holding tileset.json).")
    v.add_argument("--kind", choices=("stream", "tiles"), default=None,
                   help="Which server to use. Read off the directory by "
                        "default; only needed when it holds both a streaming "
                        "viewer and a tileset.")
    v.add_argument("--port", type=int, default=None,
                   help="TCP port. Left to the chosen server's own default "
                        "when omitted (8000 for the streaming viewer, an "
                        "OS-chosen free port for a tileset); 0 always asks "
                        "the OS for a free one.")
    v.add_argument("--bind", default=None,
                   help="Interface to bind. Left to the chosen server's own "
                        "default when omitted (localhost, or $VOXELIZER_BIND "
                        "when that is set).")
    v.add_argument("--no-open", action="store_true",
                   help="Serve without opening a browser. VOXELIZER_NO_BROWSER"
                        "=1 in the environment does the same.")
    v.add_argument("--open-viewer", choices=("cesium", "itowns"),
                   default="cesium",
                   help="Tileset directories only: which bundled viewer page "
                        "to open (default cesium).")
    v.add_argument("--open-page", default=None, metavar="NAME",
                   help="Streaming directories only: which page to open "
                        "(default: the directory's own *_stream.html, "
                        "preferring area_stream.html).")

    return p


def _write_single_run_params(store_raw_dir: Path, store, *, cell_xy, cell_z,
                             height_mode, columns_mode, columns_top_n,
                             max_boxes, roi_size, roi_cx, roi_cy,
                             keep_classes) -> Path:
    """Write ``run_params.json`` beside a single-tile raw store.

    The area pipeline's raw store carries this file so
    ``area_cli --resume-from-store DIR`` can re-run the output stages (maps,
    columns, 3-D, ``area.npz``) without the geometry being supplied again; the
    GUI's resume detector reads the same file. A single tile's grid is one
    extent, so the ``bbox`` the schema expects is synthesised from the store's
    own occupied extent, and every other key mirrors what
    ``area_outputs._write_outputs_isolated`` writes. ``group_intervals`` is
    recorded False: a single-tile run never groups, so the store on disk is
    already the raw one.

    @param store_raw_dir The directory just written by ``ColumnStore.save_dir``.
    @param store The grid, for its extent (bbox) and lattice metadata.
    @return The path written.
    """
    import json
    from .io_laz import DEFAULT_EPSG
    from .run_utils import make_provenance
    from .voxelize import _run_order

    kb = store.key_bounds()
    if kb is None:
        bbox = [float(store.x_min), float(store.y_min),
                float(store.x_min) + float(cell_xy),
                float(store.y_min) + float(cell_xy)]
    else:
        bbox = [float(store.x_min) + kb[0] * float(cell_xy),
                float(store.y_min) + kb[1] * float(cell_xy),
                float(store.x_min) + (kb[2] + 1) * float(cell_xy),
                float(store.y_min) + (kb[3] + 1) * float(cell_xy)]

    params = {
        "bbox": bbox, "cell_xy": float(cell_xy), "cell_z": float(cell_z),
        "height_mode": height_mode, "extra_header": None,
        "columns_mode": columns_mode, "columns_top_n": int(columns_top_n),
        "columns_all_max": None, "max_boxes": int(max_boxes),
        "roi_size": float(roi_size), "roi_cx": roi_cx, "roi_cy": roi_cy,
        "save_store": True, "viz3d_stream": False,
        "tile_m": 64.0, "max_instances": 4_000_000,
        "inline_threshold_mb": 64,
        "provenance": make_provenance(
            group_intervals=False, group_gap=None, keep_classes=keep_classes,
            epsg=DEFAULT_EPSG, run_order=_run_order()),
    }
    path = Path(store_raw_dir) / "run_params.json"
    path.write_text(json.dumps(params, indent=2), encoding="utf-8")
    logger.info("wrote %s (resume with: python -m voxelizer.area_cli area "
                "--resume-from-store %s)", path, store_raw_dir)
    return path


def main(argv=None) -> None:
    """Parse the ``single`` or ``serve`` verb. ``serve`` delegates to
    _run_serve() and raises ``SystemExit`` with its code when non-zero.
    ``single`` voxelizes the tile once through ``process_single_tile`` into
    ``<output-dir>/<tile_stem>/`` (auto-creating ``outputs/RunN/single``
    when no directory is given), then renders the streaming viewer
    (``--viz3d-stream``) or the box viewers (``--viz3d``) from that same
    store, and deletes the source file last when ``--delete-laz`` is set.
    Returns None; ``--viz3d`` with both ``--no-full`` and ``--no-roi`` is a
    ``SystemExit``."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "serve":
        rc = _run_serve(args, parser)
        if rc:
            raise SystemExit(rc)
        return

    if args.cmd == "single":
        if args.output_dir is None:
            from .run_utils import next_run_output_dir
            args.output_dir = next_run_output_dir(Path("outputs"), "single")
            logger.info("Auto-created run directory: %s", args.output_dir)
        if args.viz3d and args.no_full and args.no_roi:
            raise SystemExit(
                "--viz3d given with both --no-full and --no-roi: "
                "nothing to render.")
        # Put per-tile outputs in <output-dir>/<tile_stem>/.
        per_tile_dir = args.output_dir / args.laz_file.stem
        keep_classes = parse_classes(args.keep_classes)
        # The raw store directory sits at the run root, exactly where an area
        # run's store_raw/ sits in its output dir, so area_cli
        # --resume-from-store (and the GUI's resume detector) find it by the
        # same rule. Overwriting a previous tile's directory is warned about,
        # never silent.
        store_raw_dir = args.output_dir / "store_raw"
        if args.keep_raw_store and (store_raw_dir / "meta.json").is_file():
            logger.warning("%s already holds a raw store; this run replaces "
                           "it. Use a fresh --output-dir to keep one store "
                           "per tile.", store_raw_dir)
        # Voxelize once and always keep the resulting grid: this CLI passes
        # return_store=True unconditionally, though the library default in
        # pipeline.process_single_tile is False. When --viz3d is set the 3-D
        # renderer below reuses this exact store, so the LAZ is decoded a
        # single time per run; when it is not, the store is simply dropped as
        # main() returns.
        stats, store = process_single_tile(
            args.laz_file, per_tile_dir,
            cell_xy=args.cell_xy, cell_z=args.cell_z,
            columns_mode=args.columns_mode,
            columns_top_n=args.columns_top_n,
            columns_all_max=args.columns_all_max,
            height_mode=args.height_mode,
            keep_classes=keep_classes,
            save_store_to=(store_raw_dir if args.keep_raw_store else None),
            return_store=True,
        )

        # The raw store is re-enterable only with the run's parameters beside
        # it: run_params.json is what area_cli --resume-from-store reads to
        # re-run the output stages with no geometry re-supplied.
        if args.keep_raw_store:
            _write_single_run_params(
                store_raw_dir, store,
                cell_xy=args.cell_xy, cell_z=args.cell_z,
                height_mode=args.height_mode,
                columns_mode=args.columns_mode,
                columns_top_n=args.columns_top_n,
                max_boxes=args.max_boxes, roi_size=args.roi_size,
                roi_cx=args.roi_cx, roi_cy=args.roi_cy,
                keep_classes=keep_classes,
            )

        # Persist the shard set, if asked. Written from the store already in
        # memory, so this costs a save and no second decode; the shard is raw
        # (ungrouped) like every area shard, and the manifest records the
        # grouping a later merge would apply.
        if args.shards:
            from .sharding import save_single_tile_shard
            save_single_tile_shard(
                store, args.output_dir, args.laz_file.stem,
                stats=stats, height_mode=args.height_mode,
                keep_classes=keep_classes,
            )

        """
        3-D HTML viewer(s), if requested. Rendered from the same tile; the
        renderer writes into <output-dir>/<tile_stem>/ just like the 2-D
        pipeline, so both land side by side. Imported lazily so plain 2-D
        runs never touch the 3-D dependency surface.
        """
        if args.viz3d_stream:
            from .tiled_exporter import export_tiled_from_store
            logger.info("Rendering tiled streaming 3-D viewer (full detail) ...")
            out_path = per_tile_dir / f"{args.laz_file.stem}_stream.html"
            export_tiled_from_store(
                store, out_path,
                title=f"{args.laz_file.stem} - stream",
                max_instances=args.max_instances,
                tile_m=args.tile_m,
                inline_threshold=args.inline_threshold_mb * 1024 * 1024,
            )
        elif args.viz3d:
            from .viz3d_cli import _run_single
            logger.info("Rendering 3-D HTML viewer(s) ...")
            _run_single(
                args.laz_file, args.output_dir,
                cell_xy=args.cell_xy, cell_z=args.cell_z,
                max_boxes=args.max_boxes, roi_size=args.roi_size,
                roi_cx=args.roi_cx, roi_cy=args.roi_cy,
                do_full=not args.no_full, do_roi=not args.no_roi,
                store=store,
            )

        # Delete the source LAST, so the 3-D render above could still read it.
        if args.delete_laz:
            args.laz_file.unlink()
            logger.info("Deleted %s", args.laz_file)


if __name__ == "__main__":
    main()