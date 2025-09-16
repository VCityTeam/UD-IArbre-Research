import numpy as np
import geopandas as gpd
from exactextract import exact_extract
from sklearn.linear_model import LinearRegression
from rasterio import features
import rasterio

# DEM is the english equivalent for MNT (Modèle Numérique de Terrain)

def slope(mnt_data, resolution=1):
    """
    Function to calculate local slopes from a Digital Elevation Model (DEM).

    Parameters :
    - mnt_data : numpy.ndarray, DEM data as a 2D array.
    - resolution : int, spatial resolution of the DEM (default is 1 meter).

    Returns :
    - slope_rad : numpy.ndarray, slope in radians.
    - slope_angle : numpy.ndarray, slope in degrees.
    """
    x, y = np.gradient(mnt_data, resolution)
    slope = np.sqrt(x**2 + y**2)
    slope_rad = np.arctan(slope)
    slope_angle = np.degrees(slope_rad)
    return slope_rad, slope_angle


def best_fit_plane_slope(mnt_data, mask, mnt_transform):
    """
    Function to compute the average slope of a best-fit plane to the points in a DEM.
    This function aims to find the "average" slope over the grid cell that best fits all points in the study area.
    It does not consider points outside the grid cell (if the mask is applied correctly).
    It also does not take into account the direction of the tilt of the plane, only its value (e.g., 8°).

    Parameters :
    - mnt_data : numpy.ndarray, DEM data as a 2D array.
    - mask : numpy.ndarray, boolean mask indicating the area of interest (True for pixels to consider).
    - mnt_transform : affine.Affine, affine transformation of the DEM.

    Returns :
    - best_fit_plane : list, list containing the average slope of the best-fit plane in degrees.
    """
    best_fit_plane = []
    rows, cols = np.where(mask)
    zs = mnt_data[rows, cols] # Takes the z values (altitude) of the pixels on the "rows" and "cols" lines
    xs = rasterio.transform.xy(mnt_transform, rows, cols, offset='center')[0] # Takes the x and y coordinates of the pixels on the "rows" and "cols" lines, [0] for x, [1] for y
    ys = rasterio.transform.xy(mnt_transform, rows, cols, offset='center')[1]

    if len(zs) >= 3: # At least 3 points are needed to fit a plane, otherwise the slope is 0
        # Fit a plane to the points (xs, ys, zs) using linear regression
        # The plane is defined by the equation z = ax + by + c, where a and b are the slopes in x and y directions
        # We use the LinearRegression model from sklearn to find the best fit plane
        A = np.c_[xs, ys] # Combines xs and ys into a 2D array where each row is a point (x, y)
        model = LinearRegression().fit(A, zs)
        dzdx, dzdy = model.coef_
        plane_slope_rad = np.arctan(np.sqrt(dzdx**2 + dzdy**2))
        best_fit_plane.append(np.degrees(plane_slope_rad))
    else:
        best_fit_plane.append(0)

    return best_fit_plane


def calculate_slope(mnt_data, mnt_transform, casiers, resolution=1, method="mean_thresholded"):
    """
    Function to calculate the slope for each grid cell in a GeoDataFrame of casiers (grid cells) depending on the chosen configuration.

    Parameters :
    - mnt_data : numpy.ndarray, DEM data as a 2D array.
    - mnt_transform : affine.Affine, affine transformation of the DEM.
    - casiers : GeoDataFrame, grid cells (casiers) for which to calculate the slope.
    - resolution : int, spatial resolution of the DEM (default is 1 meter).
    - method : str, method to use for slope calculation. Options are:
        - "mean_thresholded": Mean slope between 5° and 50°. For not hardcoded values, the threshold can be adjusted.
        - "slope_std_dev": Standard deviation of the slope.
        - "slope_max": Maximum slope.
        - "slope_mean_denoised": Mean slope after denoising.
        - "best_fit_plane": Slope of the best-fit plane.

    Returns :
    - slope_dict : dict, dictionary containing the slope values for each grid cell in the GeoDataFrame.
    """
    slope_rad, slope_angle = slope(mnt_data, resolution)
    slope_dict = {"slope": []}

    for geom in casiers.geometry:
        mask = features.geometry_mask([geom], transform=mnt_transform, invert=True, out_shape=mnt_data.shape)
        slope_values = slope_angle[mask]

        match method:
            case "mean_thresholded":
                filtered = slope_values[(slope_values >= 5) & (slope_values <= 50)]
                slope_dict["slope"].append(filtered.mean() if filtered.size > 0 else 0)
            case "slope_std_dev":
                slope_dict["slope"].append(np.std(slope_values) if slope_values.size > 0 else 0)
            case "slope_max":
                slope_dict["slope"].append(np.max(slope_values) if slope_values.size > 0 else 0)
            case "slope_mean_denoised":
                slope_values_denoised = slope_rad[mask]
                slope_dict["slope"].append(np.mean(slope_values_denoised) if slope_values_denoised.size > 0 else 0)
            case "best_fit_plane":
                slope_dict["slope"].append(best_fit_plane_slope(mnt_data, mask, mnt_transform))

    return slope_dict

def compute_infiltration_score(casiers, imperviousness_path, imperviousness_factor, slope_factor):
    """
    Function to compute the infiltration index for each grid cell (casiers) based on imperviousness and slope.

    Parameters :
    - casiers : GeoDataFrame, grid cells (casiers) for which to compute the infiltration index.
    - imperviousness_path : str, path to the imperviousness raster file (in .tif format).
    - imperviousness_factor : float, weighting factor for imperviousness.
    - slope_factor : float, weighting factor for slope.

    Returns :
    - casiers : GeoDataFrame, grid cells with the computed infiltration index added as a new column.
    """
    # imperviousness_stats = zonal_stats(casiers, imperviousness_path, stats=["mean"], nodata=0)
    imperviousness_stats = exact_extract(imperviousness_path, casiers, ["mean"]) # ExactExtract is more precise than zonal_stats as it uses the exact geometry of the polygons and calculates the mean value of the pixels that intersect with the polygon, even if the pixels are larger than the polygons

    casiers["imperviousness"] = [
        f["properties"]["mean"]/100 if f["properties"]["mean"] is not None else 0
        for f in imperviousness_stats
    ]

    casiers["normalized_slope"] = (casiers["slope"] - casiers["slope"].min()) / (casiers["slope"].max() - casiers["slope"].min())
    #casiers["infiltration_index"] = (1-casiers["imperviousness"]) * imperviousness_factor + (1 - casiers["normalized_slope"]) * slope_factor
    casiers["infiltration_index"] = (1-casiers["imperviousness"]) * imperviousness_factor + (1-casiers["normalized_slope"]) * slope_factor
    return casiers 


def calculate_ibk(mnt):
    """
    Function to calculate the Beven Kirkby Index (IBK/TWI) from a Digital Elevation Model (DEM).
    This function computes the IBK using the formula:
    IBK = log(A / tan(slope_rad) + 1e-6)

    where:
    - A is the drainage area (simplified as a count of pixels in this case).
    - slope_rad is the slope in radians.
    - 1e-6 is a small constant to avoid division by zero.
    
    Parameters :
    - mnt : numpy.ndarray, DEM data as a 2D array.

    Returns :
    - ibk : numpy.ndarray, Beven Kirkby Index (IBK) values.
    - slope_rad : numpy.ndarray, slope in radians.
    - drainage_area : numpy.ndarray, simplified drainage area.
    """
    slope_rad, slope_angle = slope(mnt)
    drainage_area = calculate_drainage_area(mnt)
    slope_rad[slope_rad == 0] = 1e-6
    ibk = np.log(drainage_area / np.tan(slope_rad))
    return ibk, slope_rad, drainage_area


def create_grid(bounds, crs, casier_size=10):
    """
    Function to create a grid of regular casiers (grid cells) from spatial bounds.

    Parameters :
    - bounds : shapely.bounds, spatial bounds (left, bottom, right, top).
    - crs : str, coordinate reference system of the grid (e.g., "EPSG:4326").
    - casier_size : int, size of each casier in meters (default is 10 meters).

    Returns :
    - grid_gdf : GeoDataFrame, grid of casiers with the specified size and coordinate reference system.
    """
    from shapely.geometry import box
    cols = int((bounds.right - bounds.left) // casier_size)
    rows = int((bounds.top - bounds.bottom) // casier_size)

    grid = []
    for i in range(cols):
        for j in range(rows):
            minx = bounds.left + i * casier_size
            maxx = minx + casier_size
            miny = bounds.bottom + j * casier_size
            maxy = miny + casier_size
            grid.append(box(minx, miny, maxx, maxy))

    grid_gdf = gpd.GeoDataFrame(geometry=grid, crs=crs)
    return grid_gdf


def calculate_drainage_area(mnt):
    """
    Function to calculate a simplified drainage area from a Digital Elevation Model (DEM).

    Parameters :
    - mnt : numpy.ndarray, DEM data as a 2D array.

    Returns :
    - drainage_area : numpy.ndarray, simplified drainage area as a 2D array.
    """
    drainage_area = np.ones_like(mnt)
    for i in range(1, mnt.shape[0] - 1):
        for j in range(1, mnt.shape[1] - 1):
            drainage_area[i, j] += np.sum(mnt[i-1:i+2, j-1:j+2] < mnt[i, j]) # To multiply by the cell/pixel area to get an area in m²
    return drainage_area