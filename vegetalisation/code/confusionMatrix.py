from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import rasterio
import seaborn as sns
from sklearn.metrics import confusion_matrix

from workflow_utils import align_array_to_shape, write_json

REFERENCE_REMAP = {1: 0, 2: 1, 3: 1, 4: 2, 5: 2}
PREDICTION_REMAP = {0: 0, 1: 1, 2: 2, 3: 2}
CLASS_NAMES = {0: "Pelouse", 1: "Buisson_Arbuste", 2: "Arbre", 3: "Autre"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute a confusion matrix and summary metrics between a reference raster and a predicted raster."
    )
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--prediction", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--plot-name", default="confusion_matrix_percent.png")
    parser.add_argument("--metrics-name", default="metrics_summary.json")
    parser.add_argument("--log-name", default="metrics_log.txt")
    return parser.parse_args()


def load_raster(path: Path) -> np.ndarray:
    with rasterio.open(path) as src:
        data = src.read(1).astype(np.float32)
        if src.nodata is not None:
            data[data == src.nodata] = np.nan
    return data


def remap_classes(array: np.ndarray, mapping: dict[int, int]) -> np.ndarray:
    result = np.full_like(array, np.nan, dtype=np.float32)
    for source_class, target_class in mapping.items():
        result[array == source_class] = target_class
    return result


def compute_confusion_percent_with_empty(
    raster_ref_path: Path, raster_compare_path: Path
) -> tuple[np.ndarray, np.ndarray, dict[int, str]]:
    reference = load_raster(raster_ref_path)
    prediction = load_raster(raster_compare_path)

    target_shape = (
        max(reference.shape[0], prediction.shape[0]),
        max(reference.shape[1], prediction.shape[1]),
    )
    reference = pad_or_crop_to_size(reference, target_shape)
    prediction = pad_or_crop_to_size(prediction, target_shape)

    reference_remap = remap_classes(reference, REFERENCE_REMAP)
    prediction_remap = remap_classes(prediction, PREDICTION_REMAP)

    reference_final = np.where(np.isnan(reference_remap), 3, reference_remap)
    prediction_final = np.where(np.isnan(prediction_remap), 3, prediction_remap)

    labels = [0, 1, 2, 3]
    cm = confusion_matrix(reference_final.flatten(), prediction_final.flatten(), labels=labels)

    cm_percent = cm.astype(np.float64)
    row_sums = cm_percent.sum(axis=1, keepdims=True)
    cm_percent = np.divide(cm_percent, row_sums, out=np.zeros_like(cm_percent), where=row_sums != 0)
    cm_percent *= 100
    return cm, cm_percent, CLASS_NAMES


def pad_or_crop_to_size(arr: np.ndarray, target_shape: tuple[int, int]) -> np.ndarray:
    return align_array_to_shape(arr, target_shape, fill_value=np.nan, allow_crop=True)


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
        args.reference, args.prediction
    )
    plot_confusion_matrix_percent(cm_percent, class_names, args.output_dir / args.plot_name)

    metrics = compute_metrics_from_confusion_matrix(cm, class_names)
    write_json(metrics, args.output_dir / args.metrics_name)
    write_log(metrics, args.output_dir / args.log_name)

    print(f"Saved metrics JSON to: {args.output_dir / args.metrics_name}")
    print(f"Saved metrics log to: {args.output_dir / args.log_name}")


if __name__ == "__main__":
    main()
