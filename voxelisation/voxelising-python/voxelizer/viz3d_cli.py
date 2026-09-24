"""
CLI entry point for 3-D HTML visualization.
@ingroup t4_entrees


Three subcommands, all writing Three.js InstancedMesh pages.

    python -m voxelizer.viz3d_cli single PATH --output-dir DIR
                                  [--cell-xy 0.5] [--cell-z 0.5]
                                  [--max-boxes N] [--roi-size M]
                                  [--roi-cx M] [--roi-cy M]
                                  [--no-full] [--no-roi] [--grid]
                                  [--delete-laz]

        Voxelize one LAZ file and render two interactive HTML viewers
        into <out>/<tile_stem>/:

            <tile_stem>_full.html   auto-thinned overview of the whole
                                    tile, capped at --max-boxes instances
            <tile_stem>_roi.html    a square ROI of side --roi-size metres,
                                    centred by default on the centre of
                                    the occupied tile area, rendered at
                                    full detail (no thinning)

    python -m voxelizer.viz3d_cli from-store STORE.npz --output-dir DIR
                                  [--label NAME] [--max-boxes N]
                                  [--roi-size M] [--roi-cx M] [--roi-cy M]
                                  [--no-full] [--no-roi] [--grid]

        The same two pages from a saved store (a run's area.npz), with no
        voxelization: the grid is loaded from disk.

    python -m voxelizer.viz3d_cli stream STORE.npz --out PAGE.html
                                  [--label NAME] [--region XMIN YMIN XMAX YMAX]
                                  [--keep-classes 2,5,6] [--max-instances N]
                                  [--tile-m 64] [--inline-threshold-mb 64]
                                  [--stride N] [--max-boxes N]

        One streaming page for views no single-file export can hold: every
        interval is written, tiled, and Range-fetched from a sidecar .bin
        (base64-embedded in the page instead when it fits under
        --inline-threshold-mb). --stride and --max-boxes are accepted and
        IGNORED here, with a warning: nothing is decimated, and the GPU
        working set is bounded at view time by --max-instances.

``--grid`` (single, from-store) additionally writes a ``.vxg`` cache beside
each viewer - the same lossless quantized encoding the HTML embeds - so a
re-render can skip re-reading the LAZ.

Cell sizes come from ``cli_common.add_cell_args``, so ``single`` voxelizes at
the project-wide 0.5 m / 0.5 m like every other entry point.

This module is kept separate from __main__.py because (a) the 3-D path has
its own optional-dependency surface (pyvista, although export_html itself
doesn't need it) and (b) it has noticeably different parameters from the
2-D batch pipeline. Wiring it as a subcommand under __main__ would force
every CLI user to read a wall of unrelated --help entries.
"""

from __future__ import annotations
import argparse

from .cli_common import add_cell_args, positive_int
import logging
import sys
from pathlib import Path

from .data_structures import ColumnStore
from .visualizer3d import render_store_3d
from .voxelize import voxelize_laz

logger = logging.getLogger(__name__)


#  --------------------------------------------------
#  Small helpers
#  --------------------------------------------------

def _tile_label(stem: str) -> str:
    """
    Format the HTML ``<title>`` the same way the sample uploads do:
    'tile_18450_51750' -> 'Tile 18450_51750' (real stems are 5-digit
    hectometres). Falls back to the raw stem when the conventional
    'tile_' prefix is missing.
    """
    if stem.lower().startswith("tile_"):
        return f"Tile {stem[5:]}"
    return stem


#  --------------------------------------------------
#  Public: process one tile
#  --------------------------------------------------
def _run_single(
    laz_path: Path,
    out_dir: Path,
    *,
    cell_xy: float,
    cell_z: float,
    max_boxes: int,
    roi_size: float,
    roi_cx: float | None,
    roi_cy: float | None,
    do_full: bool,
    do_roi: bool,
    do_grid: bool = False,
    store: ColumnStore | None = None,
) -> None:
    """Voxelize one LAZ tile and write its full and ROI viewers to ``out_dir/<stem>/``.

    When *store* is given the LAZ is not decoded again (and need not exist);
    otherwise it is voxelized at *cell_xy* / *cell_z*. The rendering itself
    is visualizer3d.render_store_3d(), with the file label taken from
    the stem and the page title from _tile_label(); *do_grid* also
    writes the ``.vxg`` caches. Raises ValueError when both views are
    disabled, FileNotFoundError for a missing LAZ and RuntimeError for an
    empty store.
    """
    if not (do_full or do_roi):
        raise ValueError("Both --no-full and --no-roi were passed; nothing to do.")

    tile_dir = out_dir / laz_path.stem
    tile_dir.mkdir(parents=True, exist_ok=True)

    # Voxelize once per run. When the caller already built the grid for the
    # 2-D maps (the single-tile --viz3d path does), it passes that store in
    # here and we reuse it - the LAZ is decoded exactly once. Only when no
    # store is handed in do we decode it ourselves, and only then does the
    # file have to exist.
    if store is None:
        if not laz_path.exists():
            raise FileNotFoundError(f"LAZ file not found: {laz_path}")
        logger.info("Voxelizing %s ...", laz_path)
        store = voxelize_laz(laz_path, cell_xy=cell_xy, cell_z=cell_z)
    else:
        logger.info("Reusing already-voxelized store for %s (no second decode).",
                    laz_path.name)
    if not store.columns:
        raise RuntimeError("Voxelization produced an empty store - nothing to render.")

    # Shared renderer (same code path as the area pipeline). The file names use
    # the tile stem; the in-page title uses the friendlier "Tile <stem>" form.
    render_store_3d(
        store, tile_dir,
        file_label=laz_path.stem, title_label=_tile_label(laz_path.stem),
        max_boxes=max_boxes, roi_size=roi_size, roi_cx=roi_cx, roi_cy=roi_cy,
        do_full=do_full, do_roi=do_roi, do_grid=do_grid,
    )


#  --------------------------------------------------
#  Public: streaming (very large) HTML
#  --------------------------------------------------
def _run_streaming(
    npz_path: Path,
    out_path: Path,
    *,
    label: str | None = None,
    title: str | None = None,
    region: tuple[float, float, float, float] | None = None,
    classes: list[int] | None = None,
    max_instances: int = 4_000_000,
    inline_threshold_mb: int = 64,
    stride: int = 1,
    max_boxes: int | None = None,
    tile_m: float = 64.0,
) -> None:
    """
    Build a streaming HTML for 200M+ instance views.

    Reuses the existing ``export_tiled_from_store`` so the region / class
    semantics are identical to the legacy path. The output is ``<out>.html``
    plus, unless inlined, a ``<out>.bin`` payload and its ``<out>.idx.json``
    tile index.

    ``stride`` / ``max_boxes`` are accepted for CLI compatibility but
    **IGNORED** (a warning is printed): the tiled streaming exporter
    always writes every interval at full detail and lets the camera
    budget (``--max-instances``) decide what is GPU-resident. To shrink
    the payload itself, use ``--region`` (spatial subset) or
    ``--keep-classes`` (drop classes) instead.
    """
    from .tiled_exporter import export_tiled_from_store

    store = ColumnStore.load_any(npz_path)
    if not store.columns:
        raise RuntimeError("Loaded store is empty - nothing to render.")

    if stride != 1 or max_boxes is not None:
        sys.stderr.write(
            "[viz3d_cli] --stride / --max-boxes are IGNORED by the tiled "
            "streaming viewer:\n            it exports every interval at full "
            "detail and lets the camera\n            decide what is resident.\n")

    file_label = label or npz_path.stem
    export_tiled_from_store(
        store, out_path,
        title=title or file_label,
        max_instances=max_instances,
        tile_m=tile_m,
        keep_classes=set(classes) if classes else None,
        region=region,
        inline_threshold=inline_threshold_mb * 1024 * 1024,
    )


#  --------------------------------------------------
#  Public: render from a persisted store (no voxelization)
#  --------------------------------------------------
def _run_from_store(
    npz_path: Path,
    out_dir: Path,
    *,
    label: str | None = None,
    max_boxes: int,
    roi_size: float,
    roi_cx: float | None,
    roi_cy: float | None,
    do_full: bool,
    do_roi: bool,
    do_grid: bool = False,
) -> None:
    """
    Render 3-D HTML straight from a saved ``ColumnStore`` (``area.npz`` written
    by the area pipeline, or any ``ColumnStore.save`` output, or the raw
    ``store_raw/`` directory attached by memory map) - the voxelization is
    loaded from disk, never recomputed. This is the across-runs half of "3-D
    consumes the prior voxelization": run ``area`` once (which persists the
    grid), then render or re-render 3-D any time without touching the LAZ.
    """
    if not npz_path.exists():
        raise FileNotFoundError(f"store file not found: {npz_path}")
    if not (do_full or do_roi):
        raise ValueError("Both --no-full and --no-roi were passed; nothing to do.")

    logger.info("Loading store %s ...", npz_path)
    store = ColumnStore.load_any(npz_path)
    if not store.columns:
        raise RuntimeError("Loaded store is empty - nothing to render.")

    out_dir.mkdir(parents=True, exist_ok=True)
    file_label = label if label is not None else npz_path.stem
    render_store_3d(
        store, out_dir,
        file_label=file_label, title_label=_tile_label(file_label),
        max_boxes=max_boxes, roi_size=roi_size, roi_cx=roi_cx, roi_cy=roi_cy,
        do_full=do_full, do_roi=do_roi, do_grid=do_grid,
    )

#  --------------------------------------------------
#  argparse
#  --------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    """Build the parser with the ``single``, ``from-store`` and ``stream`` subcommands and the flags the module docstring lists.

    ``single`` takes its cell-size flags from ``cli_common.add_cell_args``;
``from-store`` takes a ``.npz`` or a raw ``store_raw/`` directory; the
``--max-boxes`` of ``single`` and ``from-store`` is validated by
``positive_int``, while ``stream`` accepts ``--stride`` and
``--max-boxes`` only to warn that they are ignored.
    """
    p = argparse.ArgumentParser(
        prog="python -m voxelizer.viz3d_cli",
        description="3-D HTML visualizer (Three.js InstancedMesh) for LAZ tiles.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # --- single ----------------------------------------------------------
    s = sub.add_parser("single", help="Render one LAZ tile to HTML viewer(s).")
    s.add_argument("laz_file", type=Path, help="Path to a .laz or .las file.")
    s.add_argument(
        "--output-dir", "-o", type=Path, required=True,
        help="Run directory; outputs go to <out>/<tile_stem>/.",
    )
    # The project-wide 0.5 m / 0.5 m, from the one place that defines it.
    # This subcommand used to default --cell-xy to 1.0, so a bare
    # `viz3d_cli single` voxelized at half the resolution of the same tile
    # through `python -m voxelizer single`.
    add_cell_args(s)
    s.add_argument("--max-boxes", type=positive_int, default=5_000_000,
                   help="Auto-thinning budget for the full view (default 5000000). "
                        "If the unthinned box count would exceed this, the "
                        "exporter strides columns until it fits.")
    s.add_argument("--roi-size", type=float, default=200.0,
                   help="Side of the square ROI window, in metres (default 200).")
    s.add_argument("--roi-cx", type=float, default=None,
                   help="ROI centre X in metres (RGF93/CC46 EPSG:3946). "
                        "Default: centre of the occupied tile area.")
    s.add_argument("--roi-cy", type=float, default=None,
                   help="ROI centre Y in metres (RGF93/CC46 EPSG:3946). "
                        "Default: centre of the occupied tile area.")
    s.add_argument("--no-full", action="store_true",
                   help="Skip the full-tile HTML.")
    s.add_argument("--no-roi", action="store_true",
                   help="Skip the ROI HTML.")
    s.add_argument("--grid", action="store_true",
                   help="Also write a .vxg grid cache (the same lossless "
                        "quantized encoding the HTML embeds) next to each "
                        "viewer. load_grid() reads it back into a geometry "
                        "dict for export_html_from_geom without re-reading "
                        "the LAZ.")
    s.add_argument("--delete-laz", action="store_true",
                   help="Delete the source .laz file after successful voxelization.")

    # --- from-store ------------------------------------------------------
    fs = sub.add_parser(
        "from-store",
        help="Render HTML viewer(s) from a saved store (area.npz) - no "
             "voxelization; the grid is loaded from disk.")
    fs.add_argument("store_file", type=Path,
                    help="Path to a ColumnStore .npz (e.g. a run's area.npz), or "
                         "a raw store directory (store_raw/) attached by memory "
                         "map.")
    fs.add_argument("--output-dir", "-o", type=Path, required=True,
                    help="Directory to write the viewer(s) into.")
    fs.add_argument("--label", type=str, default=None,
                    help="Base name for the output files and page title "
                         "(default: the .npz stem).")
    fs.add_argument("--max-boxes", type=positive_int, default=5_000_000,
                    help="Auto-thinning budget for the full view (default 5000000).")
    fs.add_argument("--roi-size", type=float, default=200.0,
                    help="Side of the square ROI window, in metres (default 200).")
    fs.add_argument("--roi-cx", type=float, default=None,
                    help="ROI centre X in metres. Default: centre of occupied area.")
    fs.add_argument("--roi-cy", type=float, default=None,
                    help="ROI centre Y in metres. Default: centre of occupied area.")
    fs.add_argument("--no-full", action="store_true", help="Skip the full-area HTML.")
    fs.add_argument("--no-roi", action="store_true", help="Skip the ROI HTML.")
    fs.add_argument("--grid", action="store_true",
                    help="Also write matching .vxg grid caches.")

    # --- stream ----------------------------------------------------------
    st = sub.add_parser(
        "stream",
        help="Render a streaming HTML for 200M+ instance views "
             "(Range-fetched sidecar .bin, or base64-embedded in the page "
             "when it fits under --inline-threshold-mb).")
    st.add_argument("store_file", type=Path,
                    help="Path to a ColumnStore .npz, or a raw store directory "
                         "(store_raw/) attached by memory map.")
    st.add_argument("--out", type=Path, required=True,
                    help="Path to the .html to write (sibling .bin is auto).")
    st.add_argument("--label", type=str, default=None)
    st.add_argument("--region", nargs=4, type=float, default=None,
                    metavar=("XMIN","YMIN","XMAX","YMAX"),
                    help="Optional metric bbox; default = whole store.")
    st.add_argument("--keep-classes", type=str, default=None)
    st.add_argument("--max-instances", type=int, default=4_000_000,
                    help="GPU WORKING-SET budget, in instances (default 4M; "
                         "~76 B GPU/instance -> 4M ~ 0.3 GB, 8M ~ 0.6 GB). "
                         "It decides how many TILES are resident, never how "
                         "many boxes inside them: the page keeps the tiles "
                         "nearest the camera (and where it looks) up to the "
                         "budget, at full detail, and draws the rest as cheap "
                         "per-tile impostor silhouettes. Nothing is strided "
                         "and no record is dropped - the payload always holds "
                         "every interval.")
    st.add_argument("--inline-threshold-mb", type=int, default=64,
                    help="If the .bin is below this size, base64-embed it "
                         "in the HTML and delete the sidecar. Default 64 MB.")
    st.add_argument("--tile-m", type=float, default=64.0,
                    help="Spatial tile size in metres for the streamed payload "
                         "(default 64). The page keeps the tiles nearest the "
                         "camera resident up to --max-instances and evicts the "
                         "rest; smaller tiles cull more finely, larger ones make "
                         "fewer, bigger range requests.")
    st.add_argument("--stride", type=int, default=1,
                    help="IGNORED by the streaming exporter (accepted for "
                         "compatibility; a warning is printed). It always "
                         "writes every interval at full detail - shrink "
                         "the payload with --region or --keep-classes "
                         "instead.")
    st.add_argument("--max-boxes", type=int, default=None,
                    help="IGNORED by the streaming exporter (accepted for "
                         "compatibility; a warning is printed). The GPU "
                         "working set is bounded by --max-instances at "
                         "view time, never by decimating the payload.")

    return p

def main() -> None:
    """Command-line entry point for the ``single``, ``from-store`` and ``stream`` verbs.

    Configures INFO logging, parses ``sys.argv`` with _build_parser()
    and dispatches to _run_single() (deleting the LAZ afterwards when
    ``--delete-laz`` is set), _run_from_store() or
    _run_streaming() (``--keep-classes`` parsed as comma-separated
    ints, ``--region`` as a tuple). Returns None; argparse exits with status
    2 on a bad command line and runner errors propagate.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _build_parser().parse_args()

    if args.cmd == "single":
        _run_single(
            args.laz_file, args.output_dir,
            cell_xy=args.cell_xy, cell_z=args.cell_z,
            max_boxes=args.max_boxes,
            roi_size=args.roi_size,
            roi_cx=args.roi_cx, roi_cy=args.roi_cy,
            do_full=not args.no_full, do_roi=not args.no_roi,
            do_grid=args.grid,
        )
        if args.delete_laz:
            args.laz_file.unlink()
            logger.info("Deleted %s", args.laz_file)

    elif args.cmd == "from-store":
        _run_from_store(
            args.store_file, args.output_dir,
            label=args.label,
            max_boxes=args.max_boxes,
            roi_size=args.roi_size,
            roi_cx=args.roi_cx, roi_cy=args.roi_cy,
            do_full=not args.no_full, do_roi=not args.no_roi,
            do_grid=args.grid,
        )

    elif args.cmd == "stream":
        classes = ([int(c) for c in args.keep_classes.split(",") if c]
                   if args.keep_classes else None)
        region = tuple(args.region) if args.region else None
        _run_streaming(
            args.store_file, args.out,
            label=args.label, region=region, classes=classes,
            max_instances=args.max_instances,
            inline_threshold_mb=args.inline_threshold_mb,
            stride=args.stride, max_boxes=args.max_boxes,
            tile_m=args.tile_m,
        )

if __name__ == "__main__":
    main()
