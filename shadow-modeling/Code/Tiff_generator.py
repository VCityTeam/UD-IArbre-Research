from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin


def tif_generator(x_min, y_min, DSM, DTM, resolution, method_name):

    if DSM is None or DTM is None:
        print("Error: DSM or DTM is None.")
        return

    DSM = np.asarray(DSM, dtype=np.float32)
    DTM = np.asarray(DTM, dtype=np.float32)

    if DSM.shape != DTM.shape:
        print(
            f"Error: DSM and DTM must have the same shape. "
            f"DSM: {DSM.shape}, DTM: {DTM.shape}"
        )
        return

    height_map = DSM - DTM

    height_map[height_map < 0] = 0
    height_map = np.nan_to_num(height_map, nan=0.0)
    height_map = np.flipud(height_map)

    height, width = height_map.shape

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

    output_file = output_dir / f"GeoRef_{method_name}.tif"

    with rasterio.open(
        output_file,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype="float32",
        crs="EPSG:3946",
        transform=transform,
        nodata=-9999
    ) as dst:

        dst.write(height_map.astype(np.float32), 1)