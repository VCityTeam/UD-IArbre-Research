import json
import requests
import os
import time
import rasterio
from rasterio.enums import Resampling

JSON_FILE = "ortho.json"        # Fichier Json pur récupérer les tuiles disponibles sur datagrandlyon.fr
OUTPUT_DIR = "orthophotos_08m"   # Dossier où les orthophotos misent à la résolution de 1 pixel par mètre sont stockées.
TEMP_DIR = "temp_5cm"           # Dossier temporaire pour les tuiles à très haute résolution avant le changement de résolution

# Intervalle fourni par l'utilisateur pour la récupération des tuiles
XMIN_START = 1841500
XMIN_END   = 1852000
YMIN_START = 5169000
YMIN_END   = 5179000

# Facteur de réduction de résolution 5 cm -> 1 m
SCALE = 0.8 / 0.05 


# Lecture du JSON Datagrandlyon
with open(JSON_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)

tiles = data["values"]

# Filtrage
selected_tiles = [
    tile for tile in tiles
    if XMIN_START <= tile["x_min"] <= XMIN_END
    and YMIN_START <= tile["y_min"] <= YMIN_END
]

if not selected_tiles:
    print("Aucune tuile trouvée dans cet intervalle.")
    exit()

print(f"{len(selected_tiles)} tuiles trouvées.\n")


# Téléchargement + Conversion 5cm -> 1mm de résolution
for tile in selected_tiles:

    url = tile["url"]
    filename = url.split("/")[-1]
    output_path = os.path.join(OUTPUT_DIR, filename.replace(".tif", "_08m.tif"))

    # Vérifier si l'image interpolée existe déjà
    if os.path.exists(output_path):
        print(f"Déjà traité, on passe : {output_path}\n")
        continue

    # Chemin temporaire pour l'image 5cm
    temp_path = os.path.join(TEMP_DIR, filename)

    print(f"Téléchargement : {url}")
    t0 = time.time()

    # Télécharger l'image 5 cm
    response = requests.get(url, stream=True)
    response.raise_for_status()

    with open(temp_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    t1 = time.time()
    print(f"Téléchargé en {t1 - t0:.2f} s")

    # Conversion 5 cm → 1 m
    print(f"Rééchantillonnage à 1m")

    with rasterio.open(temp_path) as src:
        new_width = int(src.width / SCALE)
        new_height = int(src.height / SCALE)

        transform = src.transform * src.transform.scale(
            (src.width / new_width), (src.height / new_height)
        )

        data = src.read(
            out_shape=(src.count, new_height, new_width),
            resampling=Resampling.bilinear # Adapté pour des images orthophotos RGB 
        )

        # Pour garder les mêmes coordonées géographiques
        meta = src.meta.copy()
        meta.update({
            "height": new_height,
            "width": new_width,
            "transform": transform
        })

    # Écrire l'image 0.8m
    with rasterio.open(output_path, "w", **meta) as dst:
        dst.write(data)

    t2 = time.time()
    print(f"Image convertie : {output_path}")
    print(f"Temps conversion : {t2 - t1:.2f} s")

    # Supprimer l'image 5 cm
    os.remove(temp_path)
    print(f"Image 5cm supprimée : {temp_path}\n")


print("\n Processus terminé")
