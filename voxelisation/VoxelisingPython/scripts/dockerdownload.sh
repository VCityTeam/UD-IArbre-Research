#!/usr/bin/env bash
# The Linux/macOS twin of the package root's dockerdownload.cmd.
# Tiles are selected by their ORIGIN: a tile is fetched when its x_min falls
# in [--xmin-start, --xmin-end] and its y_min in [--ymin-start, --ymin-end].
# --json defaults to the Grand Lyon inventory under inputs/quickhelpers/,
# which the compose file mounts into the container, so the flag can be
# omitted. Add --dry-run to list what would be fetched, or --limit N to cap
# the count.
set -e
cd "$(dirname "$0")/.."

docker compose run --rm voxelisation python -m voxelizer.download_laz --xmin-start 1831000 --xmin-end 1832000 --ymin-start 5175000 --ymin-end 5176000 --laz-dir inputs/laz

# Orthophoto dalles for the class-over-ortho map, same bbox flags:
# docker compose run --rm voxelisation python -m voxelizer.download_orthos --xmin-start 1831000 --xmin-end 1832000 --ymin-start 5175000 --ymin-end 5176000 --ortho-dir inputs/ortho
