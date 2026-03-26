import rasterio
from rasterio.merge import merge
import glob
import os

INPUT_DIR = "lidar_data_processed/heights" # Dossier contenant les images 1m interpolées
OUTPUT_FILE = "heights_08m.tif" # fichier de fusion sortie

# Cherche tous les TIFF du répertoire
tif_files = glob.glob(os.path.join(INPUT_DIR, "*.tif"))

if len(tif_files) == 0:
    print("Aucun fichier TIFF trouvé.")
    exit()

print(f"{len(tif_files)} images trouvées pour la mosaïque.")
print("Fusion en cours.. (ça peut prendre du temps)\n")

dataset = []

# Ajout des tiff dans un dataset
for f in tif_files:
    print(f"Ajout : {f}")
    dataset.append(rasterio.open(f))

# Fusionner avec la fonction merge de rasterio
mosaic, out_transform = merge(dataset)

# Utiliser les métadonnées du 1er fichier comme base
out_meta = dataset[0].meta.copy()
out_meta.update({
    "height": mosaic.shape[1],
    "width": mosaic.shape[2],
    "transform": out_transform
})

print("\n Écriture du fichier final")

# Écrire le fichier final
with rasterio.open(OUTPUT_FILE, "w", **out_meta) as dest:
    dest.write(mosaic)

print(f"\n Mosaïque créée : {OUTPUT_FILE}")
