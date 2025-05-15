import numpy as np
import rasterio
from rasterio.merge import merge
import glob


def load_multiple_tiles_and_merge(folder_path, nodata_value=-9999):
    """
    Fonction pour charger plusieurs tuiles raster et les fusionner en une seule.

    Paramètres :
    - folder_path : str, chemin du dossier contenant les fichiers raster au format .tif.
    - nodata_value : int ou float, valeur représentant les données manquantes dans les fichiers raster (par défaut : -9999).

    Retourne :
    - merged_data : numpy.ndarray, tableau 2D contenant les données fusionnées.
    - merged_transform : Affine, transformation affine associée aux données fusionnées.
    """
    asc_files = glob.glob(f"{folder_path}/*.tif")
    datasets = [rasterio.open(file) for file in asc_files]

    merged_data, merged_transform = merge(datasets, nodata=nodata_value, method='first')

    for ds in datasets:
        ds.close()

    merged_data = np.where(merged_data == nodata_value, np.nan, merged_data)

    return merged_data[0], merged_transform


def load_single_tile(tile_path):
    """
    Fonction pour charger une seule tuile raster et extraire ses métadonnées.

    Paramètres :
    - tile_path : str, chemin du fichier raster au format .tif .

    Retourne :
    - tile_data : numpy.ndarray, tableau 2D contenant les données raster.
    - bounds : tuple, limites géographiques (xmin, ymin, xmax, ymax) de la tuile.
    - crs : CRS, système de coordonnées de référence de la tuile.
    - tile_transform : Affine, transformation affine associée aux données raster.
    """
    with rasterio.open(tile_path) as src:
        bounds = src.bounds
        crs = src.crs
        tile_data = src.read(1)
        tile_data = np.where(tile_data == src.nodata, 200, tile_data)
        tile_transform = src.transform
    
    return tile_data, bounds, crs, tile_transform

