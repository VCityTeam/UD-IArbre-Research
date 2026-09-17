import matplotlib.pyplot as plt
from pathlib import Path
import rasterio
from rasterio.transform import from_origin
import numpy as np

def save_plot_shadow(shadow, time, time_index, x_min, y_min, day, month, year, title, method_name):
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

    Path("Data").mkdir(exist_ok=True)

    plt.savefig(
        Path("Data") / f"{method_name}.png",
        dpi=300,
        bbox_inches="tight",
        format="png"
    )

    plt.close()

def georef_shadow_save(shadow, x_min, y_min, DSM, resolution, method_name):
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

    output_file = output_dir / f"GeoRef_{method_name}_Shadow.tif"

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