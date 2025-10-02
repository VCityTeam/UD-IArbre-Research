import requests
import os
import numpy as np
from scipy.spatial import cKDTree
from rasterio import transform
import rasterio


def download_file(url, filename):
    """Download a file from a URL"""
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()

        if not filename:
            filename = url.split("/")[-1]

        with open(filename, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        print(f"Successfully downloaded {filename}")
        return filename

    except requests.exceptions.RequestException as e:
        print(f"Error downloading file: {e}")
        return None


class Bounds:
    """Formatting for methods.create_grid function"""

    def __init__(self, left, bottom, right, top):
        self.left = left
        self.bottom = bottom
        self.right = right
        self.top = top


def points_to_raster(points, cell_size=2.0):
    """Convert point cloud to raster DEM"""
    x_coords, y_coords, z_coords = points[:, 0], points[:, 1], points[:, 2]

    # Calculate raster dimensions
    x_min, x_max = x_coords.min(), x_coords.max()
    y_min, y_max = y_coords.min(), y_coords.max()

    cols = int(np.ceil((x_max - x_min) / cell_size))
    rows = int(np.ceil((y_max - y_min) / cell_size))

    # Create raster array
    raster = np.full((rows, cols), np.nan)

    # Build KDTree for efficient nearest neighbor search
    tree = cKDTree(points[:, :2])

    # Fill raster with elevation values
    for i in range(rows):
        for j in range(cols):
            x_center = x_min + (j + 0.5) * cell_size
            y_center = y_max - (i + 0.5) * cell_size  # Raster has y-axis flipped

            # Find nearest points within cell
            distances, indices = tree.query(
                [x_center, y_center], k=10, distance_upper_bound=cell_size
            )
            valid_indices = indices[distances < cell_size]

            if len(valid_indices) > 0:
                raster[i, j] = np.mean(z_coords[valid_indices])

    # Create affine transform
    affine_transform = transform.from_bounds(x_min, y_min, x_max, y_max, cols, rows)

    return raster, affine_transform


def export_raster(data, filename, transform, crs="EPSG:2154"):
    """Export numpy array as GeoTIFF raster"""
    with rasterio.open(
        filename,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype=data.dtype,
        crs=crs,
        transform=transform,
    ) as dst:
        dst.write(data, 1)
