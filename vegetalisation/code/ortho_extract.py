from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import rasterio
import requests
from rasterio.enums import Resampling

from workflow_utils import validate_bbox, validate_positive_number

DEFAULT_JSON_FILE = Path("ortho.json")
DEFAULT_OUTPUT_DIR = Path("orthophotos_08m")
DEFAULT_TEMP_DIR = Path("temp_5cm")
DEFAULT_XMIN_START = 1841500
DEFAULT_XMIN_END = 1852000
DEFAULT_YMIN_START = 5169000
DEFAULT_YMIN_END = 5179000
TARGET_RESOLUTION = 1
SOURCE_RESOLUTION = 0.05
CHUNK_SIZE = 8192
REQUEST_TIMEOUT_SECONDS = 60


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download orthophoto tiles and resample them from 5 cm to 0.8 m."
    )
    parser.add_argument("--json-file", type=Path, default=DEFAULT_JSON_FILE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--temp-dir", type=Path, default=DEFAULT_TEMP_DIR)
    parser.add_argument("--xmin-start", type=int, default=DEFAULT_XMIN_START)
    parser.add_argument("--xmin-end", type=int, default=DEFAULT_XMIN_END)
    parser.add_argument("--ymin-start", type=int, default=DEFAULT_YMIN_START)
    parser.add_argument("--ymin-end", type=int, default=DEFAULT_YMIN_END)
    parser.add_argument("--source-resolution", type=float, default=SOURCE_RESOLUTION)
    parser.add_argument("--target-resolution", type=float, default=TARGET_RESOLUTION)
    return parser.parse_args()


def load_tiles(json_file: Path) -> list[dict[str, Any]]:
    with json_file.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    values = data.get("values")
    if not isinstance(values, list):
        raise ValueError("The JSON file must contain a 'values' list.")
    return values


def select_tiles(
    tiles: list[dict[str, Any]],
    xmin_start: int,
    xmin_end: int,
    ymin_start: int,
    ymin_end: int,
) -> list[dict[str, Any]]:
    validate_bbox(xmin_start, xmin_end, ymin_start, ymin_end)
    return [
        tile
        for tile in tiles
        if xmin_start <= tile["x_min"] <= xmin_end and ymin_start <= tile["y_min"] <= ymin_end
    ]


def download_file(session: requests.Session, url: str, destination: Path) -> None:
    with session.get(url, stream=True, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        response.raise_for_status()
        with destination.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                if chunk:
                    handle.write(chunk)


def resample_raster(
    source_path: Path,
    output_path: Path,
    *,
    source_resolution: float,
    target_resolution: float,
) -> None:
    validate_positive_number(source_resolution, "source_resolution")
    validate_positive_number(target_resolution, "target_resolution")
    scale = target_resolution / source_resolution
    with rasterio.open(source_path) as src:
        new_width = max(1, int(src.width / scale))
        new_height = max(1, int(src.height / scale))
        transform = src.transform * src.transform.scale(
            src.width / new_width,
            src.height / new_height,
        )
        data = src.read(
            out_shape=(src.count, new_height, new_width),
            resampling=Resampling.bilinear,
        )
        meta = src.meta.copy()
        meta.update(height=new_height, width=new_width, transform=transform, compress="lzw")

    with rasterio.open(output_path, "w", **meta) as dst:
        dst.write(data)


def main() -> None:
    args = parse_args()
    validate_bbox(args.xmin_start, args.xmin_end, args.ymin_start, args.ymin_end)
    validate_positive_number(args.source_resolution, "source_resolution")
    validate_positive_number(args.target_resolution, "target_resolution")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.temp_dir.mkdir(parents=True, exist_ok=True)

    tiles = load_tiles(args.json_file)
    selected_tiles = select_tiles(
        tiles,
        xmin_start=args.xmin_start,
        xmin_end=args.xmin_end,
        ymin_start=args.ymin_start,
        ymin_end=args.ymin_end,
    )

    if not selected_tiles:
        raise SystemExit("No orthophoto tile found in the selected interval.")

    print(f"{len(selected_tiles)} orthophoto tile(s) selected.")

    with requests.Session() as session:
        for tile in selected_tiles:
            url = tile["url"].strip()
            filename = Path(url).name
            temp_path = args.temp_dir / filename
            suffix = f"_{str(args.target_resolution).replace('.', 'p')}m.tif"
            output_path = args.output_dir / filename.replace(".tif", suffix)

            if output_path.exists():
                print(f"Skipping existing file: {output_path}")
                continue

            print(f"Downloading: {url}")
            download_file(session, url, temp_path)
            print(f"Resampling to {args.target_resolution} m: {output_path}")
            try:
                resample_raster(
                    temp_path,
                    output_path,
                    source_resolution=args.source_resolution,
                    target_resolution=args.target_resolution,
                )
            finally:
                temp_path.unlink(missing_ok=True)

    print("Orthophoto extraction completed.")


if __name__ == "__main__":
    main()
