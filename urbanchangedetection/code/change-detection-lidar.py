from __future__ import annotations

import argparse
from pathlib import Path

from rasterio.windows import from_bounds

import numpy as np
import rasterio
import yaml

from workflow_utils import align_array_to_shape, max_shape

# Chemin par défaut vers le fichier de config YAML (seuils, classes LiDAR, etc.)
DEFAULT_MATRIX_CONFIG = Path("configs/baseline/configs.yml")

HEIGHT_THRESHOLD = 3  # Seuil de hauteur pour considérer un pixel comme "bâtiment" (en mètres)
GROUND_CLASSES = [1,2]  # Classes LiDAR considérées comme "sol" ou "non attribué"

def parse_args() -> argparse.Namespace:
    # Arguments en ligne de commande pour lancer le script
    parser = argparse.ArgumentParser(
        description="Combine vegetation classes derived from LiDAR and Flair rasters."
    )
    parser.add_argument("--class-map1", type=Path, required=True)   # Raster des classes LiDAR
    parser.add_argument("--class-map2", type=Path, required=True)   # Raster des classes LiDAR
    parser.add_argument("--height-map1", type=Path, required=True)  # Raster de hauteur (MNS - MNT)
    parser.add_argument("--height-map2", type=Path, required=True)  # Raster de hauteur (MNS - MNT)
    parser.add_argument("--out-dir", type=Path, default=Path("./workdir/runs/test_batiments/lidar/change"))     # Répertoire de sortie
    parser.add_argument("--matrix-config", type=Path, default=DEFAULT_MATRIX_CONFIG)  # Chemin config YAML
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


def load_matrix_config(config_path: Path) -> dict:
    # Charge le fichier YAML de configuration
    with config_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError(f"Invalid matrix config: {config_path}")
    return config


def create_change_map(
    class_lidar_map1: np.ndarray,
    height_lidar_map1: np.ndarray,
    class_lidar_map2: np.ndarray,
    height_lidar_map2: np.ndarray,
    config: dict,
) -> tuple[np.ndarray, np.ndarray]:
    
    # Pour l'instant, on n'utilise que les classes liDAR pour détecter les changements de bâtiments

    lidar_config = config["lidar"]

    # Récupérer les pixels où FLAIR dit "bâtiment" ✅​
    builds1 = (class_lidar_map1 == lidar_config["building_class"]) & (height_lidar_map1 > HEIGHT_THRESHOLD)
    builds2 = (class_lidar_map2 == lidar_config["building_class"]) & (height_lidar_map2 > HEIGHT_THRESHOLD)
    out_flair = np.full(class_lidar_map1.shape, np.nan, dtype=np.float32)

    # Logique de détection de changements entre deux masques LiDAR (bâtiments)
    # Pareil que pour FLAIR
    out_flair[builds1 & ~builds2] = 1 # Bâtiment présent dans LiDAR1 mais pas dans LiDAR2 (démoli)
    out_flair[~builds1 & builds2] = 2 # Bâtiment présent dans LiDAR2 mais pas dans LiDAR1 (nouveau)
    out_flair[builds1 & builds2] = 3 # Bâtiment présent dans les deux LiDAR (inchangé)

    return out_flair


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

    # Charge les 4 rasters d'entrée
    class_map1, _ = load_tif(args.class_map1)    # Classes LiDAR (entier par pixel)
    height_map1, _ = load_tif(args.height_map1)         # Hauteur relative (MNS - MNT)
    class_map2, profile = load_tif(args.class_map2)    # Classes LiDAR (entier par pixel)
    height_map2, _ = load_tif(args.height_map2)         # Hauteur

    with rasterio.open(args.class_map1) as src2018:
        class_map1 = src2018.read(1)

    with rasterio.open(args.class_map2) as src2023:
        class_map2 = src2023.read(1)
        bounds_2023 = src2023.bounds
    
    with rasterio.open(args.class_map1) as src2018:

        window = from_bounds(
            left=bounds_2023.left,
            bottom=bounds_2023.bottom,
            right=bounds_2023.right,
            top=bounds_2023.top,
            transform=src2018.transform,
        )

        class_map1 = src2018.read(1, window=window)

    with rasterio.open(args.height_map1) as src2018:

        window = from_bounds(
            left=bounds_2023.left,
            bottom=bounds_2023.bottom,
            right=bounds_2023.right,
            top=bounds_2023.top,
            transform=src2018.transform,
        )

        height_map1 = src2018.read(1, window=window)

    # Aligne tous les rasters sur la même forme (le plus grand)
    # target_shape = validate_shapes([class_map1, height_map1,
    #                                 class_map2, height_map2
    #                                ])
    # class_map1   = pad_to_match(class_map1,   target_shape)
    # height_map1  = pad_to_match(height_map1,  target_shape)
    # class_map2   = pad_to_match(class_map2,   target_shape)
    # height_map2  = pad_to_match(height_map2,  target_shape)



    # Produit une carte des bâtiments à partir des 4 rasters d'entrée
    change_map = create_change_map(
        class_map1,
        height_map1,
        class_map2,
        height_map2,
        config=matrix_config,
    )
    save_tif(args.out_dir / "change_map_lidar.tif", change_map, profile)


    print(f"Generated outputs in: {args.out_dir}")

if __name__ == "__main__":
    main()
