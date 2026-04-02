from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import rasterio
import seaborn as sns
import yaml
from rasterio.enums import Resampling
from rasterio.warp import reproject
from rasterio.windows import from_bounds

from workflow_utils import write_json

try:
    import torch
except ImportError:  # pragma: no cover - optional dependency outside the Docker image
    torch = None

DEFAULT_MATRIX_CONFIG = Path("configs/configs.yml")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute a confusion matrix and summary metrics between a reference raster and a predicted raster."
    )
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--prediction", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--matrix-config", type=Path, default=DEFAULT_MATRIX_CONFIG)
    parser.add_argument("--plot-name", default="confusion_matrix_percent.png")
    parser.add_argument("--metrics-name", default="metrics_summary.json")
    parser.add_argument("--log-name", default="metrics_log.txt")
    parser.add_argument(
        "--use-gpu",
        action="store_true",
        help="Use CUDA via PyTorch for confusion-matrix accumulation when available.",
    )
    return parser.parse_args()


def load_matrix_config(config_path: Path) -> dict:
    with config_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError(f"Invalid matrix config: {config_path}")
    return config


def resolve_matrix_config_path(config_path: Path) -> Path:
    if config_path.is_absolute():
        return config_path
    return Path(__file__).resolve().parent / config_path


def load_overlapping_rasters(reference_path: Path, prediction_path: Path) -> tuple[np.ndarray, np.ndarray]:
    with rasterio.open(reference_path) as reference_src, rasterio.open(prediction_path) as prediction_src:
        left = max(reference_src.bounds.left, prediction_src.bounds.left)
        bottom = max(reference_src.bounds.bottom, prediction_src.bounds.bottom)
        right = min(reference_src.bounds.right, prediction_src.bounds.right)
        top = min(reference_src.bounds.top, prediction_src.bounds.top)

        if left >= right or bottom >= top:
            raise ValueError("Reference and prediction rasters do not overlap.")

        prediction_window = from_bounds(
            left,
            bottom,
            right,
            top,
            transform=prediction_src.transform,
        ).round_offsets().round_lengths()

        prediction = prediction_src.read(1, window=prediction_window).astype(np.float32)
        if prediction_src.nodata is not None:
            prediction[prediction == prediction_src.nodata] = np.nan

        if reference_src.crs == prediction_src.crs:
            reference_window = from_bounds(
                left,
                bottom,
                right,
                top,
                transform=reference_src.transform,
            ).round_offsets().round_lengths()
            reference = reference_src.read(
                1,
                window=reference_window,
                out_shape=prediction.shape,
                resampling=Resampling.nearest,
            ).astype(np.float32)
            if reference_src.nodata is not None:
                reference[reference == reference_src.nodata] = np.nan
        else:
            reference = np.full(prediction.shape, np.nan, dtype=np.float32)
            reproject(
                source=rasterio.band(reference_src, 1),
                destination=reference,
                src_transform=reference_src.transform,
                src_crs=reference_src.crs,
                src_nodata=reference_src.nodata,
                dst_transform=prediction_src.window_transform(prediction_window),
                dst_crs=prediction_src.crs,
                dst_nodata=np.nan,
                resampling=Resampling.nearest,
            )

    return reference, prediction


def remap_classes(array: np.ndarray, mapping: dict[int, int]) -> np.ndarray:
    result = np.full_like(array, np.nan, dtype=np.float32)
    for source_class, target_class in mapping.items():
        result[array == source_class] = target_class
    return result


def compute_confusion_matrix_cpu(
    reference_final: np.ndarray, prediction_final: np.ndarray, num_classes: int
) -> np.ndarray:
    encoded = (reference_final.astype(np.int64) * num_classes) + prediction_final.astype(np.int64)
    counts = np.bincount(encoded.ravel(), minlength=num_classes * num_classes)
    return counts.reshape(num_classes, num_classes)


def compute_confusion_matrix_gpu(
    reference_final: np.ndarray, prediction_final: np.ndarray, num_classes: int
) -> np.ndarray:
    if torch is None:
        raise RuntimeError("PyTorch is not installed, so GPU evaluation is unavailable.")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available, so GPU evaluation cannot be used.")

    device = torch.device("cuda")
    reference_tensor = torch.from_numpy(reference_final.astype(np.int64, copy=False)).to(device)
    prediction_tensor = torch.from_numpy(prediction_final.astype(np.int64, copy=False)).to(device)
    encoded = reference_tensor.reshape(-1) * num_classes + prediction_tensor.reshape(-1)
    counts = torch.bincount(encoded, minlength=num_classes * num_classes)
    return counts.reshape(num_classes, num_classes).cpu().numpy()


def compute_confusion_percent_with_empty(
    raster_ref_path: Path,
    raster_compare_path: Path,
    *,
    matrix_config_path: Path = DEFAULT_MATRIX_CONFIG,
    use_gpu: bool = False,
) -> tuple[np.ndarray, np.ndarray, dict[int, str]]:
    config = load_matrix_config(resolve_matrix_config_path(matrix_config_path))
    evaluation_config = config["evaluation"]

    reference, prediction = load_overlapping_rasters(raster_ref_path, raster_compare_path)

    reference_remap = remap_classes(reference, evaluation_config["reference_remap"])
    prediction_remap = remap_classes(prediction, evaluation_config["prediction_remap"])

    class_names = {
        int(class_id): class_name
        for class_id, class_name in evaluation_config["class_names"].items()
    }
    empty_class_id = int(evaluation_config["empty_class_id"])

    reference_final = np.where(np.isnan(reference_remap), empty_class_id, reference_remap).astype(np.int64)
    prediction_final = np.where(np.isnan(prediction_remap), empty_class_id, prediction_remap).astype(np.int64)

    num_classes = len(class_names)
    if use_gpu:
        try:
            print("Computing confusion matrix on GPU.")
            cm = compute_confusion_matrix_gpu(reference_final, prediction_final, num_classes)
        except RuntimeError as error:
            print(f"{error} Falling back to CPU evaluation.")
            cm = compute_confusion_matrix_cpu(reference_final, prediction_final, num_classes)
    else:
        cm = compute_confusion_matrix_cpu(reference_final, prediction_final, num_classes)

    cm_percent = cm.astype(np.float64)
    row_sums = cm_percent.sum(axis=1, keepdims=True)
    cm_percent = np.divide(cm_percent, row_sums, out=np.zeros_like(cm_percent), where=row_sums != 0)
    cm_percent *= 100
    return cm, cm_percent, class_names


def plot_confusion_matrix_percent(
    cm_percent: np.ndarray, class_names: dict[int, str], output_path: Path
) -> None:
    plt.figure(figsize=(8, 6))
    sns.heatmap(
        cm_percent,
        annot=True,
        fmt=".1f",
        cmap="plasma",
        xticklabels=[class_names[i] for i in range(len(class_names))],
        yticklabels=[class_names[i] for i in range(len(class_names))],
    )
    plt.xlabel("Classes predites")
    plt.ylabel("Classes reelles")
    plt.title("Matrice de confusion (%) raster vs raster")
    plt.tight_layout()
    plt.savefig(output_path, dpi=300)
    plt.close()
    print(f"Saved confusion matrix plot to: {output_path}")


def compute_metrics_from_confusion_matrix(
    cm: np.ndarray, class_names: dict[int, str]
) -> dict[str, object]:
    num_classes = cm.shape[0]
    per_class: list[dict[str, float | str]] = []

    for index in range(num_classes):
        true_positive = float(cm[index, index])
        false_positive = float(np.sum(cm[:, index]) - true_positive)
        false_negative = float(np.sum(cm[index, :]) - true_positive)
        denom_iou = true_positive + false_positive + false_negative

        iou = true_positive / denom_iou if denom_iou != 0 else float("nan")
        precision = (
            true_positive / (true_positive + false_positive)
            if (true_positive + false_positive) != 0
            else float("nan")
        )
        recall = (
            true_positive / (true_positive + false_negative)
            if (true_positive + false_negative) != 0
            else float("nan")
        )
        dice = (
            (2 * true_positive) / (2 * true_positive + false_positive + false_negative)
            if (2 * true_positive + false_positive + false_negative) != 0
            else float("nan")
        )

        per_class.append(
            {
                "class_id": index,
                "class_name": class_names[index],
                "iou": iou,
                "precision": precision,
                "recall": recall,
                "dice": dice,
            }
        )

    valid_entries = [entry for entry in per_class if entry["class_name"] != "Autre"]
    summary = {
        "mean_iou": float(np.nanmean([entry["iou"] for entry in valid_entries])),
        "mean_precision": float(np.nanmean([entry["precision"] for entry in valid_entries])),
        "mean_recall": float(np.nanmean([entry["recall"] for entry in valid_entries])),
        "mean_dice": float(np.nanmean([entry["dice"] for entry in valid_entries])),
    }
    return {"per_class": per_class, "summary": summary}


def write_log(metrics: dict[str, object], log_path: Path) -> None:
    per_class = metrics["per_class"]
    summary = metrics["summary"]
    with log_path.open("w", encoding="utf-8") as handle:
        handle.write("==== Segmentation Performance Report ====\n\n")
        handle.write(f"{'Class':<20}{'IoU':>10}{'Precision':>15}{'Recall':>15}{'Dice':>15}\n")
        handle.write("-" * 75 + "\n")
        for entry in per_class:
            handle.write(
                f"{entry['class_name']:<20}"
                f"{entry['iou']:>10.3f}"
                f"{entry['precision']:>15.3f}"
                f"{entry['recall']:>15.3f}"
                f"{entry['dice']:>15.3f}\n"
            )
        handle.write("\n==== Global Means (excluding 'Autre') ====\n")
        handle.write(f"Mean IoU       : {summary['mean_iou']:.3f}\n")
        handle.write(f"Mean Precision : {summary['mean_precision']:.3f}\n")
        handle.write(f"Mean Recall    : {summary['mean_recall']:.3f}\n")
        handle.write(f"Mean Dice      : {summary['mean_dice']:.3f}\n")


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    cm, cm_percent, class_names = compute_confusion_percent_with_empty(
        args.reference,
        args.prediction,
        matrix_config_path=args.matrix_config,
        use_gpu=args.use_gpu,
    )
    plot_confusion_matrix_percent(cm_percent, class_names, args.output_dir / args.plot_name)

    metrics = compute_metrics_from_confusion_matrix(cm, class_names)
    write_json(metrics, args.output_dir / args.metrics_name)
    write_log(metrics, args.output_dir / args.log_name)

    print(f"Saved metrics JSON to: {args.output_dir / args.metrics_name}")
    print(f"Saved metrics log to: {args.output_dir / args.log_name}")


if __name__ == "__main__":
    main()
