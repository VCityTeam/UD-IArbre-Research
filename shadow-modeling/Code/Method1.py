import math
from datetime import datetime
import numpy as np
import matplotlib.pyplot as plt
import pdal
import json
from pathlib import Path
import requests
import urllib3
import rasterio
from rasterio.transform import from_origin

#------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# Purpose : This program generates a 2.5D urban cast-shadow map.
# Author : Karima Ouadah < ouadkarima@outlook.com >
# Created : June-September 2026
# Description : it takes the coordinates of an area as input, retrieves the corresponding tile from the DataGrandLyon website (LiDAR data), calculates the sun's position for a given date, optimizes the calculation using BVH, 
# and finally generates the shadow map based on the selected time of day.
#------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Def sunpos : The function calculates the sun's positions every three hours throughout the day for a given date and stores them in the 'time_list' array.
# Parameters : latitude, tau angle, date
# Outputs : elevations, azimuths, hour_angles, time_list
def sunpos(latitude_deg, tau_deg, date):
    print("begin sunpos")
    phi = math.radians(latitude_deg)
    n_day_in_year = date.timetuple().tm_yday
    Delta = (2.0 * math.pi * n_day_in_year) / 365.25
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

# Def process_cell : The function takes various parameters and tests whether a point is in shadow; if so, it adds the point to the 'shadow' list.
# Parameters : ix (x position of the cell), iy (y position of the cell), z (z coordinate of the cell), alpha (solar elevation), psi (solar azimuth), DSM, shadow list
def process_cell(ix, iy, z, alpha, psi, DSM,  shadow):
    if alpha == 0:
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

        z_cell = DSM[y, x]

        if z_cell == -np.inf:
            continue

        d = np.hypot(x - ix, y - iy)
        beta = math.atan((z_source - z_cell) / d)

        if beta >= alpha:
            shadow.append((x, y, z_cell))

# Description BVHNode : Organizes the cells into several groups to avoid unnecessarily testing all cells when calculating shadows
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

    shadow_raster = np.full(
        (height, width),
        255,
        dtype=np.uint8
    )

    for point in shadow:
        x = int(point[0])
        y = int(point[1])

        if (
                0 <= x < width and
                0 <= y < height
        ):
            shadow_raster[height - 1 - y, x] = 0

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

    output_file = output_dir / "GeoRef_Method1_Shadow.tif"

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
    print("begin converting DSM to cells")
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

def main():
    url = "https://data.grandlyon.com/fr/datapusher/ws/grandlyon/ima_gestion_images.imamnt2023laz500mcc46/all.json?maxfeatures=-1&start=1&filename=nuage-de-points-lidar-2023-de-la-metropole-de-lyon"

    year, month, day = user_date_choice()
    date = datetime(year, month, day)
    x_min, y_min = user_entry_data()
    laz_file = user_laz_api(url, x_min, y_min)

    points = load_point_cloud(laz_file, 1)
    print("points loaded choix 5")

    DSM, xmin, ymin, resolution = build_dsm(points)
    print("DSM created choix 5")
    cells = dsm_to_cells(DSM)
    print("cells created choix 5:", len(cells))

    print("begin to create BVHNode choix 5")
    bvh = BVHNode(cells)
    print("BVH created choix 5")

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
    print("begin traverse")
    traverse(
        bvh,
        alpha,
        psi,
        DSM,
        shadow
    )
    shadow = np.asarray(shadow)
    with open("Data/coord_shadow_sm1.txt", "w") as f:
        for X, Y, Z in shadow:
            f.write(f"{X+int(x_min)} {Y+int(y_min)} {Z}\n")
    plot_shadow(shadow, time, time_index, x_min, y_min, day, month, year, "Method 1 Shadow Map")
    georef_shadow_save(shadow, int(x_min), int(y_min), DSM, resolution=1.0)

if __name__ == "__main__":
    main()