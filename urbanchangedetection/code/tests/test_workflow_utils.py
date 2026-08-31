from __future__ import annotations

import numpy as np
import pytest

from workflow_utils import (
    align_array_to_shape,
    coerce_int_key_mapping,
    load_json_numeric_mapping,
    max_shape,
    validate_bbox,
    validate_odd_positive_integer,
)


def test_validate_bbox_rejects_inverted_ranges() -> None:
    with pytest.raises(ValueError):
        validate_bbox(10, 5, 0, 1)


def test_validate_odd_positive_integer_rejects_even_values() -> None:
    with pytest.raises(ValueError):
        validate_odd_positive_integer(4, "window_size")


def test_align_array_to_shape_pads_with_fill_value() -> None:
    array = np.array([[1, 2], [3, 4]], dtype=np.float32)
    result = align_array_to_shape(array, (3, 4), fill_value=np.nan, allow_crop=True)

    assert result.shape == (3, 4)
    assert np.allclose(result[:2, :2], array, equal_nan=True)
    assert np.isnan(result[2, 3])


def test_align_array_to_shape_rejects_oversized_input_when_crop_disabled() -> None:
    array = np.ones((3, 3), dtype=np.float32)
    with pytest.raises(ValueError):
        align_array_to_shape(array, (2, 2), fill_value=np.nan, allow_crop=False)


def test_max_shape_uses_largest_dimensions() -> None:
    arrays = [np.zeros((2, 5)), np.zeros((4, 3)), np.zeros((1, 9))]
    assert max_shape(arrays) == (4, 9)


def test_coerce_int_key_mapping_casts_string_pairs() -> None:
    assert coerce_int_key_mapping({"1": "2"}, "mapping") == {1: 2}


def test_load_json_numeric_mapping_reads_float_values(workspace_tmp_path) -> None:
    mapping_path = workspace_tmp_path / "weights.json"
    mapping_path.write_text('{"8": 1.25, "14": 0.5}', encoding="utf-8")

    assert load_json_numeric_mapping(mapping_path, "weights") == {8: 1.25, 14: 0.5}
