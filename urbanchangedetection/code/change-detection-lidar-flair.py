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

HEIGHT_THRESHOLD = 3.0  # Seuil de hauteur pour considérer un pixel comme "bâtiment" (en mètres)
GROUND_CLASSES = [1,2]  # Classes LiDAR considérées comme "sol" ou "non attribué"

def parse_args() -> argparse.Namespace:
    # Arguments en ligne de commande pour lancer le script
    parser = argparse.ArgumentParser(
        description="Combine vegetation classes derived from LiDAR and Flair rasters."
    )
    parser.add_argument("--class-map1", type=Path, default=Path("./workdir/runs/test_batiments/lidar/class/lidar-part-dieu-2018_class.tif"))   # Raster des classes LiDAR
    parser.add_argument("--height-map1", type=Path, default=Path("./workdir/runs/test_batiments/lidar/heights/lidar-part-dieu-2018_height.tif"))  # Raster de hauteur (MNS - MNT)
    parser.add_argument("--build-mask1", type=Path, default=Path("./workdir/runs/test_batiments/flair/inferences/argmax_2018.tif"))    # Masque FLAIR 
    parser.add_argument("--class-map2", type=Path, default=Path("./workdir/runs/test_batiments/lidar/class/lidar-part-dieu-2023_class.tif"))   # Raster des classes LiDAR
    parser.add_argument("--height-map2", type=Path, default=Path("./workdir/runs/test_batiments/lidar/heights/lidar-part-dieu-2023_height.tif"))  # Raster de hauteur (MNS - MNT)
    parser.add_argument("--build-mask2", type=Path, default=Path("./workdir/runs/test_batiments/flair/inferences/argmax_2023.tif"))    # Masque FLAIR 
    parser.add_argument("--out-dir", type=Path, default=Path("./workdir/runs/test_batiments/lidar-flair/change"))     # Répertoire de sortie
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
) -> np.ndarray:
    """Rééchantillonne un raster source sur la grille (transform/shape/crs) d'un raster de référence."""
    with rasterio.open(src_path) as src:
        dst_array = np.full((ref_height, ref_width), np.nan, dtype=np.float32)
        reproject(
            source=rasterio.band(src, 1),
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


def create_building_map(
    class_lidar_map1: np.ndarray,
    class_lidar_map2: np.ndarray,
    height_lidar_map1: np.ndarray,
    height_lidar_map2: np.ndarray,
    build_mask1: np.ndarray,
    build_mask2: np.ndarray,
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
    keep_flair1 = build_mask1 == 0 # flair_config["building_class"]
    keep_flair2 = build_mask2 == 0 # flair_config["building_class"]
    keep_lidar1 = (class_lidar_map1 == 6) & (height_lidar_map1 > HEIGHT_THRESHOLD)# lidar_config["building_class"]
    keep_lidar2 = (class_lidar_map2 == 6) & (height_lidar_map2 > HEIGHT_THRESHOLD) # lidar_config["building
    out = np.full(build_mask1.shape, np.nan, dtype=np.float32)

    is_building1 = keep_flair1  & keep_lidar1 # & (height_lidar_map1 >= HEIGHT_THRESHOLD)
    is_building2 = keep_flair2  & keep_lidar2 # & (height_lidar_map2 >= HEIGHT_THRESHOLD)
    out[is_building1 & ~is_building2] = 1 # Bâtiment présent dans 1 mais pas dans 2 (démoli)
    out[~is_building1 & is_building2] = 2 # Bâtiment présent dans 2 mais pas dans 1 (nouveau)
    out[is_building1 & is_building2] = 4 # Bâtiment présent dans les deux (inchangé)
    out[(is_building1 & is_building2) & (abs(height_lidar_map1 - height_lidar_map2) > 2.0)] = 3 # Bâtiment présent dans les deux (inchangé)

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

    # Charge les 4 rasters d'entrée
    # class_map1, _ = load_tif(args.class_map1)    # Classes LiDAR (entier par pixel)
    # height_map1, _ = load_tif(args.height_map1)         # Hauteur relative (MNS - MNT)
    # build_mask1, _ = load_tif(args.build_mask1)             # Masque FLAIR build
    # class_map2, profile = load_tif(args.class_map2)    # Classes LiDAR (entier par pixel)
    # height_map2, _ = load_tif(args.height_map2)         # Hauteur relative (MNS - MNT)
    # build_mask2, _ = load_tif(args.build_mask2)             # Masque FLAIR build
    
    # with rasterio.open(args.build_mask2) as ref:
    #     ref_transform = ref.transform
    #     ref_width = ref.width
    #     ref_height = ref.height
    #     ref_crs = ref.crs
    #     profile = ref.profile.copy()

    # Grille de référence = FLAIR 2023 (la plus fine, 0.5 m/px)
    ref_transform, ref_width, ref_height, ref_crs, profile = load_reference_grid(args.build_mask2)


    # Classes LiDAR -> nearest neighbor obligatoire (valeurs catégorielles, pas d'interpolation !)
    class_map1 = resample_to_reference(args.class_map1, ref_transform, ref_width, ref_height, ref_crs, Resampling.nearest)
    class_map2 = resample_to_reference(args.class_map2, ref_transform, ref_width, ref_height, ref_crs, Resampling.nearest)

    # Hauteurs -> continues, bilinéaire OK
    height_map1 = resample_to_reference(args.height_map1, ref_transform, ref_width, ref_height, ref_crs, Resampling.bilinear)
    height_map2 = resample_to_reference(args.height_map2, ref_transform, ref_width, ref_height, ref_crs, Resampling.bilinear)

    # Masques FLAIR -> nearest aussi (binaire/catégoriel)
    build_mask1 = resample_to_reference(args.build_mask1, ref_transform, ref_width, ref_height, ref_crs, Resampling.nearest)
    build_mask2 = resample_to_reference(args.build_mask2, ref_transform, ref_width, ref_height, ref_crs, Resampling.nearest)
    

    # Produit une carte des bâtiments à partir des 4 rasters d'entrée
    building_map = create_building_map(
        class_map1,
        class_map2,
        height_map1,
        height_map2,
        build_mask1,
        build_mask2,
        config=matrix_config,
    )
    # second_map n'est pas utilisé dans create_building_map pour l'instant, mais peut être utilisé pour des logiques plus complexes
    save_tif(args.out_dir / "change_map_lidar_flair.tif", building_map, profile)


    print(f"Generated outputs in: {args.out_dir}")

if __name__ == "__main__":
    main()
 