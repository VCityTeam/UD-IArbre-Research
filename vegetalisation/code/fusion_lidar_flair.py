import numpy as np
import rasterio
import rasterio


# Fonction de padding
def pad_to_match(arr, target_shape):
    h, w = arr.shape
    th, tw = target_shape

    if h > th or w > tw:
        raise ValueError("L'image est plus grande que la taille cible.")

    if (h == th) and (w == tw):
        return arr

    return np.pad(
        arr,
        ((0, th - h), (0, tw - w)),
        mode="constant",
        constant_values=np.nan
    )


# Lecture d'un TIFF + renvoi array + profil
def load_tif(path):
    with rasterio.open(path) as ds:
        arr = ds.read(1)
        profile = ds.profile
    return arr, profile



# Sauvegarde d’un TIFF
def save_tif(path, arr, profile):
    profile = profile.copy()
    profile.update(dtype='float32', count=1)
    with rasterio.open(path, 'w', **profile) as dst:
        dst.write(arr.astype("float32"), 1)


# Création de la carte de végétation
def create_vegetation_map(class_lidar_map, height_lidar_map, vege_mask, flair_vege, modif_flair=False, keep_class_lidar1=False):
    """
    class_lidar_map : carte de classes (int)
    height_lidar_map : carte de hauteurs (float, peut contenir NaN)
    vege_mask: mask pour filtrer le lidar, pixels invalides = 255 
    flair_vege : carte produite par flair
    modif_flair: savoir si on veut modifier les résultats sur les valeurs des classes de végétation renvoyé par Flair avec la hauteur du lidar défaut = Faux
    keep_class_lidar1: savoir si on veut prendre ne compte la classe 1 du liadr qu icorrespond aux point lidar non attribué qui petut queques fois contenir des végétaux. défaut = Faux

    Règles :
        - On garde uniquement les classes végétation du Lidar : 3, 4, 5, 8
        - On garde la classe 1 du Lidar uniquement si veg_mask != 255
        - Attribution des classes finales selon hauteur :
            0 = herbacé (<0.75 m)
            1 = buissons (1.5–5 m)
            2 = arbres (>=5 m)
    """

    vegetation_classes_lidar = [3, 4, 5, 8]

    # Masque de pixels
    keep_veg = np.isin(class_lidar_map, vegetation_classes_lidar)
    keep_class1_lidar = (class_lidar_map == 1) & (vege_mask != 255)

    if keep_class_lidar1 :
        keep = keep_veg | keep_class1_lidar
    else : 
        keep = keep_veg

    out_lidar = np.full_like(class_lidar_map, np.nan, dtype=np.float32)

    # herbacé <0.75
    out_lidar[keep & (height_lidar_map < 0.75)] = 0
    # buissons 0.75–5
    out_lidar[keep & (height_lidar_map >= 0.75) & (height_lidar_map < 5)] = 1
    # arbres >=5
    out_lidar[keep & (height_lidar_map >= 5)] = 2

    out_flair = flair_vege
    keep_flair = (vege_mask != 255)
    if modif_flair:

        out_flair = np.full_like(flair_vege, np.nan, dtype=np.float32)

        # herbacé <0.75
        out_flair[keep_flair & (height_lidar_map < 0.75)] = 0
        # buissons 0.75–5
        out_flair[keep_flair & (height_lidar_map >= 0.75) & (height_lidar_map < 5)] = 1
        # arbres >=5
        out_flair[keep_flair & (height_lidar_map >= 5)] = 2

    return out_lidar, out_flair



# Remap simple (classe 3 -> 2)
def remap_classes(arr):
    out = arr.copy()
    out[out == 3] = 2
    return out


# Fusion
def fuse_maps(Lidar, Flair, use_flair_everywhere=True):
    """
    Fusionne deux cartes avec priorité à Lidar.

    - Lidar : valeurs 0,1,2 sont prioritaires
    - Flair : utilisée selon la règle choisie

    Paramètre :
    - use_flair_everywhere (bool, défaut=True)
        False -> on utilise Flair UNIQUEMENT là où Flair == 0
        True  -> on utilise Flair partout où Lidar n'est pas dans [0,1,2]
    """

    out = Lidar.copy()

    # Pixels où Lidar n'est pas valide
    lidar_invalid = ~np.isin(Lidar, [0, 1, 2])

    if use_flair_everywhere:
        # Comportement classique
        mask = lidar_invalid
    else:
        # Flair seulement si Flair == 0
        mask = lidar_invalid & (Flair == 0)

    out[mask] = Flair[mask]

    return out


# Main
def main(class_map_path, height_map_path, veg_mask_path, second_map_path, out_dir):
    # Lecture des 4 fichiers
    class_map, profile = load_tif(class_map_path)
    height_map, _ = load_tif(height_map_path)
    veg_mask, _ = load_tif(veg_mask_path)
    second_map, _ = load_tif(second_map_path)

    # Harmonisation des tailles
    target_shape = (
        max(class_map.shape[0], height_map.shape[0], veg_mask.shape[0], second_map.shape[0]),
        max(class_map.shape[1], height_map.shape[1], veg_mask.shape[1], second_map.shape[1])
    )

    class_map  = pad_to_match(class_map,  target_shape)
    height_map = pad_to_match(height_map, target_shape)
    veg_mask   = pad_to_match(veg_mask,   target_shape)
    second_map = pad_to_match(second_map, target_shape)

    # Création carte végétation
    veg_map_lidar, vege_map_flair = create_vegetation_map(class_map, height_map, veg_mask, second_map, False, False)
    save_tif(f"{out_dir}/vegetation_map_08m.tif", veg_map_lidar, profile)

    # Remap classes (3 -> 2)
    remapped = remap_classes(vege_map_flair)
    save_tif(f"{out_dir}/second_remapped_08m.tif", remapped, profile)

    # Fusion (priorité végétation_map)
    fused = fuse_maps(veg_map_lidar, vege_map_flair)
    save_tif(f"{out_dir}/final_fused_08m.tif", fused, profile)

    print("Tous les fichiers ont été générés dans :", out_dir)


if __name__ == "__main__":
    main(
        class_map_path="orthophotos_08m/class_08m.tif",
        height_map_path="orthophotos_08m/heights_08m.tif",
        veg_mask_path="flair_predictions/vege_correct/Flair2023_08m.tif",
        second_map_path="flair_predictions/vege_correct/Flair2023_08m.tif",
        out_dir="results"
    )
