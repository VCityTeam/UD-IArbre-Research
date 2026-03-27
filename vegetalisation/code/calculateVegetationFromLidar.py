import rasterio
import numpy as np

def load_lidar(lidar_max_height_path, lidar_mini_height_path, lidar_classe_path, second_mask_path):
    """Charge les rasters de hauteur, de classes et un second masque."""
    with rasterio.open(lidar_max_height_path) as src1:
        max_height = src1.read(1)
        in_profile = src1.profile
    with rasterio.open(lidar_mini_height_path) as src2:
        min_height = src2.read(1)
    with rasterio.open(lidar_classe_path) as src3:
        classe = src3.read(1)
    with rasterio.open(second_mask_path) as src4:
        mask_raster = src4.read(1)

        if max_height.shape != mask_raster.shape:
            mask_raster_fixed = pad_or_crop_to_size(mask_raster, max_height.shape)
        else:
            mask_raster_fixed = mask_raster

    return max_height, min_height, classe, mask_raster_fixed, in_profile


def pad_or_crop_to_size(arr, target_shape=(5000, 5000)):
    """
    Ajuste un tableau numpy pour qu'il ait exactement la taille target_shape.
    - Tronque si trop grand
    - Ajoute des NaN si trop petit
    """
    rows, cols = arr.shape
    target_rows, target_cols = target_shape

    # Si le raster est plus grand → on coupe
    arr_cropped = arr[:target_rows, :target_cols]

    # Si le raster est plus petit → on complète
    diff_rows = target_rows - arr_cropped.shape[0]
    diff_cols = target_cols - arr_cropped.shape[1]

    if diff_rows > 0 or diff_cols > 0:
        arr_cropped = np.pad(
            arr_cropped,
            pad_width=((0, max(0, diff_rows)), (0, max(0, diff_cols))),
            mode="constant",
            constant_values=np.nan
        )

    return arr_cropped


def compute_multi_vege(max_height, min_height, classe, mask_raster, in_profile, path_hauteur):
    """
    Calcule la hauteur uniquement pour les pixels :
      - dont la classe du raster 'classe' est 1 (pelouse/sol bas) ET validée par le second masque
      - ou dont la classe du raster 'classe' est 5 (végétation haute)
    """

    # Vérification des dimensions
    if not (max_height.shape == min_height.shape == classe.shape == mask_raster.shape):
        raise ValueError("Tous les rasters doivent avoir la même taille.")

    # Initialisation du raster résultat
    result = np.full_like(max_height, np.nan, dtype=np.float32)

    # Masque 1 : pixels où la classe est 1
    mask_class1 = (classe == 1)
    # Masque 2 : pixels où la classe est 5
    mask_class5 = (classe == 5)

    # Second masque : valide si non 255 et non NaN
    mask_valid = (mask_raster != 255) & (~np.isnan(mask_raster))

    # Application sélective :
    # - classe 5 → toujours prise
    # - classe 1 → prise uniquement si validée par le second masque
    final_mask = mask_class5 | (mask_class1 & mask_valid) | mask_valid

    # Calcul sur les pixels valides
    result[final_mask] = max_height[final_mask] - min_height[final_mask]

    # Écriture du résultat
    profile = in_profile.copy()
    profile.update(
        dtype=rasterio.float32,
        count=1,
        nodata=np.nan
    )

    with rasterio.open(path_hauteur, "w", **profile) as dst:
        dst.write(result, 1)

    print(f"✅ Raster de hauteur écrit : {path_hauteur}")


def classify_from_difference(hauteur_tif, output_tif):
    with rasterio.open(hauteur_tif) as src:
        diff = src.read(1)
        profile = src.profile

        # Initialiser un tableau de classes
        classes = np.full_like(diff, np.nan, dtype=np.float32)

        # Appliquer les règles de classification
        classes[(diff >= 0.5) & (diff < 1.5)] = 1
        classes[(diff >= 1.5) & (diff < 5)] = 2
        classes[(diff >= 5) & (diff < 15)] = 3
        classes[(diff >= 15)] = 4

        # Mettre à jour le profil
        profile.update(dtype=rasterio.float32, count=1, nodata=np.nan)

        with rasterio.open(output_tif, "w", **profile) as dst:
            dst.write(classes, 1)

    print(f"✅ Raster de classes écrit : {output_tif}")


if __name__ == "__main__":
    lidarMaxheightPath = "LidarRaster/1845_5175/Max.tif"
    lidarMiniHeightPath = "LidarRaster/1845_5175/Mini.tif"
    lidarClassPath = "LidarRaster/1845_5175/classes.tif"
    secondMaskPath = "OtherData/1845_5175/2018_1845_5175_vege1m.tif"
    pathHauteur = "results/1845_5175/hauteurLidar.tif"
    vegeCompletePath = "results/1845_5175/vegeLidarComplete.tif"

    max_h, min_h, classe, mask_raster, profile = load_lidar(
        lidarMaxheightPath, lidarMiniHeightPath, lidarClassPath, secondMaskPath
    )

    compute_multi_vege(max_h, min_h, classe, mask_raster, profile, pathHauteur)
    classify_from_difference(pathHauteur, vegeCompletePath)
