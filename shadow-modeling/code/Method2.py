import math
from datetime import datetime
import numpy as np
import matplotlib.pyplot as plt
import pdal
import json
from pathlib import Path
import requests
import laspy
from scipy.spatial import cKDTree
from scipy.stats import binned_statistic_2d
from skimage.feature import peak_local_max
from skimage.segmentation import watershed
import urllib3
import rasterio
from rasterio.transform import from_origin

# ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# Purpose : This program generates a 2.5D urban cast-shadow map and improves the calculation of vegetation shadows.
# Author : Karima Ouadah < ouadkarima@outlook.com >
# Created : June-September 2026
# Description : it takes the coordinates of an area as input, retrieves the corresponding tile from the DataGrandLyon website (LiDAR data), calculates the sun's position for a given date, optimizes the calculation using BVH,
# separates the calculation of vegetation shadows by taking into account only the foliage (the upper third of the tree) and finally generates the shadow map based on the selected time of day.
# ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def sunpos(latitude_deg, tau_deg, date):
    phi = math.radians(latitude_deg)
    n = date.timetuple().tm_yday
    Delta = (2.0 * math.pi * n) / 365.25
    delta = math.asin(
        0.3978 * math.sin(Delta - 1.4 + 0.0355 * math.sin(Delta - 0.0489))
    )
    omega_ss = math.acos(-math.tan(phi) * math.tan(delta))
    omega_sr = -omega_ss
    tau_omega = math.radians(tau_deg)
    elevations = []
    azimuths = []
    hour_angles = []
    time_list = []
    omega = omega_sr
    while omega <= omega_ss:
        alpha = math.asin(
            math.sin(delta) * math.sin(phi)
            + math.cos(delta) * math.cos(omega) * math.cos(phi)
        )
        cos_alpha = math.cos(alpha)
        value = (
            math.sin(delta) * math.cos(phi)
            - math.cos(delta) * math.cos(omega) * math.sin(phi)
        ) / cos_alpha
        value = max(-1.0, min(1.0, value))
        psi = math.acos(value)
        if omega >= 0:
            psi = 2 * math.pi - psi

        elevations.append(math.degrees(alpha))
        azimuths.append(math.degrees(psi))
        hour_angles.append(math.degrees(omega))

        solar_hour = 12 + math.degrees(omega) / 15
        hours = int(solar_hour)
        minutes = int((solar_hour - hours) * 60)
        time_list.append(f"{hours:02d}:{minutes:02d}")

        omega += tau_omega

    return elevations, azimuths, hour_angles, time_list

def bresenham(x0, y0, x1, y1):
    x0, y0 = int(round(x0)), int(round(y0))
    x1, y1 = int(round(x1)), int(round(y1))

    points = []

    dx = abs(x1 - x0)
    dy = abs(y1 - y0)

    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1

    err = dx - dy

    while True:
        points.append((x0, y0))

        if x0 == x1 and y0 == y1:
            break

        e2 = 2 * err

        if e2 > -dy:
            err -= dy
            x0 += sx

        if e2 < dx:
            err += dx
            y0 += sy

    return points

def process_cell(ix, iy, z, alpha, psi, DSM,  shadow):
    if alpha <= 0:
        return

    theta = psi - math.pi

    D = min(z / math.tan(alpha), 2 * max(DSM.shape))

    dx = -D * math.sin(theta)
    dy = -D * math.cos(theta)

    x_top = ix - dx
    y_top = iy - dy

    line = bresenham(ix, iy, x_top, y_top)

    ix = int(ix)
    iy = int(iy)
    z_source = DSM[iy, ix]

    for x, y in line[1:]:
        if (
                x < 0 or x >= DSM.shape[1] or
                y < 0 or y >= DSM.shape[0]
        ):
            continue

        z_sol = DSM[y, x]
        z_cell = DSM[y, x]

        if z_cell == -np.inf:
            continue

        d = np.hypot(x - ix, y - iy)

        if d == 0:
            continue

        beta = math.atan((z_source - z_cell) / d)

        if beta >= alpha:   #
            shadow.append((x, y, z_sol))

def process_cell_vegetation(ix, iy, alpha, psi, DSM, shadow_vegetation, dsm_canope_top, dsm_canope_bottom, DTM):

    if alpha <= 0:
        return

    ix = int(ix)
    iy = int(iy)

    z_source = dsm_canope_top[iy, ix]

    if z_source == -np.inf:
        return

    h = z_source - DTM[iy, ix]

    if h <= 0:
        return

    theta = psi - math.pi

    D = min(
        h / math.tan(alpha),
        2 * max(DSM.shape)
    )

    dx = -D * math.sin(theta)
    dy = -D * math.cos(theta)

    x_end = ix - dx
    y_end = iy - dy

    line = bresenham(ix, iy, x_end, y_end)

    ombre = False

    for x, y in line[1:]:

        if (
                x < 0 or x >= DSM.shape[1] or
                y < 0 or y >= DSM.shape[0]
        ):
            continue

        d = np.hypot(x - ix, y - iy)

        z_ray = z_source - d * math.tan(alpha)

        z_top = dsm_canope_top[y, x]
        z_bottom = dsm_canope_bottom[y, x]
        z_sol = DTM[y, x]

        if z_top != -np.inf:

            if z_bottom <= z_ray <= z_top:
                ombre = True

        if ombre and z_sol < z_ray < z_bottom:
            shadow_vegetation.append((x, y, z_sol))

class BVHNode:
    def __init__(self, cells, leaf_size=512):
        self.left = None
        self.right = None
        self.cells = None

        xy = cells[:, :2]

        self.min = xy.min(axis=0)
        self.max = xy.max(axis=0)

        if len(cells) <= leaf_size:
            self.cells = cells
            return

        extent = self.max - self.min
        axis = np.argmax(extent)

        order = np.argsort(cells[:, axis])
        cells = cells[order]

        mid = len(cells) // 2

        self.left = BVHNode(cells[:mid], leaf_size)
        self.right = BVHNode(cells[mid:], leaf_size)

def traverse(node, alpha, psi, DSM, shadow):
    if node is None:
        return

    if node.cells is not None:

        for ix, iy, z in node.cells:

            process_cell(ix, iy, z, alpha, psi, DSM, shadow)

        return

    traverse(node.left, alpha, psi, DSM, shadow)
    traverse(node.right, alpha, psi, DSM, shadow)

def traverse_vegetation(node, alpha, psi, DSM, shadow_vegetation, dsm_canope_top, dsm_canope_bottom, dtm):
    if node is None:
        return

    if node.cells is not None:

        for ix, iy, z in node.cells:

            process_cell_vegetation(ix, iy, alpha, psi, DSM, shadow_vegetation, dsm_canope_top, dsm_canope_bottom, dtm)

        return

    traverse_vegetation(node.left, alpha, psi, DSM, shadow_vegetation, dsm_canope_top, dsm_canope_bottom, dtm)
    traverse_vegetation(node.right, alpha, psi, DSM, shadow_vegetation, dsm_canope_top, dsm_canope_bottom, dtm)

def plot_shadow(shadow, time, time_index, x_min, y_min, day, month, year, title):
    if shadow is None:
        print("Error: shadow is None.")
        return

    if shadow.ndim != 2 or shadow.shape[1] < 2:
        print(f"Error: shadow must have shape (N, 2). Current shape : {shadow.shape}")
        print("Too many points; please choose a different classification or time of day.")
        return

    if len(shadow) == 0:
        print("No points to display.")
        return

    if shadow.size == 0:
        print("No points to display.")
        return

    plt.figure(figsize=(12, 12))

    plt.scatter(
        shadow[:, 0],
        shadow[:, 1],
        alpha=1,
        s=0.2,
        c="black",
        label="Projection"
    )

    plt.axis("equal")
    plt.legend()
    plt.title(f"{title}\nX_min : {x_min} Y_min : {y_min}\nPeriod : {time_index} Hour : {time[time_index]}\nDate : {day}-{month}-{year}")
    print("plot ready")
    plt.show()

def plot_and_save_shadow(shadow, time, time_index, x_min, y_min, day, month, year, title):
    if shadow is None:
        print("Error: shadow is None.")
        return

    if shadow.ndim != 2 or shadow.shape[1] < 2:
        print(f"Error: shadow must have shape (N, 2). Current shape : {shadow.shape}")
        print("Too many points; please choose a different classification or time of day.")
        return

    if len(shadow) == 0:
        print("No points to display.")
        return

    if shadow.size == 0:
        print("No points to display.")
        return

    plt.figure(figsize=(12, 12))

    plt.scatter(
        shadow[:, 0],
        shadow[:, 1],
        alpha=1,
        s=0.2,
        c="black",
        label="Projection"
    )

    plt.axis("equal")
    plt.legend()
    plt.title(f"{title}\nX_min : {x_min} Y_min : {y_min}\nPeriod : {time_index} Hour : {time[time_index]}\nDate : {day}-{month}-{year}")
    print("plot ready")
    Path("Data").mkdir(exist_ok=True)
    plt.savefig(
        Path("Data") / "Method2_Shadow_Map.tiff",
        dpi=300,
        bbox_inches="tight",
        format="tiff"
    )
    plt.show()

def georef_shadow_save(
        shadow,
        x_min,
        y_min,
        DSM,
        resolution=1.0
):
    if shadow is None:
        print("Error: shadow is None.")
        return

    shadow = np.asarray(shadow)

    if shadow.ndim != 2 or shadow.shape[1] < 2:
        print(
            f"Error: shadow must have shape (N, 2) or (N, 3). "
            f"Current shape: {shadow.shape}"
        )
        return

    if len(shadow) == 0:
        print("No points to display.")
        return

    height, width = DSM.shape

    # 0 = no shadow
    # 1 = shadow
    shadow_raster = np.zeros(
        (height, width),
        dtype=np.uint8
    )

    for point in shadow:
        x = int(point[0])
        y = int(point[1])

        if (
                0 <= x < width and
                0 <= y < height
        ):
            shadow_raster[y, x] = 1

    x_origin = x_min
    y_origin = y_min + height * resolution

    transform = from_origin(
        x_origin,
        y_origin,
        resolution,
        resolution
    )

    output_dir = Path("Data")
    output_dir.mkdir(exist_ok=True)

    output_file = output_dir / "GeoRef_Method2_Shadow.tif"

    with rasterio.open(
            output_file,
            "w",
            driver="GTiff",
            height=height,
            width=width,
            count=1,
            dtype="uint8",
            crs="EPSG:3946",
            transform=transform,
            nodata=0
    ) as dst:

        dst.write(shadow_raster, 1)

    print(f"GeoTIFF saved: {output_file}")


def load_point_cloud(filename, classification_choice):
    if classification_choice == 1:
        pipeline = pdal.Pipeline(json.dumps({
            "pipeline": [filename]
        }))

    if classification_choice == 2:
        pipeline = pdal.Pipeline(json.dumps({
            "pipeline": [
                filename,
                {
                    "type": "filters.expression",
                    "expression": "Classification == 3 || Classification == 4 || Classification == 5",
                }
            ]
        }))

    if classification_choice == 3:
        pipeline = pdal.Pipeline(json.dumps({
            "pipeline": [
                filename,
                {
                    "type": "filters.expression",
                    "expression": "Classification == 3 || Classification == 4 || Classification == 5 || Classification == 8",
                }
            ]
        }))

    if classification_choice == 4:
        pipeline = pdal.Pipeline(json.dumps({
            "pipeline": [
                filename,
                {
                    "type": "filters.expression",
                    "expression": "Classification == 6",
                }
            ]
        }))

    if classification_choice == 5:
        pipeline = pdal.Pipeline(json.dumps({
            "pipeline": [
                filename,
                {
                    "type": "filters.expression",
                    "expression": "Classification != 3 && Classification != 4 && Classification != 5 && Classification != 8",
                }
            ]
        }))

    pipeline.execute()
    arrays = pipeline.arrays[0]
    points = np.vstack((arrays['X'], arrays['Y'], arrays['Z'], arrays['Classification'])).transpose()
    return points

def build_dsm(points, resolution=1.0):
    print("begin building DSM")
    xmin, ymin = points[:, 0].min(), points[:, 1].min()
    xmax, ymax = points[:, 0].max(), points[:, 1].max()
    nx = int((xmax - xmin) / resolution) + 1
    ny = int((ymax - ymin) / resolution) + 1

    DSM = np.full((ny, nx), -np.inf)

    for x, y, z, _ in points:
        ix = int((x - xmin) / resolution)
        iy = int((y - ymin) / resolution)

        DSM[iy, ix] = max(DSM[iy, ix], z)

    return DSM, xmin, ymin, resolution

def dsm_to_cells(DSM):
    cells = []

    for iy in range(DSM.shape[0]):
        for ix in range(DSM.shape[1]):

            z = DSM[iy, ix]

            if z > -np.inf:
                cells.append([ix, iy, z])

    return np.asarray(cells)

def user_sun_phase_choice(alpha_list, horaire_list):
    if alpha_list[0] <= 0:
        start_index = 1
    else:
        start_index = 0

    while True:
        print("\nPlease choose a phase of the day (from sunrise to sunset):\n")

        for i in range(start_index, len(alpha_list)):
            print(f"{i} : {horaire_list[i]}")

        user_time_index = input("\nEnter the index of the desired time: ")

        if user_time_index.isdigit():
            user_time_index = int(user_time_index)

            if start_index <= user_time_index < len(alpha_list):
                return user_time_index

        print(f"Invalid entry. Please enter a number between {start_index} and {len(alpha_list)-1}.")

def user_date_choice():
    while True:
        day = input("Please enter the day : ")
        month = input("Please enter the month : ")
        year = input("Please enter the year : ")
        print("\n")

        try:
            date = datetime(int(year), int(month), int(day))
            return date.year, date.month, date.day
        except ValueError:
            print("Invalid date. Please try again.")

def user_entry_data():
    user_x_min = input("Please enter x min: ")
    user_y_min = input("Please enter y min: ")

    return user_x_min, user_y_min

def user_laz_api(url_api, user_x_min, user_y_min):
    answer = requests.get(url_api, verify=False)
    data = answer.json()

    for value in data["values"]:
        if value["x_min"] == int(user_x_min) and value["y_min"] == int(user_y_min):
            url = value["url"].strip()
            break
    else:
        raise ValueError("Tile not found")

    r = requests.get(url, verify=False)
    r.raise_for_status()

    output_dir = Path("Data")
    output_dir.mkdir(exist_ok=True)
    filename = output_dir / f"tile_{user_x_min}_{user_y_min}.laz"

    with open(filename, "wb") as f:
        f.write(r.content)

    return str(filename)

def capone_laz_generate(laz_file):

    las = laspy.read(laz_file)

    x = np.asarray(las.x)
    y = np.asarray(las.y)
    z = np.asarray(las.z)
    cls = las.classification

    resolution = 1.0

    ground = cls == 2

    x_ground = x[ground]
    y_ground = y[ground]
    z_ground = z[ground]

    trees = np.isin(cls, [3, 4, 5, 8])

    x_tree = x[trees]
    y_tree = y[trees]
    z_tree = z[trees]

    xmin = x.min()
    xmax = x.max()

    ymin = y.min()
    ymax = y.max()

    bins_x = np.arange(
        xmin,
        xmax + resolution,
        resolution
    )

    bins_y = np.arange(
        ymin,
        ymax + resolution,
        resolution
    )

    dtm, _, _, _ = binned_statistic_2d(
        x_ground,
        y_ground,
        z_ground,
        statistic="min",
        bins=[bins_x, bins_y]
    )

    dtm = dtm.T

    dtm = np.nan_to_num(
        dtm,
        nan=np.nanmedian(dtm)
    )

    dsm, _, _, _ = binned_statistic_2d(
        x_tree,
        y_tree,
        z_tree,
        statistic="max",
        bins=[bins_x, bins_y]
    )

    dsm = dsm.T
    dsm = np.nan_to_num(dsm, nan=0)

    chm = dsm - dtm
    chm[chm < 0] = 0

    tops = peak_local_max(
        chm,
        min_distance=8,
        threshold_abs=2
    )

    markers = np.zeros(
        chm.shape,
        dtype=np.int32
    )

    for i, (r, c) in enumerate(tops):
        markers[r, c] = i + 1

    labels = watershed(
        -chm,
        markers,
        mask=chm > 0
    )

    col = ((x_tree - xmin) / resolution).astype(int)
    row = ((y_tree - ymin) / resolution).astype(int)

    row = np.clip(
        row,
        0,
        labels.shape[0] - 1
    )

    col = np.clip(
        col,
        0,
        labels.shape[1] - 1
    )

    tree_id = labels[row, col]

    tree_ground = cKDTree(
        np.column_stack((x_ground, y_ground))
    )

    _, idx = tree_ground.query(
        np.column_stack((x_tree, y_tree))
    )

    ground_z = z_ground[idx]

    height = z_tree - ground_z

    keep = np.zeros(
        len(z_tree),
        dtype=bool
    )

    for tree in np.unique(tree_id):

        if tree == 0:
            continue

        pts = tree_id == tree

        hmax = height[pts].max()

        seuil = 0.70 * hmax

        keep[pts] = height[pts] >= seuil

    x_keep = x_tree[keep]
    y_keep = y_tree[keep]
    z_keep = z_tree[keep]

    dsm_canope_top, _, _, _ = binned_statistic_2d(
        x_keep,
        y_keep,
        z_keep,
        statistic="max",
        bins=[bins_x, bins_y]
    )

    dsm_canope_top = dsm_canope_top.T

    dsm_canope_top = np.nan_to_num(
        dsm_canope_top,
        nan=-np.inf
    )

    dsm_canope_bottom, _, _, _ = binned_statistic_2d(
        x_keep,
        y_keep,
        z_keep,
        statistic="min",
        bins=[bins_x, bins_y]
    )

    dsm_canope_bottom = dsm_canope_bottom.T

    dsm_canope_bottom = np.nan_to_num(
        dsm_canope_bottom,
        nan=np.inf
    )

    new_las = laspy.LasData(las.header)

    new_las.points = las.points[trees][keep]

    output_file = "Data/canopee.laz"

    new_las.write(output_file)

    return (
        output_file,
        dsm_canope_top,
        dsm_canope_bottom,
        chm,
        dtm
    )

def main():
    url = "https://data.grandlyon.com/fr/datapusher/ws/grandlyon/ima_gestion_images.imamnt2023laz500mcc46/all.json?maxfeatures=-1&start=1&filename=nuage-de-points-lidar-2023-de-la-metropole-de-lyon"

    year, month, day = user_date_choice()
    date = datetime(year, month, day)
    x_min, y_min = user_entry_data()
    laz_file = user_laz_api(url, x_min, y_min)

    canope_laz, dsm_canope_top, dsm_canope_bottom, chm, dtm = capone_laz_generate(laz_file)

    points = load_point_cloud(laz_file, 5)
    DSM, xmin, ymin, resolution = build_dsm(points)
    cells = dsm_to_cells(DSM)
    bvh = BVHNode(cells)

    alpha_list, psi_list, omega, time = sunpos(
        latitude_deg=45.75,
        tau_deg= 45,
        date=date
    )

    time_index = user_sun_phase_choice(alpha_list, time)

    print("\nValeurs :")
    print("elevation value :", alpha_list[time_index])
    print("azimuth value :", psi_list[time_index])
    print("hour angle value :", omega[time_index])
    print("\n")

    alpha = math.radians(alpha_list[time_index])
    psi = math.radians(psi_list[time_index])
    shadow = []

    traverse(
        bvh,
        alpha,
        psi,
        DSM,
        shadow
    )

    shadow = np.asarray(shadow)

    cells_vegetation_2 = dsm_to_cells(chm)
    bvh_vegetation_2 = BVHNode(cells_vegetation_2)
    shadow_vegetation = []
    traverse_vegetation(
        bvh_vegetation_2,
        alpha,
        psi,
        chm,
        shadow_vegetation,
        dsm_canope_top,
        dsm_canope_bottom,
        dtm
    )

    shadow_vegetation = np.asarray(shadow_vegetation)

    shadow_all = np.unique(
        np.concatenate((shadow, shadow_vegetation), axis=0),
        axis=0
    )

    with open("Data/coord_shadow_sm2.txt", "w") as f:
        for X, Y, Z in shadow_all:
            f.write(f"{X+int(x_min)} {Y+int(y_min)} {Z}\n")

    plot_shadow(shadow, time, time_index, x_min, y_min, day, month, year, "Method 2 Vegetation-free Shadow Map")
    plot_shadow(shadow_vegetation, time, time_index, x_min, y_min, day, month, year, "Method 2 Shadow Map with vegetation")
    plot_and_save_shadow(shadow_all, time, time_index, x_min, y_min, day, month, year, "Method 2 Global Shadow Map")
    georef_shadow_save(shadow_all, int(x_min), int(y_min), DSM, resolution=1.0)

if __name__ == "__main__":
    main()
