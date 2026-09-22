import math
from datetime import datetime
import numpy as np
import urllib3

from load_point_cloud import load_point_cloud
from dsm_cell_traverse import build_dsm, dsm_to_cells, BVHNode, traverse
from sunpositions import *
from user_files import *
from output_format import *

#------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# Purpose : This program generates a 2.5D urban cast-shadow map.
# Author : Karima Ouadah < ouadkarima@outlook.com >
# Created : June-September 2026
# Description : it takes the coordinates of an area as input, retrieves the corresponding tile from the DataGrandLyon website (LiDAR data), calculates the sun's position for a given date, optimizes the calculation using BVH, 
# and finally generates the shadow map based on the selected time of day.
#------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def main():
    url = "https://data.grandlyon.com/fr/datapusher/ws/grandlyon/ima_gestion_images.imamnt2023laz500mcc46/all.json?maxfeatures=-1&start=1&filename=nuage-de-points-lidar-2023-de-la-metropole-de-lyon"

    year, month, day = user_date_choice()
    date = datetime(year, month, day)
    x_min, y_min = user_entry_data()
    laz_file = user_laz_api(url, x_min, y_min)

    points = load_point_cloud(laz_file, 1)
    print("points loaded")

    DSM, xmin, ymin, resolution = build_dsm(points)
    print("DSM created")
    cells = dsm_to_cells(DSM)
    print("cells created :", len(cells))

    print("begin to create BVHNode")
    bvh = BVHNode(cells)
    print("BVH created")

    alpha_list, psi_list, omega, time = sunpos(
        latitude_deg=45.75, # latitude of Lyon
        tau_deg= 45, # time intervals
        date=date
    )

    time_index, time_index_end = user_sun_phase_choice(alpha_list, time)

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
    save_plot_shadow(shadow, time, time_index, x_min, y_min, day, month, year, "Method 1 Shadow Map", "Method1")
    georef_shadow_save(shadow, int(x_min), int(y_min), DSM, 1.0, "Method1_georef")

if __name__ == "__main__":
    main()