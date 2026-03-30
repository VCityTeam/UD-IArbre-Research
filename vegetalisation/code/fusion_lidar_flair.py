from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio

from workflow_utils import align_array_to_shape, max_shape

LIDAR_VEGETATION_CLASSES = (3, 4, 5, 8)
VALID_OUTPUT_CLASSES = (0, 1, 2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Combine vegetation classes derived from LiDAR and Flair rasters."
    )
    parser.add_argument("--class-map", type=Path, required=True)
    parser.add_argument("--height-map", type=Path, required=True)
    parser.add_argument("--veg-mask", type=Path, required=True)
    parser.add_argument("--second-map", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--modify-flair", action="store_true")
    parser.add_argument("--keep-class-lidar1", action="store_true")
    parser.add_argument(
        "--flair-only-herbaceous",
        action="store_true",
        help="Only use Flair where LiDAR is invalid and Flair predicts class 0.",
    )
    return parser.parse_args()


def pad_to_match(arr: np.ndarray, target_shape: tuple[int, int]) -> np.ndarray:
    return align_array_to_shape(
        arr,
        target_shape,
        fill_value=np.nan if np.issubdtype(arr.dtype, np.floating) else 255,
        allow_crop=False,
    )


def load_tif(path: Path) -> tuple[np.ndarray, dict]:
    with rasterio.open(path) as dataset:
        return dataset.read(1), dataset.profile.copy()


def save_tif(path: Path, arr: np.ndarray, profile: dict) -> None:
    output = arr.astype(np.float32)
    updated_profile = profile.copy()
    updated_profile.update(dtype="float32", count=1, nodata=np.nan, compress="lzw")
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **updated_profile) as dst:
        dst.write(output, 1)


def create_vegetation_map(
    class_lidar_map: np.ndarray,
    height_lidar_map: np.ndarray,
    vege_mask: np.ndarray,
    flair_vege: np.ndarray,
    modify_flair: bool = False,
    keep_class_lidar1: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    keep_vegetation = np.isin(class_lidar_map, LIDAR_VEGETATION_CLASSES)
    keep_class1 = (class_lidar_map == 1) & (vege_mask != 255)
    keep_mask = keep_vegetation | keep_class1 if keep_class_lidar1 else keep_vegetation

    out_lidar = np.full(class_lidar_map.shape, np.nan, dtype=np.float32)
    out_lidar[keep_mask & (height_lidar_map < 0.75)] = 0
    out_lidar[keep_mask & (height_lidar_map >= 0.75) & (height_lidar_map < 5)] = 1
    out_lidar[keep_mask & (height_lidar_map >= 5)] = 2

    if not modify_flair:
        return out_lidar, flair_vege.astype(np.float32)

    keep_flair = vege_mask != 255
    out_flair = np.full(flair_vege.shape, np.nan, dtype=np.float32)
    out_flair[keep_flair & (height_lidar_map < 0.75)] = 0
    out_flair[keep_flair & (height_lidar_map >= 0.75) & (height_lidar_map < 5)] = 1
    out_flair[keep_flair & (height_lidar_map >= 5)] = 2
    return out_lidar, out_flair


def remap_classes(arr: np.ndarray) -> np.ndarray:
    out = arr.copy()
    out[out == 3] = 2
    return out


def fuse_maps(
    lidar_map: np.ndarray,
    flair_map: np.ndarray,
    use_flair_everywhere: bool,
) -> np.ndarray:
    out = lidar_map.copy()
    lidar_invalid = ~np.isin(lidar_map, VALID_OUTPUT_CLASSES)
    mask = lidar_invalid if use_flair_everywhere else lidar_invalid & (flair_map == 0)
    out[mask] = flair_map[mask]
    return out


def validate_shapes(arrays: list[np.ndarray]) -> tuple[int, int]:
    return max_shape(arrays)


def main() -> None:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    class_map, profile = load_tif(args.class_map)
    height_map, _ = load_tif(args.height_map)
    veg_mask, _ = load_tif(args.veg_mask)
    second_map, _ = load_tif(args.second_map)

    target_shape = validate_shapes([class_map, height_map, veg_mask, second_map])
    class_map = pad_to_match(class_map, target_shape)
    height_map = pad_to_match(height_map, target_shape)
    veg_mask = pad_to_match(veg_mask, target_shape)
    second_map = pad_to_match(second_map, target_shape)

    vegetation_map_lidar, vegetation_map_flair = create_vegetation_map(
        class_map,
        height_map,
        veg_mask,
        second_map,
        modify_flair=args.modify_flair,
        keep_class_lidar1=args.keep_class_lidar1,
    )
    save_tif(args.out_dir / "vegetation_map_08m.tif", vegetation_map_lidar, profile)

    remapped = remap_classes(vegetation_map_flair)
    save_tif(args.out_dir / "second_remapped_08m.tif", remapped, profile)

    fused = fuse_maps(
        vegetation_map_lidar,
        vegetation_map_flair,
        use_flair_everywhere=not args.flair_only_herbaceous,
    )
    save_tif(args.out_dir / "final_fused_08m.tif", fused, profile)

    print(f"Generated outputs in: {args.out_dir}")


if __name__ == "__main__":
    main()
