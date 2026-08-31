from __future__ import annotations

import json
import platform
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class BoundingBox:
    xmin_start: int
    xmin_end: int
    ymin_start: int
    ymin_end: int


def validate_bbox(
    xmin_start: int,
    xmin_end: int,
    ymin_start: int,
    ymin_end: int,
) -> BoundingBox:
    if xmin_start > xmin_end:
        raise ValueError("xmin-start must be less than or equal to xmin-end.")
    if ymin_start > ymin_end:
        raise ValueError("ymin-start must be less than or equal to ymin-end.")
    return BoundingBox(
        xmin_start=xmin_start,
        xmin_end=xmin_end,
        ymin_start=ymin_start,
        ymin_end=ymin_end,
    )


def validate_positive_number(value: float, name: str) -> float:
    if value <= 0:
        raise ValueError(f"{name} must be strictly positive.")
    return value


def validate_odd_positive_integer(value: int, name: str) -> int:
    if value <= 0:
        raise ValueError(f"{name} must be strictly positive.")
    if value % 2 == 0:
        raise ValueError(f"{name} must be odd.")
    return value


def coerce_int_key_mapping(raw_mapping: dict[Any, Any], name: str) -> dict[int, int]:
    mapping: dict[int, int] = {}
    for key, value in raw_mapping.items():
        try:
            mapping[int(key)] = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must contain integer-like keys and values.") from exc
    return mapping


def load_json_mapping(path: Path, name: str) -> dict[int, int]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{name} must be a JSON object.")
    return coerce_int_key_mapping(data, name)


def load_json_numeric_mapping(path: Path, name: str) -> dict[int, float]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{name} must be a JSON object.")

    mapping: dict[int, float] = {}
    for key, value in data.items():
        try:
            mapping[int(key)] = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must contain integer-like keys and numeric values.") from exc
    return mapping


def align_array_to_shape(
    array: np.ndarray,
    target_shape: tuple[int, int],
    *,
    fill_value: float | int,
    allow_crop: bool,
) -> np.ndarray:
    target_rows, target_cols = target_shape
    rows, cols = array.shape

    if not allow_crop and (rows > target_rows or cols > target_cols):
        raise ValueError("Input raster is larger than the requested target shape.")

    aligned = array[:target_rows, :target_cols]
    pad_rows = max(0, target_rows - aligned.shape[0])
    pad_cols = max(0, target_cols - aligned.shape[1])
    if pad_rows == 0 and pad_cols == 0:
        return aligned

    return np.pad(
        aligned,
        pad_width=((0, pad_rows), (0, pad_cols)),
        mode="constant",
        constant_values=fill_value,
    )


def max_shape(arrays: list[np.ndarray]) -> tuple[int, int]:
    return (
        max(array.shape[0] for array in arrays),
        max(array.shape[1] for array in arrays),
    )


def write_json(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=True, sort_keys=True)


def collect_runtime_versions(packages: list[str]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in packages:
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def build_run_manifest(
    *,
    command: list[str],
    args: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "arguments": args,
        "python": sys.version,
        "platform": platform.platform(),
    }
    if extra:
        manifest["extra"] = extra
    return manifest
