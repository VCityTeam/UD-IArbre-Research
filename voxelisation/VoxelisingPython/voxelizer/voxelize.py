"""
@ingroup t2_algos


The actual voxelization: turn point arrays into a column-compressed
ColumnStore. The pipeline is vectorized with numpy: there is no Python loop
over points and, since ``ColumnStore.from_intervals``, no per-column Python
loop either.

Two-stage factoring
-------------------
The pipeline is split into two internal stages so the whole-file path and the
streamed (chunk-by-chunk, multi-tile) path share one exact final stage and
therefore produce identical stores:

    points  --_cells_from_points-->  unique (ix,iy,iz,cls) cells + counts
    cells   --_store_from_cells--->  RLE intervals -> Column -> ColumnStore

``_store_from_cells`` re-aggregates duplicate cells (summing their point
counts) before run-length encoding, which is what makes streaming exact:
concatenating per-chunk or per-tile cell arrays and re-aggregating is
identical to voxelizing the union of all points at once.

Determinism
-----------
Cells are ordered canonically, ix-major then iy; the last two keys follow
``VOXELIZER_RUN_ORDER``, so the order is (ix, iy, cls, iz) under the
class_first default and (ix, iy, iz, cls) under height_first. For the common
case of one class per voxel the two are identical. For a multi-class voxel the
canonical order fixes an otherwise order-dependent tie so the whole-file and
streamed paths agree byte for byte.

Run-forming order (``VOXELIZER_RUN_ORDER``)
-------------------------------------------
The vertical RLE closes a run on any class change, so the sort order decides
the run structure:

    class_first (default)    (ix, iy, cls, iz) - runs are maximal per
                             (ix, iy, cls) by construction.
    height_first             (ix, iy, iz, cls) - the compatibility order. A
                             same-class run is split into touching pieces
                             wherever another class interleaves.

Both orders are deterministic and produce the same per-voxel classes and
per-class point counts; they differ only in run structure. The variable is
read at call time, so worker subprocesses inherit it through the environment.
Stores written before the switch were built height_first and record that in
their provenance (an absent ``run_order`` field means height_first); the
archive tools re-voxelize under the recorded order, and a shard resume whose
order differs from the run's is refused.
"""

from __future__ import annotations
import logging
from pathlib import Path
import numpy as np

from .data_structures import ColumnStore
from .io_laz import read_laz, read_laz_chunks, read_laz_header

logger = logging.getLogger(__name__)

# Default chunk size for the streamed reader (points per chunk). ~5M points
# of float64 x/y/z + uint8 cls is ~125 MB per chunk before voxelization.
DEFAULT_CHUNK_SIZE = 5_000_000


def _run_order() -> str:
    """The run-forming order (module docstring): 'class_first' | 'height_first'."""
    import os
    v = os.environ.get("VOXELIZER_RUN_ORDER", "class_first").strip().lower()
    if v not in ("height_first", "class_first"):
        raise ValueError(
            f"VOXELIZER_RUN_ORDER={v!r} is not supported; use "
            f"'class_first' (default) or 'height_first'.")
    return v


# ---------------------------------------------------------------------------
# Stage 1: raw points -> unique per-voxel cells
# ---------------------------------------------------------------------------
def _cells_from_points(
    x: np.ndarray, y: np.ndarray, z: np.ndarray, cls: np.ndarray,
    x0: float, y0: float, z0: float, cell_xy: float, cell_z: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Index every point into its voxel and collapse to unique
    (ix, iy, iz, cls) cells with a summed point count.

    Returns five arrays in the canonical cell order - ix-major, then iy, then
    the pair ``VOXELIZER_RUN_ORDER`` selects: (cls, iz) under the class_first
    default, (iz, cls) under height_first:
        u_ix, u_iy, u_iz : int32 voxel indices (offset from x0/y0/z0)
        u_cls            : uint8 class code
        u_count          : int32 points that fell in that cell

    @param x Point x coordinates in metres (CRS coordinates, EPSG:3946).
    @param y Point y coordinates in metres.
    @param z Point z (altitude) in metres.
    @param cls ASPRS class code of each point.
    @param x0 Grid origin x in metres; voxel index ix = 0 starts here.
    @param y0 Grid origin y in metres.
    @param z0 Grid origin z in metres; voxel index iz = 0 starts here.
    @param cell_xy Horizontal voxel size in metres.
    @param cell_z Vertical voxel size in metres.
    @return ``(u_ix, u_iy, u_iz, u_cls, u_count)``: the unique cells in
        canonical order, with ``u_count`` the number of points summed into
        each cell. Points whose voxel index would fall outside the int32
        range (2^31 cells or more from the origin) are dropped and counted
        with a warning.
    """
    fx = np.floor((x - x0) / cell_xy)
    fy = np.floor((y - y0) / cell_xy)
    fz = np.floor((z - z0) / cell_z)

    # The int32 cast below WRAPS silently, so a point far enough from the
    # origin does not fail - it lands on a plausible-looking index billions
    # of cells the other way and plants a phantom column there. Drop anything
    # outside the int32 range with a count, the treatment non-finite
    # coordinates already get upstream. NaN compares False here, so a
    # non-finite value reaching this function is dropped too rather than
    # casting to INT_MIN.
    _LIMIT = 2 ** 31
    in_range = ((np.abs(fx) < _LIMIT) & (np.abs(fy) < _LIMIT)
                & (np.abs(fz) < _LIMIT))
    if not in_range.all():
        n_bad = int(in_range.size - in_range.sum())
        logger.warning("  dropping %d point(s) whose voxel index falls "
                       "outside the int32 grid (2^31 cells or more from the "
                       "origin)", n_bad)
        fx, fy, fz = fx[in_range], fy[in_range], fz[in_range]
        cls = cls[in_range]

    ix = fx.astype(np.int32)
    iy = fy.astype(np.int32)
    iz = fz.astype(np.int32)
    del fx, fy, fz, in_range

    # Cell sort: primary key is the *last* argument to lexsort, so both
    # variants are ix-major, then iy; height_first then orders (iz, cls),
    # class_first orders (cls, iz) - see the module docstring.
    if _run_order() == "class_first":
        order = np.lexsort((iz, cls, iy, ix))
    else:
        order = np.lexsort((cls, iz, iy, ix))
    ix_s, iy_s, iz_s, cls_s = ix[order], iy[order], iz[order], cls[order]
    del ix, iy, iz, order

    if len(ix_s) == 0:
        empty_i = np.empty(0, np.int32)
        return empty_i, empty_i.copy(), empty_i.copy(), np.empty(0, np.uint8), empty_i.copy()

    voxel_changed = (
        (np.diff(ix_s)  != 0) |
        (np.diff(iy_s)  != 0) |
        (np.diff(iz_s)  != 0) |
        (np.diff(cls_s) != 0)
    )
    starts = np.concatenate(([0], np.where(voxel_changed)[0] + 1))
    del voxel_changed

    u_ix  = ix_s[starts]
    u_iy  = iy_s[starts]
    u_iz  = iz_s[starts]
    u_cls = cls_s[starts]
    u_count = np.diff(np.concatenate((starts, [len(ix_s)]))).astype(np.int32)
    return u_ix, u_iy, u_iz, u_cls, u_count


# ---------------------------------------------------------------------------
# Stage 2: unique cells -> ColumnStore
# ---------------------------------------------------------------------------
def _store_from_cells(
    u_ix: np.ndarray, u_iy: np.ndarray, u_iz: np.ndarray,
    u_cls: np.ndarray, u_count: np.ndarray,
    x0: float, y0: float, z0: float, cell_xy: float, cell_z: float,
    *, aggregate: bool,
) -> ColumnStore:
    """
    Turn unique per-voxel cells into a column-compressed ColumnStore.

    @param u_ix Voxel index ix of each cell (int32), the output of
        ``_cells_from_points`` or the concatenation of several chunk
        results.
    @param u_iy Voxel index iy of each cell (int32), parallel to ``u_ix``.
    @param u_iz Voxel index iz of each cell (int32), parallel to ``u_ix``.
    @param u_cls ASPRS class code of each cell (uint8).
    @param u_count Point count per cell (int32).
    @param x0 Grid origin x in metres (the shared origin when assembling an
        area).
    @param y0 Grid origin y in metres.
    @param z0 Grid origin z in metres.
    @param cell_xy Horizontal cell size in metres.
    @param cell_z Vertical cell size in metres.
    @param aggregate When True, the input may contain duplicate
        (ix,iy,iz,cls) cells (e.g. the concatenation of several chunks or
        tiles); they are re-sorted and their counts summed first. When
        False, the caller guarantees the cells are already unique and
        canonically sorted (the whole-file path), so this step is skipped.
    @return A ColumnStore grid-aligned to the origin; empty (no columns)
        when the input holds no cells.
    @throws ValueError When ``VOXELIZER_RUN_ORDER`` holds an unsupported
        value (raised by ``_run_order``).
    """
    if len(u_ix) == 0:
        return ColumnStore(float(x0), float(y0), float(z0), cell_xy, cell_z)

    if aggregate:
        if _run_order() == "class_first":
            order = np.lexsort((u_iz, u_cls, u_iy, u_ix))
        else:
            order = np.lexsort((u_cls, u_iz, u_iy, u_ix))
        u_ix, u_iy, u_iz = u_ix[order], u_iy[order], u_iz[order]
        u_cls, u_count = u_cls[order], u_count[order]
        changed = (
            (np.diff(u_ix)  != 0) |
            (np.diff(u_iy)  != 0) |
            (np.diff(u_iz)  != 0) |
            (np.diff(u_cls) != 0)
        )
        starts = np.concatenate(([0], np.where(changed)[0] + 1))
        u_count = np.add.reduceat(u_count, starts).astype(np.int32)
        u_ix, u_iy, u_iz, u_cls = (
            u_ix[starts], u_iy[starts], u_iz[starts], u_cls[starts]
        )
        del order, changed, starts

    # -- vertical RLE: an interval is a maximal run where (ix,iy,cls) is
    #    constant and iz increments by exactly 1 -------------------------------
    interval_changed = (
        (np.diff(u_ix)  != 0) |
        (np.diff(u_iy)  != 0) |
        (np.diff(u_cls) != 0) |
        (np.diff(u_iz)  != 1)
    )
    interval_starts = np.concatenate(([0], np.where(interval_changed)[0] + 1))
    interval_ends   = np.concatenate((interval_starts[1:], [len(u_ix)]))
    del interval_changed

    iv_ix      = u_ix[interval_starts]
    iv_iy      = u_iy[interval_starts]
    iv_z_start = u_iz[interval_starts]
    iv_z_end   = u_iz[interval_ends - 1] + 1              # exclusive
    iv_cls     = u_cls[interval_starts]
    iv_count   = np.add.reduceat(u_count, interval_starts).astype(np.int32)
    del interval_ends

    n_intervals = len(iv_ix)

    # -- hand the flat interval arrays straight to the struct-of-arrays store.
    # Height-first cells derive intervals already in the store's canonical
    # (ix, iy, z_start, cls) order: no re-sort, no per-column Python loop,
    # no per-column object overhead (~874 B/column in the old dict layout,
    # ~16 B/column + 13 B/interval now). Class-first cells derive
    # (ix, iy, cls, z_start) order instead, so from_intervals must re-sort
    # them into the canonical intra-column order the store's binary searches
    # rely on.
    store = ColumnStore.from_intervals(
        float(x0), float(y0), float(z0), cell_xy, cell_z,
        iv_ix, iv_iy, iv_z_start, iv_z_end, iv_cls, iv_count,
        assume_canonical=(_run_order() == "height_first"),
    )
    logger.info("  %d occupied columns, %d intervals total",
                len(store.columns), n_intervals)
    return store


# ---------------------------------------------------------------------------
# Public: in-memory voxelization
# ---------------------------------------------------------------------------
def voxelize(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    cls: np.ndarray,
    cell_xy: float = 0.5,
    cell_z: float = 0.5,
    keep_classes: set[int] | None = None,
    origin: tuple[float, float, float] | None = None,
) -> ColumnStore:
    """
    Turn a point cloud into a column-compressed voxel grid.

    @param x Point x coordinates in metres (output of ``read_laz``).
    @param y Point y coordinates in metres.
    @param z Point z (altitude) in metres.
    @param cls ASPRS class code of each point.
    @param cell_xy Horizontal cell size in metres (default 0.5 m).
    @param cell_z Vertical cell size in metres (default 0.5 m).
    @param keep_classes Optional set of class codes to keep; everything else
        is dropped before voxelization. None (the default) keeps every class.
    @param origin Optional explicit grid origin ``(x0, y0, z0)`` in metres.
        When None (default), the per-tile minimum corner is used, exactly as
        before. Pass a *shared* origin to place several tiles on one common
        grid (see ``process_area``) - this is what makes a multi-tile area
        index-comparable and lets a single vertical datum (one z0) span the
        whole area.
    @return A populated ColumnStore; empty (no columns) when class filtering
        and the dropping of non-finite points leave no point. Non-finite
        x/y/z points are dropped with a warning before the origin is
        derived.
    """
    if keep_classes is not None:
        mask = np.isin(cls, list(keep_classes))
        x, y, z, cls = x[mask], y[mask], z[mask], cls[mask]
        logger.info("  %d points kept after class filter", len(x))

    # Non-finite coordinates (NaN/inf from a corrupt tile) must go BEFORE
    # the origin is derived: they poison min() into NaN and collapse to
    # voxel index INT_MIN on the int32 cast, planting a bogus column ~2.1
    # billion cells away. Drop them loudly.
    finite = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
    if not finite.all():
        n_bad = int(finite.size - finite.sum())
        logger.warning("  dropping %d non-finite point(s)", n_bad)
        x, y, z, cls = x[finite], y[finite], z[finite], cls[finite]

    if len(x) == 0:
        logger.warning("No points left to voxelize - returning empty store.")
        x0, y0, z0 = origin if origin is not None else (0.0, 0.0, 0.0)
        return ColumnStore(float(x0), float(y0), float(z0), cell_xy, cell_z)

    if origin is not None:
        x0, y0, z0 = origin
    else:
        x0, y0, z0 = float(x.min()), float(y.min()), float(z.min())

    cells = _cells_from_points(x, y, z, cls, x0, y0, z0, cell_xy, cell_z)
    return _store_from_cells(*cells, x0, y0, z0, cell_xy, cell_z, aggregate=False)


def voxelize_laz(
    path: Path | str,
    cell_xy: float = 0.5,
    cell_z: float = 0.5,
    keep_classes: set[int] | None = None,
    origin: tuple[float, float, float] | None = None,
) -> ColumnStore:
    """One-call helper: file in, ColumnStore out (whole-file ``laspy.read``).

    @param path Path to one .laz/.las file, read whole (str or Path).
    @param cell_xy Horizontal cell size in metres (default 0.5 m).
    @param cell_z Vertical cell size in metres (default 0.5 m).
    @param keep_classes Optional set of class codes to keep; None (the
        default) keeps every class.
    @param origin Optional explicit grid origin ``(x0, y0, z0)`` in metres;
        None (the default) uses the file's per-tile minimum corner.
    @return The populated ColumnStore, exactly as ``voxelize`` returns it.
    """
    x, y, z, cls = read_laz(path)
    return voxelize(x, y, z, cls, cell_xy=cell_xy, cell_z=cell_z,
                    keep_classes=keep_classes, origin=origin)


# ---------------------------------------------------------------------------
# Public: streamed (chunked) voxelization of one file
# ---------------------------------------------------------------------------
def voxelize_laz_chunked(
    path: Path | str,
    cell_xy: float = 0.5,
    cell_z: float = 0.5,
    keep_classes: set[int] | None = None,
    origin: tuple[float, float, float] | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    clip_bbox: tuple[float, float, float, float] | None = None,
) -> ColumnStore:
    """
    Voxelize one LAZ/LAS file by streaming it in chunks (``laspy``
    ``chunk_iterator``) instead of loading it whole. The result is identical
    to ``voxelize_laz`` on the same file and origin.

    What this bounds is the RAW POINT side: only one chunk of x/y/z/class is
    held at a time. The reduced side is not bounded - each chunk's unique
    (ix, iy, iz, cls) cells are appended to ``parts`` and concatenated after
    the loop, so the peak is one chunk of points plus the whole file's cell
    set, and about twice that cell set at the concatenate itself. Against a
    whole-file read the saving is the points, not the cells.

    @param path Path to one .laz/.las file, streamed (str or Path).
    @param cell_xy Horizontal cell size in metres (default 0.5 m).
    @param cell_z Vertical cell size in metres (default 0.5 m).
    @param keep_classes Optional set of class codes to keep; None (the
        default) keeps every class. Applied per chunk after clipping, so
        the class filter and the clip compose.
    @param origin Shared grid origin ``(x0, y0, z0)`` in metres. When None
        (the default), the file header's minimum corner is used (a valid
        lower bound over all points, so no second pass is needed). Pass an
        explicit origin to align this file with other tiles.
    @param chunk_size Points per chunk handed to ``read_laz_chunks``
        (default ``DEFAULT_CHUNK_SIZE``, 5 million).
    @param clip_bbox Optional ``(xmin, ymin, xmax, ymax)`` in metres. Points
        outside are dropped per chunk, keeping exactly the half-open
        rectangle [xmin, xmax) x [ymin, ymax) (min edges inclusive, max
        edges exclusive, so abutting areas never double-count a boundary
        point) - this is how ``process_area`` guarantees "exactly the area
        within those coordinates". None (the default) clips nothing.
    @return A ColumnStore identical to ``voxelize_laz`` on the same file and
        origin; empty (no columns) when the clip and filters leave no point.
    @throws ValueError When the header carries no CRS or one outside the
        allowed set (raised by ``read_laz_header``); also when
        ``VOXELIZER_RUN_ORDER`` holds an unsupported value (raised by
        ``_run_order``).
    @throws FileNotFoundError When ``path`` is not an existing file.
    """
    if origin is None:
        mins, _maxs, _n = read_laz_header(path)
        origin = (float(mins[0]), float(mins[1]), float(mins[2]))
    x0, y0, z0 = origin

    keep_list = list(keep_classes) if keep_classes is not None else None
    parts: list[tuple[np.ndarray, ...]] = []
    n_read = n_kept = n_nonfinite = 0

    for x, y, z, cls in read_laz_chunks(path, chunk_size):
        n_read += len(x)
        # Drop non-finite coordinates before anything else: clipping only
        # tests x/y (a NaN z would sail through) and a NaN/inf collapses
        # to voxel index INT_MIN on the int32 cast. Warned once, after the
        # loop.
        finite = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
        if not finite.all():
            n_nonfinite += int(finite.size - finite.sum())
            x, y, z, cls = x[finite], y[finite], z[finite], cls[finite]
        if clip_bbox is not None:
            xmin, ymin, xmax, ymax = clip_bbox
            m = (x >= xmin) & (x < xmax) & (y >= ymin) & (y < ymax)
            if not m.all():
                x, y, z, cls = x[m], y[m], z[m], cls[m]
        if keep_list is not None and len(x):
            m = np.isin(cls, keep_list)
            if not m.all():
                x, y, z, cls = x[m], y[m], z[m], cls[m]
        if len(x) == 0:
            continue
        n_kept += len(x)
        parts.append(_cells_from_points(x, y, z, cls, x0, y0, z0, cell_xy, cell_z))
        del x, y, z, cls

    if n_nonfinite:
        logger.warning("  dropped %d non-finite point(s) from %s",
                       n_nonfinite, Path(path).name)
    logger.info("  streamed %d points (%d kept) from %s",
                n_read, n_kept, Path(path).name)
    if not parts:
        return ColumnStore(float(x0), float(y0), float(z0), cell_xy, cell_z)

    u_ix    = np.concatenate([p[0] for p in parts])
    u_iy    = np.concatenate([p[1] for p in parts])
    u_iz    = np.concatenate([p[2] for p in parts])
    u_cls   = np.concatenate([p[3] for p in parts])
    u_count = np.concatenate([p[4] for p in parts])
    del parts
    return _store_from_cells(u_ix, u_iy, u_iz, u_cls, u_count,
                             x0, y0, z0, cell_xy, cell_z, aggregate=True)
