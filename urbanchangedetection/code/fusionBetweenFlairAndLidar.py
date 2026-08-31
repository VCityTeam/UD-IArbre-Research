from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio

from workflow_utils import align_array_to_shape


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fuse LiDAR and Flair vegetation class rasters with LiDAR priority."
    )
    parser.add_argument("--lidar-raster", type=Path, required=True)
    parser.add_argument("--flair-raster", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--target-size",
        nargs=2,
        type=int,
        metavar=("HEIGHT", "WIDTH"),
        help="Optional final raster size. If omitted, keep LiDAR raster size.",
    )
    return parser.parse_args()


def pad_to_match(arr: np.ndarray, target_shape: tuple[int, int]) -> np.ndarray:
    return align_array_to_shape(arr, target_shape, fill_value=np.nan, allow_crop=False)


def pad_or_crop_to_size(arr: np.ndarray, target_shape: tuple[int, int]) -> np.ndarray:
    return align_array_to_shape(arr, target_shape, fill_value=np.nan, allow_crop=True)


def fusion_classes(
    lidar_raster_path: Path,
    flair_raster_path: Path,
    output_path: Path,
    target_size: tuple[int, int] | None = None,
) -> None:
    with rasterio.open(lidar_raster_path) as src1, rasterio.open(flair_raster_path) as src2:
        lidar = src1.read(1).astype(np.float32)
        flair = src2.read(1).astype(np.float32)
        profile = src1.profile.copy()

    if lidar.shape != flair.shape:
        flair = pad_to_match(flair, lidar.shape)

    lidar_reclass = np.full_like(lidar, np.nan)
    lidar_reclass[np.isin(lidar, [1, 2])] = 1
    lidar_reclass[lidar == 3] = 2
    lidar_reclass[lidar == 4] = 3

    flair_reclass = np.full_like(flair, np.nan)
    flair_reclass[flair == 0] = 0
    flair_reclass[flair == 1] = 1
    flair_reclass[flair == 2] = 2
    flair_reclass[flair == 3] = 3

    fused = np.where(~np.isnan(lidar_reclass), lidar_reclass, flair_reclass)

    if target_size is not None:
        fused = pad_or_crop_to_size(fused, target_size)
        profile.update(height=target_size[0], width=target_size[1])
    else:
        profile.update(height=fused.shape[0], width=fused.shape[1])

    profile.update(dtype="float32", count=1, nodata=np.nan, compress="lzw")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(output_path, "w", **profile) as dst:
        dst.write(fused.astype(np.float32), 1)

    print(f"Saved fused raster to: {output_path}")


def main() -> None:
    args = parse_args()
    target_size = tuple(args.target_size) if args.target_size else None
    fusion_classes(args.lidar_raster, args.flair_raster, args.output, target_size)


if __name__ == "__main__":
    main()
