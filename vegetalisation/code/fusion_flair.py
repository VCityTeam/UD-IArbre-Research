from __future__ import annotations

import argparse
from pathlib import Path

import rasterio
from rasterio.merge import merge

DEFAULT_TILES = ["HG.tif", "HD.tif", "BG.tif", "BD.tif"]
DEFAULT_OUTPUT = Path("Flair2023.tif")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rewrite Flair TIFF tiles with clean tiling metadata and merge them."
    )
    parser.add_argument(
        "--tiles",
        nargs="+",
        default=DEFAULT_TILES,
        help="List of input TIFF tiles to merge.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Merged output TIFF path.",
    )
    return parser.parse_args()


def rewrite_clean_tiff(input_path: Path, output_path: Path) -> None:
    with rasterio.open(input_path) as src:
        profile = src.profile.copy()
        profile.update(tiled=True, blockxsize=256, blockysize=256, compress="lzw")

        with rasterio.open(output_path, "w", **profile) as dst:
            for _, window in src.block_windows(1):
                dst.write(src.read(window=window), window=window)


def merge_tiles(tile_paths: list[Path], output_path: Path) -> None:
    clean_tiles: list[Path] = []
    for tile_path in tile_paths:
        clean_path = tile_path.with_name(f"{tile_path.stem}_clean{tile_path.suffix}")
        rewrite_clean_tiff(tile_path, clean_path)
        clean_tiles.append(clean_path)

    datasets = [rasterio.open(path) for path in clean_tiles]
    try:
        mosaic, transform = merge(datasets)
        out_meta = datasets[0].meta.copy()
        out_meta.update(
            height=mosaic.shape[1],
            width=mosaic.shape[2],
            transform=transform,
            tiled=True,
            blockxsize=256,
            blockysize=256,
            compress="lzw",
        )

        with rasterio.open(output_path, "w", **out_meta) as dst:
            dst.write(mosaic)
    finally:
        for dataset in datasets:
            dataset.close()


def main() -> None:
    args = parse_args()
    tile_paths = [Path(tile) for tile in args.tiles]

    missing = [str(path) for path in tile_paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing input tile(s): {', '.join(missing)}")

    merge_tiles(tile_paths, args.output)
    print(f"Merged raster created at: {args.output}")


if __name__ == "__main__":
    main()
