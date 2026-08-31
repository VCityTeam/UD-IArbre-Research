from __future__ import annotations

import argparse
from pathlib import Path

import laspy
import numpy as np
import rasterio
from rasterio.transform import from_origin
from tqdm import tqdm

from workflow_utils import validate_positive_number

GROUND_EXCLUDED_CLASSES = frozenset({1, 3, 4, 5, 6, 7, 8})
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
BUILDING_HEIGHT_THRESHOLD = 2

BUILDING_CLASS = 6


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate LiDAR height, class, MNS, and MNT rasters from LAZ tiles."
    )
    parser.add_argument("--laz-folder", type=Path, default=Path("./workdir/runs/test_batiments/lidar/mosaic"))
    parser.add_argument(
        "--height-folder",
        type=Path,
        default=Path("./workdir/runs/test_batiments/lidar/heights"),
    )
    parser.add_argument(
        "--class-folder",
        type=Path,
        default=Path("./workdir/runs/test_batiments/lidar/class"),
    )
    parser.add_argument(
        "--mns-mnt-folder",
        type=Path,
        default=Path("./workdir/runs/test_batiments/lidar/mns_mnt"),
    )
    parser.add_argument(
        "--ortho-ref",
        type=Path,
        required=True,
        help="Path to the reference orthophoto (.tif). Its bounds, dimensions and "
        "resolution will be used to build all LiDAR rasters.",
    )
    parser.add_argument(
        "--working-resolution",
        type=float,
        default=1.0,
        help="Resolution (m) used to bin LiDAR points, chosen close to the point "
        "cloud's native density. The result is then upsampled (nearest neighbor, "
        "no interpolation) to match the reference orthophoto's finer grid. Must "
        "divide evenly into the ortho resolution (e.g. 1.0 -> 0.5 = factor 2).",
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

def read_reference_grid(ortho_ref_path: Path) -> tuple[rasterio.Affine, int, int, rasterio.crs.CRS | None]:
    """Read the target grid (transform, width, height, crs) from a reference orthophoto."""
    with rasterio.open(ortho_ref_path) as ref:
        transform = ref.transform
        width = ref.width
        height = ref.height
        crs = ref.crs

    if transform.b != 0 or transform.d != 0:
        raise ValueError(
            "L'orthophoto de référence a un transform avec rotation/shear "
            "(b ou d non nul), ce cas n'est pas géré."
        )

    return transform, width, height, crs

def upsample_nearest(
    array: np.ndarray,
    scale_y: int,
    scale_x: int,
    out_height: int,
    out_width: int,
) -> np.ndarray:
    """Duplicate each cell into a scale_y x scale_x block (no interpolation),
    then crop to the exact target shape."""
    upsampled = np.repeat(np.repeat(array, scale_y, axis=0), scale_x, axis=1)
    return upsampled[:out_height, :out_width]

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
    ortho_ref_path: Path,
    working_resolution: float,
    out_mns: Path,
    out_mnt1: Path,
    out_mnt2: Path,
    out_class: Path,
) -> None:
    final_transform, final_width, final_height, ref_crs = read_reference_grid(ortho_ref_path)

    final_res_x = final_transform.a
    final_res_y = -final_transform.e
    validate_positive_number(final_res_x, "resolution_x (from ortho ref)")
    validate_positive_number(final_res_y, "resolution_y (from ortho ref)")
    validate_positive_number(working_resolution, "working_resolution")

    scale_x = working_resolution / final_res_x
    scale_y = working_resolution / final_res_y
    scale_x_int = round(scale_x)
    scale_y_int = round(scale_y)
    if abs(scale_x - scale_x_int) > 1e-6 or abs(scale_y - scale_y_int) > 1e-6:
        raise ValueError(
            f"working_resolution ({working_resolution}) doit être un multiple entier "
            f"de la résolution de l'ortho ({final_res_x} x {final_res_y}), "
            f"facteur obtenu: {scale_x:.4f} x {scale_y:.4f}."
        )
    scale_x_int = max(scale_x_int, 1)
    scale_y_int = max(scale_y_int, 1)

    xmin = final_transform.c
    ymax = final_transform.f
    xmax = xmin + final_width * final_res_x
    ymin = ymax - final_height * final_res_y

    # Grille de travail : même emprise, résolution plus grossière (adaptée à la densité LiDAR)
    work_res_x = final_res_x * scale_x_int
    work_res_y = final_res_y * scale_y_int
    work_width = int(np.ceil(final_width / scale_x_int))
    work_height = int(np.ceil(final_height / scale_y_int))
    work_transform = rasterio.Affine(work_res_x, 0.0, xmin, 0.0, -work_res_y, ymax)

    las = laspy.read(path_in)
    x = np.asarray(las.x, dtype=np.float64)
    y = np.asarray(las.y, dtype=np.float64)
    z = np.asarray(las.z, dtype=np.float64)
    classes = np.asarray(las.classification)

    in_bounds = (x >= xmin) & (x < xmax) & (y > ymin) & (y <= ymax)
    x = x[in_bounds]
    y = y[in_bounds]
    z = z[in_bounds]
    classes = classes[in_bounds]

    if x.size == 0:
        raise ValueError(
            f"Aucun point de {path_in.name} ne recouvre l'emprise de l'orthophoto "
            f"de référence ({ortho_ref_path.name})."
        )

    x_indices = ((x - xmin) / work_res_x).astype(int)
    y_indices = ((ymax - y) / work_res_y).astype(int)

    x_indices = np.clip(x_indices, 0, work_width - 1)
    y_indices = np.clip(y_indices, 0, work_height - 1)

    mns = np.full((work_height, work_width), np.nan, dtype=np.float32)
    mnt1 = np.full((work_height, work_width), np.nan, dtype=np.float32)
    mnt2 = np.full((work_height, work_width), np.nan, dtype=np.float32)
    class_raster = np.full((work_height, work_width), -1, dtype=np.int16)

    for x_index, y_index, z_value, class_value in tqdm(
        zip(x_indices, y_indices, z, classes, strict=True),
        total=len(z),
        desc=f"Processing 1 {path_in.name}",
    ):
        if (
            (np.isnan(mnt1[y_index, x_index]) or z_value < mnt1[y_index, x_index])
            and class_value not in GROUND_EXCLUDED_CLASSES
        ):
            mnt1[y_index, x_index] = z_value

        if np.isnan(mns[y_index, x_index]) or z_value > mns[y_index, x_index]:
            mns[y_index, x_index] = z_value
            class_raster[y_index, x_index] = class_value

    crs = ref_crs if ref_crs is not None else las.header.parse_crs()

    mns = clean_mnt_mns(mns, class_raster)
    mnt1 = clean_mnt_mns(mnt1, class_raster)

    for x_index, y_index, z_value, class_value in tqdm(
        zip(x_indices, y_indices, z, classes, strict=True),
        total=len(z),
        desc=f"Processing 2 {path_in.name}",
    ):
        if class_value == BUILDING_CLASS:
            if np.isnan(mnt2[y_index, x_index]) or z_value < mnt2[y_index, x_index]:
                mnt2[y_index, x_index] = z_value

    building_pixels = np.argwhere(class_raster == BUILDING_CLASS)
    for row_index, column_index in building_pixels:
        mns_value = mns[row_index, column_index]
        mnt1_value = mnt1[row_index, column_index]
        mnt2_value = mnt2[row_index, column_index]

        if np.isnan(mns_value) or np.isnan(mnt1_value) or np.isnan(mnt2_value):
            continue

        if abs(mnt1_value - mnt2_value) < 1:
            mns[row_index, column_index] = mnt1_value

    # Suréchantillonnage vers la grille finale (résolution de l'ortho), sans interpolation :
    # chaque cellule de travail devient un bloc scale_y x scale_x de pixels identiques.
    mns_final = upsample_nearest(mns, scale_y_int, scale_x_int, final_height, final_width)
    mnt1_final = upsample_nearest(mnt1, scale_y_int, scale_x_int, final_height, final_width)
    mnt2_final = upsample_nearest(mnt2, scale_y_int, scale_x_int, final_height, final_width)
    class_final = upsample_nearest(class_raster, scale_y_int, scale_x_int, final_height, final_width)

    write_raster(out_mns, mns_final, crs, final_transform, nodata=np.nan)
    write_raster(out_mnt2, mnt2_final, crs, final_transform, nodata=np.nan)
    write_raster(out_mnt1, mnt1_final, crs, final_transform, nodata=np.nan)
    write_raster(out_class, class_final, crs, final_transform, nodata=-1)

    


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
    args.height_folder.mkdir(parents=True, exist_ok=True)
    args.class_folder.mkdir(parents=True, exist_ok=True)
    args.mns_mnt_folder.mkdir(parents=True, exist_ok=True)

    if not args.ortho_ref.exists():
        raise FileNotFoundError(f"Reference orthophoto not found: {args.ortho_ref}")


    laz_files = sorted(args.laz_folder.glob("*.laz"))
    if not laz_files:
        raise FileNotFoundError(f"No LAZ file found in: {args.laz_folder}")

    print(f"{len(laz_files)} LAZ tile(s) found.")
    print(f"Using reference orthophoto: {args.ortho_ref}")
    for laz_path in laz_files:
        base_name = laz_path.stem
        out_mns = args.mns_mnt_folder / f"{base_name}_mns.tif"
        out_mnt1 = args.mns_mnt_folder / f"{base_name}_mnt1.tif"
        out_mnt2 = args.mns_mnt_folder / f"{base_name}_mnt2.tif"
        out_class = args.class_folder / f"{base_name}_class.tif"
        out_height = args.height_folder / f"{base_name}_height.tif"

        if out_height.exists() and out_class.exists():
            print(f"Skipping existing outputs for: {base_name}")
            continue

        create_mns_mnt_class(
            laz_path, args.ortho_ref, args.working_resolution,
            out_mns, out_mnt1, out_mnt2, out_class,
        )
        create_object_height_map(out_mnt1, out_mns, out_height)
        print(f"Generated height raster: {out_height}")
        print(f"Generated class raster: {out_class}")

    print("LiDAR raster generation completed.")


if __name__ == "__main__":
    main()