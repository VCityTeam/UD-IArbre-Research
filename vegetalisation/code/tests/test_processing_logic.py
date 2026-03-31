from __future__ import annotations

import numpy as np

from calculateVegetationFromLidar import classify_from_difference, compute_multi_vege
from fusion_lidar_flair import create_vegetation_map, fuse_maps
from fusion_nuage import clean_mnt_mns
from lidarCorrection import fill_nan_with_neighbors, replace_nan_by_zero

DEFAULT_MATRIX_CONFIG = {
    "lidar": {
        "vegetation_classes": [3, 4, 5, 8],
        "optional_class_1": 1,
        "mask_excluded_value": 255,
        "height_thresholds": {
            "low_to_medium": 0.30,
            "medium_to_high": 5.0,
        },
    },
    "flair": {
        "mask_excluded_value": 255,
        "height_thresholds": {
            "low_to_medium": 0.75,
            "medium_to_high": 5.0,
        },
    },
}


def test_fill_nan_with_neighbors_uses_local_average() -> None:
    image = np.array(
        [
            [1.0, 1.0, 1.0],
            [1.0, np.nan, 3.0],
            [1.0, 5.0, 7.0],
        ],
        dtype=np.float32,
    )

    filled = fill_nan_with_neighbors(image, window_size=3)

    assert filled[1, 1] == np.mean([1.0, 1.0, 1.0, 1.0, 3.0, 1.0, 5.0, 7.0])


def test_replace_nan_by_zero_fills_remaining_gaps() -> None:
    image = np.array([[np.nan, 2.0]], dtype=np.float32)
    assert np.array_equal(replace_nan_by_zero(image), np.array([[0.0, 2.0]], dtype=np.float32))


def test_compute_multi_vege_uses_lidar_and_mask_rules() -> None:
    max_height = np.array([[10.0, 8.0], [6.0, 4.0]], dtype=np.float32)
    min_height = np.array([[1.0, 2.0], [1.0, 1.0]], dtype=np.float32)
    lidar_class = np.array([[5.0, 1.0], [2.0, 1.0]], dtype=np.float32)
    mask_raster = np.array([[255.0, 0.0], [255.0, 255.0]], dtype=np.float32)

    result = compute_multi_vege(max_height, min_height, lidar_class, mask_raster)

    expected = np.array([[9.0, 6.0], [np.nan, np.nan]], dtype=np.float32)
    assert np.allclose(result, expected, equal_nan=True)


def test_classify_from_difference_applies_height_thresholds() -> None:
    diff = np.array([[0.75, 2.0, 6.0, 20.0]], dtype=np.float32)
    result = classify_from_difference(diff)
    assert np.array_equal(result, np.array([[1.0, 2.0, 3.0, 4.0]], dtype=np.float32))


def test_create_vegetation_map_generates_lidar_classes_and_keeps_flair_by_default() -> None:
    class_lidar = np.array([[5, 1], [8, 2]], dtype=np.int16)
    height_lidar = np.array([[0.2, 2.0], [6.0, 10.0]], dtype=np.float32)
    veg_mask = np.array([[0, 0], [0, 255]], dtype=np.uint8)
    flair = np.array([[3, 1], [2, 0]], dtype=np.uint8)

    lidar_out, flair_out = create_vegetation_map(
        class_lidar,
        height_lidar,
        veg_mask,
        flair,
        config=DEFAULT_MATRIX_CONFIG,
        keep_class_lidar1=True,
    )

    assert np.array_equal(lidar_out, np.array([[0.0, 1.0], [2.0, np.nan]], dtype=np.float32), equal_nan=True)
    assert np.array_equal(
        flair_out,
        np.array([[0.0, 1.0], [2.0, np.nan]], dtype=np.float32),
        equal_nan=True,
    )


def test_create_vegetation_map_uses_configured_thresholds() -> None:
    class_lidar = np.array([[5, 5], [5, 5]], dtype=np.int16)
    height_lidar = np.array([[0.4, 1.6], [4.0, 8.0]], dtype=np.float32)
    veg_mask = np.zeros((2, 2), dtype=np.uint8)
    flair = np.zeros((2, 2), dtype=np.uint8)
    matrix_config = {
        "lidar": {
            "vegetation_classes": [5],
            "optional_class_1": 1,
            "mask_excluded_value": 255,
            "height_thresholds": {
                "low_to_medium": 1.5,
                "medium_to_high": 3.5,
            },
        },
        "flair": {
            "mask_excluded_value": 255,
            "height_thresholds": {
                "low_to_medium": 0.5,
                "medium_to_high": 6.0,
            },
        },
    }

    lidar_out, flair_out = create_vegetation_map(
        class_lidar,
        height_lidar,
        veg_mask,
        flair,
        config=matrix_config,
        keep_class_lidar1=False,
    )

    assert np.array_equal(lidar_out, np.array([[0.0, 1.0], [2.0, 2.0]], dtype=np.float32))
    assert np.array_equal(flair_out, np.array([[0.0, 1.0], [1.0, 2.0]], dtype=np.float32))


def test_fuse_maps_uses_flair_only_for_invalid_lidar_cells_when_restricted() -> None:
    lidar = np.array([[0.0, np.nan], [2.0, np.nan]], dtype=np.float32)
    flair = np.array([[1.0, 0.0], [1.0, 2.0]], dtype=np.float32)

    fused = fuse_maps(lidar, flair, use_flair_everywhere=False)

    expected = np.array([[0.0, 0.0], [2.0, np.nan]], dtype=np.float32)
    assert np.allclose(fused, expected, equal_nan=True)


def test_clean_mnt_mns_fills_water_cells_with_minimum_valid_value() -> None:
    input_array = np.array(
        [
            [10.0, np.nan],
            [14.0, 16.0],
        ],
        dtype=np.float32,
    )
    input_class = np.array(
        [
            [0, 9],
            [0, 0],
        ],
        dtype=np.int16,
    )

    cleaned = clean_mnt_mns(input_array, input_class)

    assert cleaned[0, 1] == 10.0


def test_clean_mnt_mns_stops_when_isolated_nans_cannot_be_interpolated() -> None:
    input_array = np.array(
        [
            [12.0, np.nan, np.nan],
            [np.nan, np.nan, np.nan],
            [np.nan, np.nan, np.nan],
        ],
        dtype=np.float32,
    )
    input_class = np.zeros_like(input_array, dtype=np.int16)

    cleaned = clean_mnt_mns(input_array, input_class)

    assert np.array_equal(
        cleaned,
        np.full((3, 3), 12.0, dtype=np.float32),
    )
