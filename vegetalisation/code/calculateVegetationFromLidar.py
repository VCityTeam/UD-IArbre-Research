from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio

from workflow_utils import align_array_to_shape


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a LiDAR-only vegetation class raster from max/min height, classes, and a vegetation mask."
    )
    parser.add_argument("--lidar-max-height", type=Path, required=True)
    parser.add_argument("--lidar-min-height", type=Path, required=True)
    parser.add_argument("--lidar-class", type=Path, required=True)
    parser.add_argument("--mask-raster", type=Path, required=True)
    parser.add_argument("--height-output", type=Path, required=True)
    parser.add_argument("--class-output", type=Path, required=True)
    return parser.parse_args()


def pad_or_crop_to_size(arr: np.ndarray, target_shape: tuple[int, int]) -> np.ndarray:
    return align_array_to_shape(arr, target_shape, fill_value=np.nan, allow_crop=True)


def load_raster(path: Path) -> tuple[np.ndarray, dict]:
    with rasterio.open(path) as src:
        return src.read(1).astype(np.float32), src.profile.copy()


def save_raster(path: Path, data: np.ndarray, profile: dict) -> None:
    output_profile = profile.copy()
    output_profile.update(dtype="float32", count=1, nodata=np.nan, compress="lzw")
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **output_profile) as dst:
        dst.write(data.astype(np.float32), 1)


def compute_multi_vege(
    max_height: np.ndarray,
    min_height: np.ndarray,
    lidar_class: np.ndarray,
    mask_raster: np.ndarray,
) -> np.ndarray:
    if not (
        max_height.shape == min_height.shape == lidar_class.shape == mask_raster.shape
    ):
        raise ValueError("All rasters must have the same shape.")

    result = np.full_like(max_height, np.nan, dtype=np.float32)

    mask_class1 = lidar_class == 1
    mask_class5 = lidar_class == 5
    mask_valid = (mask_raster != 255) & (~np.isnan(mask_raster))
    final_mask = mask_class5 | (mask_class1 & mask_valid) | mask_valid
    result[final_mask] = max_height[final_mask] - min_height[final_mask]

    return result


def classify_from_difference(diff: np.ndarray) -> np.ndarray:
    classes = np.full_like(diff, np.nan, dtype=np.float32)
    classes[(diff >= 0.5) & (diff < 1.5)] = 1
    classes[(diff >= 1.5) & (diff < 5)] = 2
    classes[(diff >= 5) & (diff < 15)] = 3
    classes[diff >= 15] = 4
    return classes


def main() -> None:
    args = parse_args()

    max_height, profile = load_raster(args.lidar_max_height)
    min_height, _ = load_raster(args.lidar_min_height)
    lidar_class, _ = load_raster(args.lidar_class)
    mask_raster, _ = load_raster(args.mask_raster)

    target_shape = max_height.shape
    min_height = pad_or_crop_to_size(min_height, target_shape)
    lidar_class = pad_or_crop_to_size(lidar_class, target_shape)
    mask_raster = pad_or_crop_to_size(mask_raster, target_shape)

    height_result = compute_multi_vege(max_height, min_height, lidar_class, mask_raster)
    save_raster(args.height_output, height_result, profile)

    class_result = classify_from_difference(height_result)
    save_raster(args.class_output, class_result, profile)

    print(f"Saved LiDAR vegetation height raster to: {args.height_output}")
    print(f"Saved LiDAR vegetation class raster to: {args.class_output}")


if __name__ == "__main__":
    main()
