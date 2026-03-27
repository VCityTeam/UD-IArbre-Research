import numpy as np
import rasterio

def pad_to_match(arr, target_shape):
    """Ajuste un raster numpy (2D) pour qu'il ait la même taille que target_shape en ajoutant des NaN."""
    diff_rows = target_shape[0] - arr.shape[0]
    diff_cols = target_shape[1] - arr.shape[1]

    if diff_rows < 0 or diff_cols < 0:
        raise ValueError("Le raster à ajuster est plus grand que la forme cible.")

    if diff_rows == 0 and diff_cols == 0:
        return arr  # déjà bon format

    # Ajout de NaN sur les bords bas et droite
    padded = np.pad(
        arr,
        pad_width=((0, diff_rows), (0, diff_cols)),
        mode='constant',
        constant_values=np.nan
    )
    return padded

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


def fusion_classes(raster1_path, raster2_path, output_path):
    """
    Fusionne deux rasters de classes avec priorité au premier.
    Reclassification selon la correspondance :
        1,2 -> 1
        3   -> 2
        4   -> 3
        0   -> 0
    Le premier raster est prioritaire.
    Si le second est plus petit, il est étendu avec des NaN.
    """
    with rasterio.open(raster1_path) as src1, rasterio.open(raster2_path) as src2:
        r1 = src1.read(1).astype(float)
        r2 = src2.read(1).astype(float)
        profile = src1.profile

    # Ajustement de la taille si nécessaire
    if r1.shape != r2.shape:
        print(f"⚠️ Dimensions différentes : {r1.shape} vs {r2.shape}.")
        r2 = pad_to_match(r2, r1.shape)
        print(f"✅ Raster 2 ajusté à la taille {r2.shape}")

    # Reclassification du premier raster
    r1_reclass = np.full_like(r1, np.nan)
    r1_reclass[np.isin(r1, [1, 2])] = 1
    r1_reclass[r1 == 3] = 2
    r1_reclass[r1 == 4] = 3

    # Reclassification du second raster
    r2_reclass = np.full_like(r2, np.nan)
    r2_reclass[r2 == 0] = 0
    r2_reclass[r2 == 1] = 1
    r2_reclass[r2 == 2] = 2
    r2_reclass[r2 == 3] = 3

    # Fusion avec priorité au raster 1
    fused = np.where(~np.isnan(r1_reclass), r1_reclass, r2_reclass)

    
    # 🔸 Forcer la taille finale à 5000x5000
    fused_fixed = pad_or_crop_to_size(fused, (5000, 5000))

    # Profil de sortie
    profile.update(dtype=rasterio.float32, count=1, nodata=np.nan,
                   height=5000, width=5000)

    # Écriture du résultat
    with rasterio.open(output_path, "w", **profile) as dst:
        dst.write(fused.astype(np.float32), 1)

    print(f"✅ Fusion terminée et enregistrée dans : {output_path}")

if __name__ == "__main__":
    fusion_classes("results/1845_5175/vegeLidarComplete.tif", "OtherData/1845_5175/RGB_2018_1845_5175_vege1m.tif", "results/1845_5175/FusionLidarFlair2018_1845_5175.tif")