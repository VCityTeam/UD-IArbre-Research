import math
from datetime import datetime
import numpy as np
import urllib3

from load_point_cloud import load_point_cloud
from dsm_cell_traverse import build_dsm, dsm_to_cells, BVHNode, traverse, traverse_vegetation
from sunpositions import *
from user_files import *
from output_format import *
from canope_laz_generator import *
from Tiff_generator import *

# ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# Purpose : This program generates a 2.5D urban cast-shadow map and improves the calculation of vegetation shadows.
# Author : Karima Ouadah < ouadkarima@outlook.com >
# Created : June-September 2026
# Description : it takes the coordinates of an area as input, retrieves the corresponding tile from the DataGrandLyon website (LiDAR data), calculates the sun's position for a given date, optimizes the calculation using BVH,
# separates the calculation of vegetation shadows by taking into account only the foliage (the upper third of the tree) and finally generates the shadow map based on the selected time of day.
# ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def main():
    url = "https://data.grandlyon.com/fr/datapusher/ws/grandlyon/ima_gestion_images.imamnt2023laz500mcc46/all.json?maxfeatures=-1&start=1&filename=nuage-de-points-lidar-2023-de-la-metropole-de-lyon"

    shadow_all_period = []
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
        latitude_deg=45.75, # latitude of Lyon
        tau_deg= 15, # time intervals  15 -> 1h, 45 -> 3h
        date=date
    )

    time_index, time_index_end= user_sun_phase_choice(alpha_list, time)
    N = time_index_end - time_index

    print("\nValeurs :")
    print("elevation value :", alpha_list)
    print("azimuth value :", psi_list)
    print("hour angle value :", omega)
    print("\n")

    # shadow for a period of time
    if time_index_end != 99 :
        for time_index in range(time_index, time_index_end+1) :
            print("loop time_index :", time_index)
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
            shadow_all_period.append(shadow_all)

        # output tiff
        multiple_hours_shadow_save_tiff_format(N+1, shadow_all_period, int(x_min), int(y_min), DSM, 1.0, "Method2_multiple_hours_shadow")

    # shadow for a single time
    else :
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
    # Outputs
        points_building = load_point_cloud(laz_file, 4)
        DSM_building_only, xmin_building, ymin_building, resolution_building = build_dsm(points_building)
        DSM_building = np.full(dtm.shape, -np.inf, dtype=np.float32)
        offset_x = int((xmin_building - int(x_min)) / resolution_building)
        offset_y = int((ymin_building - int(y_min)) / resolution_building)
        h, w = DSM_building_only.shape
        DSM_building[
            offset_y:offset_y + h,
            offset_x:offset_x + w
        ] = DSM_building_only

        # output txt
        with open("Data/coord_shadow_sm2.txt", "w") as f:
            for X, Y, Z in shadow_all:
                f.write(f"{X+int(x_min)} {Y+int(y_min)} {Z}\n")

        # output png
        save_plot_shadow(shadow_all, time, time_index, x_min, y_min, day, month, year, "Method 2 Global Shadow Map", "Method2")

        # output tiff
        georef_shadow_save(shadow_all, int(x_min), int(y_min), DSM, 1.0, f"Method2_georef_{time[time_index]}")

        tif_generator(int(x_min), int(y_min), DSM_building, dtm, 1.0, "DSM_building")
        tif_generator(int(x_min), int(y_min), dsm_canope_top, dtm, 1.0, "DSM_vegetation_top")

        bottom = np.asarray(dsm_canope_bottom, dtype=np.float32)
        bottom[~np.isfinite(bottom)] = 0
        tif_generator(int(x_min), int(y_min), bottom, dtm, 1.0, "DSM_vegetation_bottom")

if __name__ == "__main__":
    main()