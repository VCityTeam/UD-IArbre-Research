from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio
import yaml

from workflow_utils import align_array_to_shape, max_shape

# Chemin par défaut vers le fichier de config YAML (seuils, classes LiDAR, etc.)
DEFAULT_MATRIX_CONFIG = Path("configs/baseline/configs.yml")

HEIGHT_THRESHOLD = 1.0  # Seuil de hauteur pour considérer un pixel comme "bâtiment" (en mètres)
GROUND_CLASSES = [1,2]  # Classes LiDAR considérées comme "sol" ou "non attribué"

def parse_args() -> argparse.Namespace:
    # Arguments en ligne de commande pour lancer le script
    parser = argparse.ArgumentParser(
        description="Combine vegetation classes derived from LiDAR and Flair rasters."
    )
    parser.add_argument("--class-map", type=Path, required=True)   # Raster des classes LiDAR
    parser.add_argument("--height-map", type=Path, required=True)  # Raster de hauteur (MNS - MNT)
    # A changer : peut-être avoir un build_mask à la place ?
    parser.add_argument("--build-mask", type=Path, required=True)    # Masque FLAIR : pixels classés végétation
    # parser.add_argument("--second-map", type=Path, required=True)  # Second raster FLAIR (autre date je pense)
    parser.add_argument("--out-dir", type=Path, required=True)     # Répertoire de sortie
    parser.add_argument("--matrix-config", type=Path, default=DEFAULT_MATRIX_CONFIG)  # Chemin config YAML
    # Options booléennes pour activer/désactiver des comportements de fusion
    parser.add_argument("--modify-flair", action=argparse.BooleanOptionalAction, default=None)          # Applique la hauteur LiDAR aux pixels FLAIR ?
    parser.add_argument("--keep-class-lidar1", action=argparse.BooleanOptionalAction, default=None)     # Inclut la classe LiDAR "non attribuée"
    # A changer : peut devenir "--flair-only-buildings" : n'utilise FLAIR que pour les bâtiments où LiDAR est invalide
    parser.add_argument(
        "--flair-only-buildings", 
        action=argparse.BooleanOptionalAction, 
        default=None,
        help="Only use Flair where LiDAR is invalid and Flair predicts class 0."
        ) # N'utilise FLAIR que pour la classe 0
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


def resolve_fusion_option(config: dict, args: argparse.Namespace) -> bool:
    # Résout les 3 options de fusion : priorité CLI > config YAML > défaut False
    # Retourne (modify_flair, keep_class_lidar1, flair_only_buildings) 
    # peut devenir (modify_flair, keep_class_lidar1, flair_only_buildings)
    # ou juste flair_only_buildings
    fusion_config = config.get("workflow", {}).get("fusion", {})
    flair_only_buildings = (
        args.flair_only_buildings
        if args.flair_only_buildings is not None
        else bool(fusion_config.get("flair_only_buildings", False))
    )
    return flair_only_buildings


def create_building_map(
    class_lidar_map: np.ndarray,
    height_lidar_map: np.ndarray,
    build_mask: np.ndarray,
    # flair_build: np.ndarray,
    config: dict,
) -> tuple[np.ndarray, np.ndarray]:
    
    # Logique bâtiment :
    #   1. Partir des pixels où FLAIR dit "bâtiment"
    #   2. Invalider ceux où le LiDAR dit "sol" (​🚧​ hauteur < 1 mètre ou classe LiDAR = sol)
    #   3. Retourner un masque binaire bâtiment valide / invalide

    flair_config = config["flair"]
    lidar_config = config["lidar"]

    # Récupérer les pixels où FLAIR dit "bâtiment" ✅​
    keep_flair = build_mask == flair_config["building_class"]
    out_flair = np.full(build_mask.shape, np.nan, dtype=np.float32)

    # Invalider les pixels où le LiDAR dit "sol" (classe 0) ou "non attribué" (classe 1) ✅​
    # <=> ancien classify_heights
    # ​🚧​ A MODIFIER : Pour l'instant, la hauteur seuil d'un building est à 1 mètre
    out_flair[keep_flair & (height_lidar_map >= HEIGHT_THRESHOLD) & ~np.isin(class_lidar_map, GROUND_CLASSES)] = 1

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

    # Résout les options de fusion (CLI > YAML > défaut)
    # Ca on pourra enlever, ou remplacer par flair_only_buildings
    # flair_only_buildings = resolve_fusion_option(matrix_config, args)

    # Charge les 4 rasters d'entrée
    class_map, profile = load_tif(args.class_map)    # Classes LiDAR (entier par pixel)
    height_map, _ = load_tif(args.height_map)         # Hauteur relative (MNS - MNT)
    build_mask, _ = load_tif(args.build_mask)             # Masque FLAIR build
    
    # second_map, _ = load_tif(args.second_map)         # Second raster FLAIR

    # Aligne tous les rasters sur la même forme (le plus grand)
    target_shape = validate_shapes([class_map, height_map, build_mask]) # second_map
    class_map   = pad_to_match(class_map,   target_shape)
    height_map  = pad_to_match(height_map,  target_shape)
    build_mask    = pad_to_match(build_mask,    target_shape)
    # second_map  = pad_to_match(second_map,  target_shape)

    # Produit une carte des bâtiments à partir des 4 rasters d'entrée
    building_map = create_building_map(
        class_map,
        height_map,
        build_mask,
        config=matrix_config,
    )
    # second_map n'est pas utilisé dans create_building_map pour l'instant, mais peut être utilisé pour des logiques plus complexes
    save_tif(args.out_dir / "building_map.tif", building_map, profile)


    print(f"Generated outputs in: {args.out_dir}")

if __name__ == "__main__":
    main()
