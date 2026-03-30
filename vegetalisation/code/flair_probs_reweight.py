from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio

from workflow_utils import load_json_mapping, load_json_numeric_mapping

DEFAULT_WEIGHTS = {
    0: 1,
    1: 1,
    2: 1,
    3: 1,
    4: 1,
    5: 1,
    6: 1,
    7: 1,
    8: 1,
    9: 1,
    10: 1,
    11: 1,
    12: 1,
    13: 1,
    14: 1,
    15: 1,
    16: 1,
    17: 1,
    18: 1,
}
DEFAULT_MAPPING = {
    8: 0,
    14: 1,
    12: 2,
    13: 3,
}
IGNORE_VALUE = 255


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
    return parser.parse_args()


def reweight_and_filter(
    input_tif: Path,
    output_tif: Path,
    weights: dict[int, float] | None = None,
    mapping: dict[int, int] | None = None,
) -> None:
    weights = weights or DEFAULT_WEIGHTS
    mapping = mapping or DEFAULT_MAPPING

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
    filtered = np.full(prediction.shape, IGNORE_VALUE, dtype=np.uint8)
    for original_class, mapped_class in mapping.items():
        filtered[prediction == original_class] = mapped_class

    meta.update(count=1, dtype="uint8", nodata=IGNORE_VALUE)
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
    reweight_and_filter(args.input, args.output, weights=weights, mapping=mapping)


if __name__ == "__main__":
    main()
