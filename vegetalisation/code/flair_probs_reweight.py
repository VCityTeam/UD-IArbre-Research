from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio
import yaml

from workflow_utils import load_json_mapping, load_json_numeric_mapping

DEFAULT_MATRIX_CONFIG = Path("configs/baseline/configs.yml")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reweight class probabilities, then filter and remap target classes."
    )
    parser.add_argument("--input", "-i", type=Path, required=True, help="Input probability GeoTIFF")
    parser.add_argument("--output", "-o", type=Path, required=True, help="Output remapped GeoTIFF")
    parser.add_argument(
        "--weights-json",
        type=Path,
        help="Optional JSON object mapping probability band indices to multiplicative weights.",
    )
    parser.add_argument(
        "--mapping-json",
        type=Path,
        help="Optional JSON object mapping original predicted classes to output classes.",
    )
    parser.add_argument("--matrix-config", type=Path, default=DEFAULT_MATRIX_CONFIG)
    return parser.parse_args()


def resolve_matrix_config_path(config_path: Path) -> Path:
    if config_path.is_absolute():
        return config_path
    return Path(__file__).resolve().parent / config_path


def load_reweight_config(config_path: Path) -> tuple[dict[int, float], dict[int, int], int]:
    with config_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError(f"Invalid matrix config: {config_path}")

    try:
        reweight_config = config["flair"]["reweight"]
    except KeyError as exc:
        raise KeyError(
            f"Missing flair.reweight configuration in matrix config: {config_path}"
        ) from exc

    weights = {int(class_id): float(weight) for class_id, weight in reweight_config["weights"].items()}
    mapping = {int(source): int(target) for source, target in reweight_config["mapping"].items()}
    ignore_value = int(reweight_config["ignore_value"])
    return weights, mapping, ignore_value


def reweight_and_filter(
    input_tif: Path,
    output_tif: Path,
    weights: dict[int, float] | None = None,
    mapping: dict[int, int] | None = None,
    ignore_value: int | None = None,
    matrix_config_path: Path = DEFAULT_MATRIX_CONFIG,
) -> None:
    config_weights, config_mapping, config_ignore_value = load_reweight_config(
        resolve_matrix_config_path(matrix_config_path)
    )
    weights = weights or config_weights
    mapping = mapping or config_mapping
    ignore_value = config_ignore_value if ignore_value is None else int(ignore_value)

    with rasterio.open(input_tif) as src:
        probs = src.read().astype(np.float32)
        meta = src.meta.copy()

    missing_classes = [class_id for class_id in weights if class_id >= probs.shape[0]]
    if missing_classes:
        raise ValueError(
            f"Weight(s) defined for missing class band(s): {missing_classes}. "
            f"Input only contains {probs.shape[0]} band(s)."
        )

    for class_id, weight in weights.items():
        probs[class_id] *= weight

    prediction = np.argmax(probs, axis=0).astype(np.uint8)
    filtered = np.full(prediction.shape, ignore_value, dtype=np.uint8)
    for original_class, mapped_class in mapping.items():
        filtered[prediction == original_class] = mapped_class

    meta.update(count=1, dtype="uint8", nodata=ignore_value)
    output_tif.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(output_tif, "w", **meta) as dst:
        dst.write(filtered, 1)

    print(f"Saved reweighted raster to: {output_tif}")


def main() -> None:
    args = parse_args()
    weights = (
        load_json_numeric_mapping(args.weights_json, "weights_json") if args.weights_json else None
    )
    mapping = load_json_mapping(args.mapping_json, "mapping_json") if args.mapping_json else None
    reweight_and_filter(
        args.input,
        args.output,
        weights=weights,
        mapping=mapping,
        matrix_config_path=args.matrix_config,
    )


if __name__ == "__main__":
    main()
