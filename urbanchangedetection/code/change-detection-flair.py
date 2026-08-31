from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio
import yaml

from workflow_utils import align_array_to_shape, max_shape

# Chemin par défaut vers le fichier de config YAML (seuils, classes LiDAR, etc.)
DEFAULT_MATRIX_CONFIG = Path("configs/baseline/configs.yml")

def parse_args() -> argparse.Namespace:
    # Arguments en ligne de commande pour lancer le script
    parser = argparse.ArgumentParser(
        description="Detect changes between two Flair masks."
    )
    # A changer : peut-être avoir un build_mask à la place ?
    parser.add_argument("--flair1", type=Path, required=True)    # Inférence FLAIR 1
    parser.add_argument("--flair2", type=Path, required=True)    # Inférence FLAIR 2
    parser.add_argument("--out-dir", type=Path, required=True)     # Répertoire de sortie
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
    flair_mask1: np.ndarray,
    flair_mask2: np.ndarray,
    config: dict,
) -> tuple[np.ndarray, np.ndarray]:
    
    # Logique de détection de changements entre deux masques FLAIR (bâtiments)
    # Retourne une carte de changements (1 = démoli, 2 = nouveau, 3 = inchangé)

    flair_config = config["flair"]

    # Récupérer les pixels où FLAIR dit "bâtiment" ✅​
    builds1 = flair_mask1 == 0 # flair_config["building_class"]
    builds2 = flair_mask2 == 0 # flair_config["building_class"]
    out_flair = np.full(flair_mask1.shape, np.nan, dtype=np.float32)

    out_flair[builds1 & ~builds2] = 1 # Bâtiment présent dans FLAIR1 mais pas dans FLAIR2 (démoli)
    out_flair[~builds1 & builds2] = 2 # Bâtiment présent dans FLAIR2 mais pas dans FLAIR1 (nouveau)
    out_flair[builds1 & builds2] = 3 # Bâtiment présent dans les deux FLAIR (inchangé)

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
    flair1, profile = load_tif(args.flair1)             # Inférence FLAIR 1
    flair2, _ = load_tif(args.flair2)             # Inférence FLAIR 2

    # Aligne tous les rasters sur la même forme (le plus grand)
    target_shape = validate_shapes([flair1, flair2])
    flair1    = pad_to_match(flair1, target_shape)
    flair2    = pad_to_match(flair2, target_shape)

    # Produit une carte des bâtiments à partir des 4 rasters d'entrée
    change_map = create_change_map(
        flair1,
        flair2,
        config=matrix_config,
    )
    # second_map n'est pas utilisé dans create_change_map pour l'instant, mais peut être utilisé pour des logiques plus complexes
    save_tif(args.out_dir / "change_map_flair.tif", change_map, profile)


    print(f"Generated outputs in: {args.out_dir}")

if __name__ == "__main__":
    main()
