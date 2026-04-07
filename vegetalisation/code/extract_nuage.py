from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import requests

from workflow_utils import validate_bbox

DEFAULT_JSON_FILE = Path("infra.json")
DEFAULT_OUTPUT_DIR = Path("laz_tiles")
DEFAULT_XMIN_START = 1841500
DEFAULT_XMIN_END = 1852000
DEFAULT_YMIN_START = 5169000
DEFAULT_YMIN_END = 5179000
CHUNK_SIZE = 8192
REQUEST_TIMEOUT_SECONDS = 60


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download LAZ tiles from a JSON inventory for a bounding box."
    )
    parser.add_argument("--json-file", type=Path, default=DEFAULT_JSON_FILE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--xmin-start", type=int, default=DEFAULT_XMIN_START)
    parser.add_argument("--xmin-end", type=int, default=DEFAULT_XMIN_END)
    parser.add_argument("--ymin-start", type=int, default=DEFAULT_YMIN_START)
    parser.add_argument("--ymin-end", type=int, default=DEFAULT_YMIN_END)
    return parser.parse_args()


def load_tiles(json_file: Path) -> list[dict[str, Any]]:
    with json_file.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    values = data.get("values")
    if not isinstance(values, list):
        raise ValueError("The JSON file must contain a 'values' list.")
    return values


def tile_origin(tile: dict[str, Any]) -> tuple[int, int]:
    if "x_min" in tile and "y_min" in tile:
        return int(tile["x_min"]), int(tile["y_min"])

    tile_name = tile.get("nom")
    if isinstance(tile_name, str):
        parts = tile_name.strip().split("_")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            return int(parts[0]) * 1000, int(parts[1]) * 1000

    raise ValueError(
        "Each LAZ tile must define either 'x_min'/'y_min' or a 'nom' like '1830_5175'."
    )


def select_tiles(
    tiles: list[dict[str, Any]],
    xmin_start: int,
    xmin_end: int,
    ymin_start: int,
    ymin_end: int,
) -> list[dict[str, Any]]:
    validate_bbox(xmin_start, xmin_end, ymin_start, ymin_end)
    selected_tiles: list[dict[str, Any]] = []
    for tile in tiles:
        tile_x_min, tile_y_min = tile_origin(tile)
        if xmin_start <= tile_x_min <= xmin_end and ymin_start <= tile_y_min <= ymin_end:
            selected_tiles.append(tile)
    return selected_tiles


def download_tile(session: requests.Session, url: str, output_path: Path) -> None:
    with session.get(url, stream=True, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        response.raise_for_status()
        with output_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                if chunk:
                    handle.write(chunk)


def main() -> None:
    args = parse_args()
    validate_bbox(args.xmin_start, args.xmin_end, args.ymin_start, args.ymin_end)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    tiles = load_tiles(args.json_file)
    selected_tiles = select_tiles(
        tiles,
        xmin_start=args.xmin_start,
        xmin_end=args.xmin_end,
        ymin_start=args.ymin_start,
        ymin_end=args.ymin_end,
    )

    if not selected_tiles:
        raise SystemExit("No LAZ tile found in the selected interval.")

    print(f"{len(selected_tiles)} LAZ tile(s) selected.")

    with requests.Session() as session:
        for tile in selected_tiles:
            url = tile["url"].strip()
            output_path = args.output_dir / Path(url).name

            if output_path.exists():
                print(f"Skipping existing file: {output_path}")
                continue

            print(f"Downloading: {url}")
            download_tile(session, url, output_path)
            print(f"Saved to: {output_path}")

    print("LAZ tile download completed.")


if __name__ == "__main__":
    main()
