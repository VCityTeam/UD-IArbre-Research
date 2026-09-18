"""
LAZ/LAS file reading for the IA.rbre voxelizer.
@ingroup t0_socle


Here we isolate the `laspy` READ dependency: if we ever swap to PDAL
or a streaming reader, only this file changes. (LAS/LAZ writing is the
one exception: reconstruct.py builds its output files with laspy
directly.)
"""

from __future__ import annotations
import logging
import os
from pathlib import Path
import laspy
import numpy as np

logger = logging.getLogger(__name__)

"""
We force the LASzip backend because the lazrs backends are unreliable on
large IGN tiles (30M points and more):

 - LazBackend.LazrsParallel (laspy's default) intermittently corrupts the
    heap during decompression, on lazrs 0.6.3 and still on lazrs 0.8.1 with
    laspy 2.7.0. The damage surfaces later as a Windows "access violation"
    (SIGSEGV) inside voxelize, and it can also yield silently wrong data
    without crashing at all.
 - LazBackend.Lazrs (single-threaded) is no better on lazrs 0.8.1: a
    minority of reads raise `LazrsError: failed to fill whole buffer` or
    panic with `index out of bounds` in laz-rs's chunk-table decoder. The
    outcome varies between reads of identical bytes, so it is a decoder
    bug, not a property of the file or of this package.

LazBackend.Laszip (the mature C++ LASzip via the `laszip` pip package) is
stable under repeated reads of the same tiles, so we pin it. Keep `laszip`
installed (see requirements.txt). If you switch back to a lazrs backend,
stress-test the read path first. The failure modes are recorded in
ProblemSolving.md section 6.4.
"""
_LAZ_BACKEND = laspy.LazBackend.Laszip

# --- backend override hook (crash-isolation retries ONLY) -------------------
# ``VOXELIZER_LAZ_BACKEND`` may select an alternative decoder for THIS
# process. It exists so the sharded runner's per-tile isolation worker can be
# relaunched with a second-opinion backend after a native crash, without ever
# changing the pinned default above. Accepted values:
#   "laszip" (or unset)  -> the pinned default;
#   "lazrs"              -> single-threaded lazrs.
# "lazrs_parallel" is deliberately REJECTED: it corrupts memory on large IGN
# tiles (see the module docstring).
_env_backend = os.environ.get("VOXELIZER_LAZ_BACKEND", "").strip().lower()
if _env_backend == "lazrs":
    # The second-opinion backend is a standing dependency: it IS pinned in
    # requirements.txt (lazrs>=0.8.1) and therefore IS present in the Docker
    # image, which installs that file. It stays optional at RUNTIME only -
    # laszip remains the pinned default for every read, and lazrs is reached
    # solely through VOXELIZER_LAZ_BACKEND=lazrs during a --retry-lazrs
    # isolated retry. The guard below covers a bare/partial install where the
    # wheel is genuinely missing. If it is missing,
    # fall back to the pinned backend with a loud warning instead of
    # failing the retry with a confusing import error: the retry then
    # still covers the transient-memory-pressure case, just without the
    # alternative decoder.
    if laspy.LazBackend.Lazrs.is_available():
        _LAZ_BACKEND = laspy.LazBackend.Lazrs
        logger.warning("VOXELIZER_LAZ_BACKEND=lazrs: using single-thread "
                       "lazrs for this process (crash-isolation retry mode).")
    else:
        logger.warning("VOXELIZER_LAZ_BACKEND=lazrs requested but the lazrs "
                       "package is not installed - falling back to the "
                       "pinned laszip backend (plain retry, no second "
                       "opinion). pip install lazrs>=0.8.1 to enable it.")
elif _env_backend not in ("", "laszip"):
    raise ValueError(
        f"VOXELIZER_LAZ_BACKEND={_env_backend!r} is not supported; use "
        f"'laszip' or 'lazrs' (single-thread). LazrsParallel is refused on "
        f"purpose - it is unreliable on IGN tiles.")

_ALLOWED_CRS = {3946, 2154}  # RGF93/CC46 (EPSG:3946) / Lambert-93 (EPSG:2154)

# The CRS every Grand Lyon tile arrives in, and therefore the one anything we
# WRITE must carry: `_validate_crs` below rejects a file without one, so an
# export with no CRS is unreadable by our own reader.  ColumnStore has no CRS
# field (its `meta` array is five floats and widening it would break every
# existing .npz), so the default lives here, next to the validation it feeds.
DEFAULT_EPSG = 3946


# The first EPSG code this process accepts, and whether the mixing warning
# has already been given. Each allowed code is fine on its own, but they are
# DIFFERENT projections: the same ground point takes different coordinates in
# each, so tiles read under one do not share a column lattice with tiles read
# under the other. A store carries no CRS field, so nothing downstream can
# notice the mixture afterwards - this is the only place it is visible.
_run_epsg: int | None = None
_mixed_epsg_warned = False


def _validate_crs(header, filename: str) -> None:
    """Raise ValueError if the LAS/LAZ header CRS is missing or unsupported.

    Warns once per process when a run mixes the two allowed codes.
    """
    global _run_epsg, _mixed_epsg_warned
    crs = header.parse_crs()
    if crs is None:
        raise ValueError(
            f"{filename}: no CRS found in the LAS/LAZ header. "
            f"All input tiles must carry a valid CRS (expected EPSG:3946 (CC46) or EPSG:2154 (Lambert-93))."
        )
    epsg = crs.to_epsg()
    if epsg not in _ALLOWED_CRS:
        raise ValueError(
            f"{filename}: unsupported CRS {crs} (EPSG:{epsg}). "
            f"Expected one of {_ALLOWED_CRS} (RGF93/CC46 or Lambert-93)."
        )
    if _run_epsg is None:
        _run_epsg = epsg
    elif epsg != _run_epsg and not _mixed_epsg_warned:
        _mixed_epsg_warned = True
        logger.warning(
            "%s declares EPSG:%d, but this run has been reading EPSG:%d. "
            "Both codes are accepted, but they are different projections: "
            "the same ground point takes different coordinates in each, so "
            "these tiles do not share a column lattice and the merged store "
            "will be wrong at the seam. Reproject the inputs to one CRS.",
            filename, epsg, _run_epsg)


def read_laz(path: Path | str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Read a LAZ (or LAS) file and return the four arrays we care about.

    @param path Path to the .laz or .las file (``str`` or ``Path``).
    @return Tuple ``(x, y, z, cls)``: ``x``, ``y``, ``z`` are float64
        arrays of shape (N,) holding metric coordinates in the file's
        declared CRS (EPSG:3946 RGF93/CC46 or EPSG:2154 Lambert-93;
        ``_validate_crs`` rejects anything else); ``cls`` is a uint8 array
        of shape (N,) holding the ASPRS classification per point.
    @throws FileNotFoundError when ``path`` is not an existing file.
    @throws ValueError when the file is smaller than 227 bytes, when it
        does not start with the ``LASF`` signature, or (from
        ``_validate_crs``) when the header carries no CRS or one outside
        the allowed set.
    @throws RuntimeError when ``laspy.read`` fails to decode the file;
        the backend exception is chained as the cause.
    """
    path = Path(path)
    logger.info("Opening %s ...", path.name)

    # -- Fail fast with an actionable, file-named message on a bad download. --
    # A partially-downloaded or error-body tile (0 bytes, an HTML/JSON error
    # page saved under the .laz name, or a truncated payload) otherwise
    # surfaces as an opaque laspy/laszip error deep inside a worker thread.
    if not path.is_file():
        raise FileNotFoundError(f"LAZ/LAS file not found: {path}")
    size = path.stat().st_size
    if size < 227:  # smallest legal LAS public header (227 B in LAS 1.0-1.2;
                    # LAS 1.3 is 235 B and LAS 1.4 is 375 B)
        raise ValueError(
            f"{path.name} is only {size} B - too small to be a valid LAS/LAZ "
            f"(likely a failed/partial download; re-download this tile).")
    with open(path, "rb") as fh:
        signature = fh.read(4)
    if signature != b"LASF":
        raise ValueError(
            f"{path.name} does not start with the 'LASF' signature "
            f"(got {signature!r}); it is probably an HTML/JSON error page "
            f"saved under the .laz name. Delete it and re-download the tile.")

    """
    Performance Warning: laspy.read() loads the whole file into memory. The 2,842 Grand Lyon
    LiDAR tiles (the full IGN inventory, on a 500 m x 500 m grid; edge tiles' extents can be
    far smaller) have widely varying point counts:
    median 16,197,127 points, mean 18,266,085, up to ~61M (~70 MB compressed median, up to
    300 MB). In memory as float64 x/y/z + uint8 cls (25 B/point) the median tile is ~405 MB
    and the mean ~457 MB - decimal MB, where the corpus record states the same two
    quantities in MiB (386.2 and 435.5), which is the whole of the ~5 % gap between
    them - fine on modern systems. Streaming is not
    hypothetical: :func:`read_laz_chunks` below is that path, built on
    ``laspy.open(path).chunk_iterator(chunk_size=...)``, and it is what the
    chunked area pipeline uses. This function stays for callers that want the
    whole tile in one array.
    """
    try:
        las = laspy.read(str(path), laz_backend=_LAZ_BACKEND)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Failed to decode {path.name} ({size:,} B) with the LASzip "
            f"backend: {type(exc).__name__}: {exc}. The tile is most likely "
            f"truncated or corrupt - re-download it and try again."
        ) from exc

    _validate_crs(las.header, path.name)

    """
    `.x`, `.y`, `.z` apply the file's scale + offset and return real metres.
    The raw integer storage (`.X`, `.Y`, `.Z`) is faster but unitless.
    We need metres (units) for voxel-size reasoning.
    """
    x = np.asarray(las.x, dtype=np.float64)
    y = np.asarray(las.y, dtype=np.float64)
    z = np.asarray(las.z, dtype=np.float64)
    cls = np.asarray(las.classification, dtype=np.uint8)

    logger.info("  %d points read", len(x))
    return x, y, z, cls


def read_laz_header(path: Path | str) -> tuple[np.ndarray, np.ndarray, int]:
    """
    Read only the LAS/LAZ header - no point decompression.

    Cheap enough to call on every candidate tile: it lets ``process_area``
    (a) pick the single area-wide z floor for one shared vertical datum and
    (b) select tiles by extent-intersection without touching a single point.

    @param path Path to the .laz or .las file (``str`` or ``Path``).
    @return Tuple ``(mins, maxs, n_points)``: ``mins`` and ``maxs`` are
        float64 arrays ``[x, y, z]`` giving the tile's bounding box in
        metres in the file's CRS; ``n_points`` is the int total point
        count from the header.
    @throws ValueError (from ``_validate_crs``) when the header carries no
        CRS or one outside the allowed set.
    """
    path = Path(path)
    with laspy.open(str(path), laz_backend=_LAZ_BACKEND) as reader:
        header = reader.header
        mins = np.asarray(header.mins, dtype=np.float64)
        maxs = np.asarray(header.maxs, dtype=np.float64)
        n_points = int(header.point_count)
    _validate_crs(header, path.name)
    return mins, maxs, n_points


def read_vlr_bytes(path: Path | str, user_id: str,
                   record_id: int) -> bytes | None:
    """
    Return the raw payload of one VLR, or None when the file has no such
    record - header only, no point decompression.

    This lives here rather than in the caller because it is a *read* through
    laspy, and this module is the one place the laspy dependency is isolated
    (see the module header). ``reconstruct`` uses it to recover the voxel grid
    an exported file was built on.
    """
    path = Path(path)
    with laspy.open(str(path), laz_backend=_LAZ_BACKEND) as reader:
        for vlr in reader.header.vlrs:
            if vlr.user_id == user_id and int(vlr.record_id) == int(record_id):
                return bytes(vlr.record_data)
    return None


def read_laz_chunks(path: Path | str, chunk_size: int):
    """
    Stream a LAZ/LAS file in chunks of ``chunk_size`` points.

    Yields ``(x, y, z, cls)`` tuples (same dtypes as read_laz()) one
    chunk at a time, so the caller never holds more than one chunk of raw
    points in memory. This is the streaming escape hatch referenced in
    ``read_laz``'s performance note; it uses the same pinned Laszip backend
    as ``read_laz``.

    @param path Path to the .laz or .las file (``str`` or ``Path``).
    @param chunk_size Number of points per chunk handed to
        ``reader.chunk_iterator``.
    @return Generator yielding one ``(x, y, z, cls)`` tuple per chunk:
        ``x``, ``y``, ``z`` float64 arrays of metric coordinates in the
        file's CRS and ``cls`` a uint8 array of ASPRS classifications, all
        of the chunk's length.
    @throws ValueError (from ``_validate_crs``, on the first iteration)
        when the header carries no CRS or one outside the allowed set.
    """
    path = Path(path)
    logger.info("Streaming %s (chunk_size=%d) ...", path.name, chunk_size)
    with laspy.open(str(path), laz_backend=_LAZ_BACKEND) as reader:
        _validate_crs(reader.header, path.name)
        for points in reader.chunk_iterator(chunk_size):
            x = np.asarray(points.x, dtype=np.float64)
            y = np.asarray(points.y, dtype=np.float64)
            z = np.asarray(points.z, dtype=np.float64)
            cls = np.asarray(points.classification, dtype=np.uint8)
            yield x, y, z, cls