"""
Utilitaire : recopie le CRS + transform d'un raster de référence géoréférencé
(l'orthophoto d'entrée utilisée pour l'inférence) vers un raster class_prob
qui n'a pas de géoréférencement (CRS=None), en supposant que les deux rasters
couvrent la même emprise avec la même résolution/dimensions.

Usage:
    python fix-georef-class_prob.py `
        --src-ref ./chemin/vers/orthophoto_2023.tif `
        --target ./workdir/.../class_prob_2023.tif `
        --out ./workdir/.../class_prob_2023_georef.tif
"""
from __future__ import annotations

import argparse
from pathlib import Path

import rasterio


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recopie CRS+transform d'un raster de référence vers un raster non géoréférencé.")
    parser.add_argument("--src-ref", type=Path, required=True, help="Raster source géoréférencé (ex: orthophoto d'entrée de l'inférence)")
    parser.add_argument("--target", type=Path, required=True, help="Raster à corriger (ex: class_prob sans CRS)")
    parser.add_argument("--out", type=Path, required=True, help="Chemin de sortie du raster corrigé")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    with rasterio.open(args.src_ref) as ref:
        ref_crs = ref.crs
        ref_transform = ref.transform
        ref_width, ref_height = ref.width, ref.height

    with rasterio.open(args.target) as target:
        if (target.width, target.height) != (ref_width, ref_height):
            raise ValueError(
                f"Dimensions incompatibles : référence {(ref_width, ref_height)} "
                f"vs cible {(target.width, target.height)}. "
                "Le géoréférencement ne peut pas être recopié directement — "
                "vérifie s'il y a un padding/crop entre l'orthophoto et la sortie d'inférence."
            )
        data = target.read()
        profile = target.profile.copy()

    profile.update(crs=ref_crs, transform=ref_transform)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(args.out, "w", **profile) as dst:
        dst.write(data)

    print(f"Raster géoréférencé écrit : {args.out}")
    print(f"CRS: {ref_crs}")
    print(f"Transform: {ref_transform}")


if __name__ == "__main__":
    main()