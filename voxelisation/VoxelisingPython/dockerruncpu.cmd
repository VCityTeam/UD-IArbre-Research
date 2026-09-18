docker compose run --rm voxelisation python -m voxelizer single inputs/laz/18340_51775.laz --output-dir outputs/Run1/single
rem Crash-resilient sharded example (resume + per-tile isolation):
rem docker compose run --rm voxelisation python -m voxelizer.area_cli area --xmin 1831000 --ymin 5167000 --xmax 1834000 --ymax 5170000 --laz-dir inputs/laz --output-dir outputs/SmallArea/area_output --cell-xy 1.0 --cell-z 1.0 --shard --resume-shards --isolate-tiles
