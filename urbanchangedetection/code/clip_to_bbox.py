from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject

RESAMPLING_METHODS = {
    "nearest": Resampling.nearest,
    "bilinear": Resampling.bilinear,
    "cubic": Resampling.cubic,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Découpe/rééchantillonne un raster sur une emprise et une grille de pixels "
            "exactes (xmin/xmax/ymin/ymax + résolution). Utile pour forcer deux rasters "
            "de dates différentes (ex: orthophoto 2018 vs 2023) à tomber pile sur les "
            "mêmes coordonnées, indépendamment du dallage des données sources."
        )
    )
    parser.add_argument("--input", type=Path, required=True, help="Raster source (.tif) à découper.")
    parser.add_argument("--output", type=Path, required=True, help="Raster de sortie (.tif).")
    parser.add_argument("--xmin", type=float, required=True)
    parser.add_argument("--xmax", type=float, required=True)
    parser.add_argument("--ymin", type=float, required=True)
    parser.add_argument("--ymax", type=float, required=True)
    parser.add_argument(
        "--resolution",
        type=float,
        default=None,
        help=(
            "Taille de pixel cible, mêmes unités que le CRS (ex: 0.5 pour 0.5 m/px). "
            "Par défaut, la résolution du raster source est réutilisée."
        ),
    )
    parser.add_argument(
        "--resampling",
        choices=sorted(RESAMPLING_METHODS),
        default="bilinear",
        help=(
            "Méthode de rééchantillonnage. Utiliser 'nearest' pour des rasters "
            "catégoriels (ex: classes LiDAR), 'bilinear' pour des rasters continus "
            "(hauteur, probabilités, orthophoto)."
        ),
    )
    parser.add_argument(
        "--nodata",
        type=float,
        default=None,
        help="Valeur nodata en sortie. Par défaut, celle du raster source (ou NaN/0 si absente).",
    )
    parser.add_argument(
        "--dst-crs",
        default=None,
        help="CRS de sortie (ex: EPSG:2154). Par défaut, le CRS du raster source.",
    )
    return parser.parse_args()


def build_target_grid(
    *, xmin: float, ymin: float, xmax: float, ymax: float, resolution: float
) -> tuple[rasterio.Affine, int, int]:
    """Construit un transform + largeur/hauteur pixel-exacts pour une emprise donnée.

    C'est cette fonction qui garantit que deux appels avec les mêmes xmin/ymin/xmax/
    ymax/resolution produisent exactement la même grille (même origine, même nombre
    de pixels), quel que soit le dallage du raster source.
    """
    if xmax <= xmin or ymax <= ymin:
        raise ValueError("xmax doit être strictement supérieur à xmin (idem pour ymax/ymin).")
    if resolution <= 0:
        raise ValueError("resolution doit être strictement positive.")

    width = max(1, round((xmax - xmin) / resolution))
    height = max(1, round((ymax - ymin) / resolution))
    transform = from_origin(xmin, ymax, resolution, resolution)
    return transform, width, height


def default_nodata_for_dtype(dtype: str) -> float:
    if np.issubdtype(np.dtype(dtype), np.floating):
        return float("nan")
    return 0


def main() -> None:
    args = parse_args()
    if not args.input.exists():
        raise FileNotFoundError(f"Raster source introuvable: {args.input}")

    resampling = RESAMPLING_METHODS[args.resampling]

    with rasterio.open(args.input) as src:
        resolution = args.resolution if args.resolution is not None else abs(src.transform.a)
        dst_crs = args.dst_crs or src.crs
        nodata = args.nodata if args.nodata is not None else src.nodata
        if nodata is None:
            nodata = default_nodata_for_dtype(src.dtypes[0])
        band_count = src.count

        dst_transform, width, height = build_target_grid(
            xmin=args.xmin, ymin=args.ymin, xmax=args.xmax, ymax=args.ymax, resolution=resolution
        )

        profile = src.profile.copy()
        profile.update(
            transform=dst_transform,
            width=width,
            height=height,
            crs=dst_crs,
            nodata=nodata,
        )

        args.output.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(args.output, "w", **profile) as dst:
            for band_index in range(1, src.count + 1):
                dst_array = np.full((height, width), nodata, dtype=profile["dtype"])
                reproject(
                    source=rasterio.band(src, band_index),
                    destination=dst_array,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=dst_transform,
                    dst_crs=dst_crs,
                    src_nodata=src.nodata,
                    dst_nodata=nodata,
                    resampling=resampling,
                )
                dst.write(dst_array, band_index)

    print(f"Raster découpé/aligné écrit dans: {args.output}")
    print(f"  emprise: xmin={args.xmin} ymin={args.ymin} xmax={args.xmax} ymax={args.ymax}")
    print(f"  résolution: {resolution}")
    print(f"  taille: {width}x{height} px, {band_count} bande(s)")


if __name__ == "__main__":
    main()