"""
3D Tiles exporter - converts tiled_exporter's ``.bin + .idx.json`` into a
``tileset.json`` with per-tile ``.glb`` content files.
@ingroup t1_donnees


Each tile writes the 32-byte instanced-box records into a glTF 2.0 binary
using ``EXT_mesh_gpu_instancing``: one shared unit-box mesh per class with
per-instance ``TRANSLATION`` + ``SCALE`` attributes.  This avoids expanding
every box to its own triangles: per box, ~1 kB of triangulated geometry
(the 36-vertex non-indexed layout ``_make_unit_box`` builds - 36 positions
and 36 normals at 12 B each, plus 36 uint32 indices, 1008 B) becomes 24 B
of instance data. The retired triangulating exporter predates this
repository, so the order-of-magnitude time/size saving is a design
estimate, not a benchmark.

``convert_to_3d_tiles_lod`` writes the same tileset with an LOD pyramid
over those leaves (2x2 grouping + voxel downsampling per level), streamed:
leaves are walked in Morton (Z-)order, so each interior node is written and
freed as its last child arrives - peak memory is one open node per level.

The native CRS is EPSG:3946 (RGF93/CC46, projected metric), recorded in the
tileset's ``extras.crs``. When the payload index carries a non-zero origin the
exporter also writes a georeferenced ``root.transform`` (local grid frame to
ECEF via ``_enu_to_ecef_transform``, geoid and grid convergence included), so
Cesium / iTowns place the tileset on the globe directly; only when that
transform cannot be built does the tileset stay in its local frame.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

import numpy as np

from .classes_config import CLASS_COLORS, CLASS_NAMES
from .viz_common import (CLASS_LUT_UNKNOWN_RGB, REC_BYTES, REC_DTYPE,
                         unit_box_gltf)

logger = logging.getLogger(__name__)


def _write_launchers(out_dir: Path) -> None:
    """Drop the click-to-launch files beside a finished ``tileset.json``.

    A tileset is not a document a browser opens: it needs an HTTP origin and a
    viewer page. The launchers carry that knowledge next to the data, so an
    export is one double-click away from a picture. Never fatal - an export
    that produced its tiles is complete whether or not a helper file could be
    written (a read-only or full destination, say).
    """
    try:
        from .launchers import write_tileset_launchers
        write_tileset_launchers(out_dir)
    except Exception as exc:  # noqa: BLE001 - convenience, not correctness
        logger.warning("could not write the viewer launchers in %s: %s",
                       out_dir, exc)


# The 32-byte record layout, shared with tiled_exporter (which writes the
# bytes this module re-reads); see viz_common.REC_DTYPE.
_REC_DTYPE = REC_DTYPE
_REC_BYTES = REC_BYTES            # 32
assert _REC_BYTES == 32


# ----------------------------------------------------------------------
# Shared unit-box geometry (one mesh template, used by every class)
# ----------------------------------------------------------------------

def _make_unit_box() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (positions, normals, indices) for a 1x1x1 box centred at origin.

    A 36-vertex non-indexed layout (6 faces x 2 triangles x 3 verts), wound
    counter-clockwise when viewed from outside, which back-face culling under
    ``doubleSided: false`` requires. Built by
    viz_common.unit_box_gltf(). That builder is this exporter's alone;
    viz_common keeps it beside unit_box_corners()/unit_box_tris(), the
    clockwise-inward pair visualizer3d uses for its double-sided meshes.
    """
    return unit_box_gltf()


# ----------------------------------------------------------------------
# Per-tile GLB writer (instanced)
# ----------------------------------------------------------------------

def _tile_glb(records: np.ndarray, glb_path: str | Path) -> None:
    """Write one tile as a .glb using ``EXT_mesh_gpu_instancing``.

    One shared unit-box mesh per class.  Per-instance ``TRANSLATION`` and
    ``SCALE`` arrays are written directly from the 32-byte records (no
    per-box triangulation loop).
    """
    import pygltflib as gltf

    cls_codes = np.unique(records["cls"][:, 0])
    if cls_codes.shape[0] == 0:
        return

    g = gltf.GLTF2()
    g.scene = 0
    g.scenes = [gltf.Scene(nodes=[])]
    g.asset = gltf.Asset(generator="Voxel3DTiles_exporter", version="2.0")
    g.extensionsUsed = ["EXT_mesh_gpu_instancing"]
    g.extensionsRequired = ["EXT_mesh_gpu_instancing"]

    buffer_data = bytearray()

    # -- 1. Shared unit-box geometry -----------------------------------
    box_pos, box_nrm, box_idx = _make_unit_box()
    n_box_verts = 36
    n_box_idx = 36

    off = len(buffer_data)
    pos_bytes = box_pos.tobytes()
    buffer_data.extend(pos_bytes)
    g.bufferViews.append(gltf.BufferView(
        buffer=0, byteOffset=off, byteLength=len(pos_bytes),
        target=gltf.ARRAY_BUFFER))
    acc_pos = gltf.Accessor(
        bufferView=len(g.bufferViews) - 1,
        componentType=gltf.FLOAT, count=n_box_verts, type=gltf.VEC3,
        min=[-0.5, -0.5, -0.5], max=[0.5, 0.5, 0.5])
    g.accessors.append(acc_pos)

    off = len(buffer_data)
    nrm_bytes = box_nrm.tobytes()
    buffer_data.extend(nrm_bytes)
    g.bufferViews.append(gltf.BufferView(
        buffer=0, byteOffset=off, byteLength=len(nrm_bytes),
        target=gltf.ARRAY_BUFFER))
    acc_nrm = gltf.Accessor(
        bufferView=len(g.bufferViews) - 1,
        componentType=gltf.FLOAT, count=n_box_verts, type=gltf.VEC3,
        min=[-1, -1, -1], max=[1, 1, 1])
    g.accessors.append(acc_nrm)

    off = len(buffer_data)
    idx_bytes = box_idx.tobytes()
    buffer_data.extend(idx_bytes)
    g.bufferViews.append(gltf.BufferView(
        buffer=0, byteOffset=off, byteLength=len(idx_bytes),
        target=gltf.ELEMENT_ARRAY_BUFFER))
    acc_idx = gltf.Accessor(
        bufferView=len(g.bufferViews) - 1,
        componentType=gltf.UNSIGNED_INT, count=n_box_idx, type=gltf.SCALAR,
        min=[0], max=[n_box_verts - 1])
    g.accessors.append(acc_idx)

    POS_ACC = len(g.accessors) - 3  # index of position accessor
    NRM_ACC = len(g.accessors) - 2
    IDX_ACC = len(g.accessors) - 1

    # -- 2. Materials --------------------------------------------------
    mat_for_cls: dict[int, int] = {}
    for cls in cls_codes:
        cls_int = int(cls)
        rgb = CLASS_COLORS.get(cls_int, [CLASS_LUT_UNKNOWN_RGB] * 3)
        name = CLASS_NAMES.get(cls_int, f"cls_{cls_int}")
        g.materials.append(gltf.Material(
            name=f"mat_{name}",
            pbrMetallicRoughness=gltf.PbrMetallicRoughness(
                baseColorFactor=[c / 255.0 for c in rgb] + [1.0],
                metallicFactor=0.0,
                roughnessFactor=0.8,
            ),
        ))
        mat_for_cls[cls_int] = len(g.materials) - 1

    # -- 3. Per-class instance data + meshes + nodes -------------------
    for cls in cls_codes:
        cls_int = int(cls)
        mask = records["cls"][:, 0] == cls
        subset = records[mask]
        n_inst = subset.shape[0]

        # TRANSLATION = box centre
        trans = np.ascontiguousarray(subset["xyz"])
        off = len(buffer_data)
        t_bytes = trans.tobytes()
        buffer_data.extend(t_bytes)
        # No `target` on instance-attribute bufferViews: the
        # EXT_mesh_gpu_instancing spec forbids it (validators warn).
        g.bufferViews.append(gltf.BufferView(
            buffer=0, byteOffset=off, byteLength=len(t_bytes)))
        tmin = [float(v) for v in trans.min(axis=0)]
        tmax = [float(v) for v in trans.max(axis=0)]
        acc_t = gltf.Accessor(
            bufferView=len(g.bufferViews) - 1,
            componentType=gltf.FLOAT, count=n_inst, type=gltf.VEC3,
            min=tmin, max=tmax)
        g.accessors.append(acc_t)

        # SCALE = box dimensions
        scl = np.ascontiguousarray(subset["siz"])
        off = len(buffer_data)
        s_bytes = scl.tobytes()
        buffer_data.extend(s_bytes)
        g.bufferViews.append(gltf.BufferView(
            buffer=0, byteOffset=off, byteLength=len(s_bytes)))
        smin = [float(v) for v in scl.min(axis=0)]
        smax = [float(v) for v in scl.max(axis=0)]
        acc_s = gltf.Accessor(
            bufferView=len(g.bufferViews) - 1,
            componentType=gltf.FLOAT, count=n_inst, type=gltf.VEC3,
            min=smin, max=smax)
        g.accessors.append(acc_s)

        # Mesh (same geometry, class-specific material)
        name = CLASS_NAMES.get(cls_int, f"cls_{cls_int}")
        mesh_idx = len(g.meshes)
        g.meshes.append(gltf.Mesh(
            name=f"cls_{cls_int}_{name}",
            primitives=[gltf.Primitive(
                attributes=gltf.Attributes(POSITION=POS_ACC, NORMAL=NRM_ACC),
                indices=IDX_ACC,
                material=mat_for_cls[cls_int],
            )],
        ))

        # Node with EXT_mesh_gpu_instancing
        node = gltf.Node(
            name=f"cls_{cls_int}_{name}",
            mesh=mesh_idx,
            extensions={
                "EXT_mesh_gpu_instancing": {
                    "attributes": {
                        "TRANSLATION": len(g.accessors) - 2,
                        "SCALE": len(g.accessors) - 1,
                    }
                }
            },
        )
        g.nodes.append(node)

    # glTF content is +Y-up by spec; 3D Tiles runtimes rotate glTF content
    # +90 deg about X at load time (glTF +Y -> tileset +Z). Our instance data
    # is authored Z-up (x=east, y=north, z=up), so parent everything under a
    # -90 deg X rotation: the runtime's rotation then cancels it exactly and
    # the content lands Z-up inside its (Z-up) bounding volumes.
    class_nodes = list(range(len(g.nodes)))
    g.nodes.append(gltf.Node(
        name="zup_to_yup",
        rotation=[-0.7071067811865476, 0.0, 0.0, 0.7071067811865476],
        children=class_nodes,
    ))
    g.scenes[0].nodes = [len(g.nodes) - 1]

    g.buffers.append(gltf.Buffer(byteLength=len(buffer_data)))
    g.set_binary_blob(bytes(buffer_data) if buffer_data else None)
    g.save(str(glb_path))


# ----------------------------------------------------------------------
# Georeferencing: local ENU frame (origin, in `crs`) -> ECEF
# ----------------------------------------------------------------------

def _enu_to_ecef_transform(
    easting: float, northing: float, height: float, crs: str,
    *,
    vertical_crs: str | None = "EPSG:5720",
    height_offset: float | None = None,
) -> list[float] | None:
    """Column-major 4x4 matrix mapping the tileset's local East-North-Up
    frame (centred at ``(easting, northing, height)`` in ``crs``) to
    ECEF (EPSG:4978), as required by ``tileset.json``'s ``root.transform``.

    LiDAR altitudes are orthometric (NGF-IGN69 for the Lyon deliveries),
    but ECEF needs ellipsoidal heights - the difference is the geoid
    undulation (~+49.7 m at Lyon). ``vertical_crs`` (default EPSG:5720 =
    NGF-IGN69) makes pyproj apply the real geoid grid, fetched from the
    PROJ CDN on first use. If the grid can't be applied (offline, no
    cache) a WARNING is logged and heights stay orthometric - the model
    then sits ~50 m low against real terrain. ``height_offset`` (metres,
    added to ``height``) overrides the grid entirely; ``vertical_crs=None``
    disables vertical handling (legacy behaviour).

    Returns ``None`` if the CRS can't be resolved (e.g. offline / bad code),
    in which case the tileset stays in its unplaced local frame.
    """
    import math

    try:
        from pyproj import Transformer

        h_in = height + (height_offset or 0.0)
        lon = lat = h = None
        if height_offset is None and vertical_crs:
            try:
                import pyproj.network
                pyproj.network.set_network_enabled(True)
                compound = f"{crs}+{vertical_crs.split(':')[-1]}"
                to_geodetic = Transformer.from_crs(compound, "EPSG:4979",
                                                   always_xy=True)
                lon, lat, h = to_geodetic.transform(easting, northing, h_in)
                if abs(h - h_in) < 0.005:
                    logger.warning(
                        "Geoid grid for %s not applied (offline / grid "
                        "unavailable): origin height stays orthometric and "
                        "the model will sit ~50 m low against real terrain. "
                        "Install the RAF grid, go online, or pass an "
                        "explicit height offset.", vertical_crs)
                else:
                    logger.info("Geoid undulation applied: %+.2f m "
                                "(%s -> ellipsoidal)", h - h_in, vertical_crs)
            except Exception:
                logger.warning("Vertical CRS %s handling failed; treating "
                               "origin height as ellipsoidal.", vertical_crs,
                               exc_info=True)
                lon = None
        if lon is None:
            to_geodetic = Transformer.from_crs(crs, "EPSG:4979",
                                               always_xy=True)
            lon, lat, h = to_geodetic.transform(easting, northing, h_in)
        to_ecef = Transformer.from_crs("EPSG:4979", "EPSG:4978", always_xy=True)
        x, y, z = to_ecef.transform(lon, lat, h)
    except Exception:
        logger.warning("Could not georeference root transform for crs=%s "
                        "(offline or unknown code?); tileset will stay in "
                        "its local frame.", crs, exc_info=True)
        return None

    lat_r, lon_r = math.radians(lat), math.radians(lon)
    sl, cl = math.sin(lat_r), math.cos(lat_r)
    sg, cg = math.sin(lon_r), math.cos(lon_r)

    east = (-sg, cg, 0.0)
    north = (-sl * cg, -sl * sg, cl)
    up = (cl * cg, cl * sg, sl)

    # ------------------------------------------------------------------
    # Grid convergence.
    #
    # The tileset's local x/y are PROJECTED grid axes (easting/northing in
    # `crs`), which are NOT true east/north: in a conformal conic like
    # RGF93/CC46 the meridians converge, so grid north is rotated away from
    # true north by the grid convergence - about 1.3 degrees at Lyon, growing
    # with distance from the projection's central meridian.
    #
    # Mapping the grid axes straight onto (east, north) as this function used
    # to do yaws the whole model about its origin. Measured against rigorous
    # reprojection that is roughly 23 m of displacement 1 km from the origin
    # and 49 m at 2.1 km: correct shapes in the wrong place, and increasingly
    # so with distance.
    #
    # Rather than compute the convergence analytically and risk a sign or
    # hemisphere convention error, determine where the grid axes ACTUALLY
    # point by finite difference through the same transformers used above: a
    # 1 m step along projected +x and +y, converted to ECEF and differenced.
    # This is convention-free and picks up the true local behaviour of the
    # projection, including its scale distortion.
    # ------------------------------------------------------------------
    try:
        step = 1.0
        gx_ecef = _grid_axis_ecef(easting, northing, h_in, h, crs,
                                  to_ecef, step, axis=0, origin_ecef=(x, y, z))
        gy_ecef = _grid_axis_ecef(easting, northing, h_in, h, crs,
                                  to_ecef, step, axis=1, origin_ecef=(x, y, z))
    except Exception:
        logger.warning("Grid-convergence correction failed for crs=%s; "
                       "falling back to the true-ENU basis, which yaws the "
                       "model by the convergence angle.", crs, exc_info=True)
        gx_ecef = gy_ecef = None

    if gx_ecef is not None and gy_ecef is not None:
        # Orthonormalise: projection distortion means the two grid axes are
        # not exactly orthogonal, and root.transform must stay a rigid frame.
        ex, ey, ez = _normalise(gx_ecef)
        # Remove any component of gy along the corrected x axis (Gram-Schmidt).
        dot = gy_ecef[0] * ex + gy_ecef[1] * ey + gy_ecef[2] * ez
        ny = (gy_ecef[0] - dot * ex,
              gy_ecef[1] - dot * ey,
              gy_ecef[2] - dot * ez)
        nx2, ny2, nz2 = _normalise(ny)
        # Up = x cross y, keeping the frame right-handed.
        ux = ey * nz2 - ez * ny2
        uy = ez * nx2 - ex * nz2
        uz = ex * ny2 - ey * nx2
        east, north, up = (ex, ey, ez), (nx2, ny2, nz2), _normalise((ux, uy, uz))

    return [
        east[0], east[1], east[2], 0.0,
        north[0], north[1], north[2], 0.0,
        up[0], up[1], up[2], 0.0,
        x, y, z, 1.0,
    ]


def default_bin_path(idx_path: str | Path) -> Path:
    """The ``.bin`` payload that goes with *idx_path*.

    ``tiled_exporter.export_tiled_from_store`` writes ``<out>.idx.json`` and
    ``<out>.bin`` from one stem, so the index carries a DOUBLE suffix and the
    payload does not. ``Path.with_suffix('.bin')`` replaces only the last one
    and yields ``<out>.idx.bin``, which is never written; the whole
    ``.idx.json`` tail has to come off. A path that does not end in
    ``.idx.json`` falls back to plain suffix replacement.
    """
    p = Path(idx_path)
    tail = ".idx.json"
    if p.name.endswith(tail):
        return p.with_name(p.name[: -len(tail)] + ".bin")
    return p.with_suffix(".bin")


def _normalise(v: tuple[float, float, float]) -> tuple[float, float, float]:
    """Return *v* scaled to unit length; raises ValueError for a zero-length vector."""
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if n == 0.0:
        raise ValueError("zero-length basis vector")
    return (v[0] / n, v[1] / n, v[2] / n)


def _grid_axis_ecef(easting, northing, h_in, h, crs, to_ecef, step, *,
                    axis: int, origin_ecef):
    """ECEF direction of the projected +x (axis=0) or +y (axis=1) axis.

    Steps ``step`` metres along that projected axis, converts through the same
    geodetic path as the origin, and differences in ECEF. The height passed in
    is the ALREADY-CORRECTED ellipsoidal height, so the step stays on the same
    height surface and the difference is purely horizontal.
    """
    from pyproj import Transformer                       # local: optional dep
    e2 = easting + (step if axis == 0 else 0.0)
    n2 = northing + (step if axis == 1 else 0.0)
    to_geodetic = Transformer.from_crs(crs, "EPSG:4979", always_xy=True)
    lon2, lat2, _ = to_geodetic.transform(e2, n2, h_in)
    x2, y2, z2 = to_ecef.transform(lon2, lat2, h)
    return (x2 - origin_ecef[0], y2 - origin_ecef[1], z2 - origin_ecef[2])


# ----------------------------------------------------------------------
# Tileset builder  (flat layout: root -> full-detail leaves)
# ----------------------------------------------------------------------

def convert_to_3d_tiles(
    idx_path: str | Path,
    bin_path: str | Path | None = None,
    out_dir: str | Path | None = None,
    *,
    root_geometric_error: float | None = None,
    crs: str = "EPSG:3946",
    vertical_crs: str | None = "EPSG:5720",
    height_offset: float | None = None,
    progress: bool = True,
) -> Path:
    """Convert ``.idx.json`` (+ ``.bin``) to a 3D Tiles tileset.

    @param idx_path             Path to the ``.idx.json`` produced by ``write_tiled_payload``.
    @param bin_path             Path to the ``.bin`` payload.  If *None*,
                                ``default_bin_path`` derives it by stripping the whole
                                ``.idx.json`` tail off *idx_path* and appending ``.bin`` - so
                                ``area_stream.idx.json`` pairs with ``area_stream.bin``, the
                                name the exporter actually writes.
    @param out_dir              Output directory for ``tileset.json`` + ``tile_*.glb``.  Defaults
                                to the parent of *idx_path*.
    @param root_geometric_error Tileset-level geometric error; the root tile itself gets half this
                                value.  Default: ``tile_m * sqrt(2) * 4``.
    @param crs                  Native CRS, written into ``extras.crs``.
    @param vertical_crs         Vertical CRS of the store's altitudes, compounded with *crs* so
                                ``root.transform`` converts them to the ellipsoidal heights ECEF
                                needs (default ``EPSG:5720`` = NGF-IGN69). ``None`` disables
                                vertical handling. See ``_enu_to_ecef_transform``.
    @param height_offset        Metres added to the origin height, overriding the geoid grid
                                entirely. Useful offline, where the grid cannot be fetched.
    @param progress             Log a progress line every 5 % of the tiles (default True).
    @return Path to the written ``tileset.json``.
    """
    idx_path = Path(idx_path)
    if bin_path is None:
        bin_path = default_bin_path(idx_path)
    bin_path = Path(bin_path)
    out_dir = Path(out_dir) if out_dir else idx_path.parent

    out_dir.mkdir(parents=True, exist_ok=True)

    with open(idx_path, encoding="utf-8") as f:
        idx: dict[str, Any] = json.load(f)

    # Memory-map instead of read_bytes(): the flat path used to load the
    # whole payload into RAM (OOM at the ~10 GB payloads the LOD path
    # already memmaps). frombuffer on memmap slices is zero-copy.
    # (mmap rejects empty files, so a 0-byte payload gets an empty array.)
    if bin_path.stat().st_size == 0:
        payload = np.empty(0, dtype=np.uint8)
    else:
        payload = np.memmap(bin_path, dtype=np.uint8, mode="r")

    tile_m = float(idx["tile_m"])
    n_tiles = len(idx["tiles"]["off"])

    if root_geometric_error is None:
        root_geometric_error = tile_m * math.sqrt(2) * 4

    # -- Per-tile GLB files -------------------------------------------
    tiles_json: list[dict[str, Any]] = []

    for ti in range(n_tiles):
        off = int(idx["tiles"]["off"][ti])
        cnt = int(idx["tiles"]["cnt"][ti])
        if cnt == 0:
            continue

        x0 = float(idx["tiles"]["x0"][ti])
        y0 = float(idx["tiles"]["y0"][ti])
        z0 = float(idx["tiles"]["z0"][ti])
        z1 = float(idx["tiles"]["z1"][ti])

        chunk = payload[off:off + cnt * _REC_BYTES]
        records = np.frombuffer(chunk, dtype=_REC_DTYPE, count=cnt)

        glb_name = f"tile_{ti}.glb"
        glb_path = out_dir / glb_name
        _tile_glb(records, glb_path)

        # Bounding volume (centred-frame box)
        cx = x0 + tile_m / 2
        cy = y0 + tile_m / 2
        cz = (z0 + z1) / 2
        hz = max((z1 - z0) / 2, 0.5)

        tiles_json.append({
            "boundingVolume": {
                "box": [cx, cy, cz,
                        tile_m / 2, 0, 0,
                        0, tile_m / 2, 0,
                        0, 0, hz],
            },
            # Leaves are full detail: geometricError 0, matching the LOD
            # exporter's leaf convention. A non-zero error on a childless
            # node tells a client there is finer detail to refine to when
            # there is none, so leaves must report zero.
            "geometricError": 0.0,
            "content": {"uri": glb_name},
        })

        if progress and (ti + 1) % max(1, n_tiles // 20) == 0:
            logger.info("  tile %d/%d  (%d boxes)", ti + 1, n_tiles, cnt)

    if not tiles_json:
        logger.warning("No non-empty tiles found; writing minimal tileset.")
        tiles_json = []

    # -- Root tile -----------------------------------------------------
    bb = idx.get("bbox", [0] * 6)
    if all(v == 0 for v in bb) and tiles_json:
        root_cx, root_cy, root_cz = 0.0, 0.0, 0.0
        root_hx, root_hy, root_hz = tile_m, tile_m, tile_m
    else:
        root_cx = (bb[0] + bb[3]) / 2
        root_cy = (bb[1] + bb[4]) / 2
        root_cz = (bb[2] + bb[5]) / 2
        root_hx = (bb[3] - bb[0]) / 2 or tile_m
        root_hy = (bb[4] - bb[1]) / 2 or tile_m
        root_hz = max((bb[5] - bb[2]) / 2, 0.5)

    tileset: dict[str, Any] = {
        "asset": {
            "version": "1.1",
            "generator": "Voxel3DTiles_exporter",
        },
        "extras": {
            "crs": crs,
            "origin": idx.get("origin", [0, 0, 0]),
            "n_records": idx.get("n_records", 0),
            "cell_xy": idx.get("cell_xy", 1.0),
            "cell_z": idx.get("cell_z", 1.0),
            "tile_m": tile_m,
        },
        "geometricError": root_geometric_error,
        "root": {
            "boundingVolume": {
                "box": [root_cx, root_cy, root_cz,
                        root_hx, 0, 0, 0, root_hy, 0, 0, 0, root_hz],
            },
            "geometricError": root_geometric_error / 2,
            "refine": "REPLACE",
            "children": tiles_json,
        },
    }

    origin = idx.get("origin")
    if origin and any(origin):
        ecef_transform = _enu_to_ecef_transform(
            origin[0], origin[1], origin[2], crs,
            vertical_crs=vertical_crs, height_offset=height_offset)
        if ecef_transform is not None:
            tileset["root"]["transform"] = ecef_transform
    # The root is a pure bounding volume with children - no content.
    # (It used to point at tile_0.glb, so viewers briefly stretched one
    # arbitrary tile_m-sized tile across the whole area's bounding volume.)

    tileset_path = out_dir / "tileset.json"
    with open(tileset_path, "w", encoding="utf-8") as f:
        json.dump(tileset, f, indent=2)

    _write_launchers(out_dir)
    logger.info(
        "3D Tiles export: %d tiles -> %s", len(tiles_json), tileset_path)
    return tileset_path
# ----------------------------------------------------------------------
# LOD pyramid builder
# ----------------------------------------------------------------------
#
# The flat exporter above emits root -> N full-detail leaves, which forces
# every client to fetch the whole payload as soon as the area is framed.
# The functions below add coarse interior levels: leaves (the existing
# tile_m-sized tiles at full voxel resolution) are grouped 2x2 per level,
# and each interior node carries REAL content produced by downsampling its
# children's boxes (class-aware z-interval union on a coarser grid). Viewers
# then show a cheap overview immediately and only stream leaves near the
# camera.


def _union_class_intervals(
    colx: np.ndarray, coly: np.ndarray, cls: np.ndarray,
    zs: np.ndarray, ze: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Merge overlapping/touching integer z-intervals per (colx, coly, cls).

    All inputs are int arrays; ``zs``/``ze`` must be >= 0.
    Returns the merged (colx, coly, cls, zs, ze).
    """
    order = np.lexsort((zs, cls, coly, colx))
    cx, cy, cc = colx[order], coly[order], cls[order]
    s = zs[order].astype(np.int64)
    e = ze[order].astype(np.int64)

    n = cx.shape[0]
    grp = np.empty(n, dtype=bool)
    grp[0] = True
    grp[1:] = (cx[1:] != cx[:-1]) | (cy[1:] != cy[:-1]) | (cc[1:] != cc[:-1])
    gid = np.cumsum(grp) - 1

    # Group-local running max of interval ends, computed globally by
    # offsetting each group into its own disjoint value range.
    big = int(e.max()) + 2
    off = gid * big
    cummax_e = np.maximum.accumulate(e + off)

    new_seg = np.empty(n, dtype=bool)
    new_seg[0] = True
    new_seg[1:] = (s[1:] + off[1:]) > cummax_e[:-1]

    starts = np.flatnonzero(new_seg)
    m_ze = np.maximum.reduceat(e, starts)
    return cx[starts], cy[starts], cc[starts], s[starts], m_ze


def _coarsen_records(
    rec: np.ndarray,
    pitch_xy: float,
    step_z: float,
    bx0: float, by0: float, bz0: float,
) -> np.ndarray:
    """Downsample box records onto a coarser grid.

    Boxes are binned to ``pitch_xy`` columns; per (column, class) their
    z-extents are quantised to ``step_z`` and unioned. Returns new records
    in the same ``_REC_DTYPE`` layout (area-local coordinates preserved).
    """
    from .classes_config import CLASS_COLORS

    if rec.shape[0] == 0:
        return rec

    x = rec["xyz"][:, 0].astype(np.float64)
    y = rec["xyz"][:, 1].astype(np.float64)
    z = rec["xyz"][:, 2].astype(np.float64)
    sz = rec["siz"][:, 2].astype(np.float64)

    colx = np.floor((x - bx0) / pitch_xy).astype(np.int64)
    coly = np.floor((y - by0) / pitch_xy).astype(np.int64)
    # The epsilon snaps float32 wobble on exact cell boundaries back to the
    # boundary, so a box that merely grazes a coarse cell cannot bleed into
    # it; the np.maximum on the next line (not the epsilon) is what
    # guarantees every interval keeps at least one cell of height.
    zs = np.floor((z - sz * 0.5 - bz0) / step_z + 1e-6).astype(np.int64)
    ze = np.ceil((z + sz * 0.5 - bz0) / step_z - 1e-6).astype(np.int64)
    ze = np.maximum(ze, zs + 1)
    zs = np.maximum(zs, 0)

    cx, cy, cc, mzs, mze = _union_class_intervals(
        colx, coly, rec["cls"][:, 0], zs, ze)

    out = np.empty(cx.shape[0], dtype=_REC_DTYPE)
    out["xyz"][:, 0] = bx0 + (cx + 0.5) * pitch_xy
    out["xyz"][:, 1] = by0 + (cy + 0.5) * pitch_xy
    out["xyz"][:, 2] = bz0 + (mzs + mze) * 0.5 * step_z
    out["siz"][:, 0] = pitch_xy
    out["siz"][:, 1] = pitch_xy
    out["siz"][:, 2] = (mze - mzs) * step_z
    out["cls"][:, :] = 0
    out["cls"][:, 0] = cc
    for code in np.unique(cc):
        out["rgb"][cc == code] = CLASS_COLORS.get(
            int(code), [CLASS_LUT_UNKNOWN_RGB] * 3)
    return out


def _morton_key(i: int, j: int) -> int:
    """Interleave the bits of two non-negative tile indices (Z-order).

    Sorting leaves by this key makes the descendants of every quadtree
    node a contiguous run at every level, which is the property the LOD
    cascade in ``convert_to_3d_tiles_lod`` relies on to write each
    interior node the moment its last child arrives. 21 bits per axis
    covers two million tiles per side, far beyond any real area.
    """
    key = 0
    for b in range(21):
        key |= ((i >> b) & 1) << (2 * b)
        key |= ((j >> b) & 1) << (2 * b + 1)
    return key


def convert_to_3d_tiles_lod(
    idx_path: str | Path,
    bin_path: str | Path | None = None,
    out_dir: str | Path | None = None,
    *,
    crs: str = "EPSG:3946",
    vertical_crs: str | None = "EPSG:5720",
    height_offset: float | None = None,
    error_factor: float = 8.0,
    reuse_leaf_glbs: bool = True,
    progress: bool = True,
) -> Path:
    """Convert ``.idx.json`` + ``.bin`` to a 3D Tiles tileset WITH an LOD
    pyramid: full-detail leaves plus coarse interior levels built by 2x2
    grouping and voxel downsampling.

    ``error_factor`` scales each level's geometricError relative to its
    voxel size (leaves get 0). ``reuse_leaf_glbs`` skips rewriting
    ``tile_N.glb`` files that already exist in *out_dir* - but only when
    the ``leaf_glbs.provenance.json`` sidecar shows they were built from
    this same ``.idx.json``/``.bin`` pair (by size + mtime); any mismatch
    rebuilds every leaf, so a re-export with different data or filters can
    never ship stale geometry under a fresh ``tileset.json``.

    ``bin_path`` defaults through ``default_bin_path``: the whole
    ``.idx.json`` tail comes off *idx_path* and ``.bin`` goes on, so
    ``area_stream.idx.json`` pairs with ``area_stream.bin``. ``out_dir``
    defaults to the parent of *idx_path*.

    ``vertical_crs`` and ``height_offset`` mean exactly what they mean in
    convert_to_3d_tiles(). ``progress`` is the same switch on a
    different cadence: a line every 10 % of the leaves here, every 5 % of
    the tiles there.

    @param idx_path         Path to the ``.idx.json`` produced by ``write_tiled_payload``.
    @param bin_path         Path to the ``.bin`` payload.  If *None*, ``default_bin_path``
                            derives it by stripping the whole ``.idx.json`` tail off *idx_path*
                            and appending ``.bin``.
    @param out_dir          Output directory for ``tileset.json``, ``tile_*.glb`` and
                            ``node_<lv>_<i>_<j>.glb``.  Defaults to the parent of *idx_path*.
    @param crs              Native CRS, written into ``extras.crs``.
    @param vertical_crs     Vertical CRS of the store's altitudes, compounded with *crs* so
                            ``root.transform`` converts them to the ellipsoidal heights ECEF
                            needs (default ``EPSG:5720`` = NGF-IGN69). ``None`` disables
                            vertical handling.
    @param height_offset    Metres added to the origin height, overriding the geoid grid
                            entirely. Useful offline, where the grid cannot be fetched.
    @param error_factor     Scales each level's geometricError relative to its voxel size;
                            a level *lv* node reports ``error_factor * cell_xy * 2**lv``
                            and leaves report 0 (default 8.0).
    @param reuse_leaf_glbs  Skip rewriting ``tile_N.glb`` files that already exist in *out_dir*,
                            but only when the ``leaf_glbs.provenance.json`` sidecar matches
                            this ``.idx.json``/``.bin`` pair by size + mtime; any mismatch
                            rebuilds every leaf (default True).
    @param progress         Log a progress line every 10 % of the leaves, then one per level
                            (default True).
    @return Path to the written ``tileset.json``.
    @throws RuntimeError When the top level of the quadtree holds more than one root cell, so
                         no single root node can be written.
    """
    idx_path = Path(idx_path)
    if bin_path is None:
        bin_path = default_bin_path(idx_path)
    bin_path = Path(bin_path)
    out_dir = Path(out_dir) if out_dir else idx_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    # A tile_N.glb carries no provenance of its own, and serve_tiles caches
    # on the premise that tile files change meaning every export - so reuse
    # is only sound while the leaves in out_dir still describe THIS source.
    # The sidecar records the source identity leaf content is a pure
    # function of: the .idx.json/.bin pair, by size + mtime (the transform
    # and error knobs shape only tileset.json, not the leaves). A missing
    # or mismatched sidecar rebuilds every leaf; it is (re)written only
    # after a completed export, so an interrupted run can never bless
    # half-rebuilt leaves as belonging to the new source.
    fp_path = out_dir / "leaf_glbs.provenance.json"
    fingerprint = {
        "idx": [idx_path.name, idx_path.stat().st_size,
                idx_path.stat().st_mtime_ns],
        "bin": [bin_path.name, bin_path.stat().st_size,
                bin_path.stat().st_mtime_ns],
    }
    if reuse_leaf_glbs:
        try:
            reuse_leaf_glbs = (
                json.loads(fp_path.read_text(encoding="utf-8")) == fingerprint)
        except (OSError, ValueError):
            reuse_leaf_glbs = False
        if not reuse_leaf_glbs:
            logger.info("leaf reuse: no provenance match for this "
                        ".idx.json/.bin in %s - rebuilding every tile_N.glb.",
                        out_dir)

    with open(idx_path, encoding="utf-8") as f:
        idx: dict[str, Any] = json.load(f)

    tile_m = float(idx["tile_m"])
    cell_xy = float(idx.get("cell_xy", 1.0))
    cell_z = float(idx.get("cell_z", 1.0))
    bb = idx["bbox"]  # area-local [xmin, ymin, zmin, xmax, ymax, zmax]
    bz0 = float(bb[2])

    n_tiles = len(idx["tiles"]["off"])
    cnts = np.asarray(idx["tiles"]["cnt"], dtype=np.int64)
    xs0 = np.asarray(idx["tiles"]["x0"], dtype=np.float64)[cnts > 0]
    ys0 = np.asarray(idx["tiles"]["y0"], dtype=np.float64)[cnts > 0]
    # Anchor the quadtree to the TILE grid, not the record bbox: tile
    # origins sit on their own tile_m-aligned grid whose min edge can lie
    # below the (record-derived) bbox minimum, which would round the first
    # tile row/column to index -1 (and -1 // 2 stays -1 all the way up).
    gx0 = float(xs0.min())
    gy0 = float(ys0.min())
    grid_w = int(round((xs0.max() - gx0) / tile_m)) + 1
    grid_h = int(round((ys0.max() - gy0) / tile_m)) + 1
    n_levels = max(1, math.ceil(math.log2(max(grid_w, grid_h))))

    if bin_path.stat().st_size == 0:  # mmap rejects empty files
        payload = np.empty(0, dtype=np.uint8)
    else:
        payload = np.memmap(bin_path, dtype=np.uint8, mode="r")

    # nodes[level][(i, j)] -> dict: 'parts' (pending record chunks) until
    # the node is written, then 'glb', 'bv', 'n_boxes'; leaves get
    # 'glb'/'bv' directly. Children are derived from index arithmetic in
    # _tile_json, never stored here.
    nodes: list[dict[tuple[int, int], dict[str, Any]]] = [
        {} for _ in range(n_levels + 1)]

    # -- Level 0: existing full-detail leaves --------------------------
    # Leaves are walked in MORTON (Z-)ORDER, which keeps the descendants of
    # every quadtree node contiguous at EVERY level - not just level 1, as
    # the earlier group-by-parent order did. That contiguity is what lets
    # the cascade below write each interior node the moment its last child
    # arrives and free it, at any depth: peak memory is one open node per
    # level instead of a whole level held in RAM. (The group-by-parent
    # order broke contiguity at level 2, so levels 2+ buffered the entire
    # area's coarsened records and a fine --tile-m export died there.)
    # Node content cannot depend on the walk: every part is tagged with
    # its child's identity and each flush sorts before concatenating.
    leaf_list = []
    for ti in range(n_tiles):
        if int(idx["tiles"]["cnt"][ti]) == 0:
            continue
        lx = float(idx["tiles"]["x0"][ti])
        ly = float(idx["tiles"]["y0"][ti])
        leaf_list.append((ti,
                          int(round((lx - gx0) / tile_m)),
                          int(round((ly - gy0) / tile_m))))
    leaf_list.sort(key=lambda t: _morton_key(t[1], t[2]))

    # open_key[lv] is the node currently accepting parts at that level;
    # in Morton order there is never more than one per level.
    open_key: list[tuple[int, int] | None] = [None] * (n_levels + 1)

    def _flush_node(lv: int) -> None:
        """Write the interior node open at level *lv* and hand its coarsened records to its parent.

        Sorts the node's pending parts by child tag, concatenates them,
        writes ``node_<lv>_<i>_<j>.glb``, records the glb name, the bounding
        volume hugging the records and the box count on the node, then (below
        the top level) coarsens the records by another factor of two and
        appends them as a part of the parent node at ``lv + 1``.
        """
        key = open_key[lv]
        node = nodes[lv][key]
        parts = node.pop("parts")
        # Canonical child order, independent of the walk: level-1 parts are
        # tagged with the leaf's payload index ti, deeper parts with the
        # child's (i, j) key.
        parts.sort(key=lambda p: p[0])
        rec = np.concatenate([arr for _, arr in parts])
        i, j = key
        glb_name = f"node_{lv}_{i}_{j}.glb"
        _tile_glb(rec, out_dir / glb_name)
        hx = rec["siz"][:, 0] * 0.5
        hy = rec["siz"][:, 1] * 0.5
        hz = rec["siz"][:, 2] * 0.5
        node["glb"] = glb_name
        node["bv"] = (
            float((rec["xyz"][:, 0] - hx).min()),
            float((rec["xyz"][:, 1] - hy).min()),
            float((rec["xyz"][:, 2] - hz).min()),
            float((rec["xyz"][:, 0] + hx).max()),
            float((rec["xyz"][:, 1] + hy).max()),
            float((rec["xyz"][:, 2] + hz).max()),
        )
        node["n_boxes"] = int(rec.shape[0])
        if lv < n_levels:
            coarse = _coarsen_records(rec, cell_xy * (2 ** (lv + 1)),
                                      cell_z * (2 ** (lv + 1)),
                                      gx0, gy0, bz0)
            pk = (i // 2, j // 2)
            nodes[lv + 1].setdefault(pk, {"parts": []})
            nodes[lv + 1][pk].setdefault("parts", []).append((key, coarse))

    for done, (ti, i, j) in enumerate(leaf_list, 1):
        # The cascade: a changed key at level lv means every leaf of the
        # node that was open there has been seen (Morton contiguity), so
        # it is complete. Flush ascending - a flush at lv appends the
        # coarsened node to its parent at lv+1, which may itself be
        # flushed one step later in this same loop.
        for lv in range(1, n_levels + 1):
            key = (i >> lv, j >> lv)
            if open_key[lv] is not None and open_key[lv] != key:
                _flush_node(lv)
            open_key[lv] = key

        cnt = int(idx["tiles"]["cnt"][ti])
        off = int(idx["tiles"]["off"][ti])
        x0 = float(idx["tiles"]["x0"][ti])
        y0 = float(idx["tiles"]["y0"][ti])
        z0 = float(idx["tiles"]["z0"][ti])
        z1 = float(idx["tiles"]["z1"][ti])

        rec = np.frombuffer(
            payload[off:off + cnt * _REC_BYTES].tobytes(), dtype=_REC_DTYPE)

        glb_name = f"tile_{ti}.glb"
        glb_path = out_dir / glb_name
        if not (reuse_leaf_glbs and glb_path.exists()):
            _tile_glb(rec, glb_path)

        nodes[0][(i, j)] = {
            "glb": glb_name,
            "bv": (x0, y0, z0, x0 + tile_m, y0 + tile_m, z1),
        }

        coarse = _coarsen_records(rec, cell_xy * 2, cell_z * 2, gx0, gy0, bz0)
        nodes[1].setdefault((i // 2, j // 2), {"parts": []})
        nodes[1][(i // 2, j // 2)].setdefault("parts", []).append((ti, coarse))

        if progress and done % max(1, n_tiles // 10) == 0:
            logger.info("  leaf %d/%d", done, n_tiles)

    # Drain the still-open path - children strictly before their parents,
    # so every partial node at every level is written, whatever the tile
    # count (it need not be a power of four).
    for lv in range(1, n_levels + 1):
        if open_key[lv] is not None:
            _flush_node(lv)

    if progress:
        for lv in range(1, n_levels + 1):
            logger.info("  level %d: %d nodes (streamed, voxel %.2gm)",
                        lv, len(nodes[lv]), cell_xy * (2 ** lv))

    # -- Assemble the tileset tree ------------------------------------
    # The two bv conventions above disagree: a leaf claims its whole
    # nominal tile_m square, an interior node hugs the coarsened records
    # it actually holds. A sparse leaf therefore sticks out of the parent
    # that hugs the same data (29 of 1024 parent->child pairs, 90 m worst
    # overhang, on the first shipped run), and the 3D Tiles spec requires
    # a child's boundingVolume to lie inside its parent's - a client is
    # free to cull the parent and never look at the part of the child
    # outside it. _tile_json therefore unions each node's bv with its
    # children's on the way back up, root included; leaf bvs are left as
    # they are, the nominal square does contain the leaf's own content.
    def _box(bv: tuple[float, ...]) -> dict[str, Any]:
        """Turn a ``(xmin, ymin, zmin, xmax, ymax, zmax)`` tuple into a 3D Tiles ``boundingVolume`` box (centre plus axis-aligned half-extents, z half-extent at least 0.05 m)."""
        cx = (bv[0] + bv[3]) / 2
        cy = (bv[1] + bv[4]) / 2
        cz = (bv[2] + bv[5]) / 2
        return {"box": [cx, cy, cz,
                        (bv[3] - bv[0]) / 2, 0, 0,
                        0, (bv[4] - bv[1]) / 2, 0,
                        0, 0, max((bv[5] - bv[2]) / 2, 0.05)]}

    def _floor_z(bv: tuple[float, ...]) -> tuple[float, ...]:
        """Return *bv* with its z extent widened symmetrically to 0.1 m about its centre when it is thinner than that, so the union matches the box _box() emits."""
        # Pre-apply the 0.05 m minimum z half-extent _box enforces, so the
        # union sees the box the client is actually handed: a flat child
        # inflated by that floor could otherwise still escape its parent.
        if bv[5] - bv[2] >= 0.1:
            return bv
        cz = (bv[2] + bv[5]) / 2
        return (bv[0], bv[1], cz - 0.05, bv[3], bv[4], cz + 0.05)

    def _tile_json(lv: int, i: int, j: int
                   ) -> tuple[dict[str, Any], tuple[float, ...]]:
        """Build the tileset.json entry of node (lv, i, j) recursively and return it with its final bounding tuple.

        ``geometricError`` is 0 for a leaf and ``error_factor * cell_xy *
        2**lv`` above; the four children are found by index arithmetic at
        ``lv - 1`` (with ``refine: REPLACE`` when any exist) and the node's
        z-floored bounding tuple is unioned with theirs before being
        converted by _box().
        """
        node = nodes[lv][(i, j)]
        g_err = 0.0 if lv == 0 else error_factor * cell_xy * (2 ** lv)
        t: dict[str, Any] = {
            "boundingVolume": None,  # filled below, after the child union
            "geometricError": g_err,
            "content": {"uri": node["glb"]},
        }
        bv = _floor_z(node["bv"])
        if lv > 0:
            kids = [
                _tile_json(lv - 1, ci, cj)
                for ci in (2 * i, 2 * i + 1)
                for cj in (2 * j, 2 * j + 1)
                if (ci, cj) in nodes[lv - 1]
            ]
            if kids:
                t["children"] = [kt for kt, _ in kids]
                t["refine"] = "REPLACE"
                for _, kbv in kids:
                    bv = (min(bv[0], kbv[0]), min(bv[1], kbv[1]),
                          min(bv[2], kbv[2]), max(bv[3], kbv[3]),
                          max(bv[4], kbv[4]), max(bv[5], kbv[5]))
        t["boundingVolume"] = _box(bv)  # assignment keeps the key first
        return t, bv

    top = sorted(nodes[n_levels].keys())
    if len(top) != 1:
        raise RuntimeError(f"expected a single root cell, got {top}")
    root, _ = _tile_json(n_levels, *top[0])
    root_ge = error_factor * cell_xy * (2 ** n_levels)

    tileset: dict[str, Any] = {
        "asset": {"version": "1.1", "generator": "Voxel3DTiles_exporter"},
        "extras": {
            "crs": crs,
            "origin": idx.get("origin", [0, 0, 0]),
            "n_records": idx.get("n_records", 0),
            "cell_xy": cell_xy,
            "cell_z": cell_z,
            "tile_m": tile_m,
            "lod_levels": n_levels + 1,
        },
        "geometricError": root_ge * 2,
        "root": root,
    }

    origin = idx.get("origin")
    if origin and any(origin):
        ecef = _enu_to_ecef_transform(
            origin[0], origin[1], origin[2], crs,
            vertical_crs=vertical_crs, height_offset=height_offset)
        if ecef is not None:
            tileset["root"]["transform"] = ecef

    tileset_path = out_dir / "tileset.json"
    with open(tileset_path, "w", encoding="utf-8") as f:
        json.dump(tileset, f, indent=2)

    _write_launchers(out_dir)
    fp_path.write_text(json.dumps(fingerprint, indent=2), encoding="utf-8")
    n_interior = sum(len(nodes[lv]) for lv in range(1, n_levels + 1))
    logger.info("LOD tileset: %d leaves + %d interior nodes (%d levels) -> %s",
                len(nodes[0]), n_interior, n_levels + 1, tileset_path)
    return tileset_path
