import numpy as np
import rasterio

def fill_nan_with_neighbors(img, window_size=3):
    """
    Remplit les NaN par la moyenne des voisins valides.
    Les pixels sans voisins valides restent NaN.
    """
    if window_size % 2 == 0:
        raise ValueError("window_size doit être impair")

    filled = img.copy()
    offset = window_size // 2
    rows, cols = img.shape

    for i in range(offset, rows - offset):
        for j in range(offset, cols - offset):
            if np.isnan(filled[i, j]):
                window = filled[i - offset:i + offset + 1, j - offset:j + offset + 1]
                valid = window[~np.isnan(window)]
                if valid.size > 0:
                    filled[i, j] = np.mean(valid)
    return filled

def replace_nan_by_zero(img):
    """Remplace tous les NaN par 0"""
    img[np.isnan(img)] = 0
    return img

def process_raster(tif_path, window_size=3):
    with rasterio.open(tif_path, "r+") as src:
        img = src.read(1)

        # Étape 1 : remplissage par les voisins
        img = fill_nan_with_neighbors(img, window_size)

        # Étape 2 : remplacer les NaN restants par 0
        img = replace_nan_by_zero(img)

        # Écriture dans le fichier original
        src.write(img, 1)
        remaining_nan = np.sum(np.isnan(img))
        if remaining_nan == 0:
            print("Tous les pixels NaN ont été remplacés.")
        else:
            print(f"Attention : il reste {remaining_nan} pixels NaN dans l'image.")

        print("Raster traité et mis à jour directement.")


# Exemple d'utilisation
if __name__ == "__main__":
    tif_path = "LidarRaster/1845_5175_8cm_CC46/2018MaxRaster.tif"
    process_raster(tif_path, window_size=3)
