"""
Shared geometry, colour and payload helpers for the visual output modules.
@ingroup t1_donnees


The three exporters answer different questions and are not interchangeable:

  * ``visualizer3d`` writes a self-contained HTML/PLY from one store
    (``--viz3d``, the ``viz3d_roi`` / ``viz3d_full`` stages);
  * ``tiled_exporter`` writes the streaming ``.bin`` + ``.idx.json`` payload
    and its view-dependent viewer (``--viz3d-stream``);
  * ``tileset_exporter`` consumes that payload and writes 3D Tiles
    (``tileset_cli``, the Cesium and Unreal delivery).

Everything they share lives here so it is defined once: the colour lookup
table (the two store-driven viewers, plus the 2-D map renderer in
visualization), the 32-byte record layout (the streaming payload and the
3D Tiles exporter, which re-reads those bytes), and the unit-box mesh
(visualizer3d's double-sided meshes and the glTF exporter's culling-safe
one). What stays in each exporter is what actually differs - the payload
assembly and the page each one ships.
"""
from __future__ import annotations

import numpy as np

from .classes_config import CLASS_COLORS

#: Grey used for any class code with no entry in ``CLASS_COLORS``.
CLASS_LUT_UNKNOWN_RGB = 128


def class_color_lut() -> np.ndarray:
    """256x3 uint8 colour table indexed by class code.

    ``CLASS_COLORS`` where defined, mid-grey (128, 128, 128) everywhere else,
    so an unexpected code renders visibly neutral rather than black.
    """
    lut = np.full((256, 3), CLASS_LUT_UNKNOWN_RGB, dtype=np.uint8)
    for code, rgb in CLASS_COLORS.items():
        if 0 <= int(code) < 256:
            lut[int(code)] = rgb
    return lut


#: The 32-byte instancing record both the streaming payload and the 3D Tiles
#: exporter write: position, box size, colour, class code.
REC_DTYPE = np.dtype([
    ("xyz", "<f4", 3),
    ("siz", "<f4", 3),
    ("rgb", "<u1", 3),
    ("cls", "<u1", 5),
])
REC_BYTES = REC_DTYPE.itemsize  # 32

#: The eight corners of a 1x1x1 box centred at the origin.
_UNIT_CORNERS = np.array([
    [-0.5, -0.5, -0.5], [ 0.5, -0.5, -0.5],
    [ 0.5,  0.5, -0.5], [-0.5,  0.5, -0.5],
    [-0.5, -0.5,  0.5], [ 0.5, -0.5,  0.5],
    [ 0.5,  0.5,  0.5], [-0.5,  0.5,  0.5],
], dtype=np.float32)

#: The twelve triangles of the box as index triples into ``_UNIT_CORNERS``.
#: Wound so that the right-hand-rule normal points INTO the box (clockwise as
#: seen from outside). Both consumers render double-sided, so winding does not
#: matter to them; it is the opposite of the glTF mesh in
#: :func:`unit_box_gltf`, which needs counter-clockwise outward winding for
#: back-face culling under ``doubleSided: false``.
_UNIT_TRIS = np.array([
    [0, 1, 2], [0, 2, 3],   # bottom
    [4, 6, 5], [4, 7, 6],   # top
    [0, 4, 5], [0, 5, 1],   # -y
    [1, 5, 6], [1, 6, 2],   # +x
    [2, 6, 7], [2, 7, 3],   # +y
    [3, 7, 4], [3, 4, 0],   # -x
], dtype=np.int64)


def unit_box_corners() -> np.ndarray:
    """The 8 corners of a 1x1x1 box centred at the origin (float32)."""
    return _UNIT_CORNERS.copy()


def unit_box_tris() -> np.ndarray:
    """The 12 triangle index triples of the unit box (int64)."""
    return _UNIT_TRIS.copy()


def unit_box_gltf() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (positions, normals, indices) for the unit box as glTF wants it.

    36 vertices: each of the six faces is emitted as two triangles with its
    four corners duplicated rather than shared, so every vertex carries one
    face normal. The index buffer is therefore an identity mapping over those
    36 vertices. Counter-clockwise winding when viewed from outside, which
    back-face culling under ``doubleSided: false`` requires.
    """
    corners = _UNIT_CORNERS
    # Each quad (a, b, c, d) wound counter-clockwise from outside.
    faces = [
        (1, 2, 6, 5, ( 1,  0,  0)),   # +X
        (0, 4, 7, 3, (-1,  0,  0)),   # -X
        (3, 7, 6, 2, ( 0,  1,  0)),   # +Y
        (1, 5, 4, 0, ( 0, -1,  0)),   # -Y
        (4, 5, 6, 7, ( 0,  0,  1)),   # +Z
        (0, 3, 2, 1, ( 0,  0, -1)),   # -Z
    ]

    verts: list[np.ndarray] = []
    norms: list[np.ndarray] = []
    for a, b, c, d, n in faces:
        normal = np.array(n, dtype=np.float32)
        for tri in ((a, b, c), (a, c, d)):
            for idx in tri:
                verts.append(corners[idx])
                norms.append(normal)

    positions = np.asarray(verts, dtype=np.float32).reshape(-1, 3)
    normals = np.asarray(norms, dtype=np.float32).reshape(-1, 3)
    # Flat, not (n_tris, 3): glTF index accessors are a flat uint sequence,
    # and the exporter this replaces returned the same shape.
    indices = np.arange(positions.shape[0], dtype=np.uint32)
    return positions, normals, indices
