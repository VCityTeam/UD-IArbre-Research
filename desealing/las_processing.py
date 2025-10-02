import os
import time
import laspy
import numpy as np
import geopandas as gpd
import humanize

from methods import slope
from utils.utils import download_file, points_to_raster, export_raster
from utils.utils_network import (
    compute_flow_direction,
    compute_flow_accumulation,
    extract_stream_network,
    process_hydrology_jax,
)

# Constants
CASIER_SIZE = 1
SLOPE_RESOLUTION = 2.0
CRS = "EPSG:2154"
GROUND_CLASSIFICATION = 2

# LIDAR data
LIDAR_URL = "https://data.geopf.fr/telechargement/download/LiDARHD-NUALID/NUALHD_1-0__LAZ_LAMB93_OL_2025-02-20/LHD_FXX_0844_6520_PTS_LAMB93_IGN69.copc.laz"
FILENAME = LIDAR_URL.split("/")[-1]
print(f"FILENAME: {FILENAME}")


def download_and_load_lidar():
    if not os.path.exists(FILENAME):
        print(f"Downloading file from {LIDAR_URL}...")
        download_file(LIDAR_URL, FILENAME)
    return laspy.read(FILENAME)


def extract_ground_points(las):
    ground_mask = las.classification == GROUND_CLASSIFICATION
    return np.column_stack([las.x[ground_mask], las.y[ground_mask], las.z[ground_mask]])


def process_hydrology(dem, dem_transform):
    print("Computing flow directions...")
    flow_direction = compute_flow_direction(dem)

    print("Computing flow accumulation...")
    flow_accumulation = compute_flow_accumulation(flow_direction)

    print("Extracting stream network...")
    stream_network, stream_mask, threshold = extract_stream_network(
        flow_accumulation, dem_transform, flow_direction
    )

    return flow_direction, flow_accumulation, stream_network, stream_mask, threshold


def export_all_rasters(
    dem, flow_direction, flow_accumulation, stream_mask, slope_degrees, transform
):
    export_raster(dem, "outputs/dem.tif", transform)
    export_raster(flow_direction, "outputs/flow_direction.tif", transform)
    export_raster(flow_accumulation, "outputs/flow_accumulation.tif", transform)
    export_raster(stream_mask.astype(np.uint8), "outputs/stream_mask.tif", transform)
    export_raster(slope_degrees, "outputs/slope_angle.tif", transform)


def main():
    start_time = time.time()

    las = download_and_load_lidar()
    ground_points = extract_ground_points(las)
    print("Ground points extracted")

    print("Converting points to raster DEM...")
    dem, dem_transform = points_to_raster(ground_points, cell_size=CASIER_SIZE)

    print("Computing slopes...")
    _, slope_degrees = slope(dem, resolution=SLOPE_RESOLUTION)

    flow_direction, flow_accumulation, stream_network, stream_mask, _ = (
        process_hydrology_jax(dem, dem_transform)
    )

    if not os.path.exists("outputs"):
        os.makedirs("outputs")

    print("Exporting stream network as vector shapefile...")
    stream_gdf = gpd.GeoDataFrame(geometry=stream_network, crs=CRS)
    stream_gdf["length"] = stream_gdf.geometry.length
    stream_gdf["stream_id"] = range(len(stream_gdf))
    stream_gdf.to_file("outputs/stream_network.shp")
    print(f"Saved {len(stream_network)} stream lines to stream_network.shp")

    export_all_rasters(
        dem,
        flow_direction,
        flow_accumulation,
        stream_mask,
        slope_degrees,
        dem_transform,
    )

    end_time = time.time()
    elapsed_time = end_time - start_time
    print(f"Processing completed in {humanize.naturaldelta(elapsed_time)}")


if __name__ == "__main__":
    main()
