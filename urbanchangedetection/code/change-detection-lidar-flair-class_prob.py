from __future__ import annotations

import argparse
from pathlib import Path

from rasterio.windows import from_bounds
from rasterio.warp import reproject, Resampling

import numpy as np
import rasterio
import yaml

from workflow_utils import align_array_to_shape, max_shape

# Chemin par défaut vers le fichier de config YAML (seuils, classes LiDAR, etc.)
DEFAULT_MATRIX_CONFIG = Path("configs/baseline/configs.yml")

HEIGHT_THRESHOLD = 0.0  # Seuil de hauteur pour considérer un pixel comme "valide" (en mètres)
HEIGHT_CHANGE_THRESHOLD = 2.0  # Seuil de changement de hauteur pour considérer un pixel valide comme "modifié" (en mètres)
GROUND_CLASSES = [1, 2]  # Classes LiDAR considérées comme "sol" ou "non attribué"

VALID_BAND = 1  # Bande rasterio (1-indexed) correspondant à la classe "bâtiment" (index 0 côté FLAIR-HUB)
PROB_THRESHOLD = 20 / 255  # Seuil de probabilité pour binariser la bande valide


def parse_args() -> argparse.Namespace:
    # Arguments en ligne de commande pour lancer le script
    parser = argparse.ArgumentParser(
        description="Combine vegetation classes derived from LiDAR and Flair rasters."
    )
    parser.add_argument("--class-map1", type=Path, required=True) # default=Path("./workdir/runs/test_batiments/lidar/Confluences/class/lidar-2018_class.tif"))   # Raster des classes LiDAR
    parser.add_argument("--height-map1", type=Path, required=True) # default=Path("./workdir/runs/test_batiments/lidar/Confluences/heights/lidar-2018_height.tif"))  # Raster de hauteur (MNS - MNT)
    parser.add_argument("--class-prob1", type=Path, required=True) # default=Path("./workdir/runs/test_batiments/flair/inferences/Confluences/ir-class_prob-conf-2018-20cm.tif"))    # Raster de probas FLAIR (multi-bandes)
    parser.add_argument("--class-map2", type=Path, required=True) # default=Path("./workdir/runs/test_batiments/lidar/Confluences/class/lidar-2023_class.tif"))   # Raster des classes LiDAR
    parser.add_argument("--height-map2", type=Path, required=True) # default=Path("./workdir/runs/test_batiments/lidar/Confluences/heights/lidar-2023_height.tif"))  # Raster de hauteur (MNS - MNT)
    parser.add_argument("--class-prob2", type=Path, required=True) # default=Path("./workdir/runs/test_batiments/flair/inferences/Confluences/ir-class_prob-conf-2023-20cm.tif"))    # Raster de probas FLAIR (multi-bandes)
    parser.add_argument("--out-dir", type=Path, default=Path("./workdir/runs/test_batiments/lidar-flair/change"))     # Répertoire de sortie
    parser.add_argument("--target-class", type=str, required=True, choices=["building", "vegetation"]) 
    parser.add_argument("--matrix-config", type=Path, default=DEFAULT_MATRIX_CONFIG)  # Chemin config YAML
    parser.add_argument("--prob-threshold", type=float, default=PROB_THRESHOLD)  # Seuil de proba pour binariser
    return parser.parse_args()


def pad_to_match(arr: np.ndarray, target_shape: tuple[int, int]) -> np.ndarray:
    # Aligne un raster sur une forme cible (padding avec NaN ou 255)
    # Nécessaire car LiDAR et orthophotos peuvent avoir des dimensions légèrement différentes
    return align_array_to_shape(
        arr,
        target_shape,
        fill_value=np.nan if np.issubdtype(arr.dtype, np.floating) else 255,
        allow_crop=False,
    )


def load_tif(path: Path) -> tuple[np.ndarray, dict]:
    # Lit un fichier .tif, retourne le tableau numpy + le profil rasterio (métadonnées)
    with rasterio.open(path) as dataset:
        return dataset.read(1), dataset.profile.copy()

def save_tif(path: Path, arr: np.ndarray, profile: dict) -> None:
    # Sauvegarde un tableau numpy en .tif float32 avec compression LZW
    output = arr.astype(np.float32)
    updated_profile = profile.copy()
    updated_profile.update(dtype="float32", count=1, nodata=np.nan, compress="lzw")
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **updated_profile) as dst:
        dst.write(output, 1)

"""def save_tif(path: Path, arr: np.ndarray, profile: dict) -> None:
    # On prépare un raster RGBA (4 bandes) en entiers 8-bits (0-255)
    # C'est le format standard pour les images colorées directement lisibles
    updated_profile = profile.copy()
    updated_profile.update(
        dtype="uint8", 
        count=4, 
        nodata=0, # Le 0 servira de transparence pour les zones sans données (ex-NaN)
        compress="lzw"
    )
    
    # Création des 4 bandes (Initialisées à 0 pour la transparence)
    height, width = arr.shape
    r = np.zeros((height, width), dtype=np.uint8)
    g = np.zeros((height, width), dtype=np.uint8)
    b = np.zeros((height, width), dtype=np.uint8)
    a = np.zeros((height, width), dtype=np.uint8) # Bande Alpha (Transparence)

    # Masques basés sur vos valeurs thématiques
    # On remplit les couches RVB + on active l'alpha (255 = opaque) là où il y a de la donnée
    
    # Valeur 1 : Rouge pur (255, 0, 0)
    m1 = (arr == 1)
    r[m1], g[m1], b[m1], a[m1] = 255, 0, 0, 255
    
    # Valeur 2 : Vert pur (0, 255, 0)
    m2 = (arr == 2)
    r[m2], g[m2], b[m2], a[m2] = 0, 255, 0, 255
    
    # Valeur 3 : Jaune pur (255, 255, 0)
    m3 = (arr == 3)
    r[m3], g[m3], b[m3], a[m3] = 255, 255, 0, 255
    
    # Valeur 4 : Bleu pur (0, 0, 255)
    m4 = (arr == 4)
    r[m4], g[m4], b[m4], a[m4] = 0, 0, 255, 255

    path.parent.mkdir(parents=True, exist_ok=True)
    
    with rasterio.open(path, "w", **updated_profile) as dst:
        # Écriture des 4 bandes
        dst.write(r, 1)
        dst.write(g, 2)
        dst.write(b, 3)
        dst.write(a, 4)
        
        # --- Astuce QGIS : Injection du mode de fusion "Multiply" ---
        # GDAL permet de stocker des métadonnées sous forme de chaînes de caractères.
        # QGIS lit ces métadonnées et applique le mode de fusion "Multiply" automatiquement.
        dst.update_tags(
            GDAL_METADATA="<GDALMetadata><Item name=\"BlendMode\">Multiply</Item></GDALMetadata>"
        )
"""

def load_matrix_config(config_path: Path) -> dict:
    # Charge le fichier YAML de configuration
    with config_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError(f"Invalid matrix config: {config_path}")
    return config


def load_reference_grid(path: Path):
    """Ouvre le raster de référence et retourne sa grille (transform, taille, crs, profil)."""
    with rasterio.open(path) as ref:
        return ref.transform, ref.width, ref.height, ref.crs, ref.profile.copy()


def resample_to_reference(
    src_path: Path,
    ref_transform,
    ref_width: int,
    ref_height: int,
    ref_crs,
    resampling: Resampling,
    band: int = 1,
) -> np.ndarray:
    """Rééchantillonne une bande d'un raster source sur la grille (transform/shape/crs) d'un raster de référence.

    `band` est l'index rasterio 1-indexed. Pour un raster class_prob multi-bandes,
    band=1 correspond à la bande valide côté FLAIR-HUB (classe valide).
    """
    with rasterio.open(src_path) as src:
        dst_array = np.full((ref_height, ref_width), np.nan, dtype=np.float32)
        reproject(
            source=rasterio.band(src, band),
            destination=dst_array,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=ref_transform,
            dst_crs=ref_crs,
            src_nodata=src.nodata,   # <-- ignore le nodata source, quel qu'il soit
            dst_nodata=np.nan,       # <-- remplit le nodata en NaN dans la destination
            resampling=resampling,
        )
    return dst_array

def resample_and_sum_bands(
    src_path: Path,
    ref_transform, ref_width: int, ref_height: int, ref_crs,
    bands: list[int],  # 1-indexed rasterio
) -> np.ndarray:
    """Reprojette et additionne plusieurs bandes de probabilité (ex: sous-classes de végétation)."""
    acc = np.zeros((ref_height, ref_width), dtype=np.float32)
    valid_anywhere = np.zeros((ref_height, ref_width), dtype=bool)

    for band in bands:
        band_arr = resample_to_reference(
            src_path, ref_transform, ref_width, ref_height, ref_crs,
            Resampling.bilinear, band=band,
        )
        is_valid = ~np.isnan(band_arr)
        acc[is_valid] += band_arr[is_valid]
        valid_anywhere |= is_valid

    acc[~valid_anywhere] = np.nan
    return acc


def prob_to_valid_mask(prob_band: np.ndarray, threshold: float) -> np.ndarray:
    """Binarise une bande de probabilité (0-255 ou 0-1) en masque valide 0/1.

    Les pixels NaN (hors emprise / nodata) restent à 0 : ils ne pourront de toute
    façon jamais être marqués "valide" côté LiDAR non plus.
    """
    # Si le raster source est encodé en 0-255 (uint8 typique FLAIR-HUB), on ramène le seuil
    # déjà exprimé en fraction (20/255) donc on normalise la proba sur la même échelle si besoin.
    prob = prob_band
    if np.nanmax(prob) > 1.0:  # raster encodé en 0-255
        prob = prob / 255.0
    mask = np.where(np.isnan(prob), 0.0, (prob > threshold).astype(np.float32))
    return mask


def create_change_map(
    class_lidar_map1, class_lidar_map2,
    height_lidar_map1, height_lidar_map2,
    valid_mask1, valid_mask2,
    lidar_valid_classes: list[int],
    height_threshold: float,
    height_change_threshold: float
) -> np.ndarray:
    keep_flair1 = valid_mask1 == 1
    keep_flair2 = valid_mask2 == 1
    keep_lidar1 = np.isin(class_lidar_map1, lidar_valid_classes) & (height_lidar_map1 > height_threshold)
    keep_lidar2 = np.isin(class_lidar_map2, lidar_valid_classes) & (height_lidar_map2 > height_threshold)
    
    out = np.full(valid_mask1.shape, np.nan, dtype=np.float32)

    is_valid1 = keep_flair1 & keep_lidar1
    is_valid2 = keep_flair2 & keep_lidar2
    out[is_valid1 & ~is_valid2] = 1  # Valide dans 1 mais pas dans 2 (démoli)
    out[~is_valid1 & is_valid2] = 2  # Valide dans 2 mais pas dans 1 (nouveau)
    out[is_valid1 & is_valid2] = 4  # Valide dans les deux (inchangé)
    out[(is_valid1 & is_valid2) & (abs(height_lidar_map1 - height_lidar_map2) > height_change_threshold)] = 3  # Valide dans les deux (changement de hauteur)

    return out


def validate_shapes(arrays: list[np.ndarray]) -> tuple[int, int]:
    return max_shape(arrays)


def main():
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    # Charge la config YAML
    config_path = args.matrix_config
    if not config_path.is_absolute():
        config_path = Path(__file__).resolve().parent / config_path
    matrix_config = load_matrix_config(config_path)
    class_config = matrix_config["classes"][args.target_class]
    flair_bands = [b + 1 for b in class_config["flair_bands"]]  # -> 1-indexed
    lidar_valid_classes = class_config["lidar_valid_classes"]
    height_threshold = class_config.get("height_threshold", HEIGHT_THRESHOLD)
    height_change_threshold = class_config.get("height_change_threshold", HEIGHT_CHANGE_THRESHOLD)


    # Grille de référence = FLAIR 2023 (la plus fine, 0.5 m/px)
    ref_transform, ref_width, ref_height, ref_crs, profile = load_reference_grid(args.class_prob2)

    # Classes LiDAR -> nearest neighbor obligatoire (valeurs catégorielles, pas d'interpolation !)
    class_map1 = resample_to_reference(args.class_map1, ref_transform, ref_width, ref_height, ref_crs, Resampling.nearest)
    class_map2 = resample_to_reference(args.class_map2, ref_transform, ref_width, ref_height, ref_crs, Resampling.nearest)

    # Hauteurs -> continues, bilinéaire OK
    height_map1 = resample_to_reference(args.height_map1, ref_transform, ref_width, ref_height, ref_crs, Resampling.bilinear)
    height_map2 = resample_to_reference(args.height_map2, ref_transform, ref_width, ref_height, ref_crs, Resampling.bilinear)

    # Bande valide -> continue, bilinéaire, seuillage APRÈS reprojection
    prob_band1 = resample_and_sum_bands(args.class_prob1, ref_transform, ref_width, ref_height, ref_crs, flair_bands)
    prob_band2 = resample_and_sum_bands(args.class_prob2, ref_transform, ref_width, ref_height, ref_crs, flair_bands)


    valid_mask1 = prob_to_valid_mask(prob_band1, args.prob_threshold)
    valid_mask2 = prob_to_valid_mask(prob_band2, args.prob_threshold)

    # --- DEBUG ---
    keep_lidar1_dbg = np.isin(class_map1, lidar_valid_classes) & (height_map1 > height_threshold)
    keep_lidar2_dbg = np.isin(class_map2, lidar_valid_classes) & (height_map2 > height_threshold)
    print(f"[DEBUG] target_class={args.target_class}")
    print(f"[DEBUG] flair_bands(1-indexed)={flair_bands} lidar_valid_classes={lidar_valid_classes}")
    print(f"[DEBUG] valid_mask1==1 : {int((valid_mask1 == 1).sum())} px / {valid_mask1.size}")
    print(f"[DEBUG] valid_mask2==1 : {int((valid_mask2 == 1).sum())} px / {valid_mask2.size}")
    print(f"[DEBUG] keep_lidar1    : {int(keep_lidar1_dbg.sum())} px")
    print(f"[DEBUG] keep_lidar2    : {int(keep_lidar2_dbg.sum())} px")
    print(f"[DEBUG] is_valid1 (flair&lidar) : {int((keep_lidar1_dbg & (valid_mask1==1)).sum())} px")
    print(f"[DEBUG] is_valid2 (flair&lidar) : {int((keep_lidar2_dbg & (valid_mask2==1)).sum())} px")
    print(f"[DEBUG] class_map1 unique: {np.unique(class_map1[~np.isnan(class_map1)], return_counts=True)}")
    # --- FIN DEBUG ---

    # Produit une carte des valides à partir des rasters d'entrée
    change_map = create_change_map(
        class_map1, class_map2,
        height_map1, height_map2,
        valid_mask1, valid_mask2,
        lidar_valid_classes,
        height_threshold,
        height_change_threshold,
    )
    save_tif(args.out_dir / "change_map_lidar_flair_prob.tif", change_map, profile)

    print(f"Generated outputs in: {args.out_dir}")


if __name__ == "__main__":
    main()