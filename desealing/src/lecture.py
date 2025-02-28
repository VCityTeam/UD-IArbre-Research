import numpy as np
import rasterio
from rasterio.merge import merge
import glob
import geopandas as gpd
import pandas as pd

# Fusion de tuiles de cartes MNT (.Asc)
def read_and_merge_tiles(folder_path, nodata_value=-9999):
    asc_files = glob.glob(f"{folder_path}/*.asc")
    datasets = [rasterio.open(file) for file in asc_files]

    # Fusion des tuiles
    merged_data, merged_transform = merge(datasets, nodata=nodata_value, method='first')

    for ds in datasets:
        ds.close()

    # Remplace les valeurs nodata par np.nan
    merged_data = np.where(merged_data == nodata_value, np.nan, merged_data)

    return merged_data[0], merged_transform

# Fusion de tuiles de cartes MNT en GDF
def read_merge_tiles_to_gdf(folder_path, nodata_value=-9999):
    merged_data, merged_transform = read_and_merge_tiles(folder_path, nodata_value)
    
    # Créer un DataFrame à partir des données fusionnées
    rows, cols = merged_data.shape
    xs, ys = np.meshgrid(np.arange(cols), np.arange(rows))
    xs, ys = rasterio.transform.xy(merged_transform, ys, xs)
    xs = np.array(xs).flatten()
    ys = np.array(ys).flatten()
    values = merged_data.flatten()

    # Créer un GeoDataFrame
    df = pd.DataFrame({'x': xs, 'y': ys, 'value': values})
    gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.x, df.y))
    
    return gdf

# Lecture GeoDataFrame
def load_data(filepath, layer):
    gdf = gpd.read_file(filepath, layer=layer)
    return gdf

