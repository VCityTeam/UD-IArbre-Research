import rasterio
import numpy as np
import matplotlib.pyplot as plt
from sklearn.metrics import confusion_matrix
import seaborn as sns
import os

def load_raster(path):
    """Charge un raster et remplace les nodata par NaN."""
    with rasterio.open(path) as src:
        data = src.read(1).astype(float)
        if src.nodata is not None:
            data[data == src.nodata] = np.nan
    return data

def remap_reference_classes_bellec(array):
    """
    Remappe les classes du raster référence (1–5) en classes finales 0–3 :
      1 -> 0 (Pelouse)
      2 -> 1 (Buisson / Arbuste)
      3 -> 1 (Buisson / Arbuste)
      4 -> 2 (Arbre)
      5 -> 2 (arbre)
    NaN reste NaN pour l'instant.
    """
    remap = {1: 0, 2: 1, 3: 1, 4: 2, 5: 2}
    result = np.full_like(array, np.nan, dtype=float)
    for k, v in remap.items():
        result[array == k] = v
    return result

def remap_reference_classes_LidarFlair(array):
    """
    Remappe les classes du raster référence (1–5) en classes finales 0–3 :
      1 -> 0 (Pelouse)
      2 -> 1 (Buisson / Arbuste)
      3 -> 1 (Buisson / Arbuste)
      4 -> 2 (Arbre)
      5 -> 2 (arbre)
    NaN reste NaN pour l'instant.
    """
    remap = {0: 0, 1: 1, 2: 2, 3: 2}
    result = np.full_like(array, np.nan, dtype=float)
    for k, v in remap.items():
        result[array == k] = v
    return result

def compute_confusion_percent_with_empty(raster_ref_path, raster_compare_path):
    # Charger les rasters
    r1 = load_raster(raster_ref_path)      # Référence 1–5
    r2 = load_raster(raster_compare_path)  # Comparé 0–3

    # Remapper le raster référence
    r1_remap = remap_reference_classes_bellec(r1)
    r2_remap = remap_reference_classes_LidarFlair(r2)

    # Remplacer les NaN par 4 (classe Vide)
    r1_final = np.where(np.isnan(r1_remap), 3, r1_remap)
    r2_final = np.where(np.isnan(r2_remap), 3, r2_remap)

    # Calculer la matrice
    labels = [0, 1, 2, 3]
    cm = confusion_matrix(r1_final.flatten(), r2_final.flatten(), labels=labels)

    # Normalisation par ligne pour le pourcentage
    cm_percent = cm.astype(float)
    cm_percent = cm_percent / cm_percent.sum(axis=1, keepdims=True) * 100

    # Noms des classes
    class_names = {
        0: "Pelouse",
        1: "Buisson / Arbuste",
        2: "Arbre",
        3: "Autre"
    }

    return cm, cm_percent, class_names

def plot_confusion_matrix_percent(cm_percent, class_names, output_path="confusion_matrix_percent.png"):
    plt.figure(figsize=(8, 6))
    sns.heatmap(
        cm_percent,
        annot=True,
        fmt=".1f",
        cmap="plasma",  # jaune -> violet
        xticklabels=[class_names[i] for i in range(len(class_names))],
        yticklabels=[class_names[i] for i in range(len(class_names))],
    )
    plt.xlabel("Classes prédites")
    plt.ylabel("Classes réelles")
    plt.title("Matrice de confusion (%) raster vs raster (avec Vide)")
    plt.tight_layout()
    plt.savefig(output_path, dpi=300)
    plt.close()
    print(f"✅ Matrice de confusion en pourcentage sauvegardée : {output_path}")

def compute_metrics_from_confusion_matrix(cm, class_names, log_path="metrics_log.txt"):
    """
    Calcule IoU, Precision, Recall, Dice par classe et globalement.
    Enregistre les résultats dans un fichier log.
    """
    num_classes = cm.shape[0]

    # Initialisation des tableaux
    iou_list, precision_list, recall_list, dice_list = [], [], [], []

    with open(log_path, "w", encoding="utf-8") as f:
        f.write("==== Rapport de performance segmentation ====\n\n")
        f.write(f"{'Classe':<20}{'IoU':>10}{'Precision':>15}{'Recall':>15}{'Dice':>15}\n")
        f.write("-" * 70 + "\n")

        for i in range(num_classes):
            TP = cm[i, i]
            FP = np.sum(cm[:, i]) - TP
            FN = np.sum(cm[i, :]) - TP
            denom_iou = TP + FP + FN

            IoU = TP / denom_iou if denom_iou != 0 else np.nan
            Precision = TP / (TP + FP) if (TP + FP) != 0 else np.nan
            Recall = TP / (TP + FN) if (TP + FN) != 0 else np.nan
            Dice = (2 * TP) / (2 * TP + FP + FN) if (2 * TP + FP + FN) != 0 else np.nan

            iou_list.append(IoU)
            precision_list.append(Precision)
            recall_list.append(Recall)
            dice_list.append(Dice)

            f.write(f"{class_names[i]:<20}{IoU:>10.3f}{Precision:>15.3f}{Recall:>15.3f}{Dice:>15.3f}\n")

        f.write("\n==== Moyennes globales (hors classe 'Vide') ====\n")

        valid_idx = [i for i in range(num_classes) if class_names[i] != "Vide"]
        mean_iou = np.nanmean([iou_list[i] for i in valid_idx])
        mean_precision = np.nanmean([precision_list[i] for i in valid_idx])
        mean_recall = np.nanmean([recall_list[i] for i in valid_idx])
        mean_dice = np.nanmean([dice_list[i] for i in valid_idx])

        f.write(f"\nIoU moyen       : {mean_iou:.3f}\n")
        f.write(f"Precision moyenne: {mean_precision:.3f}\n")
        f.write(f"Recall moyen     : {mean_recall:.3f}\n")
        f.write(f"Dice moyen       : {mean_dice:.3f}\n")

    print(f"✅ Rapport complet enregistré dans : {os.path.abspath(log_path)}")

if __name__ == "__main__":
    raster_ref_path = "OtherData/1845_5175/Bellec_1845_5175.tif"      # 1–5
    raster_compare_path = "results/1845_5175/FusionLidarFlair2018_1845_5175.tif" # 0–3

    cm, cm_percent, class_names = compute_confusion_percent_with_empty(raster_ref_path, raster_compare_path)
    plot_confusion_matrix_percent(cm_percent, class_names)
    compute_metrics_from_confusion_matrix(cm, class_names, log_path="metrics_log.txt")
