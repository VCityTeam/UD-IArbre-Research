from __future__ import annotations

import argparse
from pathlib import Path

import rasterio
from rasterio.merge import merge


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge GeoTIFF rasters from a directory into a single mosaic."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("lidar_data_processed/heights"),
        help="Directory containing TIFF files to merge.",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=Path("heights_08m.tif"),
        help="Merged GeoTIFF output path.",
    )
    return parser.parse_args()


def merge_tiffs(input_dir: Path, output_file: Path) -> None:
    tif_files = sorted(input_dir.glob("*.tif"))
    if not tif_files:
        raise FileNotFoundError(f"No TIFF file found in: {input_dir}")

    print(f"{len(tif_files)} raster(s) found for mosaicking.")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    datasets = [rasterio.open(path) for path in tif_files]
    try:
        mosaic, transform = merge(datasets)
        out_meta = datasets[0].meta.copy()
        out_meta.update(
            height=mosaic.shape[1],
            width=mosaic.shape[2],
            transform=transform,
            compress="lzw",
        )

        with rasterio.open(output_file, "w", **out_meta) as dst:
            dst.write(mosaic)
    finally:
        for dataset in datasets:
            dataset.close()

    print(f"Mosaic created at: {output_file}")


def main() -> None:
    args = parse_args()
    merge_tiffs(args.input_dir, args.output_file)


if __name__ == "__main__":
    main()
