from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
import yaml
from rasterio.transform import from_origin

from confusionMatrix import compute_confusion_percent_with_empty
from extract_nuage import select_tiles as select_lidar_tiles
from ortho_extract import resample_raster
from run_workflow import (
    ensure_flair_hub_source,
    ensure_inventory_file,
    require_existing_file,
    stage_matching_tiles,
    write_runtime_config,
)


def test_write_runtime_config_injects_runtime_values(workspace_tmp_path) -> None:
    template = {
        "modalities": {"AERIAL_RGBI": {"input_img_path": "placeholder.tif", "channels": [1, 2, 3, 4]}}
    }
    template_path = workspace_tmp_path / "template.yaml"
    output_path = workspace_tmp_path / "runtime.yaml"
    template_path.write_text(yaml.safe_dump(template), encoding="utf-8")

    write_runtime_config(
        template_path,
        output_path,
        model_path=Path("/models/model.safetensors"),
        orthophoto_mosaic=Path("/data/ortho.tif"),
        flair_output_dir=Path("/outputs"),
        run_name="demo",
        use_gpu=True,
        batch_size=2,
        num_worker=3,
        img_pixels_detection=512,
        margin=128,
        output_px_meters=0.8,
    )

    runtime = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    assert runtime["model_weights"] == "/models/model.safetensors"
    assert runtime["use_gpu"] is True
    assert runtime["modalities"]["AERIAL_RGBI"]["input_img_path"] == "/data/ortho.tif"
    assert runtime["modalities"]["AERIAL_RGBI"]["channels"] == [1, 2, 3]


def test_stage_matching_tiles_copies_only_matching_rasters(workspace_tmp_path) -> None:
    source = workspace_tmp_path / "source"
    target = workspace_tmp_path / "target"
    source.mkdir()
    (source / "tile_1_mns.tif").write_bytes(b"mns")
    (source / "tile_1_mnt.tif").write_bytes(b"mnt")

    copied = stage_matching_tiles(source, target, "*_mns.tif")

    assert copied == 1
    assert sorted(path.name for path in target.glob("*.tif")) == ["tile_1_mns.tif"]


def test_resample_raster_writes_expected_output_shape(workspace_tmp_path) -> None:
    source_path = workspace_tmp_path / "source.tif"
    output_path = workspace_tmp_path / "output.tif"
    data = np.arange(16, dtype=np.float32).reshape(1, 4, 4)

    with rasterio.open(
        source_path,
        "w",
        driver="GTiff",
        height=4,
        width=4,
        count=1,
        dtype="float32",
        transform=from_origin(0, 4, 0.05, 0.05),
    ) as dst:
        dst.write(data)

    resample_raster(
        source_path,
        output_path,
        output_resolution=0.1,
    )

    with rasterio.open(output_path) as src:
        assert src.width == 2
        assert src.height == 2
        assert src.res == (0.1, 0.1)


def test_ensure_inventory_file_keeps_existing_file(workspace_tmp_path) -> None:
    inventory_path = workspace_tmp_path / "inputs" / "nuage.json"
    inventory_path.parent.mkdir(parents=True)
    inventory_path.write_text('{"values": []}', encoding="utf-8")

    resolved = ensure_inventory_file(
        inventory_path,
        label="LiDAR inventory",
        url=None,
        download_missing=False,
    )

    assert resolved == inventory_path
    assert inventory_path.read_text(encoding="utf-8") == '{"values": []}'


def test_ensure_inventory_file_downloads_missing_file(workspace_tmp_path, monkeypatch) -> None:
    inventory_path = workspace_tmp_path / "inputs" / "ortho.json"

    class DummyResponse:
        def __init__(self, content: bytes) -> None:
            self.content = content

        def __enter__(self) -> "DummyResponse":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

    def fake_get(url: str, timeout: int) -> DummyResponse:
        assert url == "https://example.test/ortho.json"
        assert timeout > 0
        return DummyResponse(b'{"values": []}')

    monkeypatch.setattr("run_workflow.requests.get", fake_get)

    resolved = ensure_inventory_file(
        inventory_path,
        label="orthophoto inventory",
        url="https://example.test/ortho.json",
        download_missing=True,
    )

    assert resolved == inventory_path
    assert inventory_path.read_text(encoding="utf-8") == '{"values": []}'


def test_select_lidar_tiles_supports_nom_based_inventory() -> None:
    tiles = [
        {"nom": "1845_5175", "url": "https://example.test/1845_5175.laz"},
        {"nom": "1846_5175", "url": "https://example.test/1846_5175.laz"},
    ]

    selected = select_lidar_tiles(
        tiles,
        xmin_start=1845000,
        xmin_end=1845000,
        ymin_start=5175000,
        ymin_end=5175000,
    )

    assert [tile["nom"] for tile in selected] == ["1845_5175"]


def test_select_lidar_tiles_supports_explicit_coordinates() -> None:
    tiles = [
        {"x_min": 1845000, "y_min": 5175000, "url": "https://example.test/a.laz"},
        {"x_min": 1846000, "y_min": 5175000, "url": "https://example.test/b.laz"},
    ]

    selected = select_lidar_tiles(
        tiles,
        xmin_start=1845000,
        xmin_end=1845000,
        ymin_start=5175000,
        ymin_end=5175000,
    )

    assert [tile["url"] for tile in selected] == ["https://example.test/a.laz"]


def test_ensure_flair_hub_source_reuses_existing_clone(workspace_tmp_path) -> None:
    source_root = workspace_tmp_path / ".deps"
    src_dir = source_root / "FLAIR-HUB" / "src"
    (src_dir / "flair_hub").mkdir(parents=True)
    (src_dir / "flair_zonal_detection").mkdir(parents=True)

    resolved = ensure_flair_hub_source(
        source_root,
        repo_url="https://example.test/FLAIR-HUB.git",
        ref="main",
    )

    assert resolved == src_dir


def test_require_existing_file_returns_path_when_present(workspace_tmp_path) -> None:
    raster_path = workspace_tmp_path / "existing.tif"
    raster_path.write_bytes(b"data")

    resolved = require_existing_file(raster_path, label="existing raster")

    assert resolved == raster_path


def test_require_existing_file_raises_when_missing(workspace_tmp_path) -> None:
    missing_path = workspace_tmp_path / "missing.tif"

    try:
        require_existing_file(missing_path, label="missing raster")
    except FileNotFoundError as exc:
        assert "Missing missing raster" in str(exc)
        assert str(missing_path) in str(exc)
    else:
        raise AssertionError("require_existing_file should raise for a missing path.")


def test_confusion_matrix_uses_only_overlapping_extent(workspace_tmp_path) -> None:
    reference_path = workspace_tmp_path / "reference.tif"
    prediction_path = workspace_tmp_path / "prediction.tif"

    reference_data = np.array(
        [
            [1, 1, 1, 1],
            [1, 2, 2, 1],
            [1, 4, 5, 1],
            [1, 1, 1, 1],
        ],
        dtype=np.uint8,
    )
    prediction_data = np.array(
        [
            [0, 1],
            [2, 3],
        ],
        dtype=np.uint8,
    )

    with rasterio.open(
        reference_path,
        "w",
        driver="GTiff",
        height=4,
        width=4,
        count=1,
        dtype="uint8",
        transform=from_origin(0, 4, 1, 1),
    ) as dst:
        dst.write(reference_data, 1)

    with rasterio.open(
        prediction_path,
        "w",
        driver="GTiff",
        height=2,
        width=2,
        count=1,
        dtype="uint8",
        transform=from_origin(1, 3, 1, 1),
    ) as dst:
        dst.write(prediction_data, 1)

    cm, cm_percent, class_names = compute_confusion_percent_with_empty(
        reference_path,
        prediction_path,
    )

    assert cm.shape == (4, 4)
    assert int(cm.sum()) == 4
    assert np.count_nonzero(cm) > 0
    assert class_names[2] == "Arbre"


def test_confusion_matrix_uses_mapping_from_matrix_config(workspace_tmp_path) -> None:
    reference_path = workspace_tmp_path / "reference_custom.tif"
    prediction_path = workspace_tmp_path / "prediction_custom.tif"
    matrix_config_path = workspace_tmp_path / "configs.yml"

    with rasterio.open(
        reference_path,
        "w",
        driver="GTiff",
        height=1,
        width=1,
        count=1,
        dtype="uint8",
        transform=from_origin(0, 1, 1, 1),
    ) as dst:
        dst.write(np.array([[7]], dtype=np.uint8), 1)

    with rasterio.open(
        prediction_path,
        "w",
        driver="GTiff",
        height=1,
        width=1,
        count=1,
        dtype="uint8",
        transform=from_origin(0, 1, 1, 1),
    ) as dst:
        dst.write(np.array([[9]], dtype=np.uint8), 1)

    matrix_config = {
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
        "evaluation": {
            "reference_remap": {7: 0},
            "prediction_remap": {9: 0},
            "class_names": {0: "CustomGrass", 1: "CustomShrub", 2: "CustomTree", 3: "Autre"},
            "empty_class_id": 3,
        },
    }
    matrix_config_path.write_text(yaml.safe_dump(matrix_config), encoding="utf-8")

    cm, _, class_names = compute_confusion_percent_with_empty(
        reference_path,
        prediction_path,
        matrix_config_path=matrix_config_path,
    )

    assert cm[0, 0] == 1
    assert class_names[0] == "CustomGrass"
