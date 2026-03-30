from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio

from workflow_utils import validate_odd_positive_integer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fill NaN values in a raster using neighboring pixels, then replace remaining NaN with 0."
    )
    parser.add_argument("--input", type=Path, required=True, help="Input GeoTIFF raster.")
    parser.add_argument(
        "--output",
        type=Path,
        help="Output GeoTIFF raster. If omitted, overwrite the input raster.",
    )
    parser.add_argument(
        "--window-size",
        type=int,
        default=3,
        help="Odd-sized moving window used to fill NaN values.",
    )
    return parser.parse_args()


def fill_nan_with_neighbors(img: np.ndarray, window_size: int = 3) -> np.ndarray:
    validate_odd_positive_integer(window_size, "window_size")

    filled = img.copy()
    offset = window_size // 2
    rows, cols = img.shape

    for row in range(offset, rows - offset):
        for col in range(offset, cols - offset):
            if np.isnan(filled[row, col]):
                window = filled[
                    row - offset : row + offset + 1,
                    col - offset : col + offset + 1,
                ]
                valid = window[~np.isnan(window)]
                if valid.size > 0:
                    filled[row, col] = float(np.mean(valid))
    return filled


def replace_nan_by_zero(img: np.ndarray) -> np.ndarray:
    output = img.copy()
    output[np.isnan(output)] = 0
    return output


def process_raster(input_path: Path, output_path: Path, window_size: int = 3) -> None:
    with rasterio.open(input_path) as src:
        img = src.read(1).astype(np.float32)
        profile = src.profile.copy()

    img = fill_nan_with_neighbors(img, window_size)
    img = replace_nan_by_zero(img)

    profile.update(dtype="float32", count=1, nodata=0, compress="lzw")
    with rasterio.open(output_path, "w", **profile) as dst:
        dst.write(img.astype(np.float32), 1)

    remaining_nan = int(np.isnan(img).sum())
    if remaining_nan == 0:
        print(f"Saved corrected raster to: {output_path}")
    else:
        print(f"Saved raster to: {output_path} (remaining NaN: {remaining_nan})")


def main() -> None:
    args = parse_args()
    validate_odd_positive_integer(args.window_size, "window_size")
    output_path = args.output or args.input
    process_raster(args.input, output_path, window_size=args.window_size)


if __name__ == "__main__":
    main()
