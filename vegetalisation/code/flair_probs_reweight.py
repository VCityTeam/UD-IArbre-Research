import numpy as np
import rasterio
import argparse

def reweight_and_filter(input_tif, output_tif):
    weights = {
        0: 1,
        1: 1,
        2: 1,
        3: 1,
        4: 1,
        5: 1,
        6: 1,
        7: 1,
        9: 1,
        10: 1,
        11: 1,
        15: 1,
        16: 1,
        17: 1,
        18: 1,


        8: 1,     # herbaceous vegetation
        12: 1,   # deciduous
        13: 1,    # coniferous POUR RGB
        14: 1,  # brushwood
    }

    # Mapping final des classes
    mapping = {
        8: 0,    # herbaceous vegetation -> 0
        14: 1,   # brushwood            -> 1
        12: 2,   # deciduous            -> 2
        13: 3,   # coniferous           -> 3
    }
    ignore_value = 255

    # Lecture des probabilités
    with rasterio.open(input_tif) as src:
        probs = src.read().astype(np.float32)  # shape: (n_classes, H, W)
        meta = src.meta.copy()
        print(probs.shape)

    # Repondération
    for cls_id, w in weights.items():    
        probs[cls_id] *= w
        

    # Prédiction
    pred = np.argmax(probs, axis=0).astype(np.uint8)
     

    # Filtrage et remapping
    filtered = np.full_like(pred, ignore_value, dtype=np.uint8)
    for orig_class, new_class in mapping.items():
        filtered[pred == orig_class] = new_class

    # Sauvegarde
    meta.update({
        "count": 1,
        "dtype": "uint8",
        "nodata": ignore_value
    })
    with rasterio.open(output_tif, "w", **meta) as dst:
        dst.write(filtered, 1)

    print(f"Image repondérée et filtrée sauvegardée dans : {output_tif}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Repondérer les probabilités, filtrer et remapper les classes")
    parser.add_argument("--input", "-i", type=str, required=True, help="Chemin vers le GeoTIFF de probabilités en entrée")
    parser.add_argument("--output", "-o", type=str, required=True, help="Chemin vers le GeoTIFF de sortie")
    args = parser.parse_args()

    reweight_and_filter(args.input, args.output)
