cd /d "%~dp0.."
docker compose -f docker\docker-compose.yml run --rm voxelisation python -m voxelizer.download_laz --xmin-start 1831000 --xmin-end 1832000 --ymin-start 5175000 --ymin-end 5176000 --laz-dir inputs/laz
rem Tiles are selected by their ORIGIN: a tile is fetched when its x_min falls in
rem [--xmin-start, --xmin-end] and its y_min in [ymin-start, --ymin-end].
rem --json defaults to the Grand Lyon inventory under inputs/quickhelpers/, which
rem the compose file mounts into the container, so the flag can be omitted.
rem Add --dry-run to list what would be fetched, or --limit N to cap the count.
rem
rem Orthophoto dalles for the class-over-ortho map, same bbox flags:
rem docker compose -f docker\docker-compose.yml run --rm voxelisation python -m voxelizer.download_orthos --xmin-start 1831000 --xmin-end 1832000 --ymin-start 5175000 --ymin-end 5176000 --ortho-dir inputs/ortho
