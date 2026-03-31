from __future__ import annotations

import argparse
from pathlib import Path

import laspy
import numpy as np
import rasterio
from rasterio.transform import from_origin
from tqdm import tqdm

from workflow_utils import validate_positive_number

GROUND_EXCLUDED_CLASSES = frozenset({1, 3, 4, 5, 8})
WATER_CLASS = 9
NEIGHBOR_OFFSETS = (
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate LiDAR height, class, MNS, and MNT rasters from LAZ tiles."
    )
    parser.add_argument("--laz-folder", type=Path, default=Path("laz_tiles"))
    parser.add_argument(
        "--height-folder",
        type=Path,
        default=Path("lidar_data_processed/heights"),
    )
    parser.add_argument(
        "--class-folder",
        type=Path,
        default=Path("lidar_data_processed/class"),
    )
    parser.add_argument(
        "--mns-mnt-folder",
        type=Path,
        default=Path("lidar_data_processed/mns_mnt"),
    )
    parser.add_argument("--resolution", type=float, default=1)
    return parser.parse_args()


def write_raster(
    output_path: Path,
    array: np.ndarray,
    crs: rasterio.crs.CRS | None,
    transform: rasterio.Affine,
    nodata: float | int,
) -> None:
    with rasterio.open(
        output_path,
        "w",
        driver="GTiff",
        height=array.shape[0],
        width=array.shape[1],
        count=1,
        dtype=array.dtype,
        crs=crs,
        transform=transform,
        nodata=nodata,
        compress="lzw",
    ) as dst:
        dst.write(array, 1)


def _iter_valid_neighbors(
    array: np.ndarray,
    row_index: int,
    column_index: int,
) -> list[float]:
    height, width = array.shape
    neighbors: list[float] = []

    for row_offset, column_offset in NEIGHBOR_OFFSETS:
        neighbor_row = row_index + row_offset
        neighbor_column = column_index + column_offset
        if 0 <= neighbor_row < height and 0 <= neighbor_column < width:
            neighbor_value = array[neighbor_row, neighbor_column]
            if not np.isnan(neighbor_value):
                neighbors.append(float(neighbor_value))

    return neighbors


def clean_mnt_mns(input_array: np.ndarray, input_class: np.ndarray) -> np.ndarray:
    cleaned = input_array.astype(np.float32, copy=True)
    valid_values = cleaned[~np.isnan(cleaned)]
    if valid_values.size == 0:
        return np.zeros_like(cleaned, dtype=np.float32)

    fallback_value = float(valid_values.min())
    cleaned[input_class == WATER_CLASS] = fallback_value

    while True:
        nan_positions = np.argwhere(np.isnan(cleaned))
        if nan_positions.size == 0:
            break

        next_cleaned = cleaned.copy()
        replaced_count = 0
        for row_index, column_index in nan_positions:
            neighbors = _iter_valid_neighbors(cleaned, row_index, column_index)
            if neighbors:
                next_cleaned[row_index, column_index] = np.float32(np.mean(neighbors))
                replaced_count += 1

        cleaned = next_cleaned
        if replaced_count == 0:
            break

    cleaned[np.isnan(cleaned)] = fallback_value
    return cleaned


def create_mns_mnt_class(
    path_in: Path,
    resolution: float,
    out_mns: Path,
    out_mnt: Path,
    out_class: Path,
) -> None:
    las = laspy.read(path_in)
    x = las.x
    y = las.y
    z = las.z
    classes = las.classification

    xmin, ymin = x.min(), y.min()
    xmax, ymax = x.max(), y.max()
    width = int(np.ceil((xmax - xmin) / resolution)) + 1
    height = int(np.ceil((ymax - ymin) / resolution)) + 1
    transform = from_origin(xmin, ymax, resolution, resolution)

    mns = np.full((height, width), np.nan, dtype=np.float32)
    mnt = np.full((height, width), np.nan, dtype=np.float32)
    class_raster = np.full((height, width), -1, dtype=np.int16)

    x_indices = ((x - xmin) / resolution).astype(int)
    y_indices = ((ymax - y) / resolution).astype(int)

    for x_index, y_index, z_value, class_value in tqdm(
        zip(x_indices, y_indices, z, classes, strict=True),
        total=len(z),
        desc=f"Processing {path_in.name}",
    ):
        if (
            (np.isnan(mnt[y_index, x_index]) or z_value < mnt[y_index, x_index])
            and class_value not in GROUND_EXCLUDED_CLASSES
        ):
            mnt[y_index, x_index] = z_value

        if np.isnan(mns[y_index, x_index]) or z_value > mns[y_index, x_index]:
            mns[y_index, x_index] = z_value
            class_raster[y_index, x_index] = class_value

    crs = las.header.parse_crs()
    write_raster(out_mns, clean_mnt_mns(mns, class_raster), crs, transform, nodata=np.nan)
    write_raster(out_mnt, clean_mnt_mns(mnt, class_raster), crs, transform, nodata=np.nan)
    write_raster(out_class, class_raster, crs, transform, nodata=-1)


def create_object_height_map(mnt_path: Path, mns_path: Path, out_path: Path) -> None:
    with rasterio.open(mnt_path) as src_mnt:
        mnt = src_mnt.read(1).astype(np.float32)
        profile = src_mnt.profile.copy()

    with rasterio.open(mns_path) as src_mns:
        mns = src_mns.read(1).astype(np.float32)

    if mnt.shape != mns.shape:
        raise ValueError("MNT and MNS rasters must have the same shape.")

    height = mns - mnt
    only_mns = np.isnan(mnt) & ~np.isnan(mns)
    only_mnt = np.isnan(mns) & ~np.isnan(mnt)
    both_nan = np.isnan(mnt) & np.isnan(mns)

    height[only_mns] = mns[only_mns]
    height[only_mnt] = mnt[only_mnt]
    height[both_nan] = 0

    profile.update(dtype="float32", nodata=np.nan, compress="lzw")
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(height.astype(np.float32), 1)


def main() -> None:
    args = parse_args()
    validate_positive_number(args.resolution, "resolution")
    args.height_folder.mkdir(parents=True, exist_ok=True)
    args.class_folder.mkdir(parents=True, exist_ok=True)
    args.mns_mnt_folder.mkdir(parents=True, exist_ok=True)

    laz_files = sorted(args.laz_folder.glob("*.laz"))
    if not laz_files:
        raise FileNotFoundError(f"No LAZ file found in: {args.laz_folder}")

    print(f"{len(laz_files)} LAZ tile(s) found.")
    for laz_path in laz_files:
        base_name = laz_path.stem
        out_mns = args.mns_mnt_folder / f"{base_name}_mns.tif"
        out_mnt = args.mns_mnt_folder / f"{base_name}_mnt.tif"
        out_class = args.class_folder / f"{base_name}_class_08m.tif"
        out_height = args.height_folder / f"{base_name}_height_08.tif"

        if out_height.exists() and out_class.exists():
            print(f"Skipping existing outputs for: {base_name}")
            continue

        create_mns_mnt_class(laz_path, args.resolution, out_mns, out_mnt, out_class)
        create_object_height_map(out_mnt, out_mns, out_height)
        print(f"Generated height raster: {out_height}")
        print(f"Generated class raster: {out_class}")

    print("LiDAR raster generation completed.")


if __name__ == "__main__":
    main()
