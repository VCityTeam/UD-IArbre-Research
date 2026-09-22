"""
Reconstruct a point cloud (LAS/LAZ) from a ColumnStore `.npz`, and rebuild a
ColumnStore from such a file.
@ingroup t2_algos


Three export modes
------------------
``density`` (default)
    One point per ORIGINAL LiDAR point (using the ``count`` field), minimum
    one - a zero-count interval (which ``resolve()`` can produce by rounding
    a proportional share down) still emits a single point.  All of them sit
    at the centre of their interval.  Point density matches the source
    tile; the vertical extent of an interval does NOT survive a
    re-voxelization (every point of an interval shares one z).

``one_per_voxel``
    One point per interval VOXEL, each at its own voxel centre.  The
    occupied-voxel geometry survives a re-voxelization; the per-interval
    point counts do not.

``exact``
    One point per interval voxel PLUS ``count - (z_end - z_start)`` padding
    points parked in the interval's first voxel.  This is the only mode that
    is a **bit-exact** round trip: re-voxelizing the result on the same grid
    reproduces the store's six flat arrays byte for byte.  The `.npz` written
    from it is NOT byte-identical - it also carries the metadata and ground
    index members, and a zip archive stamps its entries with the time they
    were written.  See store_from_laz() for the inverse.

Why ``exact`` works
-------------------
``voxelize._store_from_cells`` sums point counts PER INTERVAL, so where the
padding points sit inside the interval is irrelevant - only the set of
occupied ``(ix, iy, iz, cls)`` cells and the per-interval totals matter.  A
raw store IS the canonical run-length encoding of its own cell set, so
expanding it back to cells and re-running the RLE is idempotent - even for
columns where two classes interleave and their intervals overlap in z.

``count >= z_end - z_start`` holds for every interval of a voxelized store
(each occupied voxel contributed at least one point), so the padding count is
never negative.

What is preserved
-----------------
- X, Y        : column-centre coordinates, precise to ``cell_xy / 2``
- Z           : in ``density`` mode the interval centre - within
                ``cell_z / 2`` only for single-voxel intervals; a grouped
                multi-voxel run snaps every point to the centre of its
                whole span (error up to half the run height).
                ``one_per_voxel`` and ``exact`` instead give each point its
                own voxel centre (``exact`` parks its padding points in the
                interval's first voxel).
- ASPRS class : exactly the class stored in the store

What is lost (and why)
----------------------
- RGB colour, NIR, intensity
- Return number, number of returns
- Classification flags (synthetic, key-point, withheld)
- GPS time, scanner channel, scan angle, user data, point source ID
- Exact per-point elevation (snapped to the interval centre in ``density``
  mode; individual voxel centres under both ``one_per_voxel`` and ``exact``)

Loss is *not* a limitation of the round-trip tool - it reflects the
voxelizer's design: ``io_laz.read_laz()`` extracts only ``x, y, z, cls`` from
the original LAZ, and the rest is discarded before any voxel is built.  A
``mode="exact"`` file is therefore a faithful container for the STORE, never a
replacement for the original IGN tile.

Grid self-description
---------------------
A re-voxelization only reproduces the store when it uses the SAME grid
origin: ``voxelize(origin=None)`` derives the origin from ``min(x)``, which
for a reconstructed cloud is a voxel *centre*, so the rebuilt store would land
half a cell off.  Exported files therefore carry an ``IARBRE`` VLR holding
``(x_min, y_min, z_min, cell_xy, cell_z, epsg)``; store_from_laz() reads
it back and needs no sidecar file.

Only RAW stores round-trip
--------------------------
``ColumnStore.grouped()`` merges same-class intervals ACROSS empty z-gaps, and
a grouped store is NOT reliably archivable:

- most merges leave the interval with ``count < height`` (the gap voxels add
  height but no points), which ``mode="exact"`` refuses outright;
- but a merge whose summed count still covers the merged height, with no
  foreign class inside the span, round-trips perfectly well - to the GROUPED
  store.

Nothing on the store distinguishes the two cases, so the archive tooling does
not try: it accepts only ``shards/``, which ``shard_worker`` guarantees is raw
(grouping happens in memory at merge time), and never ``area.npz``.  Since the
shard merge is deterministic, ``area.npz`` is regenerated rather than archived
- which is what the recorded grouping settings in ``shards/manifest.json`` are
for.

Usage
-----
    from voxelizer.reconstruct import store_to_laz, store_from_laz
    from voxelizer.data_structures import ColumnStore

    store = ColumnStore.load("shard.npz")
    store_to_laz(store, "shard.laz", mode="exact")
    same = store_from_laz("shard.laz")        # grid read from the VLR

Or from the command line::

    python -m voxelizer.reconstruct to-laz shard.npz shard.laz --mode exact
    python -m voxelizer.reconstruct to-npz shard.laz rebuilt.npz
    python -m voxelizer.reconstruct verify   shard.npz shard.laz
"""

from __future__ import annotations
import contextlib
import logging
import struct
from pathlib import Path

import numpy as np

from .data_structures import ColumnStore, _unpack_keys
from .io_laz import DEFAULT_EPSG

logger = logging.getLogger(__name__)

# Named EXPORT_MODES, not MODES: ``pipeline.MODES`` is the four 2-D map /
# render modes, a different vocabulary again from the height modes, and
# both MODES and EXPORT_MODES are re-exported from ``voxelizer``.
EXPORT_MODES = ("density", "one_per_voxel", "exact")

# --- grid VLR --------------------------------------------------------------
# 8-byte magic + 5 float64 (x_min, y_min, z_min, cell_xy, cell_z) + uint32
# EPSG = 52 bytes.  The magic is versioned so a future layout change is
# detectable rather than silently misread.
GRID_VLR_USER_ID = "IARBRE"
GRID_VLR_RECORD_ID = 1
_GRID_VLR_MAGIC = b"VOXGRID1"
_GRID_VLR_STRUCT = "<8s5dI"
_GRID_VLR_DESC = "voxelizer grid origin + cell sizes"

# ASPRS classification is a 5-bit sub-field in LAS point formats 0-5, so codes
# above this need LAS 1.4 / point format 6 (a full byte).  Grand Lyon data is
# LAS 1.2 and only ever carries 0-22, but IGN's custom 64-67 and synthetic test
# fixtures would otherwise raise OverflowError deep inside laspy.
_PF3_MAX_CLASS = 31


def pack_grid_vlr(store: ColumnStore, epsg: int) -> bytes:
    """Serialize a store's grid geometry for the ``IARBRE`` VLR."""
    return struct.pack(_GRID_VLR_STRUCT, _GRID_VLR_MAGIC,
                       float(store.x_min), float(store.y_min),
                       float(store.z_min), float(store.cell_xy),
                       float(store.cell_z), int(epsg))


def unpack_grid_vlr(raw: bytes) -> tuple[float, float, float, float, float, int]:
    """Inverse of pack_grid_vlr() -> (x_min, y_min, z_min, cell_xy,
    cell_z, epsg).  Raises ValueError on a foreign or truncated record."""
    size = struct.calcsize(_GRID_VLR_STRUCT)
    if len(raw) < size:
        raise ValueError(
            f"{GRID_VLR_USER_ID} grid VLR is {len(raw)} B, expected {size} B "
            f"- the file was written by a different (or broken) exporter.")
    magic, x0, y0, z0, cxy, cz, epsg = struct.unpack(
        _GRID_VLR_STRUCT, raw[:size])
    if magic != _GRID_VLR_MAGIC:
        raise ValueError(
            f"{GRID_VLR_USER_ID} grid VLR carries magic {magic!r}, expected "
            f"{_GRID_VLR_MAGIC!r} - unsupported layout version.")
    return x0, y0, z0, cxy, cz, int(epsg)


def _resolve_mode(mode: str | None, one_per_voxel: bool) -> str:
    """Reconcile the ``mode`` argument with the legacy ``one_per_voxel`` flag.

    ``one_per_voxel`` predates ``mode`` and is still used by callers and
    tests; it stays supported as an alias so no caller breaks.
    """
    if mode is None:
        return "one_per_voxel" if one_per_voxel else "density"
    if mode not in EXPORT_MODES:
        raise ValueError(f"mode must be one of {EXPORT_MODES}, got {mode!r}")
    if one_per_voxel and mode != "one_per_voxel":
        raise ValueError(
            f"conflicting arguments: mode={mode!r} with one_per_voxel=True. "
            f"one_per_voxel is the legacy spelling of mode='one_per_voxel'; "
            f"pass only one of them.")
    return mode


def store_to_points(
    store: ColumnStore,
    *,
    one_per_voxel: bool = False,
    z_as_centre: bool = True,
    mode: str | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Expand a ColumnStore into per-point arrays.

    @param store The ColumnStore to expand.
    @param one_per_voxel Legacy alias for ``mode="one_per_voxel"``. Kept for
        backwards compatibility; passing both is an error unless they agree.
    @param z_as_centre If True (default), z is the voxel/interval centre: in
        ``density`` mode ``z_min + (z_start + z_end) / 2 * cell_z``, otherwise
        each point's own voxel centre. If False, z is the interval (or voxel)
        base. The store's z_min origin is always added - output z is CRS
        altitude. ``mode="exact"`` REJECTS False (raises ValueError):
        interval-base z sits exactly on a voxel boundary and would not
        survive the round trip.
    @param mode One of ``"density"``, ``"one_per_voxel"``, ``"exact"``; see
        the module docstring. Defaults to ``"density"`` (or
        ``"one_per_voxel"`` when the legacy flag is set).
    @return ``(x, y, z, cls)``: float64, float64, float64, uint8 arrays, one
        entry per emitted point (all empty for a store with no intervals).
    @throws ValueError when ``mode`` is not one of EXPORT_MODES, when
        ``one_per_voxel=True`` conflicts with another ``mode``, when
        ``mode="exact"`` is combined with ``z_as_centre=False``, or when an
        interval has ``count < height`` under ``mode="exact"`` (the usual
        grouped-store signature).
    """
    mode = _resolve_mode(mode, one_per_voxel)
    if mode == "exact" and not z_as_centre:
        raise ValueError(
            "mode='exact' requires z_as_centre=True: interval-base z would "
            "put every point of an interval on the same voxel, destroying "
            "the round trip.")

    keys = np.asarray(store._keys)
    off = np.asarray(store._off)
    zs = np.asarray(store._zs)
    ze = np.asarray(store._ze)
    cl = np.asarray(store._cl)
    ct = np.asarray(store._ct)
    cell_xy = float(store.cell_xy)
    cell_z = float(store.cell_z)
    x_min = float(store.x_min)
    y_min = float(store.y_min)
    z_min = float(store.z_min)

    n_iv = int(zs.shape[0])
    if n_iv == 0:
        e = np.empty(0, np.float64)
        return e, e.copy(), e.copy(), np.empty(0, np.uint8)

    # Fully vectorized expansion (the previous per-point Python lists held
    # one entry PER LiDAR POINT - tens of billions at metropolis scale,
    # where the corpus holds 51.9e9 points and even the 1.0 m grouped
    # store keeps 1.35e9 INTERVALS - the exact dict/list layout the store
    # redesign removed).
    zs64 = zs.astype(np.int64)
    ze64 = ze.astype(np.int64)
    nivs = np.diff(off)
    col_of = np.repeat(np.arange(int(keys.shape[0]), dtype=np.int64), nivs)
    ix_all, iy_all = _unpack_keys(keys)
    cx_all = x_min + (ix_all.astype(np.float64) + 0.5) * cell_xy
    cy_all = y_min + (iy_all.astype(np.float64) + 0.5) * cell_xy

    if mode == "density":
        n_out = np.maximum(ct.astype(np.int64), 1)
    else:
        # One point PER VOXEL of the interval, each at its own voxel centre.
        n_out = ze64 - zs64

    rep_iv = np.repeat(np.arange(n_iv, dtype=np.int64), n_out)
    x_arr = cx_all[col_of[rep_iv]]
    y_arr = cy_all[col_of[rep_iv]]
    c_arr = np.asarray(cl, np.uint8)[rep_iv]

    if mode == "density":
        if z_as_centre:
            z_iv = z_min + (zs64 + ze64).astype(np.float64) * 0.5 * cell_z
        else:
            z_iv = z_min + zs64.astype(np.float64) * cell_z
        z_arr = z_iv[rep_iv]
    else:
        starts = np.concatenate(([0], np.cumsum(n_out)[:-1]))
        k = np.arange(int(n_out.sum()), dtype=np.int64) \
            - np.repeat(starts, n_out)
        base = 0.5 if z_as_centre else 0.0
        z_arr = z_min + (zs64[rep_iv].astype(np.float64) + k + base) * cell_z

    if mode != "exact":
        return x_arr, y_arr, z_arr, c_arr

    # -- exact: restore the per-interval point counts -------------------------
    # Every voxel already carries one point; the remainder is parked in the
    # interval's first voxel. Only the per-interval TOTAL is recoverable from
    # the store (it never stored per-voxel counts), and only the total is what
    # _store_from_cells reads back, so this is exact - see the module docstring.
    pad = ct.astype(np.int64) - (ze64 - zs64)
    if np.any(pad < 0):
        bad = int((pad < 0).sum())
        raise ValueError(
            f"mode='exact': {bad} interval(s) have count < height, which a "
            f"freshly voxelized store cannot produce - every occupied voxel "
            f"holds at least one point. The usual cause is a GROUPED store: "
            f"ColumnStore.grouped() merges same-class intervals across empty "
            f"z-gaps, and those gap voxels add height without adding points. "
            f"Grouped stores are not reliably archivable; export the raw "
            f"store (the per-tile shards) instead.")
    if not pad.any():
        return x_arr, y_arr, z_arr, c_arr

    rep_pad = np.repeat(np.arange(n_iv, dtype=np.int64), pad)
    x_pad = cx_all[col_of[rep_pad]]
    y_pad = cy_all[col_of[rep_pad]]
    z_pad = z_min + (zs64[rep_pad].astype(np.float64) + 0.5) * cell_z
    c_pad = np.asarray(cl, np.uint8)[rep_pad]

    return (np.concatenate((x_arr, x_pad)), np.concatenate((y_arr, y_pad)),
            np.concatenate((z_arr, z_pad)), np.concatenate((c_arr, c_pad)))


def store_to_las(
    store: ColumnStore,
    path: str | Path,
    *,
    one_per_voxel: bool = False,
    z_as_centre: bool = True,
    mode: str | None = None,
    epsg: int = DEFAULT_EPSG,
    grid_vlr: bool = True,
    laz_backend: str | None = None,
) -> None:
    """Write a ColumnStore as a LAS (or LAZ) file.

    @param store The ColumnStore to write; must hold at least one column.
    @param path Output path.  Use ``.laz`` extension for compressed output
        (needs the ``laszip`` or ``lazrs`` backend installed).
    @param one_per_voxel Legacy alias for ``mode="one_per_voxel"``, passed to
        store_to_points().
    @param z_as_centre Passed to store_to_points(): True (default) puts each
        point at its interval or voxel centre, False at the base.
    @param mode Export mode, one of ``"density"``, ``"one_per_voxel"``,
        ``"exact"``; None (default) means ``"density"`` unless
        ``one_per_voxel`` is set.  Passed to store_to_points().
    @param epsg CRS written into the header.  Defaults to EPSG:3946
        (RGF93/CC46), the Grand Lyon working CRS.  ``io_laz.read_laz``
        REJECTS files without a CRS, so omitting this would make our own
        output unreadable by our own reader - which is exactly what happened
        before this parameter existed.
    @param grid_vlr Write the ``IARBRE`` grid VLR (origin + cell sizes +
        EPSG) so store_from_laz() can rebuild without a sidecar (default
        True).
    @param laz_backend One of ``"laszip"``, ``"lazrs"``.  If None, ``.laz``
        output is pinned to the laszip backend (matching ``io_laz``) rather
        than left to the laspy default.  LAS output (``.las`` extension) does
        not need a compression backend.
    @throws ValueError when the store is empty (0 columns), or propagated
        from _resolve_mode() / store_to_points() for a bad ``mode``, a
        conflicting ``one_per_voxel``, ``mode="exact"`` with
        ``z_as_centre=False``, or an interval with ``count < height`` under
        ``mode="exact"``.
    @throws AttributeError when ``laz_backend`` names no
        ``laspy.LazBackend`` member.
    """
    import laspy as _laspy
    import pyproj as _pyproj

    path = Path(path)
    if not store.columns:
        # min() over zero points below would raise an opaque "zero-size
        # array to reduction operation" - fail with a clear message
        # instead.
        raise ValueError(
            "store_to_las: the store is empty (0 columns) - nothing to "
            "write.")
    mode = _resolve_mode(mode, one_per_voxel)
    x, y, z, cls = store_to_points(store, mode=mode, z_as_centre=z_as_centre)

    # LAS 1.2 matches the IGN input; the input's point format is 1, and pf3
    # is pf1 plus RGB, so this is a superset that keeps the output readable
    # by everything. Both store classification in the same 5-bit sub-field,
    # so codes above 31 (IGN's custom 64-67, synthetic fixtures) need
    # LAS 1.4 / pf6 either way.
    if cls.size and int(cls.max()) > _PF3_MAX_CLASS:
        header = _laspy.LasHeader(point_format=6, version="1.4")
        logger.info("class code %d > %d - writing LAS 1.4 / point format 6",
                    int(cls.max()), _PF3_MAX_CLASS)
    else:
        header = _laspy.LasHeader(point_format=3, version="1.2")
    header.offsets = [float(x.min()), float(y.min()), float(z.min())]
    header.scales = [0.001, 0.001, 0.001]
    header.add_crs(_pyproj.CRS.from_epsg(int(epsg)))
    if grid_vlr:
        header.vlrs.append(_laspy.VLR(
            user_id=GRID_VLR_USER_ID, record_id=GRID_VLR_RECORD_ID,
            description=_GRID_VLR_DESC,
            record_data=pack_grid_vlr(store, epsg)))

    las = _laspy.LasData(header)
    las.x = x
    las.y = y
    las.z = z
    las.classification = cls

    kwargs = {}
    if laz_backend is not None:
        kwargs["laz_backend"] = getattr(_laspy.LazBackend, laz_backend.capitalize())

    if path.suffix.lower() == ".laz" and not kwargs:
        kwargs["laz_backend"] = _laspy.LazBackend.Laszip

    las.write(str(path), **kwargs)
    n = len(x)
    mb = path.stat().st_size / 1e6
    logger.info("Wrote %s -> %s (mode=%s, %s points, %.1f MB)",
                store, path, mode, f"{n:,}", mb)


def store_to_laz(store: ColumnStore, path: str | Path, **kwargs) -> None:
    """Convenience: same as store_to_las(), defaulting the suffix to
    ``.laz`` when the path has neither ``.laz`` nor ``.las`` (an explicit
    ``.las`` is respected, not forced to ``.laz``).

    @param store The ColumnStore to write.
    @param path Output path; any suffix other than ``.laz`` / ``.las`` is
        replaced by ``.laz``.
    @param kwargs Forwarded unchanged to store_to_las() (``mode``,
        ``one_per_voxel``, ``z_as_centre``, ``epsg``, ``grid_vlr``,
        ``laz_backend``).
    @throws ValueError propagated from store_to_las() (empty store, bad mode,
        or a non-archivable store under ``mode="exact"``).
    """
    path = Path(path)
    if not path.suffix.lower() in (".laz", ".las"):
        path = path.with_suffix(".laz")
    return store_to_las(store, path, **kwargs)


# ---------------------------------------------------------------------------
# Inverse: LAS/LAZ -> ColumnStore
# ---------------------------------------------------------------------------
def read_grid_vlr(path: str | Path):
    """Return ``(x_min, y_min, z_min, cell_xy, cell_z, epsg)`` from a file's
    ``IARBRE`` grid VLR, or None when the file does not carry one."""
    from .io_laz import read_vlr_bytes
    raw = read_vlr_bytes(path, GRID_VLR_USER_ID, GRID_VLR_RECORD_ID)
    return None if raw is None else unpack_grid_vlr(raw)


def store_from_laz(
    path: str | Path,
    *,
    origin: tuple[float, float, float] | None = None,
    cell_xy: float | None = None,
    cell_z: float | None = None,
    keep_classes: set[int] | None = None,
    chunk_size: int | None = None,
) -> ColumnStore:
    """Rebuild a ColumnStore from a LAS/LAZ file.

    The grid (origin + cell sizes) comes from the file's ``IARBRE`` VLR when
    present; explicit arguments override it, and are REQUIRED when the file
    carries no VLR - re-voxelizing on a derived origin would land half a cell
    off (see the module docstring).

    Feed this a ``mode="exact"`` export and it reproduces the source store's
    six flat arrays exactly.  Feed it anything else - a density export, a
    grouped store's export, an unrelated LiDAR tile - and you get a valid
    store that is simply not the one you started from.

    @param path         The LAS/LAZ file to rebuild from.
    @param origin       ``(x, y, z)`` grid origin in metres; overrides the
                        ``IARBRE`` VLR, and is REQUIRED when the file carries none.
    @param cell_xy      Horizontal voxel size in metres; overrides the VLR, and is
                        REQUIRED when the file carries none.
    @param cell_z       Vertical voxel size in metres; overrides the VLR, and is
                        REQUIRED when the file carries none.
    @param keep_classes When set, keep only points of these ASPRS codes. The result is
                        then a filtered store, not a reproduction of the source store.
    @param chunk_size   When set, stream the file in chunks of this many points instead
                        of loading it whole (see ``voxelize.voxelize_laz_chunked``); the
                        result is identical either way.
    @return The rebuilt ColumnStore, grid-aligned to the effective origin and cell sizes.
    @throws ValueError When the file carries no ``IARBRE`` grid VLR and any of
                       ``origin``, ``cell_xy``, ``cell_z`` was not passed: rebuilding on
                       a derived origin would shift the grid by half a cell.
    """
    from .voxelize import voxelize_laz, voxelize_laz_chunked

    grid = read_grid_vlr(path)
    if grid is not None:
        vx, vy, vz, vcxy, vcz, _epsg = grid
        if origin is None:
            origin = (vx, vy, vz)
        if cell_xy is None:
            cell_xy = vcxy
        if cell_z is None:
            cell_z = vcz
    missing = [n for n, v in (("origin", origin), ("cell_xy", cell_xy),
                              ("cell_z", cell_z)) if v is None]
    if missing:
        raise ValueError(
            f"{Path(path).name} carries no {GRID_VLR_USER_ID} grid VLR, so "
            f"{', '.join(missing)} must be passed explicitly. Rebuilding on a "
            f"derived origin would shift the grid by half a cell.")

    if chunk_size:
        return voxelize_laz_chunked(path, cell_xy=cell_xy, cell_z=cell_z,
                                    keep_classes=keep_classes, origin=origin,
                                    chunk_size=chunk_size)
    return voxelize_laz(path, cell_xy=cell_xy, cell_z=cell_z,
                        keep_classes=keep_classes, origin=origin)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
_STORE_ARRAYS = ("_keys", "_off", "_zs", "_ze", "_cl", "_ct")
_STORE_META = ("x_min", "y_min", "z_min", "cell_xy", "cell_z")


def stores_equal(a: ColumnStore, b: ColumnStore) -> tuple[bool, list[str]]:
    """Compare two stores field by field.

    Returns ``(equal, differences)`` where each difference is a short
    human-readable string naming the field.  The derived ground index
    (``_gi``) is deliberately ignored: it is recomputed downstream and is not
    part of what a round trip has to preserve.
    """
    diffs: list[str] = []
    for f in _STORE_META:
        va, vb = getattr(a, f), getattr(b, f)
        if va != vb:
            diffs.append(f"{f}: {va!r} != {vb!r}")
    for f in _STORE_ARRAYS:
        va, vb = np.asarray(getattr(a, f)), np.asarray(getattr(b, f))
        if va.shape != vb.shape:
            diffs.append(f"{f}: shape {va.shape} != {vb.shape}")
        elif not np.array_equal(va, vb):
            diffs.append(f"{f}: {int((va != vb).sum()):,}/{va.size:,} "
                         f"elements differ")
    return (not diffs), diffs


@contextlib.contextmanager
def _forced_run_order(order: str | None):
    """Pin ``VOXELIZER_RUN_ORDER`` for the duration, or leave it untouched.

    ``None`` yields without touching the environment, which is what every
    caller predating the parameter gets.

    ``archive_cli._forced_run_order`` is the same context manager without
    the ``None`` branch: its callers always have an order recorded in the
    source manifest. The bodies are otherwise identical; change one and
    check the other.
    """
    if order is None:
        yield
        return
    import os
    prev = os.environ.get("VOXELIZER_RUN_ORDER")
    os.environ["VOXELIZER_RUN_ORDER"] = order
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("VOXELIZER_RUN_ORDER", None)
        else:
            os.environ["VOXELIZER_RUN_ORDER"] = prev


def verify_exact(store: ColumnStore, *,
                 run_order: str | None = None) -> tuple[bool, list[str]]:
    """In-memory round-trip check: expand with ``mode="exact"``, re-voxelize
    on the store's own grid, compare.

    This never touches the disk, so it is the cheap pre-flight before writing
    an archive file.  It is also the ONLY reliable way to tell a raw store
    from a grouped one: a gap-merged interval whose count still covers its
    height is indistinguishable from a genuinely solid one by inspection.

    A store the exact mode outright REFUSES (``count < height``, the usual
    grouped-store signature) is reported as a difference rather than raised:
    the caller asked a yes/no question, and "no, because ..." is the answer.

    ``run_order`` re-voxelizes under a named run-forming order rather than the
    one the environment currently selects. The two orders place the same
    points in the same voxels but form different RUNS, so a store built under
    one and checked under the other reports differences that are not
    corruption. ``None`` (the default) leaves the environment alone.

    @param store     The ColumnStore to check; an empty store (0 columns) passes
                     trivially.
    @param run_order Keyword-only. Name of the run-forming order to re-voxelize
                     under; None (the default) leaves the environment alone.
    @return ``(equal, differences)``: a bool, plus one short human-readable string per
            field that differs. A store the exact mode refuses is reported as
            ``(False, [<reason>])`` rather than raised, because the caller asked a
            yes/no question.
    """
    from .voxelize import voxelize
    if not store.columns:
        return True, []
    try:
        x, y, z, cls = store_to_points(store, mode="exact")
    except ValueError as exc:
        return False, [str(exc)]
    with _forced_run_order(run_order):
        rebuilt = voxelize(x, y, z, cls, cell_xy=store.cell_xy,
                           cell_z=store.cell_z,
                           origin=(store.x_min, store.y_min, store.z_min))
    return stores_equal(store, rebuilt)


def verify_roundtrip(store: ColumnStore,
                     path: str | Path, *,
                     run_order: str | None = None) -> tuple[bool, list[str]]:
    """On-disk round-trip check: rebuild from ``path`` and compare to
    ``store``.  This is what the archive tooling asserts before it is willing
    to delete an ``.npz``.

    ``run_order`` carries the same meaning as in verify_exact(): pin the
    run-forming order the rebuild uses, or ``None`` to leave the environment
    as it stands.

    @param store     The source ColumnStore to compare against; the rebuild is pinned
                     to its origin and cell sizes.
    @param path      The LAS/LAZ file to rebuild from.
    @param run_order Keyword-only. Name of the run-forming order the rebuild uses;
                     None (the default) leaves the environment as it stands.
    @return ``(equal, differences)`` from ``stores_equal``: a bool, plus one short
            human-readable string per field that differs.
    """
    with _forced_run_order(run_order):
        rebuilt = store_from_laz(
            path, origin=(store.x_min, store.y_min, store.z_min),
            cell_xy=store.cell_xy, cell_z=store.cell_z)
    return stores_equal(store, rebuilt)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _add_legacy_compat(argv: list[str] | None) -> list[str] | None:
    """Accept the pre-subcommand spelling ``reconstruct <npz> <out>``.

    The original CLI took two bare positionals; scripts and the docs use it,
    so a first argument that is not a known subcommand is treated as the old
    form and rewritten to ``to-laz``.
    """
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    subs = {"to-laz", "to-npz", "verify", "-h", "--help"}
    if args and args[0] not in subs:
        return ["to-laz"] + args
    return args


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point for the ``to-laz``, ``to-npz`` and ``verify`` verbs.

    *argv* is first passed through _add_legacy_compat(), so the old
    two-positional spelling still means ``to-laz``. ``to-laz`` loads the
    ``.npz`` and writes it with store_to_las() (``--mode``,
    ``--one-per-voxel``, ``--z-base``, ``--epsg``, ``--no-grid-vlr``), then
    with ``--verify`` rebuilds from the file and returns 0 for EXACT or 1
    for MISMATCH; ``to-npz`` rebuilds a store from a LAS/LAZ
    (``--origin``, ``--cell-xy``, ``--cell-z`` when the file has no grid
    VLR), saves it and returns 0; ``verify`` compares a ``.npz`` against a
    ``.laz`` and returns 0 or 1 the same way. Differences are logged at
    ERROR level.
    """
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m voxelizer.reconstruct",
        description="Convert between a ColumnStore .npz and a LAS/LAZ file.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_out = sub.add_parser(
        "to-laz", help="Reconstruct a LAS/LAZ file from a ColumnStore .npz")
    p_out.add_argument("npz", type=Path, help="Input .npz (ColumnStore)")
    p_out.add_argument("output", type=Path, help="Output .las or .laz path")
    p_out.add_argument("--mode", choices=EXPORT_MODES, default=None,
                       help="Export mode (default: density). 'exact' is the "
                            "only bit-exact round trip.")
    p_out.add_argument("--one-per-voxel", action="store_true",
                       help="Legacy alias for --mode one_per_voxel")
    p_out.add_argument("--z-base", action="store_true",
                       help="Use interval base (not centre) for z")
    p_out.add_argument("--epsg", type=int, default=DEFAULT_EPSG,
                       help=f"CRS written into the header (default: {DEFAULT_EPSG})")
    p_out.add_argument("--no-grid-vlr", action="store_true",
                       help="Omit the IARBRE grid VLR (the rebuild then needs "
                            "--origin/--cell-xy/--cell-z)")
    p_out.add_argument("--verify", action="store_true",
                       help="After writing, rebuild from the file and assert "
                            "it matches the source store")

    p_in = sub.add_parser(
        "to-npz", help="Rebuild a ColumnStore .npz from a LAS/LAZ file")
    p_in.add_argument("laz", type=Path, help="Input .las/.laz")
    p_in.add_argument("output", type=Path, help="Output .npz path")
    p_in.add_argument("--origin", type=float, nargs=3, default=None,
                      metavar=("X", "Y", "Z"),
                      help="Grid origin, if the file has no IARBRE VLR")
    p_in.add_argument("--cell-xy", type=float, default=None)
    p_in.add_argument("--cell-z", type=float, default=None)

    p_v = sub.add_parser(
        "verify", help="Check that a .laz rebuilds a .npz exactly")
    p_v.add_argument("npz", type=Path)
    p_v.add_argument("laz", type=Path)

    args = parser.parse_args(_add_legacy_compat(argv))
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.cmd == "to-laz":
        store = ColumnStore.load(args.npz)
        store_to_las(store, args.output, mode=args.mode,
                     one_per_voxel=args.one_per_voxel,
                     z_as_centre=not args.z_base, epsg=args.epsg,
                     grid_vlr=not args.no_grid_vlr)
        if args.verify:
            ok, diffs = verify_roundtrip(store, args.output)
            for d in diffs:
                logger.error("  %s", d)
            logger.info("verify: %s", "EXACT" if ok else "MISMATCH")
            return 0 if ok else 1
        return 0

    if args.cmd == "to-npz":
        origin = tuple(args.origin) if args.origin else None
        store = store_from_laz(args.laz, origin=origin, cell_xy=args.cell_xy,
                               cell_z=args.cell_z)
        store.save(args.output)
        logger.info("wrote %s (%s columns, %s intervals)", args.output,
                    f"{len(store.columns):,}", f"{store.n_intervals:,}")
        return 0

    store = ColumnStore.load(args.npz)
    ok, diffs = verify_roundtrip(store, args.laz)
    for d in diffs:
        logger.error("  %s", d)
    logger.info("verify: %s", "EXACT" if ok else "MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
