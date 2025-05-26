import numpy as np
import rasterio
from rasterio.merge import merge
import glob


def load_multiple_tiles_and_merge(folder_path, nodata_value=-9999):
    """
    Function to load multiple raster tiles and merge them into a single one.

    parameters :
    - folder_path : String, path to the folder containing the raster files (in .tif format).
    - nodata_value : int or float, value representing no data in the raster files (default is -9999).

    Returns :
    - merged_data : numpy.ndarray, 2D array containing the merged raster data.
    - merged_transform : affine.Affine, affine transformation associated with the merged raster data.
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
    Function to load a single raster tile and extract its metadata.

    parameters :
    - tile_path : String, path to the raster tile file (in .tif format).

    Retourne :
    - tile_data : numpy.ndarray, 2D array containing the raster data of the tile.
    - bounds : tuple, bounds of the tile in the format (minx, miny, maxx, maxy).
    - crs : rasterio.crs.CRS, coordinate reference system of the tile.
    - tile_transform : affine.Affine, affine transformation associated with the raster data.
    """
    with rasterio.open(tile_path) as src:
        bounds = src.bounds
        crs = src.crs
        tile_data = src.read(1)
        tile_data = np.where(tile_data == src.nodata, 200, tile_data)
        tile_transform = src.transform
    
    return tile_data, bounds, crs, tile_transform

