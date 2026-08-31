import rasterio
import numpy as np

means = []
stds = []

with rasterio.open("C:\\dev\\Stage\\VCity\\Projects\\IArbre\\Stage-Changements_Urbains\\code\\workdir\\runs\\test_batiments\\ortho\\gc\\IRG\\irg-gc-2023-50cm.tif") as src:
    for i in range(1, 4):
        band = src.read(i).astype(float)
        valid = band[band > 0]
        means.append(round(valid.mean(), 2))
        stds.append(round(valid.std(), 2))

print(f"means: {[round(float(v), 2) for v in means]}")
print(f"stds: {[round(float(v), 2) for v in stds]}")