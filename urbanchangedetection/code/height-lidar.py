from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio

from workflow_utils import validate_positive_number



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate LiDAR height, from MNS and MNT."
    )
    parser.add_argument(
        "--mns-folder",
        type=Path,
        default=Path("lidar_data_processed/mns"),
    )
    parser.add_argument(
        "--mnt-folder",
        type=Path,
        default=Path("lidar_data_processed/mnt"),
    )
    parser.add_argument(
        "--height-folder",
        type=Path,
        default=Path("lidar_data_processed/heights"),
    )
    
    # parser.add_argument("--resolution", type=float, default=1)
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
    args.mns_folder.mkdir(parents=True, exist_ok=True)
    args.mnt_folder.mkdir(parents=True, exist_ok=True)

    out_height = args.height_folder / f"lidar_height.tif"

    if out_height.exists():
        print(f"Skipping existing output for lidar height: {out_height}")

    mns_files = sorted(args.mns_folder.glob("*.tif"))
    mnt_files = sorted(args.mnt_folder.glob("*.tif"))

    if not mns_files or not mnt_files:
        raise ValueError("No matching MNS and MNT files found.")

    in_mns = mns_files[0]
    in_mnt = mnt_files[0]

    create_object_height_map(in_mnt, in_mns, out_height)
    print(f"Generated height raster: {out_height}")

    print("LiDAR raster generation completed.")


if __name__ == "__main__":
    main()