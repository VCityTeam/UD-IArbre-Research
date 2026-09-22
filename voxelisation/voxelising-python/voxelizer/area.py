"""
Coordinate-driven, whole-area voxelization.
@ingroup t3_orchestr


Given an RGF93/CC46 (EPSG:3946) bounding box and a set of LAZ/LAS tiles, ``process_area``
builds a *single* ColumnStore covering the entire rectangle, with:

  * one shared grid origin for every tile, so indices are directly
    comparable across tile seams (no per-tile index space);
  * one vertical datum - a single ``z0`` (the area-wide header z-floor) - 
    so heights are comparable everywhere and the whole area is "one z";
  * exactly the content inside the coordinates - tiles are selected by
    extent-intersection (not origin-point), and points are clipped to the
    rectangle, so nothing outside the box leaks in and no overlapping edge
    tile is missed.

Reading strategy (matches the GUI's two modes):
  * a single dropped file goes through the whole-file ``laspy.read`` path
    (``voxelize_laz``) - see ``pipeline.process_single_tile``;
  * an area given by coordinates streams every tile with ``chunk_iterator``
    (``voxelize_laz_chunked``) so the raw-point buffer stays bounded (one
    chunk at a time) no matter how many tiles the box spans; the merged
    store itself still grows with the area (``--max-rss-mb`` and the
    sharding pipeline exist for that side).
"""

from __future__ import annotations
import logging
import math
import shutil
from pathlib import Path

from .data_structures import ColumnStore
from .io_laz import read_laz, read_laz_header
from .preflight import RamBudgetExceeded, check_rss_budget
from .voxelize import (
    DEFAULT_CHUNK_SIZE,
    voxelize,
    voxelize_laz_chunked,
)

from ._download_common import (
    download_one,
    load_values,
    select_tiles,
)

logger = logging.getLogger(__name__)

BBox = tuple[float, float, float, float]  # (xmin, ymin, xmax, ymax)


def _extent_intersects(mins, maxs, bbox: BBox) -> bool:
    """True if a tile's [x,y] extent overlaps the query rectangle at all.

    Non-finite bounds (NaN/inf from a corrupt header) return False. NaN
    comparisons are always False, so without this check a corrupt tile
    would slip past every "outside" test below, be kept, and later poison
    the shared z floor / voxel indices with NaN.
    """
    if not all(math.isfinite(float(v))
               for v in (mins[0], mins[1], maxs[0], maxs[1])):
        return False
    xmin, ymin, xmax, ymax = bbox
    return not (
        maxs[0] < xmin or mins[0] > xmax or
        maxs[1] < ymin or mins[1] > ymax
    )


def _gather_laz(paths_or_dir) -> list[Path]:
    """Accept a directory or an iterable of paths; return a sorted file list."""
    if isinstance(paths_or_dir, (str, Path)):
        p = Path(paths_or_dir)
        if p.is_dir():
            return sorted(list(p.glob("*.laz")) + list(p.glob("*.las")))
        raise NotADirectoryError(f"Not a directory: {p}")
    return sorted(Path(x) for x in paths_or_dir)


def select_area_tiles(laz_paths, bbox: BBox) -> tuple[list[Path], float | None]:
    """
    Keep tiles whose extent intersects ``bbox`` and return them together with
    the area-wide z floor (min header z over kept tiles) - the single ``z0``
    for the shared vertical datum. Uses headers only (no point decompression).

    @param laz_paths A directory (every ``*.laz`` / ``*.las`` in it, sorted)
        or an iterable of tile paths.
    @param bbox ``(xmin, ymin, xmax, ymax)`` in metres (RGF93/CC46
        EPSG:3946); a tile is kept when its header x/y extent overlaps it.
        Tiles with an unreadable header or non-finite bounds are skipped
        with a warning.
    @return ``(kept, z_floor)``: the kept paths in sorted order, and the
        minimum header z over them in metres, or None when no tile is kept.
    @throws NotADirectoryError when ``laz_paths`` is a single path that is
        not a directory (raised by ``_gather_laz``).
    """
    kept: list[Path] = []
    z_floor: float | None = None
    for path in _gather_laz(laz_paths):
        try:
            mins, maxs, _ = read_laz_header(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("  skipping unreadable header %s: %s", path.name, exc)
            continue
        # A corrupt header can carry NaN/inf bounds; a NaN z can poison the
        # z floor (a NaN FIRST tile sets z_floor = NaN and min(NaN, x) stays
        # NaN; min(x, NaN) returns x, so an already-finite floor survives)
        # -> NaN origin -> INT_MIN voxel indices on the int32 cast. Skip
        # such tiles loudly instead of silently keeping them.
        if not all(math.isfinite(float(v)) for v in (*mins, *maxs)):
            logger.warning("  skipping %s: non-finite header bounds "
                           "(corrupt file?)", path.name)
            continue
        if _extent_intersects(mins, maxs, bbox):
            kept.append(path)
            z_floor = mins[2] if z_floor is None else min(z_floor, float(mins[2]))
    return kept, z_floor


def process_area(
    bbox: BBox,
    laz_paths,
    *,
    cell_xy: float = 0.5,
    cell_z: float = 0.5,
    keep_classes: set[int] | None = None,
    use_chunks: bool = True,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    clip: bool = True,
    delete_laz: bool = False,
    max_rss_mb: float | None = None,
) -> ColumnStore:
    """
    Voxelize every tile intersecting ``bbox`` into one shared-origin store.

    @param bbox ``(xmin, ymin, xmax, ymax)`` in metres (RGF93/CC46 EPSG:3946).
    @param laz_paths A directory, or an iterable of .laz/.las paths (see
        select_area_tiles for the header-based selection).
    @param cell_xy Horizontal voxel size in metres (default 0.5).
    @param cell_z Vertical voxel size in metres (default 0.5).
    @param keep_classes When set, keep only points carrying these ASPRS
        codes; None (the default) keeps every class.
    @param use_chunks Stream each tile with ``chunk_iterator`` (True, the
        default, for the coordinate/area workflow) or read it whole (False).
    @param chunk_size Points per chunk when ``use_chunks`` is on (default
        ``DEFAULT_CHUNK_SIZE``).
    @param clip Drop points outside ``bbox`` so the result is exactly the
        rectangle (default True); False keeps whole intersecting tiles.
    @param delete_laz DESTRUCTIVE (default False). Unlink each source tile
        once it has been voxelized, so the points survive only in the
        returned store and a re-run has to fetch the tiles again. A tile
        that voxelizes to nothing is deleted only if its header reports
        zero points, since clipping and class filtering can empty the
        result while the file still holds data.
    @param max_rss_mb Optional RAM watchdog in MB (None, the default,
        disables it). After each tile, if this process's resident set
        exceeds the budget, the loop aborts cleanly by raising
        preflight.RamBudgetExceeded (carrying the partial store) instead
        of grinding into the pagefile.
    @return A single ColumnStore. Its origin is grid-aligned to ``cell_xy``
        at the box's lower corner, and its ``z_min`` is the one area-wide
        floor. When no tile intersects the box, or every intersecting tile
        is empty after clip/filter, the store is empty (z_min 0.0 in the
        first case, the area floor in the second).
    @throws RamBudgetExceeded when ``max_rss_mb`` is set and the resident
        set exceeds it after a tile; the exception carries the partial
        store and the progress counters.
    @throws NotADirectoryError when ``laz_paths`` is a single path that is
        not a directory (propagated from select_area_tiles).
    """
    kept, z_floor = select_area_tiles(laz_paths, bbox)
    if not kept:
        logger.warning("No tiles intersect the requested area %s.", bbox)
        x0 = math.floor(bbox[0] / cell_xy) * cell_xy
        y0 = math.floor(bbox[1] / cell_xy) * cell_xy
        return ColumnStore(x0, y0, 0.0, cell_xy, cell_z)

    # Shared origin: x/y aligned to the voxel grid at the box's lower corner
    # (keeps tile seams on column boundaries -> merges stay disjoint unions);
    # z is the single area-wide floor (one vertical datum for the whole area).
    x0 = math.floor(bbox[0] / cell_xy) * cell_xy
    y0 = math.floor(bbox[1] / cell_xy) * cell_xy
    z0 = float(z_floor)
    origin = (x0, y0, z0)
    clip_bbox = bbox if clip else None

    logger.info("Area %s: %d tile(s), shared origin=(%.1f, %.1f, %.3f), "
                "cell_xy=%.3f cell_z=%.3f, %s read",
                bbox, len(kept), x0, y0, z0, cell_xy, cell_z,
                "streamed" if use_chunks else "whole-file")

    area: ColumnStore | None = None
    for i, path in enumerate(kept, 1):
        logger.info("[%d/%d] %s", i, len(kept), path.name)
        if use_chunks:
            tile = voxelize_laz_chunked(
                path, cell_xy=cell_xy, cell_z=cell_z,
                keep_classes=keep_classes, origin=origin,
                chunk_size=chunk_size, clip_bbox=clip_bbox,
            )
        else:
            x, y, z, cls = read_laz(path)
            if clip_bbox is not None:
                xmin, ymin, xmax, ymax = clip_bbox
                m = (x >= xmin) & (x < xmax) & (y >= ymin) & (y < ymax)
                x, y, z, cls = x[m], y[m], z[m], cls[m]
            tile = voxelize(x, y, z, cls, cell_xy=cell_xy, cell_z=cell_z,
                            keep_classes=keep_classes, origin=origin)

        if not tile.columns:
            if delete_laz:
                # An empty *result* does not mean an empty *file*: clipping
                # and class filtering can drop every point while the source
                # still holds data outside the box or in other classes.
                # Only delete when the file itself has zero points.
                try:
                    _hmins, _hmaxs, n_file_points = read_laz_header(path)
                except Exception as exc:  # noqa: BLE001
                    n_file_points = -1
                    logger.warning("  could not re-read header of %s: %s",
                                   path.name, exc)
                if n_file_points == 0:
                    path.unlink()
                    logger.info("  deleted %s (file has 0 points)", path.name)
                else:
                    logger.info(
                        "  kept %s: empty after clip/filter, but the file "
                        "holds %s point(s)", path.name,
                        n_file_points if n_file_points >= 0 else "an unknown "
                        "number of")
            continue
        if area is None:
            area = tile
        else:
            area.merge(tile, inplace=True)
        if delete_laz:
            path.unlink()
            logger.info("  deleted %s", path.name)

        exceeded, rss = check_rss_budget(max_rss_mb)
        if exceeded:
            logger.error(
                "RAM budget exceeded after tile %d/%d: RSS %.0f MB > "
                "budget %.0f MB - aborting cleanly with a partial store.",
                i, len(kept), rss, max_rss_mb)
            raise RamBudgetExceeded(
                f"RSS {rss:.0f} MB exceeded budget {max_rss_mb:.0f} MB "
                f"after {i}/{len(kept)} tiles",
                partial_store=area, tiles_done=i, tiles_total=len(kept),
                rss_mb=rss, budget_mb=float(max_rss_mb))

    if area is None:  # every intersecting tile was empty after clip/filter
        return ColumnStore(x0, y0, z0, cell_xy, cell_z)
    logger.info("Area store: %d columns.", len(area.columns))
    return area


def process_area_streamed(
    bbox: BBox,
    json_file: Path,
    *,
    cell_xy: float = 0.5,
    cell_z: float = 0.5,
    keep_classes: set[int] | None = None,
    use_chunks: bool = True,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    clip: bool = True,
    templaz_dir: Path | str = "templaz",
    tile_pitch: int = 500,
    limit: int = 0,
    max_rss_mb: float | None = None,
) -> ColumnStore:
    """
    Stream tiles from a Grand Lyon inventory JSON: download each one to
    *templaz_dir*, voxelize immediately, delete the temp file, and merge
    into one shared-origin store.

    The vertical datum (z0) is taken from the first tile's header. z0 is a
    reference height, not a floor: a later, lower tile voxelizes to negative
    z indices (signed int32), so correctness does not depend on which tile
    comes first. Header z floors of adjacent tiles differ by ~4 m in the
    median but by up to ~150 m across the hilly corpus (see the Genstats
    corpus record, per_file_stats.csv).

    @param bbox ``(xmin, ymin, xmax, ymax)`` in metres (RGF93/CC46 EPSG:3946).
    @param json_file Path to the Grand Lyon inventory JSON
        (``{"values": [...]}``).
    @param cell_xy Horizontal voxel size in metres (default 0.5).
    @param cell_z Vertical voxel size in metres (default 0.5).
    @param keep_classes When set, keep only points carrying these ASPRS
        codes; None (the default) keeps every class.
    @param use_chunks Stream each downloaded tile with ``chunk_iterator``
        (True, the default) or read it whole (False).
    @param chunk_size Points per chunk when ``use_chunks`` is on (default
        ``DEFAULT_CHUNK_SIZE``).
    @param clip Drop points outside ``bbox`` so the result is exactly the
        rectangle (default True).
    @param templaz_dir Directory for the per-tile temp files (default
        ``"templaz"``, relative to the working directory). Created if
        missing; each downloaded tile is unlinked right after it is
        voxelized, and the WHOLE directory is removed with
        ``shutil.rmtree`` at the end of the run and on a RAM-budget abort,
        so it must not hold anything else.
    @param tile_pitch Tile size in metres (default 500); used to compute
        the exact origin-selection window (see
        ``_download_common.origin_window``).
    @param limit Max tiles to process (default 0 = no limit).
    @param max_rss_mb Optional RAM watchdog in MB (None, the default,
        disables it); see process_area(). On abort the temp directory is
        cleaned before raising.
    @return A single ColumnStore (same shape as process_area()). Its z_min
        is the first tile's header z floor (the z reference); an empty
        result is an empty store with z_min at that reference, or 0.0 when
        no tile was read.
    @throws ValueError when ``bbox`` is inverted (a min above its max).
    @throws RamBudgetExceeded when ``max_rss_mb`` is set and the resident
        set exceeds it after a tile; the temp directory is removed first
        and the exception carries the partial store.
    """
    if bbox[0] > bbox[2] or bbox[1] > bbox[3]:
        raise ValueError(f"Invalid bbox: min must be <= max (got {bbox}).")

    json_file = Path(json_file)

    # 1. Load inventory and select tiles by origin (expanded bbox).
    values = load_values(json_file)
    from ._download_common import origin_window
    selected = select_tiles(values, *origin_window(bbox, tile_pitch))
    if limit > 0:
        selected = selected[:limit]

    if not selected:
        logger.warning("No tiles found in the inventory for area %s.", bbox)
        x0 = math.floor(bbox[0] / cell_xy) * cell_xy
        y0 = math.floor(bbox[1] / cell_xy) * cell_xy
        return ColumnStore(x0, y0, 0.0, cell_xy, cell_z)

    # 2. Shared x/y origin (grid-aligned to bbox lower corner).
    x0 = math.floor(bbox[0] / cell_xy) * cell_xy
    y0 = math.floor(bbox[1] / cell_xy) * cell_xy
    clip_bbox = bbox if clip else None

    templaz = Path(templaz_dir)
    templaz.mkdir(parents=True, exist_ok=True)
    logger.info("Streaming %d tile(s) via %s ...", len(selected), templaz)

    area: ColumnStore | None = None
    z0: float | None = None

    for i, tile_info in enumerate(selected, 1):
        url = tile_info["url"].strip()
        name = Path(url).name
        dest = templaz / name
        logger.info("[%d/%d] downloading %s ...", i, len(selected), name)

        _name, _status, err = download_one(url, dest)
        if err:
            logger.warning("  skipped %s: %s", name, err)
            continue

        # 3. Read header for the shared z reference (first tile sets it).
        # It is a reference height, not a floor: see this function's
        # docstring - a later, lower tile simply voxelizes to negative z.
        # Same skip-and-continue policy as select_area_tiles: a truncated or
        # corrupt download must cost one tile, not the whole streamed run.
        try:
            mins, _maxs, _n = read_laz_header(dest)
        except Exception as exc:  # noqa: BLE001
            logger.warning("  skipped %s: unreadable header: %s", name, exc)
            dest.unlink(missing_ok=True)
            continue
        if not all(math.isfinite(float(v)) for v in (*mins, *_maxs)):
            logger.warning("  skipped %s: non-finite header bounds "
                           "(corrupt file?)", name)
            dest.unlink(missing_ok=True)
            continue
        if z0 is None:
            z0 = float(mins[2])
            origin = (x0, y0, z0)
            logger.info("  z reference = %.3f (from first tile)", z0)

        # 4. Voxelize.
        if use_chunks:
            tile = voxelize_laz_chunked(
                dest, cell_xy=cell_xy, cell_z=cell_z,
                keep_classes=keep_classes, origin=origin,
                chunk_size=chunk_size, clip_bbox=clip_bbox,
            )
        else:
            x, y, z, cls = read_laz(dest)
            if clip_bbox is not None:
                xmin, ymin, xmax, ymax = clip_bbox
                m = (x >= xmin) & (x < xmax) & (y >= ymin) & (y < ymax)
                x, y, z, cls = x[m], y[m], z[m], cls[m]
            tile = voxelize(x, y, z, cls, cell_xy=cell_xy, cell_z=cell_z,
                            keep_classes=keep_classes, origin=origin)

        # 5. Delete temp file immediately.
        dest.unlink(missing_ok=True)

        if not tile.columns:
            continue
        if area is None:
            area = tile
        else:
            area.merge(tile, inplace=True)

        exceeded, rss = check_rss_budget(max_rss_mb)
        if exceeded:
            shutil.rmtree(templaz, ignore_errors=True)
            logger.error(
                "RAM budget exceeded after tile %d/%d: RSS %.0f MB > "
                "budget %.0f MB - aborting cleanly with a partial store.",
                i, len(selected), rss, max_rss_mb)
            raise RamBudgetExceeded(
                f"RSS {rss:.0f} MB exceeded budget {max_rss_mb:.0f} MB "
                f"after {i}/{len(selected)} tiles",
                partial_store=area, tiles_done=i, tiles_total=len(selected),
                rss_mb=rss, budget_mb=float(max_rss_mb))

    # 6. Clean up the temp directory.
    shutil.rmtree(templaz, ignore_errors=True)

    if area is None:
        logger.warning("All streamed tiles were empty after clip/filter.")
        return ColumnStore(x0, y0, z0 or 0.0, cell_xy, cell_z)

    logger.info("Streamed area store: %d columns.", len(area.columns))
    return area
