from __future__ import annotations

import argparse
from pathlib import Path

import rasterio


def compute_band_stats(raster_path: Path, channels: list[int]) -> tuple[list[float], list[float]]:
    """Calcule means/stds par bande, dans l'ordre de `channels` (indices 1-based),
    sur les pixels valides (> 0), pour la normalisation FLAIR-HUB.
    """
    means: list[float] = []
    stds: list[float] = []
    with rasterio.open(raster_path) as src:
        if src.count < max(channels):
            raise ValueError(
                f"{raster_path} n'a que {src.count} bande(s), "
                f"mais la bande {max(channels)} est demandée dans channels={channels}."
            )
        for band_index in channels:
            band = src.read(band_index).astype(float)
            valid = band[band > 0]
            if valid.size == 0:
                raise ValueError(
                    f"Bande {band_index} de {raster_path} ne contient aucun pixel valide (>0)."
                )
            means.append(round(float(valid.mean()), 2))
            stds.append(round(float(valid.std()), 2))
    return means, stds


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calcule means/stds par bande pour la normalisation FLAIR-HUB."
    )
    parser.add_argument("--input", type=Path, required=True, help="Mosaïque orthophoto (GeoTIFF).")
    parser.add_argument(
        "--channels",
        type=int,
        nargs="+",
        default=[1, 2, 3],
        help=(
            "Indices de bandes (1-based), dans l'ordre attendu par la config FLAIR "
            "(ex: 1 2 3 pour RGB, 4 1 2 pour IR-Rouge-Vert)."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    means, stds = compute_band_stats(args.input, args.channels)
    print(f"channels: {args.channels}")
    print(f"means: {means}")
    print(f"stds: {stds}")


if __name__ == "__main__":
    main()