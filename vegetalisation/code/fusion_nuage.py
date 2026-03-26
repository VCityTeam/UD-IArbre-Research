import os
import glob
import numpy as np
import rasterio
from rasterio.transform import from_origin
import laspy
from tqdm import tqdm

def create_mns_mnt_class(path_in, resolution, out_mns, out_mnt, out_class):
    """
    Crée simultanément :
        - MNS (Zmax)
        - MNT (Zmin)
        - raster de CLASSE basée sur le point ayant le Zmax
    Le tout dans un seul parcours des points → très performant.
    """

    # Lecture du LAZ
    las = laspy.read(path_in)

    x = las.x
    y = las.y
    z = las.z
    cls = las.classification

    # Étendue
    xmin, ymin = x.min(), y.min()
    xmax, ymax = x.max(), y.max()

    width  = int(np.ceil((xmax - xmin) / resolution)) + 1
    height = int(np.ceil((ymax - ymin) / resolution)) + 1

    transform = from_origin(xmin, ymax, resolution, resolution)

    # Création images 
    mns = np.full((height, width), np.nan)
    mnt = np.full((height, width), np.nan)
    class_r = np.full((height, width), -1, dtype=np.int16)  # -1 = nodata

    # Indices pixel par points LAZ
    ix = ((x - xmin) / resolution).astype(int)
    iy = ((ymax - y) / resolution).astype(int)

    # Boucle pour selectionner pour chaque pixel sa valeur en fonction des points LAZ
    for xx, yy, zz, cc in tqdm(zip(ix, iy, z, cls),
                               desc="Traitement simultané MNS/MNT/Classes"):
        # MNT
        if np.isnan(mnt[yy, xx]) or zz < mnt[yy, xx]:
            mnt[yy, xx] = zz

        # MNS + classe associée
        if np.isnan(mns[yy, xx]) or zz > mns[yy, xx]:
            mns[yy, xx] = zz
            class_r[yy, xx] = cc

    # Sauvegarde MNS
    with rasterio.open(
        out_mns, "w", driver="GTiff",
        height=height, width=width, count=1,
        dtype=mns.dtype, crs=las.header.parse_crs(),
        transform=transform, nodata=np.nan
    ) as dst:
        dst.write(mns, 1)
    dst.close()
    # Sauvegarde MNT
    with rasterio.open(
        out_mnt, "w", driver="GTiff",
        height=height, width=width, count=1,
        dtype=mnt.dtype, crs=las.header.parse_crs(),
        transform=transform, nodata=np.nan
    ) as dst:
        dst.write(mnt, 1)
    dst.close()
    # Sauvegarde CLASS
    with rasterio.open(
        out_class, "w", driver="GTiff",
        height=height, width=width, count=1,
        dtype=class_r.dtype, crs=las.header.parse_crs(),
        transform=transform, nodata=-1
    ) as dst:
        dst.write(class_r, 1)
    dst.close()
    print("MNS / MNT / CLASS créés.")


def create_object_height_map(mnt_path, mns_path, out_path):
    """
    Crée une carte de hauteur des objets à partir d'un MNT et d'un MNS.
    Règles :
      - si MNT et MNS existent -> output = MNS - MNT
      - si MNT est NaN -> output = MNS
      - si MNS est NaN -> output = MNT
      - si les deux sont NaN -> output = 0
    """

    # Lecture MNT
    with rasterio.open(mnt_path) as src_mnt:
        mnt = src_mnt.read(1).astype(float)
        mnt_profile = src_mnt.profile

    # Lecture MNS
    with rasterio.open(mns_path) as src_mns:
        mns = src_mns.read(1).astype(float)

    # Vérification tailles
    if mnt.shape != mns.shape:
        raise ValueError("Les dimensions du MNT et du MNS ne correspondent pas.")

    # Calcul des hauteurs
    # Différence classique
    height = mns - mnt

    # Cas particuliers avec les valeurs nulles
    only_mns = np.isnan(mnt) & ~np.isnan(mns)
    only_mnt = np.isnan(mns) & ~np.isnan(mnt)
    both_nan  = np.isnan(mnt) & np.isnan(mns)

    # Application règles
    height[only_mns] = mns[only_mns]
    height[only_mnt] = mnt[only_mnt]
    height[both_nan] = 0

    # Écriture GeoTIFF
    mnt_profile.update(
        dtype="float32",
        nodata=np.nan,
        compress="lzw"
    )

    with rasterio.open(out_path, "w", **mnt_profile) as dst:
        dst.write(height.astype("float32"), 1)
    dst.close()

    print(f"Carte des hauteurs enregistrée : {out_path}")


def main():
    laz_folder = "laz_tiles/"
    height_folder = "lidar_data_processed/heights"
    class_folder = "lidar_data_processed/class"
    out_folder = "lidar_data_processed/mns_mnt"
    resolution = 0.8

    os.makedirs(height_folder, exist_ok=True)
    os.makedirs(class_folder, exist_ok=True)
    os.makedirs(out_folder, exist_ok=True)

    laz_files = glob.glob(os.path.join(laz_folder, "*.laz"))

    print(f"Nombre de fichiers LAZ trouvés : {len(laz_files)}\n")

    for laz_path in tqdm(laz_files, desc="Traitement des tuiles LAZ"):

        base = os.path.splitext(os.path.basename(laz_path))[0]

        out_mns   = os.path.join(out_folder,  f"{base}_mns.tif")
        out_mnt   = os.path.join(out_folder,  f"{base}_mnt.tif")
        out_class = os.path.join(class_folder, f"{base}_class_08m.tif")
        out_height = os.path.join(height_folder, f"{base}_height_08.tif")

        # SKIP si déjà faits
        if os.path.exists(out_height) and os.path.exists(out_class):
            print(f"→ SKIP : {base} déjà traité.")
            continue

        print(f"\n--- {base} ---")

        print(f"Étape 1 : créer MNS + MNT + CLASS")
        create_mns_mnt_class(laz_path, resolution, out_mns, out_mnt, out_class)

        print(f"Étape 2 : calcul HEIGHT")
        create_object_height_map(out_mnt, out_mns, out_height)

        print(f" Hauteur : {out_height}")
        print(f" Classe  : {out_class}")

    print("\n Processus terminé ")

if __name__ == "__main__":
    main()