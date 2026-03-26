import json
import requests
import os
import time

# Chemins
JSON_FILE = "nuage.json"  
OUTPUT_DIR = "laz_tiles"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Intervalle fourni par l'utilisateur
XMIN_START = 1841500
XMIN_END   = 1852000
YMIN_START = 5169000
YMIN_END   = 5179000


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

print(f"{len(selected_tiles)} tuiles LAZ trouvées.\n")


# Téléchargement des LAZ
for tile in selected_tiles:

    url = tile["url"].strip()  # retire espace final dans le JSON
    filename = url.split("/")[-1]
    output_path = os.path.join(OUTPUT_DIR, filename)

    # Vérifier si déjà téléchargé
    if os.path.exists(output_path):
        print(f"Déjà téléchargé, on saute : {output_path}")
        continue

    print(f"Téléchargement : {url}")
    t0 = time.time()

    # Télécharger le LAZ
    response = requests.get(url, stream=True)
    response.raise_for_status()

    with open(output_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    t1 = time.time()
    print(f"Téléchargé en {t1 - t0:.2f} s → {output_path}\n")


print("\n Téléchargement terminé : tous les nuages de points LAZ sont prêts.")
