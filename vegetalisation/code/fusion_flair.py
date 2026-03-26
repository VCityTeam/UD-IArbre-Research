import rasterio
from rasterio.merge import merge
from rasterio.windows import Window
import numpy as np
import os

def rewrite_clean_tiff(input_path, output_path):
    """Réécrit un TIFF en blocs réguliers pour éviter les erreurs de strip."""
    with rasterio.open(input_path) as src:
        profile = src.profile.copy()
        profile.update(
            tiled=True,
            blockxsize=256,
            blockysize=256,
            compress="lzw"
        )

        with rasterio.open(output_path, "w", **profile) as dst:
            for ji, window in src.block_windows(1):
                data = src.read(1, window=window)
                dst.write(data, 1, window=window)

# Réécriture propre des 4 TIFF
tiles = ["HG_08m.tif", "HD_08m.tif", "BG_08m.tif", "BD_08m.tif"]
clean_tiles = []

for tif in tiles:
    clean_name = tif.replace(".tif", "_clean.tif")
    rewrite_clean_tiff(tif, clean_name)
    clean_tiles.append(clean_name)

# Fusion avec rasterio.merge
datasets = [rasterio.open(p) for p in clean_tiles]
mosaic, transform = merge(datasets)

out_meta = datasets[0].meta.copy()
out_meta.update({
    "height": mosaic.shape[1],
    "width": mosaic.shape[2],
    "transform": transform,
    "tiled": True,
    "blockxsize": 256,
    "blockysize": 256,
    "compress": "lzw"
})

# Export final
out_path = "Flair2023_08m.tif"
with rasterio.open(out_path, "w", **out_meta) as dst:
    dst.write(mosaic)

print("✔ Fusion terminée : Flair2023.tif créé avec succès")