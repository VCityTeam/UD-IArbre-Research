from __future__ import annotations

import numpy as np
import rasterio
from rasterio.transform import from_origin

from flair_probs_reweight import reweight_and_filter


def test_reweight_and_filter_applies_weights_and_mapping(workspace_tmp_path) -> None:
    input_path = workspace_tmp_path / "probs.tif"
    output_path = workspace_tmp_path / "out.tif"

    probabilities = np.zeros((19, 2, 2), dtype=np.float32)
    probabilities[8, 0, 0] = 0.9
    probabilities[14, 0, 0] = 0.1
    probabilities[8, 0, 1] = 0.1
    probabilities[14, 0, 1] = 0.8
    probabilities[12, 1, 0] = 0.6

    with rasterio.open(
        input_path,
        "w",
        driver="GTiff",
        height=2,
        width=2,
        count=19,
        dtype="float32",
        transform=from_origin(0, 2, 1, 1),
    ) as dst:
        dst.write(probabilities)

    reweight_and_filter(
        input_path,
        output_path,
        weights={8: 1.0, 14: 2.0, 12: 1.0},
        mapping={8: 0, 14: 1, 12: 2},
    )

    with rasterio.open(output_path) as src:
        result = src.read(1)

    expected = np.array([[0, 1], [2, 255]], dtype=np.uint8)
    assert np.array_equal(result, expected)


def test_reweight_and_filter_rejects_missing_band_weights(workspace_tmp_path) -> None:
    input_path = workspace_tmp_path / "probs.tif"
    output_path = workspace_tmp_path / "out.tif"

    with rasterio.open(
        input_path,
        "w",
        driver="GTiff",
        height=1,
        width=1,
        count=2,
        dtype="float32",
        transform=from_origin(0, 1, 1, 1),
    ) as dst:
        dst.write(np.zeros((2, 1, 1), dtype=np.float32))

    try:
        reweight_and_filter(input_path, output_path, weights={3: 1.0}, mapping={0: 0})
    except ValueError as exc:
        assert "missing class band" in str(exc)
    else:
        raise AssertionError("Expected ValueError for missing class band weight.")
