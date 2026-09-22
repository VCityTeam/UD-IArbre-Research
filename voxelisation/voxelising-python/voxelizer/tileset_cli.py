"""
CLI entry point for 3-D Tiles export.
@ingroup t4_entrees


    python -m voxelizer.tileset_cli from-store PATH/TO/area.npz             \
        --out-dir DIR  [--tile-m 100]  [--region XMIN YMIN XMAX YMAX]       \
        [--flat]  [--no-geoid]  [--vertical-crs EPSG:5720]                  \
        [--height-offset M]  [--keep-classes 2,3,4,5,6]  [--crs EPSG:3946]

    python -m voxelizer.tileset_cli from-payload PATH/TO/area_stream.idx.json \
        [PATH/TO/area_stream.bin]  --out-dir DIR  [--crs EPSG:3946]           \
        [--flat]  [--no-geoid]  [--vertical-crs EPSG:5720]                    \
        [--height-offset M]

``from-store`` chains all three steps internally: load .npz, produce the
tiled payload, convert to tileset.json + tile_*.glb.

``from-payload`` reuses an existing ``.idx.json`` + ``.bin`` (from a previous
``export_tiled_from_store`` call) - useful if you already generated the
payload and just want to re-export to 3-D Tiles with different parameters.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _run_from_store(
    npz_path: Path,
    out_dir: Path,
    *,
    tile_m: float = 100.0,
    keep_classes: set[int] | None = None,
    crs: str = "EPSG:3946",
    vertical_crs: str | None = "EPSG:5720",
    height_offset: float | None = None,
    region: tuple[float, float, float, float] | None = None,
    flat: bool = False,
) -> Path:
    """Load a ``ColumnStore`` from ``.npz``, produce tiled payload,
    convert to 3-D Tiles (LOD pyramid by default), return path to
    ``tileset.json``."""
    from .data_structures import ColumnStore
    from .tiled_exporter import export_tiled_from_store
    from .tileset_exporter import convert_to_3d_tiles, convert_to_3d_tiles_lod

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Loading store %s ...", npz_path)
    store = ColumnStore.load(npz_path)
    if not store.columns:
        raise RuntimeError("Loaded store is empty - nothing to export.")

    temp_html = out_dir / "_tileset_payload.html"
    logger.info("Producing tiled payload (tile_m=%s, keep_classes=%s, "
                "region=%s) ...", tile_m, keep_classes, region)
    # No max_instances here: it is only a viewer GPU working-set budget baked
    # into the payload HTML, which this path deletes below. The .bin and the
    # .idx.json - the only inputs the 3-D Tiles converter reads - are written
    # by write_tiled_payload(), which never sees it.
    html = export_tiled_from_store(
        store, temp_html,
        tile_m=tile_m,
        keep_classes=keep_classes,
        region=region,
        # Never inline: the converter below needs the .bin + .idx.json
        # sidecar files, but under the default 64 MB threshold small
        # payloads were base64-embedded in the HTML and the .idx.json was
        # never written -> FileNotFoundError for every store under ~2M
        # intervals. The payload
        # is deleted after conversion anyway, so inlining bought nothing.
        inline_threshold=0,
    )
    # Sidecar names mirror export_tiled_from_store's own derivation from
    # the html path: <stem>.bin and <stem>.idx.json. Note that
    # idx.with_suffix(".bin") would be <stem>.idx.bin - a file that never
    # exists; the old code passed that to the converter (FileNotFoundError
    # once the sidecar actually existed) and deleted the same wrong name,
    # leaking the real .bin.
    idx = html.with_suffix(".idx.json")
    bin_path = html.with_suffix(".bin")
    convert = convert_to_3d_tiles if flat else convert_to_3d_tiles_lod
    tileset_path = convert(idx, bin_path, out_dir, crs=crs,
                           vertical_crs=vertical_crs,
                           height_offset=height_offset)

    for f in (html, idx, bin_path):
        f.unlink(missing_ok=True)

    logger.info("3-D Tiles export complete -> %s", tileset_path)
    return tileset_path


def _run_from_payload(
    idx_path: Path,
    bin_path: Path | None,
    out_dir: Path,
    *,
    crs: str = "EPSG:3946",
    vertical_crs: str | None = "EPSG:5720",
    height_offset: float | None = None,
    flat: bool = False,
) -> Path:
    """Convert existing ``.idx.json`` + ``.bin`` to 3-D Tiles.

    By default builds an LOD pyramid (coarse interior levels + full-detail
    leaves); ``flat=True`` restores the legacy root->leaves layout.
    """
    from .tileset_exporter import convert_to_3d_tiles, convert_to_3d_tiles_lod

    out_dir.mkdir(parents=True, exist_ok=True)
    convert = convert_to_3d_tiles if flat else convert_to_3d_tiles_lod
    tileset_path = convert(idx_path, bin_path, out_dir, crs=crs,
                           vertical_crs=vertical_crs,
                           height_offset=height_offset)
    logger.info("3-D Tiles export complete -> %s", tileset_path)
    return tileset_path


def _build_parser() -> argparse.ArgumentParser:
    """Build the parser with its two required subcommands.

    ``from-store`` takes ``npz``, ``--out-dir``, ``--tile-m``, ``--flat``,
    ``--keep-classes``, ``--crs`` and ``--region``; ``from-payload`` takes
    ``idx``, an optional ``bin``, ``--out-dir``, ``--crs`` and ``--flat``.
    Both get the height-datum flags from _add_vertical_args().
    """
    p = argparse.ArgumentParser(
        prog="python -m voxelizer.tileset_cli",
        description="Export a ColumnStore (.npz) to 3-D Tiles (tileset.json + .glb).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # --- from-store ------------------------------------------------------
    fs = sub.add_parser(
        "from-store",
        help="Load .npz, produce tiled payload, convert to 3-D Tiles.")
    fs.add_argument("npz", type=Path,
                    help="Path to a ColumnStore .npz (e.g. area.npz).")
    fs.add_argument("--out-dir", "-o", type=Path, required=True,
                    help="Output directory for tileset.json + tile_*.glb.")
    fs.add_argument("--tile-m", type=float, default=100.0,
                    help="Spatial tile size in metres (default 100; the LOD "
                         "pyramid groups these leaves 2x2 per level).")
    fs.add_argument("--flat", action="store_true",
                    help="Legacy flat layout (root -> full-detail leaves) "
                         "instead of the default LOD pyramid.")
    _add_vertical_args(fs)
    # No --max-instances here (removed): it only sized the viewer working set
    # in the intermediate payload HTML that this path deletes, so it could not
    # change the tileset. It remains meaningful on `viz3d_cli stream` and on
    # `area_cli --viz3d-stream`, which keep their HTML.
    fs.add_argument("--keep-classes", type=str, default=None,
                    help="Comma/space list of ASPRS classes to keep.")
    fs.add_argument("--crs", type=str, default="EPSG:3946",
                    help="Native CRS for tileset extras (default EPSG:3946).")
    fs.add_argument("--region", nargs=4, type=float, default=None,
                    metavar=("XMIN", "YMIN", "XMAX", "YMAX"),
                    help="Spatial subset (metric bbox) to reduce payload.")

    # --- from-payload ----------------------------------------------------
    fp = sub.add_parser(
        "from-payload",
        help="Convert existing .idx.json + .bin to 3-D Tiles.")
    fp.add_argument("idx", type=Path,
                    help="Path to a .idx.json file (from export_tiled_from_store).")
    fp.add_argument("bin", type=Path, nargs="?",
                    help="Path to the .bin payload (default: the idx path "
                         "with .idx.json replaced by .bin, e.g. "
                         "area_stream.idx.json -> area_stream.bin).")
    fp.add_argument("--out-dir", "-o", type=Path, required=True,
                    help="Output directory for tileset.json + tile_*.glb.")
    fp.add_argument("--crs", type=str, default="EPSG:3946",
                    help="Native CRS for tileset extras (default EPSG:3946).")
    fp.add_argument("--flat", action="store_true",
                    help="Legacy flat layout (root -> full-detail leaves) "
                         "instead of the default LOD pyramid.")
    _add_vertical_args(fp)

    return p


def _add_vertical_args(sub: argparse.ArgumentParser) -> None:
    """Height-datum options shared by both subcommands."""
    sub.add_argument("--vertical-crs", type=str, default="EPSG:5720",
                     help="Vertical CRS of the input altitudes (default "
                          "EPSG:5720 = NGF-IGN69). The geoid grid is applied "
                          "via pyproj (PROJ CDN on first use) so the tileset "
                          "gets true ellipsoidal placement.")
    sub.add_argument("--no-geoid", action="store_true",
                     help="Treat input altitudes as already ellipsoidal "
                          "(legacy behaviour; model sits ~50 m low vs real "
                          "terrain at Lyon).")
    sub.add_argument("--height-offset", type=float, default=None,
                     help="Explicit metres to add to the origin height "
                          "instead of the geoid grid (e.g. 49.7 for Lyon).")


def main() -> None:
    """Command-line entry point for the ``from-store`` and ``from-payload`` verbs.

    Configures INFO logging, parses ``sys.argv`` with _build_parser()
    and dispatches to _run_from_store() (``--keep-classes`` parsed into
    a set of ints, ``--region`` into a tuple) or _run_from_payload();
    ``--no-geoid`` passes ``vertical_crs=None``. Returns None; argparse exits
    with status 2 on a bad command line and exporter errors propagate.

    @throws SystemExit When argparse rejects the command line (bad or missing
                       verb, unknown flag), exiting with status 2.
    @throws RuntimeError When the ``from-store`` verb loads a store with no columns
                         (raised by _run_from_store).
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _build_parser().parse_args()

    if args.cmd == "from-store":
        classes = ({int(c) for c in args.keep_classes.replace(",", " ").split()
                    if c} if args.keep_classes else None)
        region = tuple(args.region) if args.region else None
        _run_from_store(
            args.npz, args.out_dir,
            tile_m=args.tile_m,
            keep_classes=classes,
            crs=args.crs,
            vertical_crs=None if args.no_geoid else args.vertical_crs,
            height_offset=args.height_offset,
            region=region,
            flat=args.flat,
        )

    elif args.cmd == "from-payload":
        _run_from_payload(
            args.idx, args.bin, args.out_dir,
            crs=args.crs,
            vertical_crs=None if args.no_geoid else args.vertical_crs,
            height_offset=args.height_offset,
            flat=args.flat,
        )


if __name__ == "__main__":
    main()
