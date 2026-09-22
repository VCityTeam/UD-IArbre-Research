"""
Interactive 3-D viewers for the column-RLE voxel grid.
@ingroup t2_algos


The natural primitive for visualizing a ColumnStore in 3-D is the *interval*:
each interval is already an axis-aligned box of size (cell_xy x cell_xy x
n_voxels * cell_z), with a single class colour. A 500 m IGN tile (median ~16M points) produces a few
million intervals, too many to render as individual triangle meshes, but very
comfortable for GPU-instanced rendering.

The three primary entry points (the module also exports the geometry
collector/merger, the grid codec, the thin-stride estimator and the
shared render_store_3d driver):

    export_html(store, path)
        Write a single .html file using Three.js InstancedMesh. Open it in
        any modern browser. Mouse + WASD navigation (pointer-lock
        fly-through). This is the "truly traversable" path - no Python needed
        to view the result, no server, just open the file. The VOXEL DATA is
        self-contained (base64 in the page), but the Three.js modules are
        fetched from a CDN (unpkg, plus a pako fallback from jsdelivr on
        browsers without DecompressionStream), so the first load needs
        network access. See export_html's own docstring.

    show_pyvista(store)
        Open an interactive PyVista window in the current Python session.
        Optional dependency - `pip install pyvista`. Convenient when you want
        to explore the tile while inspecting `store` in the same REPL.

    export_ply(store, path)
        Write a Stanford PLY mesh. Universal format - loads in CloudCompare,
        MeshLab, ParaView, Blender. Heavy on disk (one cube per interval),
        but the most portable option.

A 500 m tile at cell_xy=1.0 m (median ~16M points) has at most 250,000 columns, and
the 1 m metropolis run averaged ~511 k intervals per tile (1.453 G over 2,842 shards);
the count rises with vertical complexity and with a finer cell_z. By default we
auto-thin to a per-export box budget (`max_boxes`, default 5M) by striding
columns; pass `region=(x0, y0, x1, y1)` in metric (RGF93/CC46 EPSG:3946) coordinates
to inspect a sub-region at full detail instead.
"""

from __future__ import annotations
import base64
import json
import math
import struct
import zlib
from pathlib import Path
from typing import Iterable, Optional
import numpy as np

import logging

from .classes_config import CLASS_COLORS, CLASS_NAMES
from .viz_common import class_color_lut, unit_box_corners, unit_box_tris
from .data_structures import ColumnStore, _unpack_keys
# _unpack_keys: the vectorized collector reads the store's flat key
# array directly (int32 ix/iy, no boxed tuples).

logger = logging.getLogger(__name__)

# Hard ceiling on the requested box budget. The auto-thinner already keeps
# the count under `max_boxes`; this clamps the budget itself so a runaway
# value cannot ask the collector for an unbounded allocation. Geometry costs
# ~36 B/box resident (float32 centers 12 + sizes 4 + colors 3 + cls 1 +
# four int32 grid arrays 16), plus roughly int64 re-sort copies (~40 B/box)
# transiently inside the HTML/.vxg codec:
#   500k boxes ~ 18 MB | 10M ~ 0.36 GB | 200M ~ 7.2 GB (+ ~8 GB transient).
# ~10M is the SUGGESTED practical ceiling for the single-file viewer
# (browsers choke far earlier; the STREAMING exporter is the path for
# anything bigger); the shipped cap is 200M so an informed power user is
# not blocked. The budget is a per-run knob (--max-boxes on every CLI).
_MAX_BOXES_HARD_CAP = 200_000_000

#  --------------------------------------------------
#  Geometry collection - shared between every backend
#  --------------------------------------------------

def estimate_thin_stride(n_columns: int, avg_intervals_per_column: float,
                         max_boxes: Optional[int], base_stride: int = 1) -> int:
    """
    Column stride that keeps the box count under ``max_boxes``.

    ``expected = n_columns * avg_intervals_per_column``; if that already fits
    the budget the ``base_stride`` is returned unchanged, otherwise it is scaled
    by ``ceil(sqrt(expected / max_boxes))`` (a grid stride of ``s`` keeps ~1/s^2
    of the columns). Centralized so the single-store auto-thinner in
    _collect_voxel_geometry() and the sharded full-view path
    (voxelizer.sharding.render_full_from_shards()) pick strides by the
    identical rule - the property that makes a set of shards decimated at one
    global stride equivalent to the merged store decimated at that stride.
    """
    if not max_boxes or n_columns <= 0:
        return base_stride
    expected = n_columns * max(float(avg_intervals_per_column), 1e-9)
    if expected <= max_boxes:
        return base_stride
    extra = math.ceil(math.sqrt(expected / max_boxes))
    return base_stride * extra


def _collect_voxel_geometry(
    store: ColumnStore,
    classes: Optional[Iterable[int]] = None,
    region: Optional[tuple[float, float, float, float]] = None,
    stride: int = 1,
    max_boxes: Optional[int] = None,
    need_grid: bool = True,
) -> dict:
    """
    Flatten every interval into per-box arrays suitable for any 3-D backend.

    VECTORIZED: works directly on the store's flat arrays
    (``_keys`` / ``_off`` / ``_zs`` / ``_ze`` / ``_cl``) - no Python tuples,
    no per-column loop, no per-column binary search. The previous
    implementation opened with ``sorted(store.columns.keys())``, which boxed
    every key into a Python tuple: measured ~96-140 B/key, i.e. 12-16 GB of
    pure object overhead at 116.5M columns, allocated BEFORE any filter ran -
    the demonstrated kill site of full-area exports. ``sorted()`` itself was redundant:
    packed keys are bias-shifted (``_pack_keys`` adds 2^31), so canonical
    store order IS ascending (ix, iy) order, negatives included. The output
    keeps the old loop's field layout and canonical (ix, iy) order. No
    pristine copy of the old loop survives to diff against, so the invariant
    bench (tests/stress_test_scripts/t15_viz3d_ab.py) asserts determinism,
    budget, subset and stride properties instead - and the auto-thin sampler
    below intentionally diverges from the old first-1000 sample (see
    t13b_autothin_fixed.py).

    @param store         The ColumnStore to visualize.
    @param classes       Optional whitelist of class codes to keep. ``None`` keeps everything.
                         Useful e.g. for `classes={5, 6}` (vegetation + buildings only) when
                         you want to see the "above ground" structure clearly.
    @param region        Optional metric bounding box ``(x0, y0, x1, y1)`` in the same CRS as
                         the store (typically RGF93/CC46 EPSG:3946). Columns whose centre falls outside
                         this box are dropped. Use this to zoom in on a 100 m x 100 m
                         neighbourhood at full detail.
    @param stride        Decimate columns: keep only one out of every `stride` in each
                         horizontal axis. ``stride=1`` keeps everything, ``stride=4`` keeps
                         ~6 %. Applied *after* `region` clipping.
    @param max_boxes     Soft cap on the number of boxes returned. If the unthinned count
                         would exceed this, the function automatically picks a stride that
                         brings the count below the budget and emits a warning to stderr.
                         (The estimate samples ~1000 kept columns at a fixed stride across
                         the whole key range - the first-1000-in-canonical-order sample it
                         replaced read one western edge strip and could blow the budget; the
                         exact per-column counts are known, so this could be made exact -
                         kept sampled for speed.)
    @param need_grid     When False, the four exact integer grid arrays (``gx``/``gy``/
                         ``gz0``/``gnz``, 16 B/box - 5.1 GB at 317M boxes) are omitted from
                         the result. The tiled streaming exporter never calls this
                         function; the .vxg grid cache, ``export_html_from_geom`` and the
                         shard merge path (merge_geometries()) all read them. Default True preserves
                         the full legacy contract.
    @return dict with keys:
            centers     float32 (N, 3) - voxel-box centres, centred on the kept
                                        columns' mean centre
                                        (subtract origin to avoid WebGL precision
                                          loss with EPSG:3946's 10^6-magnitude
                                         coordinates).
            sizes_z     float32 (N,)   - vertical extent of each box in metres.
            classes     uint8   (N,)   - ASPRS / IGN class code per box.
            colors      uint8   (N, 3) - RGB colour per box, looked up from
                                        CLASS_COLORS with grey fallback.
            cell_xy     float           horizontal box footprint in metres.
            origin      (x, y, z)       metric offset that was subtracted from
                                        `centers` (so caller can map back if
                                        they need real-world coords).
            cell_z      float           vertical voxel size in metres.
            grid_min    (x, y, z)       the store's own minimum corner. `origin`
                                        is the centring offset and moves with
                                        the kept columns; this one does not, so
                                        gx/gy/gz0 stay interpretable.
            n_center_cols int           number of columns the centring mean was
                                        taken over, before any class filter.
                                        `merge_geometries` needs it to combine
                                        shard means by weight.
            n_boxes     int             == centers.shape[0].
            applied_stride int          the stride that was actually used (may
                                        be larger than the requested one if
                                        `max_boxes` forced thinning).
            bbox        (xmin, ymin, zmin, xmax, ymax, zmax) of the kept boxes
                        in the *centered* coordinate frame.
            gx/gy/gz0/gnz int32 (N,)    exact grid coords (only if need_grid).
    """
    import sys

    if not store.columns:
        out = {
            "centers": np.zeros((0, 3), dtype=np.float32),
            "sizes_z": np.zeros((0,),  dtype=np.float32),
            "classes": np.zeros((0,),  dtype=np.uint8),
            "colors":  np.zeros((0, 3), dtype=np.uint8),
            "cell_xy": store.cell_xy,
            "cell_z":  store.cell_z,
            "grid_min": (store.x_min, store.y_min, store.z_min),
            "origin":  (store.x_min, store.y_min, store.z_min),
            "n_center_cols": 0,
            "n_boxes": 0,
            "applied_stride": stride,
            "bbox":    (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        }
        if need_grid:
            out["gx"]  = np.zeros((0,), dtype=np.int32)
            out["gy"]  = np.zeros((0,), dtype=np.int32)
            out["gz0"] = np.zeros((0,), dtype=np.int32)
            out["gnz"] = np.zeros((0,), dtype=np.int32)
        return out

    # -- 1. column-level filters, on flat arrays ------------------------------
    # ix/iy as int32 arrays (0.47 GB each at 116.5M columns) instead of
    # boxed tuples; per-column interval counts exactly from the offsets.
    ix_all, iy_all = _unpack_keys(store._keys)
    off = store._off
    niv = np.diff(off)

    keep = np.ones(ix_all.shape[0], dtype=bool)
    if region is not None:
        x0, y0, x1, y1 = region
        cxm = store.x_min + (ix_all + 0.5) * store.cell_xy
        keep &= (cxm >= x0) & (cxm <= x1)
        del cxm
        cym = store.y_min + (iy_all + 0.5) * store.cell_xy
        keep &= (cym >= y0) & (cym <= y1)
        del cym

    # -- 2. stride / auto-thinning --------------------------------------------
    # Stride keys on the grid index (every s-th column in x AND y), so
    # decimation is spatially regular, exactly like the old key filter.
    if stride > 1:
        keep &= (ix_all % stride == 0) & (iy_all % stride == 0)

    applied_stride = stride
    if max_boxes is not None and keep.any():
        kept_idx = np.flatnonzero(keep)
        # ~1000 kept columns sampled at a FIXED STRIDE across the whole
        # key range - deterministic and spatially representative. (The
        # previous first-1000-in-canonical-order sample was a single
        # western edge strip, which under-estimated avg intervals on
        # stores with a sparse edge and blew the box budget.)
        _step = max(1, kept_idx.size // 1000)
        avg_iv = float(niv[kept_idx[::_step][:1000]].mean())
        expected = kept_idx.size * avg_iv
        applied_stride = estimate_thin_stride(int(kept_idx.size), avg_iv,
                                              max_boxes, base_stride=stride)
        if applied_stride != stride:
            keep &= ((ix_all % applied_stride == 0)
                     & (iy_all % applied_stride == 0))
            sys.stderr.write(
                f"[visualizer3d] auto-thinned to stride={applied_stride} "
                f"(would have been ~{int(expected):,} boxes, "
                f"budget={max_boxes:,})\n"
            )

    cols = np.flatnonzero(keep)
    del keep

    # Centering origin: centre of the kept columns' index range, at z=z_min -
    # computed AFTER thinning and BEFORE class filtering, like the old loop
    # (the class filter never moved the frame).
    if cols.size:
        cx0 = store.x_min + (float(ix_all[cols].mean()) + 0.5) * store.cell_xy
        cy0 = store.y_min + (float(iy_all[cols].mean()) + 0.5) * store.cell_xy
    else:
        cx0 = store.x_min
        cy0 = store.y_min
    cz0 = store.z_min
    # Exported with the geometry so merge_geometries can reproduce this
    # exact pre-class-filter centring for disjoint shards (a weighted mean
    # of shard means) instead of re-deriving it from surviving boxes,
    # which shifts the frame whenever a class filter emptied columns.
    n_center_cols = int(cols.size)

    # -- 3. expand intervals -> per-box arrays, one gather ---------------------
    # Fast path: nothing filtered -> the interval arrays ARE the box rows.
    niv_k = niv[cols]
    if cols.size == ix_all.shape[0]:
        zs = store._zs
        ze = store._ze
        cs = store._cl
    else:
        # Flat gather indices: repeat each kept column's start offset over its
        # run, plus a within-run ramp. Both "how many" and "where" derive from
        # the SAME _off array in one expression, so the old two-pass
        # count-vs-fill divergence hazard (the access violation)
        # structurally cannot return.
        total = int(niv_k.sum())
        idx = np.repeat(off[cols], niv_k)
        ramp = np.arange(total, dtype=np.int64)
        ramp -= np.repeat(np.cumsum(niv_k) - niv_k, niv_k)
        idx += ramp
        del ramp
        zs = store._zs[idx]
        ze = store._ze[idx]
        cs = store._cl[idx]
        del idx

    bix = np.repeat(ix_all[cols], niv_k)
    biy = np.repeat(iy_all[cols], niv_k)
    del ix_all, iy_all, niv, niv_k, cols

    if classes is not None:
        classes_set = set(int(c) for c in classes)
        m = np.isin(cs, list(classes_set))
        zs = zs[m]
        ze = ze[m]
        cs = cs[m]
        bix = bix[m]
        biy = biy[m]
        del m

    write = int(zs.shape[0])

    # Box centres: identical dtype chains to the old per-column loop -
    # x/y computed in float64 and rounded once on the float32 assignment;
    # z kept in the old all-float32 chain so results stay bit-identical.
    centers = np.empty((write, 3), dtype=np.float32)
    centers[:, 0] = store.x_min + (bix + 0.5) * store.cell_xy - cx0
    if not need_grid:
        bix = None   # 4 B/box (1.27 GB at metropolis scale); only the grid
                     # output needs it
    centers[:, 1] = store.y_min + (biy + 0.5) * store.cell_xy - cy0
    if not need_grid:
        biy = None
    centers[:, 2] = (zs.astype(np.float32) + ze.astype(np.float32)) * 0.5 \
                     * store.cell_z + (store.z_min - cz0)
    sizes_z = (ze - zs).astype(np.float32) * store.cell_z
    classes_out = np.ascontiguousarray(cs, dtype=np.uint8)
    if classes_out is store._cl:          # fast path: never alias internals
        classes_out = classes_out.copy()

    # -- 4. per-box colour LUT ------------------------------------------------
    lut = class_color_lut()
    colors = lut[classes_out]

    # -- 5. bbox in the centred frame -----------------------------------------
    if write:
        half_xy = store.cell_xy * 0.5
        half_z  = sizes_z * 0.5
        xmin = float(centers[:, 0].min() - half_xy)
        ymin = float(centers[:, 1].min() - half_xy)
        zmin = float((centers[:, 2] - half_z).min())
        xmax = float(centers[:, 0].max() + half_xy)
        ymax = float(centers[:, 1].max() + half_xy)
        zmax = float((centers[:, 2] + half_z).max())
        bbox = (xmin, ymin, zmin, xmax, ymax, zmax)
    else:
        bbox = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    out = {
        "centers":  centers,
        "sizes_z":  sizes_z,
        "classes":  classes_out,
        "colors":   colors,
        "cell_xy":  float(store.cell_xy),
        "cell_z":   float(store.cell_z),
        # Metric grid origin (x_min,y_min,z_min) so a consumer can rebuild
        # centres from (gx,gy,gz0,gnz) exactly the way the old fill loop did.
        "grid_min": (float(store.x_min), float(store.y_min), float(store.z_min)),
        "origin":   (float(cx0), float(cy0), float(cz0)),
        "n_center_cols": n_center_cols,
        "n_boxes":  write,
        "applied_stride": int(applied_stride),
        "bbox":     bbox,
    }
    if need_grid:
        # Exact integer grid coordinates (int32), parallel to `centers`.
        out["gx"]  = np.ascontiguousarray(bix, dtype=np.int32)
        out["gy"]  = np.ascontiguousarray(biy, dtype=np.int32)
        out["gz0"] = zs.astype(np.int32, copy=True)
        out["gnz"] = (ze - zs).astype(np.int32)
    return out


def collect_geometry(
    store: ColumnStore,
    *,
    classes: Optional[Iterable[int]] = None,
    region: Optional[tuple[float, float, float, float]] = None,
    stride: int = 1,
    max_boxes: Optional[int] = None,
) -> dict:
    """
    Public wrapper over _collect_voxel_geometry().

    Exposed so out-of-core callers (the sharded full-view path) can flatten one
    shard at a time to the same geometry dict the in-core exporters consume,
    then hand the pieces to merge_geometries(). Defaults to
    ``max_boxes=None`` (no auto-thin) because the sharded path chooses one
    global stride up front and passes it to every shard.
    """
    return _collect_voxel_geometry(store, classes=classes, region=region,
                                   stride=stride, max_boxes=max_boxes)


def _empty_geometry(grid_min=(0.0, 0.0, 0.0),
                    cell_xy: float = 1.0, cell_z: float = 0.5) -> dict:
    """A zero-box geometry dict, shaped exactly like a non-empty one.

    ``n_center_cols`` is 0 and is present: the same key on the same zero, as
    ``_collect_voxel_geometry``'s own empty path already writes. It cannot
    reach ``merge_geometries``' weighted-centring branch, which drops every
    zero-box input before it looks at the field, and no geometry with boxes
    can carry a zero here (the count IS the kept columns, and no columns
    means no boxes). So the key is inert; what it buys is that the sentence
    above stays true, and a consumer reading the field off a merged result
    gets a number rather than a KeyError.
    """
    z = np.zeros
    gm = tuple(float(v) for v in grid_min)
    return {
        "centers": z((0, 3), np.float32), "sizes_z": z((0,), np.float32),
        "classes": z((0,), np.uint8), "colors": z((0, 3), np.uint8),
        "gx": z((0,), np.int32), "gy": z((0,), np.int32),
        "gz0": z((0,), np.int32), "gnz": z((0,), np.int32),
        "cell_xy": float(cell_xy), "cell_z": float(cell_z),
        "grid_min": gm, "origin": gm,
        "n_center_cols": 0,
        "n_boxes": 0, "applied_stride": 1,
        "bbox": (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    }


def _float_geometry_from_grid(gx, gy, gz0, gnz, classes, grid_min, origin,
                              cell_xy, cell_z):
    """
    Rebuild float32 ``centers``/``sizes_z``/``colors`` and the centred-frame
    ``bbox`` from absolute integer grid coords, with the same float64-then-
    float32 arithmetic as _decode_grid_payload() - bit-identical to the
    fill loop in _collect_voxel_geometry() for power-of-two cell sizes,
    within 1 ULP in z otherwise (see the codec note). Used when concatenating shards, whose float centres were expressed in
    per-shard frames and must be recomputed against one shared centering origin.
    """
    x_min, y_min, z_min = grid_min
    cx0, cy0, cz0 = origin
    gx64 = gx.astype(np.float64); gy64 = gy.astype(np.float64)
    gz064 = gz0.astype(np.float64); gnz64 = gnz.astype(np.float64)
    n = int(gx.shape[0])
    centers = np.empty((n, 3), dtype=np.float32)
    centers[:, 0] = x_min + (gx64 + 0.5) * cell_xy - cx0
    centers[:, 1] = y_min + (gy64 + 0.5) * cell_xy - cy0
    centers[:, 2] = (gz064 + gnz64 * 0.5) * cell_z + (z_min - cz0)
    sizes_z = (gnz64 * cell_z).astype(np.float32)

    lut = class_color_lut()
    colors = lut[classes]

    if n:
        half_xy = cell_xy * 0.5
        half_z = sizes_z * 0.5
        bbox = (float(centers[:, 0].min() - half_xy),
                float(centers[:, 1].min() - half_xy),
                float((centers[:, 2] - half_z).min()),
                float(centers[:, 0].max() + half_xy),
                float(centers[:, 1].max() + half_xy),
                float((centers[:, 2] + half_z).max()))
    else:
        bbox = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    return centers, sizes_z, colors, bbox


def merge_geometries(geoms: Iterable[dict], *, atol: float = 1e-6) -> dict:
    """
    Concatenate several geometry dicts that share a grid origin and cell size
    into one, with a single global centering origin and a recomputed bbox.

    The integer grid coords ``(gx, gy, gz0, gnz)`` are absolute in the shared
    grid frame, so concatenation across shards is exact; only the float
    ``centers``/``bbox`` - which are expressed relative to a centering origin -
    are recomputed for the merged set. The global centre mirrors
    _collect_voxel_geometry() (mean over the *distinct* kept columns at
    ``z = z_min``), so the merged world coordinates match the equivalent
    single merged store box for box - bit-identically for power-of-two cell
    sizes, within 1 ULP in z otherwise (the float z chain differs; see the
    codec note). Inputs that disagree on grid
    origin or cell size are skipped with a warning (the same compatibility
    test as ColumnStore.merge(), which raises instead of skipping).
    """
    geoms = [g for g in geoms if g and g["n_boxes"]]
    if not geoms:
        return _empty_geometry()

    ref = geoms[0]
    grid_min = tuple(float(v) for v in ref["grid_min"])
    cell_xy = float(ref["cell_xy"]); cell_z = float(ref["cell_z"])

    kept = []
    for g in geoms:
        gm = g["grid_min"]
        if (abs(float(gm[0]) - grid_min[0]) > atol
                or abs(float(gm[1]) - grid_min[1]) > atol
                or abs(float(gm[2]) - grid_min[2]) > atol
                or abs(float(g["cell_xy"]) - cell_xy) > atol
                or abs(float(g["cell_z"]) - cell_z) > atol):
            logger.warning("merge_geometries: skipping a geometry with "
                           "mismatched grid origin/cell size.")
            continue
        kept.append(g)
    if not kept:
        return _empty_geometry(grid_min, cell_xy, cell_z)

    gx = np.concatenate([g["gx"] for g in kept]).astype(np.int32, copy=False)
    gy = np.concatenate([g["gy"] for g in kept]).astype(np.int32, copy=False)
    gz0 = np.concatenate([g["gz0"] for g in kept]).astype(np.int32, copy=False)
    gnz = np.concatenate([g["gnz"] for g in kept]).astype(np.int32, copy=False)
    classes = np.concatenate([g["classes"] for g in kept]).astype(np.uint8,
                                                                  copy=False)
    n = int(gx.shape[0])

    x_min, y_min, z_min = grid_min
    # One global centering origin matching _collect_voxel_geometry's
    # pre-class-filter mean-over-kept-columns. For disjoint shards that
    # exact value is the count-weighted mean of the shard origins (each
    # shard exports n_center_cols for the weights) - and it avoids the
    # giant np.unique-over-all-boxes this used to do. Fall back to the
    # surviving-box derivation only for legacy geoms without the field
    # (that path shifts the frame when a class filter emptied columns).
    if all(g.get("n_center_cols") for g in kept):
        w = np.array([float(g["n_center_cols"]) for g in kept])
        ox = np.array([float(g["origin"][0]) for g in kept])
        oy = np.array([float(g["origin"][1]) for g in kept])
        cx0 = float((ox * w).sum() / w.sum())
        cy0 = float((oy * w).sum() / w.sum())
        merged_center_cols = int(w.sum())
    else:
        cols = np.unique(np.stack([gx, gy], axis=1), axis=0)
        cx0 = x_min + (float(cols[:, 0].mean()) + 0.5) * cell_xy
        cy0 = y_min + (float(cols[:, 1].mean()) + 0.5) * cell_xy
        merged_center_cols = int(cols.shape[0])
    cz0 = z_min
    centers, sizes_z, colors, bbox = _float_geometry_from_grid(
        gx, gy, gz0, gnz, classes, (x_min, y_min, z_min),
        (cx0, cy0, cz0), cell_xy, cell_z)

    return {
        "centers": centers, "sizes_z": sizes_z,
        "classes": classes, "colors": colors,
        "gx": gx, "gy": gy, "gz0": gz0, "gnz": gnz,
        "cell_xy": cell_xy, "cell_z": cell_z,
        "grid_min": (x_min, y_min, z_min),
        "origin": (cx0, cy0, cz0), "n_center_cols": merged_center_cols,
        "n_boxes": n,
        "applied_stride": int(kept[0].get("applied_stride", 1)),
        "bbox": bbox,
    }


# -----------------------------------------------------------------------------
#  HTML / Three.js export - the "truly traversable" path
# -----------------------------------------------------------------------------

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>__TITLE__</title>
  <style>
    html, body { margin: 0; height: 100%; background: #0e1014; color: #d8d8d8;
                 font-family: -apple-system, system-ui, "Segoe UI", sans-serif; }
    #c { display: block; width: 100%; height: 100%; cursor: crosshair; }
    .panel { position: fixed; background: rgba(14, 16, 20, 0.78);
             border: 1px solid #2a2f38; border-radius: 6px; padding: 10px 12px;
             font-size: 12px; line-height: 1.5; backdrop-filter: blur(6px); }
    #info { top: 10px; left: 10px; pointer-events: none; min-width: 220px; }
    #info .k { color: #8a93a4; }
    #info .v { color: #fff; }
    #legend { top: 10px; right: 10px; max-height: calc(100vh - 20px);
              overflow-y: auto; min-width: 200px; }
    #legend h3 { margin: 0 0 6px 0; font-size: 12px; color: #fff; font-weight: 600; }
    #legend label { display: flex; align-items: center; padding: 3px 0;
                    cursor: pointer; user-select: none; }
    #legend label:hover { color: #fff; }
    #legend .sw { display: inline-block; width: 14px; height: 14px;
                  margin-right: 8px; border-radius: 3px; border: 1px solid #00000055; }
    #legend input { margin: 0 6px 0 0; accent-color: #6aa9ff; }
    #legend .count { color: #6c7383; margin-left: auto; font-variant-numeric: tabular-nums; }
    #help { bottom: 10px; left: 10px; max-width: 540px; }
    #help b { color: #fff; }
    #locked-msg { position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%);
                  font-size: 18px; padding: 14px 22px; border-radius: 8px;
                  background: rgba(0,0,0,0.7); border: 1px solid #2a2f38;
                  pointer-events: none; opacity: 0; transition: opacity 0.2s; }
    #locked-msg.show { opacity: 1; }
    #loading { position: fixed; inset: 0; display: flex; align-items: center;
               justify-content: center; background: #0e1014; font-size: 14px;
               color: #8a93a4; }
    #search { top: 125px; left: 10px; min-width: 248px; z-index: 10; }
    #search-body { margin-top: 6px; }
    #search .sec { color: #8a93a4; font-size: 11px; margin-bottom: 2px; }
    #search .row { margin-bottom: 5px; display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
    #search input[type="number"], #search input[type="text"] {
      background: #1a1e26; border: 1px solid #2a2f38; border-radius: 3px;
      color: #fff; padding: 2px 4px; font-size: 11px; outline: none; width: 56px; }
    #search input:focus { border-color: #6aa9ff; }
    #search button {
      background: #2a2f38; border: 1px solid #3a3f48; border-radius: 3px;
      color: #d8d8d8; padding: 2px 8px; font-size: 11px; cursor: pointer; }
    #search button:hover { background: #3a3f48; }
    #search .sep { border: none; border-top: 1px solid #2a2f38; margin: 6px 0; }
    #search input[type="range"] { -webkit-appearance: none; appearance: none;
      height: 4px; background: #2a2f38; border-radius: 2px; outline: none;
      flex: 1; min-width: 60px; }
    #search input[type="range"]::-webkit-slider-thumb { -webkit-appearance: none;
      width: 12px; height: 12px; background: #6aa9ff; border-radius: 50%; cursor: pointer; }
    #search input[type="range"]::-moz-range-thumb { width: 12px; height: 12px;
      background: #6aa9ff; border-radius: 50%; cursor: pointer; border: none; }
    #s-tile-id { width: 140px !important; }
    #s-dim-val { color: #fff; font-size: 11px; min-width: 28px; text-align: right; }
    #s-status { color: #8a93a4; font-size: 11px; margin-top: 4px; min-height: 14px; }
    #search .btn-full { width: 100%; margin-top: 4px; }
  </style>
</head>
<body>
<canvas id="c"></canvas>
<div id="loading">Loading voxels...</div>
<div id="info" class="panel" style="display:none">
  <div><span class="k">tile:&nbsp;</span><span class="v" id="i-tile">__TILE_LABEL__</span></div>
  <div><span class="k">boxes:&nbsp;</span><span class="v" id="i-boxes">0</span></div>
  <div><span class="k">FPS:&nbsp;</span><span class="v" id="i-fps">0</span></div>
  <div><span class="k">cam:&nbsp;</span><span class="v" id="i-cam"> - </span></div>
  <div><span class="k">speed:&nbsp;</span><span class="v" id="i-speed"> - </span></div>
</div>
<div id="legend" class="panel" style="display:none">
  <h3>Classes</h3>
  <div id="legend-rows"></div>
</div>
<div id="help" class="panel" style="display:none">
  <b>Click the canvas</b> to capture the mouse, then:
  &nbsp;<b>W&nbsp;A&nbsp;S&nbsp;D</b> move &nbsp;|&nbsp;
  <b>Q / E</b> down / up &nbsp;|&nbsp;
  <b>Shift</b> sprint &nbsp;|&nbsp;
  <b>Wheel</b> speed &nbsp;|&nbsp;
  <b>R</b> reset &nbsp;|&nbsp;
  <b>T</b> toggle orbit &nbsp;|&nbsp;
  <b>Esc</b> release.
</div>
<div id="locked-msg">Click to enter, Esc to release</div>

<div id="search" class="panel" style="display:none">
  <div id="search-toggle" style="cursor:pointer;user-select:none;">
    <span id="search-icon">&#9654;</span> Search
  </div>
  <div id="search-body" style="display:none;">
    <div class="sec">Column</div>
    <div class="row">
      ix <input id="s-col-ix" type="number">
      iy <input id="s-col-iy" type="number">
      <button id="s-col-go">Go</button>
    </div>
    <div class="sec">Range</div>
    <div class="row">
      ix <input id="s-rng-ix0" type="number" style="width:44px;">
      &ndash; <input id="s-rng-ix1" type="number" style="width:44px;">
      iy <input id="s-rng-iy0" type="number" style="width:44px;">
      &ndash; <input id="s-rng-iy1" type="number" style="width:44px;">
      <button id="s-rng-go">Go</button>
    </div>
    <div class="sec">Tile</div>
    <div class="row">
      <input id="s-tile-id" type="text" placeholder="e.g. 18315_51785">
      <button id="s-tile-go">Go</button>
    </div>
    <hr class="sep">
    <div class="row">
      <span style="color:#8a93a4;font-size:11px;">Dim others:</span>
      <input id="s-dim" type="range" min="0" max="100" value="30">
      <span id="s-dim-val">30%</span>
    </div>
    <button id="s-clear" class="btn-full">Clear Search</button>
    <div id="s-status"></div>
  </div>
</div>

<script type="importmap">
{
  "imports": {
    "three": "https://unpkg.com/three@0.160.0/build/three.module.js",
    "three/addons/": "https://unpkg.com/three@0.160.0/examples/jsm/"
  }
}
</script>

<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

// -- 1. Inline voxel payload (quantized grid codec) -------------------------
const META = __META_JSON__;
const PAYLOAD_B64 = "__PAYLOAD_B64__";
const N = META.n_boxes;

// Reconstruction constants. A box transform is a pure function of its integer
// grid coords (ix,iy,iz0,nz) - the payload ships those, not floats. That is
// 8 B/box at the uint16 index dtype and 16 B/box when the ranges force
// uint32; META.idx_dtype says which, and IDX_BYTES below reads it:
//   px = x_min + (ix+0.5)*cell_xy - cx0
//   py = y_min + (iy+0.5)*cell_xy - cy0
//   pz = (iz0 + nz*0.5)*cell_z + (z_min - cz0)
//   sz = nz*cell_z
const G = META.grid;
const [CX0, CY0, CZ0] = META.origin_centered;
const cellXY = META.cell_xy, cellZ = G.cell_z;
const IDX_BYTES = ({ uint16: 2, uint32: 4 })[META.idx_dtype];
if (!IDX_BYTES) throw new Error(`Unsupported grid idx_dtype: ${META.idx_dtype}`);

// class code -> THREE.Color, built once from the legend palette.
const PALETTE = {};
for (const k in META.class_colors) {
  const c = META.class_colors[k];
  PALETTE[k] = new THREE.Color(c[0] / 255, c[1] / 255, c[2] / 255);
}

// Decode: base64 -> (zlib inflate) -> four integer arrays. The heavy part
// (inflating tens of MB) runs in a Web Worker so the spinner keeps animating;
// the arrays come back via transferable buffers (zero-copy). Older browsers or
// a Worker failure fall back to the identical main-thread path.
async function decodeGridBytes(b64, compressed, idxBytes, n) {
  const binStr = atob(b64);
  let bytes = new Uint8Array(binStr.length);
  for (let i = 0; i < binStr.length; i++) bytes[i] = binStr.charCodeAt(i);
  if (compressed) {
    if (typeof DecompressionStream !== 'undefined') {
      // Python's zlib.compress emits an RFC-1950 zlib stream -> 'deflate'.
      const ds = new DecompressionStream('deflate');
      const w = ds.writable.getWriter(); w.write(bytes); w.close();
      const r = ds.readable.getReader(); const chunks = [];
      while (true) { const { done, value } = await r.read(); if (done) break; chunks.push(value); }
      let total = 0; for (const c of chunks) total += c.length;
      bytes = new Uint8Array(total); let o = 0;
      for (const c of chunks) { bytes.set(c, o); o += c.length; }
    } else {
      const { inflate } = await import('https://cdn.jsdelivr.net/npm/pako@2.1.0/dist/pako.esm.min.js');
      bytes = inflate(bytes);
    }
  }
  const buf = bytes.buffer;
  const View = idxBytes === 4 ? Uint32Array : Uint16Array;
  const gx  = new View(buf, 0 * n * idxBytes, n).slice();
  const gy  = new View(buf, 1 * n * idxBytes, n).slice();
  const gz0 = new View(buf, 2 * n * idxBytes, n).slice();
  const gnz = new View(buf, 3 * n * idxBytes, n).slice();
  return { gx, gy, gz0, gnz };
}

function makeDecodeWorker() {
  const src = `self.onmessage = async (e) => {
    const { b64, compressed, idxBytes, n } = e.data;
    const binStr = atob(b64);
    let bytes = new Uint8Array(binStr.length);
    for (let i = 0; i < binStr.length; i++) bytes[i] = binStr.charCodeAt(i);
    if (compressed) {
      if (typeof DecompressionStream !== 'undefined') {
        const ds = new DecompressionStream('deflate');
        const w = ds.writable.getWriter(); w.write(bytes); w.close();
        const r = ds.readable.getReader(); const chunks = [];
        while (true) { const { done, value } = await r.read(); if (done) break; chunks.push(value); }
        let total = 0; for (const c of chunks) total += c.length;
        bytes = new Uint8Array(total); let o = 0;
        for (const c of chunks) { bytes.set(c, o); o += c.length; }
      } else {
        const { inflate } = await import('https://cdn.jsdelivr.net/npm/pako@2.1.0/dist/pako.esm.min.js');
        bytes = inflate(bytes);
      }
    }
    const buf = bytes.buffer;
    const View = idxBytes === 4 ? Uint32Array : Uint16Array;
    const gx  = new View(buf, 0*n*idxBytes, n).slice();
    const gy  = new View(buf, 1*n*idxBytes, n).slice();
    const gz0 = new View(buf, 2*n*idxBytes, n).slice();
    const gnz = new View(buf, 3*n*idxBytes, n).slice();
    self.postMessage({ gx, gy, gz0, gnz }, [gx.buffer, gy.buffer, gz0.buffer, gnz.buffer]);
  };`;
  return new Worker(URL.createObjectURL(new Blob([src], { type: 'text/javascript' })));
}

// Top-level await is fine in a module script.
let GX, GY, GZ0, GNZ;
try {
  const worker = makeDecodeWorker();
  const res = await new Promise((resolve, reject) => {
    worker.onmessage = (e) => resolve(e.data);
    worker.onerror = (e) => reject(e);
    worker.postMessage({ b64: PAYLOAD_B64, compressed: !!META.compressed,
                         idxBytes: IDX_BYTES, n: N });
  });
  ({ gx: GX, gy: GY, gz0: GZ0, gnz: GNZ } = res);
  worker.terminate();
} catch (err) {
  console.warn('decode worker unavailable, decoding on main thread:', err);
  ({ gx: GX, gy: GY, gz0: GZ0, gnz: GNZ } =
      await decodeGridBytes(PAYLOAD_B64, !!META.compressed, IDX_BYTES, N));
}

// -- 2. Scene, camera, renderer ---------------------------------------------
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0e1014);
scene.fog = new THREE.Fog(0x0e1014, 300, 1600);

// Z-up: matches the data orientation (altitude is +z).
const camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight,
                                            0.5, 5000);
camera.up.set(0, 0, 1);

const renderer = new THREE.WebGLRenderer({ canvas: document.getElementById('c'),
                                            antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(window.innerWidth, window.innerHeight);

window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

// -- 3. Lighting ------------------------------------------------------------
// Two directional lights at orthogonal angles + a soft ambient so the cube
// faces all read differently. We don't bother with shadow maps - there are
// hundreds of thousands of instances and shadowing would be the bottleneck.
scene.add(new THREE.AmbientLight(0xffffff, 0.55));
const sun = new THREE.DirectionalLight(0xfff0d8, 0.85);
sun.position.set(0.6, 0.4, 1.0);
scene.add(sun);
const fill = new THREE.DirectionalLight(0x90b0ff, 0.35);
fill.position.set(-0.8, -0.2, 0.5);
scene.add(fill);

// -- 4. One InstancedMesh per class -----------------------------------------
// The payload is class-sorted, so each class owns a contiguous slice
// [start, start+count). One flat-coloured mesh per class means: no per-instance
// colour buffer at all (the material carries the colour), and toggling a class
// is just mesh.visible - O(1), no matrix rewrites. We fill instanceMatrix.array
// directly (no Matrix4 allocation per box) and reconstruct each transform from
// the integer grid coords inline.
const boxGeom = new THREE.BoxGeometry(1, 1, 1);
const meshesByClass = {};                 // code -> InstancedMesh
const classCounts = {};                   // code -> instance count (for legend)
const x_min = G.x_min, y_min = G.y_min, z_min = G.z_min;
const ix_min = G.ix_min, iy_min = G.iy_min, iz_min = G.iz_min;
const zbase = z_min - CZ0;

for (const [code, start, count] of META.class_offsets) {
  classCounts[code] = count;
  const colObj = PALETTE[code] || new THREE.Color(0.5, 0.5, 0.5);
  const m = new THREE.InstancedMesh(
      boxGeom, new THREE.MeshLambertMaterial({ color: colObj }), count);
  m.instanceMatrix.setUsage(THREE.StaticDrawUsage);
  const arr = m.instanceMatrix.array;     // Float32Array(16 * count)
  for (let j = 0; j < count; j++) {
    const i = start + j;
    const ix = GX[i] + ix_min, iy = GY[i] + iy_min;
    const iz0 = GZ0[i] + iz_min, nz = GNZ[i];
    const px = x_min + (ix + 0.5) * cellXY - CX0;
    const py = y_min + (iy + 0.5) * cellXY - CY0;
    const pz = (iz0 + nz * 0.5) * cellZ + zbase;
    const sz = nz * cellZ;
    const o = j * 16;
    // Column-major 4x4 affine: scale(cellXY,cellXY,sz) then translate(px,py,pz).
    arr[o]    = cellXY; arr[o+1]  = 0;      arr[o+2]  = 0;  arr[o+3]  = 0;
    arr[o+4]  = 0;      arr[o+5]  = cellXY; arr[o+6]  = 0;  arr[o+7]  = 0;
    arr[o+8]  = 0;      arr[o+9]  = 0;      arr[o+10] = sz; arr[o+11] = 0;
    arr[o+12] = px;     arr[o+13] = py;     arr[o+14] = pz; arr[o+15] = 1;
  }
  m.instanceMatrix.needsUpdate = true;
  m.computeBoundingSphere();   // direct writes don't update it; culling needs it
  meshesByClass[code] = m;
  scene.add(m);
}

// Simple ground reference plane at z = bbox.zmin - 0.5 so users have a
// horizon to orient against even when classes are filtered out.
{
  const [xmin, ymin, zmin, xmax, ymax] = META.bbox;
  const plane = new THREE.Mesh(
    new THREE.PlaneGeometry(xmax - xmin + 200, ymax - ymin + 200),
    new THREE.MeshBasicMaterial({ color: 0x14181f })
  );
  plane.position.set((xmin + xmax)/2, (ymin + ymax)/2, zmin - 0.5);
  scene.add(plane);
}

// -- 5. Camera defaults - a comfortable oblique view of the bbox ------------
const [xmin, ymin, zmin, xmax, ymax, zmax] = META.bbox;
const cx = (xmin + xmax) / 2, cy = (ymin + ymax) / 2, cz = (zmin + zmax) / 2;
const span = Math.max(xmax - xmin, ymax - ymin);

const defaultCam = { pos: new THREE.Vector3(cx + span*0.6, cy - span*0.6, cz + span*0.5),
                     look: new THREE.Vector3(cx, cy, cz) };

let cameraAnim = null;   // hoisted above resetCamera(), which is called below

function resetCamera() {
  cameraAnim = null;
  camera.position.copy(defaultCam.pos);
  camera.lookAt(defaultCam.look);
  // Pull yaw/pitch out of the matrix so flyMode can resume coherently.
  const dir = new THREE.Vector3().subVectors(defaultCam.look, camera.position).normalize();
  flyState.yaw = Math.atan2(dir.y, dir.x);
  flyState.pitch = Math.asin(dir.z);
  orbit.target.copy(defaultCam.look);
  orbit.update();
}

// -- 6. Two camera modes: orbit (default) and fly (pointer-lock + WASD) -----
const orbit = new OrbitControls(camera, renderer.domElement);
orbit.enableDamping = true;
orbit.dampingFactor = 0.07;
orbit.target.set(cx, cy, cz);

let mode = 'orbit';  // or 'fly'
const flyState = {
  yaw: 0, pitch: 0, vx: 0, vy: 0, vz: 0,
  keys: new Set(),
  speed: Math.max(20, span / 25),   // metres / second base
};
resetCamera();

function setMode(next) {
  if (next === mode) return;
  mode = next;
  orbit.enabled = (mode === 'orbit');
  document.getElementById('c').style.cursor =
      mode === 'fly' ? (document.pointerLockElement ? 'none' : 'crosshair')
                     : 'grab';
}

// Pointer-lock fly controls.
const canvas = document.getElementById('c');
canvas.addEventListener('click', () => {
  if (mode === 'fly') canvas.requestPointerLock();
});

document.addEventListener('pointerlockchange', () => {
  const locked = (document.pointerLockElement === canvas);
  document.getElementById('locked-msg').classList.toggle('show', !locked && mode === 'fly');
  if (locked) document.getElementById('locked-msg').classList.remove('show');
});

document.addEventListener('mousemove', e => {
  if (mode !== 'fly' || document.pointerLockElement !== canvas) return;
  const sens = 0.0022;
  flyState.yaw   -= e.movementX * sens;
  flyState.pitch -= e.movementY * sens;
  const lim = Math.PI / 2 - 0.001;
  flyState.pitch = Math.max(-lim, Math.min(lim, flyState.pitch));
});

document.addEventListener('keydown', e => {
  // Don't steal keys typed into the search panel's inputs.
  if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) return;
  if (e.key === 'r' || e.key === 'R') { resetCamera(); return; }
  if (e.key === 't' || e.key === 'T') { setMode(mode === 'orbit' ? 'fly' : 'orbit'); return; }
  flyState.keys.add(e.code);
});
document.addEventListener('keyup', e => flyState.keys.delete(e.code));

// Mouse wheel adjusts fly speed. Orbit mode keeps the wheel for zoom
// (OrbitControls is the only listener while orbit.enabled === true).
// We multiply by 1/0.9 (~1.11) per notch upward, 0.9 downward - multiplicative
// feels right because the useful range spans ~3 orders of magnitude
// (1 m/s for inspecting a building, 1000 m/s for tile flyover).
canvas.addEventListener('wheel', e => {
  if (mode !== 'fly') return;
  e.preventDefault();
  // sign() collapses the deltaY zoo (pixel-mode deltas of ~100, line-mode
  // deltas of a few units, fractional trackpad deltas) to a consistent
  // +/-1 step.
  const step = Math.sign(e.deltaY);
  flyState.speed *= Math.pow(0.9, step);
  flyState.speed = Math.max(1, Math.min(5000, flyState.speed));
  speedEl.textContent = flyState.speed.toFixed(0) + ' m/s';
}, { passive: false });

// -- 7. Class legend with checkboxes ----------------------------------------
// With one mesh per class, a toggle is just mesh.visible - the GPU skips the
// whole draw call, so this is O(1) regardless of how many boxes the class has.
const classRows = {};
const legendBox = document.getElementById('legend-rows');
const CLASS_NAMES = META.class_names;

const sortedClasses = Object.keys(classCounts)
  .map(k => parseInt(k))
  .sort((a, b) => classCounts[b] - classCounts[a]);

for (const code of sortedClasses) {
  const rgb = META.class_colors[code] || [128, 128, 128];
  const label = document.createElement('label');
  label.innerHTML = `<input type="checkbox" checked data-code="${code}">
                     <span class="sw" style="background:rgb(${rgb.join(',')})"></span>
                     <span>${CLASS_NAMES[code] || ('class_' + code)}</span>
                     <span class="count">${classCounts[code].toLocaleString()}</span>`;
  legendBox.appendChild(label);
  classRows[code] = label;
}

legendBox.addEventListener('change', e => {
  if (e.target.tagName !== 'INPUT') return;
  const code = parseInt(e.target.dataset.code);
  const m = meshesByClass[code];
  if (m) m.visible = e.target.checked;
});

// -- 7b. Search feature (column, range, tile) --------------------------------
function getClassCode(flatIdx) {
  for (const [code, start, cnt] of META.class_offsets) {
    if (flatIdx >= start && flatIdx < start + cnt) return code;
  }
  return 0;
}

function parseTileStem(input) {
  let s = input.trim();
  s = s.replace(/\\.laz$/i, '').replace(/^tile_/i, '');
  const p = s.split('_');
  if (p.length < 2) return null;
  const x = parseInt(p[0]), y = parseInt(p[1]);
  if (isNaN(x) || isNaN(y)) return null;
  return { x0: x * 100, y0: y * 100, x1: x * 100 + 500, y1: y * 100 + 500 };
}

function indexInBbox(i, bbox) {
  const ix = GX[i] + ix_min, iy = GY[i] + iy_min;
  const cx = x_min + (ix + 0.5) * cellXY;
  const cy = y_min + (iy + 0.5) * cellXY;
  return cx >= bbox.x0 && cx <= bbox.x1 && cy >= bbox.y0 && cy <= bbox.y1;
}

let highlightGroup = null;
let searchActive = false;

function clearSearch() {
  cameraAnim = null;
  if (highlightGroup) {
    scene.remove(highlightGroup);
    highlightGroup.traverse(c => { if (c.isInstancedMesh) { c.dispose(); if (c.geometry) c.geometry.dispose(); } });
    highlightGroup = null;
  }
  for (const code in meshesByClass) {
    const mat = meshesByClass[code].material;
    mat.transparent = false;
    mat.opacity = 1;
    mat.needsUpdate = true;
  }
  searchActive = false;
  document.getElementById('s-status').textContent = '';
}

function flyToScene(cx, cy, cz, extent) {
  const dist = Math.max(extent * 0.8, cellXY * 20);
  const endPos = new THREE.Vector3(cx + dist * 0.6, cy - dist * 0.6, cz + dist * 0.5);
  const endTarget = new THREE.Vector3(cx, cy, cz);
  cameraAnim = { t0: performance.now(), p0: camera.position.clone(), t0t: orbit.target.clone(), p1: endPos, t1t: endTarget };
}

function buildHighlightMesh(flatIndices) {
  if (!flatIndices.length) return null;
  const n = flatIndices.length;
  const geom = new THREE.BoxGeometry(1, 1, 1);
  // NOT vertexColors:true - this BoxGeometry carries no per-vertex "color"
  // attribute, so WebGL supplies the default (0,0,0) for it and zeroes out
  // every instance color before instanceColor is even multiplied in.
  // instanceColor alone (the same path the class meshes use) is sufficient.
  const mat = new THREE.MeshLambertMaterial({ depthWrite: true });
  const mesh = new THREE.InstancedMesh(geom, mat, n);
  mesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(n * 3), 3);
  const arr = mesh.instanceMatrix.array;
  const ca = mesh.instanceColor.array;
  for (let j = 0; j < n; j++) {
    const i = flatIndices[j];
    const ix = GX[i] + ix_min, iy = GY[i] + iy_min;
    const iz0 = GZ0[i] + iz_min, nz = GNZ[i];
    const px = x_min + (ix + 0.5) * cellXY - CX0;
    const py = y_min + (iy + 0.5) * cellXY - CY0;
    const pz = (iz0 + nz * 0.5) * cellZ + zbase;
    const sz = nz * cellZ;
    const o = j * 16;
    arr[o] = cellXY; arr[o+1] = 0; arr[o+2] = 0; arr[o+3] = 0;
    arr[o+4] = 0; arr[o+5] = cellXY; arr[o+6] = 0; arr[o+7] = 0;
    arr[o+8] = 0; arr[o+9] = 0; arr[o+10] = sz; arr[o+11] = 0;
    arr[o+12] = px; arr[o+13] = py; arr[o+14] = pz; arr[o+15] = 1;
    const code = getClassCode(i);
    const col = PALETTE[code] || new THREE.Color(0.5, 0.5, 0.5);
    ca[j*3] = col.r; ca[j*3+1] = col.g; ca[j*3+2] = col.b;
  }
  mesh.instanceMatrix.needsUpdate = true;
  mesh.instanceColor.needsUpdate = true;
  mesh.frustumCulled = false;
  mesh.computeBoundingSphere();
  return mesh;
}

function doSearch(flatIndices) {
  clearSearch();
  if (!flatIndices || !flatIndices.length) {
    document.getElementById('s-status').textContent = 'No matching boxes found.';
    return;
  }
  // Compute world bbox of matched boxes for camera fly-to
  let x0 = Infinity, y0 = Infinity, z0 = Infinity;
  let x1 = -Infinity, y1 = -Infinity, z1 = -Infinity;
  for (const i of flatIndices) {
    const ix = GX[i] + ix_min, iy = GY[i] + iy_min;
    const iz0 = GZ0[i] + iz_min, nz = GNZ[i];
    const px = x_min + (ix + 0.5) * cellXY - CX0;
    const py = y_min + (iy + 0.5) * cellXY - CY0;
    const pz = (iz0 + nz * 0.5) * cellZ + zbase;
    if (px < x0) x0 = px; if (px > x1) x1 = px;
    if (py < y0) y0 = py; if (py > y1) y1 = py;
    if (pz < z0) z0 = pz; if (pz > z1) z1 = pz;
  }
  const dimVal = parseInt(document.getElementById('s-dim').value) / 100;
  for (const code in meshesByClass) {
    const mat = meshesByClass[code].material;
    mat.transparent = true;
    mat.opacity = dimVal;
    mat.needsUpdate = true;
  }
  highlightGroup = new THREE.Group();
  const mesh = buildHighlightMesh(flatIndices);
  if (mesh) { highlightGroup.add(mesh); scene.add(highlightGroup); }
  // Include z1-z0: a column search is tall and thin (one footprint, many z
  // intervals), and framing on XY extent alone leaves it too far away to read.
  flyToScene((x0+x1)/2, (y0+y1)/2, (z0+z1)/2, Math.max(x1-x0, y1-y0, z1-z0, cellXY));
  searchActive = true;
  document.getElementById('s-status').textContent = 'Found ' + flatIndices.length + ' box(es).';
}

document.getElementById('s-col-go').addEventListener('click', () => {
  const ix = parseInt(document.getElementById('s-col-ix').value);
  const iy = parseInt(document.getElementById('s-col-iy').value);
  if (isNaN(ix) || isNaN(iy)) return;
  const m = [];
  for (let i = 0; i < N; i++) { if ((GX[i] + ix_min) === ix && (GY[i] + iy_min) === iy) m.push(i); }
  doSearch(m);
});

document.getElementById('s-rng-go').addEventListener('click', () => {
  const ix0 = parseInt(document.getElementById('s-rng-ix0').value);
  const ix1 = parseInt(document.getElementById('s-rng-ix1').value);
  const iy0 = parseInt(document.getElementById('s-rng-iy0').value);
  const iy1 = parseInt(document.getElementById('s-rng-iy1').value);
  if (isNaN(ix0) || isNaN(ix1) || isNaN(iy0) || isNaN(iy1)) return;
  const m = [];
  for (let i = 0; i < N; i++) {
    const ix = GX[i] + ix_min, iy = GY[i] + iy_min;
    if (ix >= ix0 && ix <= ix1 && iy >= iy0 && iy <= iy1) m.push(i);
  }
  doSearch(m);
});

document.getElementById('s-tile-go').addEventListener('click', () => {
  const bbox = parseTileStem(document.getElementById('s-tile-id').value);
  if (!bbox) { document.getElementById('s-status').textContent = 'Invalid tile name.'; return; }
  const m = [];
  for (let i = 0; i < N; i++) { if (indexInBbox(i, bbox)) m.push(i); }
  doSearch(m);
});

document.getElementById('s-dim').addEventListener('input', () => {
  const val = parseInt(document.getElementById('s-dim').value);
  document.getElementById('s-dim-val').textContent = val + '%';
  if (!searchActive) return;
  const opacity = val / 100;
  for (const code in meshesByClass) {
    meshesByClass[code].material.opacity = opacity;
    meshesByClass[code].material.needsUpdate = true;
  }
});

document.getElementById('s-clear').addEventListener('click', clearSearch);

document.getElementById('search-toggle').addEventListener('click', () => {
  const body = document.getElementById('search-body');
  const icon = document.getElementById('search-icon');
  const expanded = body.style.display !== 'none';
  body.style.display = expanded ? 'none' : 'block';
  icon.textContent = expanded ? '\u25B6' : '\u25BC';
});

// -- 8. Animation loop ------------------------------------------------------
const fpsEl  = document.getElementById('i-fps');
const camEl  = document.getElementById('i-cam');
const boxEl  = document.getElementById('i-boxes');
const speedEl = document.getElementById('i-speed');
boxEl.textContent = N.toLocaleString();
speedEl.textContent = flyState.speed.toFixed(0) + ' m/s';

let last = performance.now();
let frames = 0, fpsAccum = 0;

function tick(now) {
  const dt = Math.min(0.1, (now - last) / 1000);
  last = now;

  if (cameraAnim) {
    const t = (now - cameraAnim.t0) / 500;
    if (t >= 1) {
      camera.position.copy(cameraAnim.p1);
      orbit.target.copy(cameraAnim.t1t);
      camera.lookAt(orbit.target);
      const d = new THREE.Vector3().subVectors(cameraAnim.t1t, cameraAnim.p1).normalize();
      flyState.yaw = Math.atan2(d.y, d.x);
      flyState.pitch = Math.asin(d.z);
      cameraAnim = null;
    } else {
      const s = t * t * (3 - 2 * t);
      camera.position.lerpVectors(cameraAnim.p0, cameraAnim.p1, s);
      orbit.target.lerpVectors(cameraAnim.t0t, cameraAnim.t1t, s);
      camera.lookAt(orbit.target);
    }
  } else if (mode === 'fly') {
    // Build the forward / right vectors from yaw/pitch.
    const cy_ = Math.cos(flyState.yaw),   sy_ = Math.sin(flyState.yaw);
    const cp  = Math.cos(flyState.pitch), sp = Math.sin(flyState.pitch);
    const fwd   = new THREE.Vector3(cp * cy_, cp * sy_, sp);
    const right = new THREE.Vector3(-sy_, cy_, 0);
    const up    = new THREE.Vector3(0, 0, 1);

    let speed = flyState.speed;
    if (flyState.keys.has('ShiftLeft') || flyState.keys.has('ShiftRight')) speed *= 4;

    const dv = new THREE.Vector3();
    if (flyState.keys.has('KeyW')) dv.add(fwd);
    if (flyState.keys.has('KeyS')) dv.sub(fwd);
    if (flyState.keys.has('KeyA')) dv.add(right);
    if (flyState.keys.has('KeyD')) dv.sub(right);
    if (flyState.keys.has('KeyE')) dv.add(up);
    if (flyState.keys.has('KeyQ')) dv.sub(up);
    if (dv.lengthSq()) dv.normalize().multiplyScalar(speed * dt);

    camera.position.add(dv);
    camera.lookAt(camera.position.clone().add(fwd));
  } else {
    orbit.update();
  }

  // HUD
  frames++;
  fpsAccum += dt;
  if (fpsAccum > 0.5) {
    fpsEl.textContent = (frames / fpsAccum).toFixed(0);
    frames = 0; fpsAccum = 0;
    const p = camera.position;
    camEl.textContent = `${p.x.toFixed(0)} / ${p.y.toFixed(0)} / ${p.z.toFixed(0)} m`;
  }

  renderer.render(scene, camera);
  requestAnimationFrame(tick);
}

// Reveal UI now that everything's ready.
document.getElementById('loading').style.display = 'none';
document.getElementById('info').style.display = 'block';
document.getElementById('legend').style.display = 'block';
document.getElementById('help').style.display = 'block';
document.getElementById('search').style.display = 'block';

// Default to fly mode so the experience is "traversable" out of the box;
// users can switch with T.
setMode('fly');
requestAnimationFrame(tick);
</script>
</body>
</html>
"""


# -----------------------------------------------------------------------------
#  Grid-index codec - the shared, lossless quantized encoding
# -----------------------------------------------------------------------------
#
# The viewer and the PLY/PyVista backends all ultimately need per-box centres
# and heights. Those are *pure functions of integer grid coordinates*:
#
#     px = x_min + (ix + 0.5) * cell_xy - cx0
#     py = y_min + (iy + 0.5) * cell_xy - cy0
#     pz = (iz0 + nz * 0.5) * cell_z + (z_min - cz0)
#     sz = nz * cell_z
#
# so storing float32 centres (16 B/box: 3 centre + 1 size) is storing grid
# integers with mantissa noise. This codec ships the four small integers
# (ix, iy, iz0, nz) instead - 8 B/box at uint16 - plus the scalar reconstruction
# constants in the header. The integer coords are exact; the float32
# reconstruction is bit-identical to the original fill loop for power-of-two
# cell sizes (1.0 / 0.5 / 0.25 m; the tests pin 1.0 / 0.5). With a
# non-power-of-two cell_z (the 0.25/0.1 m runs included) the z centres can
# differ by 1 ULP (~1e-6 m, measured): the fill loop's z chain is float32,
# the decoder rounds once from float64.
#
# Two further wins are baked in:
#   * boxes are sorted by class, so `classes` need not be stored per box at all
#     (the header carries a compact [code, start, count] table) and the viewer
#     can build one flat-coloured InstancedMesh per class;
#   * the four arrays are laid out struct-of-arrays and offset to their own
#     minima, so gx/gy are near-monotone runs that gzip crushes.
#
# The same (header, blob) pair is what both `export_html` (base64 in the page)
# and `export_grid` (a .vxg file on disk) emit; `load_grid` / the JS worker
# invert it.

_GRID_CODEC = "grid-u16-soa-v1"
_VXG_MAGIC = b"VXG1"


def _encode_grid_payload(geom: dict) -> tuple[dict, bytes]:
    """
    Turn a geometry dict (from _collect_voxel_geometry()) into a compact,
    class-sorted, struct-of-arrays integer blob plus a JSON-serializable header
    describing how to reconstruct it.

    Returns ``(header, raw_blob)`` where ``raw_blob`` is *uncompressed*; the
    caller decides whether to compress it (the HTML path always does, with
    ``zlib.compress`` - an RFC 1950 zlib stream, not a gzip file).
    """
    n = int(geom["n_boxes"])
    gx  = np.ascontiguousarray(geom["gx"],  dtype=np.int64)
    gy  = np.ascontiguousarray(geom["gy"],  dtype=np.int64)
    gz0 = np.ascontiguousarray(geom["gz0"], dtype=np.int64)
    gnz = np.ascontiguousarray(geom["gnz"], dtype=np.int64)
    cls = np.ascontiguousarray(geom["classes"], dtype=np.uint8)

    # --- sort by class so each class is a contiguous slice ------------------
    # Stable sort keeps spatial locality inside a class, which helps gzip and
    # keeps the reconstruction order deterministic.
    if n:
        order = np.argsort(cls, kind="stable")
        gx, gy, gz0, gnz, cls = gx[order], gy[order], gz0[order], gnz[order], cls[order]

    # --- offset each axis to its own minimum (smallest possible range) -------
    ix_min = int(gx.min())  if n else 0
    iy_min = int(gy.min())  if n else 0
    iz_min = int(gz0.min()) if n else 0
    gx -= ix_min
    gy -= iy_min
    gz0 -= iz_min

    # --- choose the narrowest integer dtype that holds every value -----------
    hi = 0
    for a in (gx, gy, gz0, gnz):
        if a.size:
            hi = max(hi, int(a.max()))
    if hi <= 0xFFFF:
        dt, dtype_name = np.uint16, "uint16"
    elif hi <= 0xFFFFFFFF:
        dt, dtype_name = np.uint32, "uint32"
    else:  # pragma: no cover - astronomically large grids
        dt, dtype_name = np.uint64, "uint64"

    # --- struct-of-arrays blob: [all gx][all gy][all gz0][all gnz] -----------
    blob = (gx.astype(dt, copy=False).tobytes()
            + gy.astype(dt, copy=False).tobytes()
            + gz0.astype(dt, copy=False).tobytes()
            + gnz.astype(dt, copy=False).tobytes())

    # --- per-class [code, start, count] table (mesh build order) -------------
    class_offsets = []
    if n:
        codes, starts, counts = np.unique(cls, return_index=True, return_counts=True)
        # np.unique returns ascending codes with the first index of each; since
        # cls is class-sorted, [start, start+count) is exactly that class's slice.
        for c, s, k in zip(codes.tolist(), starts.tolist(), counts.tolist()):
            class_offsets.append([int(c), int(s), int(k)])

    cx0, cy0, cz0 = geom["origin"]
    x_min, y_min, z_min = geom["grid_min"]

    header = {
        "codec": _GRID_CODEC,
        "idx_dtype": dtype_name,
        "n_boxes": n,
        # scalar reconstruction constants
        "grid": {
            "x_min": float(x_min), "y_min": float(y_min), "z_min": float(z_min),
            "cell_xy": float(geom["cell_xy"]), "cell_z": float(geom["cell_z"]),
            "ix_min": ix_min, "iy_min": iy_min, "iz_min": iz_min,
        },
        "origin_centered": [float(cx0), float(cy0), float(cz0)],
        "class_offsets": class_offsets,
    }
    return header, blob


def _grid_dtype(name: str):
    """Map a grid-cache ``idx_dtype`` name (``uint16``, ``uint32`` or ``uint64``) to the numpy type; any other name raises KeyError."""
    return {"uint16": np.uint16, "uint32": np.uint32, "uint64": np.uint64}[name]


def _decode_grid_payload(header: dict, raw_blob: bytes) -> dict:
    """
    Inverse of _encode_grid_payload(). Reconstructs ``classes`` exactly,
    and float32 ``centers``/``sizes_z`` bit-identically for power-of-two cell
    sizes (within 1 ULP in z otherwise - see the codec note), plus the
    ``colors`` LUT lookup. The result is a geometry dict in the
    _collect_voxel_geometry() layout, consumable by
    export_html_from_geom() and merge_geometries(); note that
    export_ply() / show_pyvista() / export_html() take a
    ColumnStore, not a geometry dict.
    """
    n = int(header["n_boxes"])
    dt = _grid_dtype(header["idx_dtype"])
    itemsize = np.dtype(dt).itemsize
    g = header["grid"]

    if n == 0:
        z = np.zeros
        return {
            "centers": z((0, 3), np.float32), "sizes_z": z((0,), np.float32),
            "classes": z((0,), np.uint8), "colors": z((0, 3), np.uint8),
            "gx": z((0,), np.int32), "gy": z((0,), np.int32),
            "gz0": z((0,), np.int32), "gnz": z((0,), np.int32),
            "cell_xy": g["cell_xy"], "cell_z": g["cell_z"],
            "grid_min": (g["x_min"], g["y_min"], g["z_min"]),
            "origin": tuple(header["origin_centered"]),
            "n_boxes": 0, "applied_stride": 1,
            "bbox": (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        }

    flat = np.frombuffer(raw_blob, dtype=dt)
    if flat.size != 4 * n:
        raise ValueError(
            f"grid blob has {flat.size} values, expected {4 * n} "
            f"(4 arrays x {n} boxes at {itemsize}B)")
    gx  = flat[0 * n:1 * n].astype(np.int64) + g["ix_min"]
    gy  = flat[1 * n:2 * n].astype(np.int64) + g["iy_min"]
    gz0 = flat[2 * n:3 * n].astype(np.int64) + g["iz_min"]
    gnz = flat[3 * n:4 * n].astype(np.int64)

    # classes from the [code, start, count] table
    classes = np.empty((n,), dtype=np.uint8)
    for code, s, k in header["class_offsets"]:
        classes[s:s + k] = code

    cx0, cy0, cz0 = header["origin_centered"]
    cell_xy, cell_z = g["cell_xy"], g["cell_z"]
    # Mirrors the fill loop's x/y chain (float64, one down-cast to f32). The
    # fill loop's z chain is float32 throughout, so z matches bit-for-bit
    # only for power-of-two cell sizes (see the codec note).
    px = g["x_min"] + (gx.astype(np.float64) + 0.5) * cell_xy - cx0
    py = g["y_min"] + (gy.astype(np.float64) + 0.5) * cell_xy - cy0
    pz = (gz0.astype(np.float64) + gnz.astype(np.float64) * 0.5) * cell_z \
         + (g["z_min"] - cz0)
    centers = np.empty((n, 3), dtype=np.float32)
    centers[:, 0] = px
    centers[:, 1] = py
    centers[:, 2] = pz
    sizes_z = (gnz.astype(np.float64) * cell_z).astype(np.float32)

    lut = class_color_lut()
    colors = lut[classes]

    half_xy = cell_xy * 0.5
    half_z = sizes_z * 0.5
    bbox = (
        float(centers[:, 0].min() - half_xy), float(centers[:, 1].min() - half_xy),
        float((centers[:, 2] - half_z).min()),
        float(centers[:, 0].max() + half_xy), float(centers[:, 1].max() + half_xy),
        float((centers[:, 2] + half_z).max()),
    )
    return {
        "centers": centers, "sizes_z": sizes_z, "classes": classes, "colors": colors,
        "gx": (gx.astype(np.int32)), "gy": gy.astype(np.int32),
        "gz0": gz0.astype(np.int32), "gnz": gnz.astype(np.int32),
        "cell_xy": float(cell_xy), "cell_z": float(cell_z),
        "grid_min": (g["x_min"], g["y_min"], g["z_min"]),
        "origin": (cx0, cy0, cz0),
        "n_boxes": n, "applied_stride": 1, "bbox": bbox,
    }


# -----------------------------------------------------------------------------
#  On-disk grid format (.vxg) - the quantized payload as a cache file
# -----------------------------------------------------------------------------

def export_grid(
    store: ColumnStore,
    out_path: str | Path,
    *,
    classes: Optional[Iterable[int]] = None,
    region: Optional[tuple[float, float, float, float]] = None,
    stride: int = 1,
    max_boxes: Optional[int] = 5_000_000,
    compresslevel: int = 9,
) -> Path:
    """
    Write the quantized grid encoding to a self-describing ``.vxg`` file.

    This is the *same* lossless encoding the HTML viewer embeds, persisted as a
    cache: voxelize once, keep the ``.vxg``, and re-render HTML/PLY/PyVista from
    it via load_grid() without re-reading the LAZ or re-expanding the
    store. Typical size is ~8 B/box before gzip and a few bits/box after.

    File layout::

        b"VXG1" | uint32 header_len (LE) | header_json (utf-8) | zlib(blob)

    (``zlib.compress`` output - an RFC 1950 zlib stream, not a gzip file.)

    ``load_grid`` reads it back into a geometry dict. Note this captures the
    *box geometry* the viewers need (position, height, class), not the full
    ColumnStore - per-voxel point counts are intentionally dropped.

    Returns the path written.
    """
    if max_boxes is not None and max_boxes > _MAX_BOXES_HARD_CAP:
        logger.warning("max_boxes=%s clamped to the hard cap %s.",
                       f"{max_boxes:,}", f"{_MAX_BOXES_HARD_CAP:,}")
        max_boxes = _MAX_BOXES_HARD_CAP
    geom = _collect_voxel_geometry(store, classes=classes, region=region,
                                   stride=stride, max_boxes=max_boxes)
    header, blob = _encode_grid_payload(geom)
    header["bbox"] = list(geom["bbox"])
    header["cell_xy"] = float(geom["cell_xy"])
    payload = zlib.compress(blob, compresslevel)

    hbytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
    out_path = Path(out_path)
    with out_path.open("wb") as f:
        f.write(_VXG_MAGIC)
        f.write(struct.pack("<I", len(hbytes)))
        f.write(hbytes)
        f.write(payload)
    return out_path


def load_grid(in_path: str | Path) -> dict:
    """
    Read a ``.vxg`` file written by export_grid() back into a geometry
    dict (float32 ``centers`` / ``sizes_z`` / ``classes`` / ``colors``, plus the
    integer grid arrays). The dict feeds export_html_from_geom() and
    merge_geometries(); the ColumnStore-based exporters
    (export_ply(), show_pyvista(), export_html()) do not
    accept it.
    """
    in_path = Path(in_path)
    with in_path.open("rb") as f:
        magic = f.read(4)
        if magic != _VXG_MAGIC:
            raise ValueError(f"{in_path} is not a VXG1 grid file (bad magic {magic!r})")
        (hlen,) = struct.unpack("<I", f.read(4))
        header = json.loads(f.read(hlen).decode("utf-8"))
        blob = zlib.decompress(f.read())
    geom = _decode_grid_payload(header, blob)
    if "bbox" in header:  # trust the stored bbox verbatim (was computed on write)
        geom["bbox"] = tuple(header["bbox"])
    return geom


def export_html(
    store: ColumnStore,
    out_path: str | Path,
    *,
    title: str = "Voxel tile",
    classes: Optional[Iterable[int]] = None,
    region: Optional[tuple[float, float, float, float]] = None,
    stride: int = 1,
    max_boxes: int = 5_000_000,
) -> Path:
    """
    Write a single self-contained HTML viewer.

    The output file is fully self-contained except for two Three.js modules
    loaded from a CDN (unpkg) and, only on browsers without native
    DecompressionStream, a pako fallback (jsdelivr). The voxel data is
    base64-encoded into the HTML itself, so the file is shareable as-is.

    Returns the path written.

    - At `max_boxes=500_000` expect an HTML file of a few MB at most: the
      quantized payload is 8 B/box before gzip, so even incompressible data
      caps out near ~5.4 MB, and a measured synthetic 500k-box export is
      ~0.4 MB (the old 10-30 MB figures described the float32 payload).
      Loading takes a couple of seconds in a modern browser.
    - For dense inspection of small areas, pass `region` in metric
      coordinates and `max_boxes=None` to keep every box.
    - For a fast overview of a full tile, leave defaults and let the
      auto-thinner pick a stride.
    - `max_boxes` is clamped to `_MAX_BOXES_HARD_CAP` (200 M) so an accidental
      order-of-magnitude budget can't ask the collector for an array that
      would dominate RAM on a 32 GB Windows box.

    @param store      The ColumnStore to visualize; passed straight to
                      ``_collect_voxel_geometry`` and not modified.
    @param out_path   Destination ``.html`` file for the self-contained viewer.
    @param title      Page title baked into the HTML (default "Voxel tile").
    @param classes    Optional whitelist of class codes to keep; ``None`` keeps
                      every class.
    @param region     Optional metric bounding box ``(x0, y0, x1, y1)``; columns
                      whose centre falls outside are dropped. ``None`` keeps all.
    @param stride     Keep one column out of every `stride` per horizontal axis
                      (default 1, keep everything).
    @param max_boxes  Soft cap on the boxes collected (default 5,000,000); the
                      collector thins with a larger stride to stay under it.
                      ``None`` keeps every box. Values above
                      ``_MAX_BOXES_HARD_CAP`` are clamped down with a warning.
    @return Path to the written HTML file.
    @throws MemoryError When geometry collection runs out of memory; re-raised
                        with a message naming the column count and ``max_boxes``.
    """
    if max_boxes is not None and max_boxes > _MAX_BOXES_HARD_CAP:
        logger.warning(
            "export_html: max_boxes=%s exceeds the hard cap; clamping to %s",
            f"{max_boxes:,}", f"{_MAX_BOXES_HARD_CAP:,}",
        )
        max_boxes = _MAX_BOXES_HARD_CAP

    # A true C-level access violation inside numpy cannot be caught here - the
    # collector's single-gather design (count and placement derived from one
    # _off array) is what prevents that class of crash. This guard is
    # for the Python-level failures that *are* recoverable/diagnosable at large
    # scale (chiefly MemoryError), turning a bare traceback into an actionable
    # message that names the store size and the budget in play.
    try:
        geom = _collect_voxel_geometry(store, classes=classes, region=region,
                                       stride=stride, max_boxes=max_boxes)
    except MemoryError as exc:
        n_cols = len(store.columns)
        raise MemoryError(
            f"Ran out of memory collecting voxel geometry for {n_cols:,} "
            f"columns (max_boxes={max_boxes}). Lower max_boxes or pass a "
            f"tighter `region`."
        ) from exc
    return export_html_from_geom(geom, out_path, title=title)


def export_html_from_geom(geom: dict, out_path: str | Path, *,
                          title: str = "Voxel tile") -> Path:
    """
    Serialize an already-collected geometry dict to a self-contained HTML
    viewer - the encode/compress/template half of export_html(), split
    out so it can be fed geometry from a single store *or* from concatenated
    per-shard geometry (merge_geometries()). Identical geometry yields the
    same rendered scene regardless of which path produced it.
    """
    # Quantized, class-sorted, struct-of-arrays integer encoding (see the codec
    # section). This is 8 B/box (uint16) vs the old 20 B/box float+colour blob,
    # and being integer it compresses far better than the float mantissas did.
    header, blob = _encode_grid_payload(geom)

    # zlib-deflate the blob, then base64 for inlining. These files open over
    # file:// so there is no transport Content-Encoding to lean on - the
    # on-disk compression is what shrinks the shareable file. (zlib, not gzip:
    # the payload carries a zlib wrapper, which is what the page's inflater
    # expects.) base64 re-inflates by 4/3, which is why
    # the win is ~15-20x rather than the raw compression ratio.
    compressed = zlib.compress(blob, 9)
    payload_b64 = base64.b64encode(compressed).decode("ascii")

    class_names_min = {int(k): v for k, v in CLASS_NAMES.items()}
    class_colors_min = {int(k): list(v) for k, v in CLASS_COLORS.items()}

    meta = {
        "n_boxes":     int(geom["n_boxes"]),
        "cell_xy":     geom["cell_xy"],
        "bbox":        geom["bbox"],
        "class_names": class_names_min,
        "class_colors": class_colors_min,
        # grid-codec fields the JS side needs to reconstruct the boxes
        "compressed":  True,
        "codec":       header["codec"],
        "idx_dtype":   header["idx_dtype"],
        "grid":        header["grid"],
        "origin_centered": header["origin_centered"],
        "class_offsets":   header["class_offsets"],
    }

    html = (_HTML_TEMPLATE
            .replace("__TITLE__", title)
            .replace("__TILE_LABEL__", title)
            .replace("__META_JSON__", json.dumps(meta))
            .replace("__PAYLOAD_B64__", payload_b64))

    out_path = Path(out_path)
    out_path.write_text(html, encoding="utf-8")
    return out_path


# -----------------------------------------------------------------------------
#  PyVista - optional interactive Python viewer
# -----------------------------------------------------------------------------

def show_pyvista(
    store: ColumnStore,
    *,
    classes: Optional[Iterable[int]] = None,
    region: Optional[tuple[float, float, float, float]] = None,
    stride: int = 1,
    max_boxes: int = 5_000_000,
    screenshot: Optional[str | Path] = None,
):
    """
    Open an interactive PyVista (VTK) window in the current Python session.

    Optional dependency: `pip install pyvista`. The window supports orbit,
    zoom, pan via mouse; press 'q' to close.

    Parameters identical to `export_html`. Pass `screenshot=...` to also
    save a PNG of the current view (works headless if you have a virtual
    framebuffer).

    Returns the constructed `pyvista.PolyData` so callers can save it
    themselves (e.g. ``poly.save('boxes.vtp')``) if they want a persistent
    VTK artefact.
    """
    if max_boxes is not None and max_boxes > _MAX_BOXES_HARD_CAP:
        logger.warning("max_boxes=%s clamped to the hard cap %s.",
                       f"{max_boxes:,}", f"{_MAX_BOXES_HARD_CAP:,}")
        max_boxes = _MAX_BOXES_HARD_CAP
    try:
        import pyvista as pv
    except ImportError as e:
        raise ImportError(
            "pyvista is required for show_pyvista(); install it with "
            "`pip install pyvista`."
        ) from e

    geom = _collect_voxel_geometry(store, classes=classes, region=region,
                                   stride=stride, max_boxes=max_boxes)

    # Build one PolyData of *all* boxes in a single vectorized pass (verts
    # and faces assembled below by numpy broadcasting) - much faster than a
    # python loop adding meshes one by one.
    centers = geom["centers"]
    sizes_z = geom["sizes_z"]
    colors  = geom["colors"]
    n = geom["n_boxes"]
    cell_xy = geom["cell_xy"]

    if n == 0:
        raise ValueError("Empty geometry - nothing to display.")

    # Vectorized box mesh build: 8 vertices and 12 triangles per box,
    # all expressed by offsets from the centre. We expand to a single
    # PolyData with N*8 vertices and N*12 triangles.
    unit = unit_box_corners()

    sizes = np.stack([np.full(n, cell_xy, dtype=np.float32),
                      np.full(n, cell_xy, dtype=np.float32),
                      sizes_z], axis=1)                        # (n, 3)
    # (n, 8, 3): centre + unit*size, broadcasted
    verts = centers[:, None, :] + unit[None, :, :] * sizes[:, None, :]
    verts = verts.reshape(-1, 3).astype(np.float32)

    # Triangle indices for a cube (12 triangles). All 12 are wound CLOCKWISE
    # as seen from outside, so the right-hand-rule normal points into the
    # box. Both consumers of this mesh (show_pyvista, export_ply) render
    # double-sided, so the winding does not matter here. It is the opposite
    # of tileset_exporter._make_unit_box, which is counter-clockwise outward
    # because glTF back-face culling under doubleSided:false requires it.
    cube_tris = unit_box_tris()
    # Tile: (n, 12, 3) -> flatten, then add per-box vertex offset (i*8).
    tris = np.tile(cube_tris, (n, 1, 1))
    tris += (np.arange(n) * 8)[:, None, None]
    tris = tris.reshape(-1, 3)

    # PyVista PolyData wants face arrays as [n_verts_per_face, i0, i1, i2,
    # n_verts_per_face, ...]. Tris are all triangles -> prefix every row
    # with 3.
    faces = np.hstack([np.full((tris.shape[0], 1), 3, dtype=np.int64), tris]) \
                .ravel()

    poly = pv.PolyData(verts, faces)
    # One colour per *triangle* group of 12 -> repeat each box colour 12 times
    # so we can use cell scalars (much faster than per-vertex shading).
    poly.cell_data["RGB"] = np.repeat(colors, 12, axis=0)

    p = pv.Plotter(window_size=(1200, 800))
    p.set_background("#0e1014")
    p.add_mesh(poly, scalars="RGB", rgb=True, show_edges=False, lighting=True)
    p.enable_eye_dome_lighting()  # gives the voxel boxes a depth-cued look
    p.camera_position = 'iso'

    if screenshot is not None:
        p.show(screenshot=str(screenshot), auto_close=False)
    else:
        p.show()
    return poly


# -----------------------------------------------------------------------------
#  PLY export - universal compatibility
# -----------------------------------------------------------------------------
def export_ply(
    store: ColumnStore,
    out_path: str | Path,
    *,
    classes: Optional[Iterable[int]] = None,
    region: Optional[tuple[float, float, float, float]] = None,
    stride: int = 1,
    max_boxes: int = 5_000_000,
    keep_world_coords: bool = False,
) -> Path:
    """
    Write a binary little-endian PLY mesh of all voxel boxes.

    Output loads in MeshLab, CloudCompare, Blender, ParaView. By default
    we save in the *centred* coordinate frame so floating-point precision
    is preserved; pass `keep_world_coords=True` to write in the original
    metric CRS (helpful for overlaying on aerial imagery or GIS layers,
    but the consumer must handle float64).

    Returns the path written.
    """
    if max_boxes is not None and max_boxes > _MAX_BOXES_HARD_CAP:
        logger.warning("max_boxes=%s clamped to the hard cap %s.",
                       f"{max_boxes:,}", f"{_MAX_BOXES_HARD_CAP:,}")
        max_boxes = _MAX_BOXES_HARD_CAP
    geom = _collect_voxel_geometry(store, classes=classes, region=region,
                                   stride=stride, max_boxes=max_boxes)

    centers = geom["centers"]
    sizes_z = geom["sizes_z"]
    colors  = geom["colors"]
    n = geom["n_boxes"]
    cell_xy = geom["cell_xy"]

    if n == 0:
        raise ValueError("Empty geometry - nothing to export.")

    # PLY face indices go to disk as '<i4' (see fdt below), so the format
    # itself caps addressable vertices at 2**31 - 1; past that the indices
    # wrap silently on serialization and the mesh is corrupt. Refuse loudly
    # instead (reachable with max_boxes=None on large regions).
    max_verts = 2**31 - 1
    if n * 8 > max_verts:
        raise ValueError(
            f"{n:,} boxes need {n * 8:,} vertices, beyond the int32 index "
            f"range of the PLY face list ({max_verts:,}); cap --max-boxes "
            f"(or the max_boxes argument), or use the streaming viewer "
            f"(--viz3d-stream / viz3d_cli stream) for full-detail exports.")

    # Same unit cube + tri index trick as in show_pyvista, but emitted as a
    # binary PLY. We use little-endian as it's the de-facto standard.
    unit = unit_box_corners()
    sizes = np.stack([np.full(n, cell_xy, dtype=np.float32),
                      np.full(n, cell_xy, dtype=np.float32),
                      sizes_z], axis=1)
    verts = centers[:, None, :] + unit[None, :, :] * sizes[:, None, :]
    verts = verts.reshape(-1, 3)
    if keep_world_coords:
        ox, oy, oz = geom["origin"]
        verts = verts.astype(np.float64)
        verts[:, 0] += ox
        verts[:, 1] += oy
        verts[:, 2] += oz

    # Per-vertex colours by replicating the per-box colour 8 times.
    vcols = np.repeat(colors, 8, axis=0)

    cube_tris = unit_box_tris()
    tris = np.tile(cube_tris, (n, 1, 1))
    # int64 for the intermediate offset arithmetic only: the on-disk indices
    # are '<i4', which is safe solely because the vertex-count guard above
    # rejects any export past the int32 range before we get here.
    tris = tris.astype(np.int64) + (np.arange(n, dtype=np.int64) * 8)[:, None, None]
    tris = tris.reshape(-1, 3)

    out_path = Path(out_path)
    n_verts = verts.shape[0]
    n_faces = tris.shape[0]
    vert_dtype = "float64" if keep_world_coords else "float"

    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        "comment generated by voxelizer.visualizer3d.export_ply\n"
        f"element vertex {n_verts}\n"
        f"property {vert_dtype} x\n"
        f"property {vert_dtype} y\n"
        f"property {vert_dtype} z\n"
        "property uchar red\n"
        "property uchar green\n"
        "property uchar blue\n"
        f"element face {n_faces}\n"
        "property list uchar int vertex_indices\n"
        "end_header\n"
    ).encode("ascii")

    # Interleave xyz + rgb per vertex. Need a structured dtype to write
    # the mixed-type vertex record in one shot.
    if keep_world_coords:
        vdt = np.dtype([('x','<f8'),('y','<f8'),('z','<f8'),
                        ('r','u1'),('g','u1'),('b','u1')])
    else:
        vdt = np.dtype([('x','<f4'),('y','<f4'),('z','<f4'),
                        ('r','u1'),('g','u1'),('b','u1')])
    vrec = np.empty(n_verts, dtype=vdt)
    vrec['x'] = verts[:, 0]; vrec['y'] = verts[:, 1]; vrec['z'] = verts[:, 2]
    vrec['r'] = vcols[:, 0]; vrec['g'] = vcols[:, 1]; vrec['b'] = vcols[:, 2]

    # Face record: uint8 count (always 3) followed by three int32 indices.
    fdt = np.dtype([('n','u1'),('i','<i4'),('j','<i4'),('k','<i4')])
    frec = np.empty(n_faces, dtype=fdt)
    frec['n'] = 3
    frec['i'] = tris[:, 0]; frec['j'] = tris[:, 1]; frec['k'] = tris[:, 2]

    with out_path.open("wb") as f:
        f.write(header)
        f.write(vrec.tobytes())
        f.write(frec.tobytes())

    return out_path


# -----------------------------------------------------------------------------
#  One-store 3-D render (shared by the area pipeline and the single-tile CLI)
# -----------------------------------------------------------------------------
def store_occupied_centre(store: ColumnStore) -> tuple[float, float]:
    """
    Metric (x, y) centre of the bounding box of *occupied* columns.

    Uses the occupied extent rather than (x_min, y_min) + a nominal tile size:
    a store near a dataset edge may not fill a full square, and centring on the
    empty middle would drop the ROI in a near-empty region. Single source of
    truth for both the area path and the single-tile viewer (which used to keep
    two identical private copies of this).
    """
    if not store.columns:
        raise ValueError("Empty store - no columns to centre on.")
    # key_bounds() avoids materializing every (ix, iy) as a Python tuple - on
    # the 116.5M-column area store that list was ~15 GB, sitting directly in
    # the --viz3d path (crash diagnosis).
    ix_min, iy_min, ix_max, iy_max = store.key_bounds()
    x0 = store.x_min + ix_min * store.cell_xy
    x1 = store.x_min + (ix_max + 1) * store.cell_xy
    y0 = store.y_min + iy_min * store.cell_xy
    y1 = store.y_min + (iy_max + 1) * store.cell_xy
    return (x0 + x1) / 2.0, (y0 + y1) / 2.0


def render_store_3d(
    store: ColumnStore,
    out_dir: str | Path,
    *,
    file_label: str = "area",
    title_label: str | None = None,
    max_boxes: int | None = 5_000_000,
    roi_size: float = 200.0,
    roi_cx: float | None = None,
    roi_cy: float | None = None,
    do_full: bool = True,
    do_roi: bool = True,
    do_grid: bool = False,
) -> list[Path]:
    """
    Render the full and/or ROI interactive HTML viewers for one already-built
    ``ColumnStore`` - no voxelization happens here.

    This is the single implementation behind both entry points that render 3-D:
    the area pipeline (``area_cli.render_area_3d``, on the merged area store)
    and the single-tile CLI (``viz3d_cli``, on a freshly voxelized or a
    ``.npz``-loaded store). Outputs go to ``<out_dir>/<file_label>_full.html``
    and ``<file_label>_roi.html`` (plus matching ``.vxg`` grid caches when
    ``do_grid``). ``title_label`` sets the in-page HTML title and defaults to
    ``file_label``.

    Returns the list of written paths.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not store.columns:
        logger.warning("3-D: empty store - nothing to render.")
        return []
    if not (do_full or do_roi):
        logger.info("3-D: both full and ROI disabled - nothing to render.")
        return []
    title_label = file_label if title_label is None else title_label
    written: list[Path] = []

    if do_full:
        out_full = out_dir / f"{file_label}_full.html"
        logger.info("Writing %s (max_boxes=%s) ...", out_full, max_boxes)
        export_html(store, out_full, title=f"{title_label} - full",
                    max_boxes=max_boxes)
        written.append(out_full)
        if do_grid:
            out_grid = out_dir / f"{file_label}_full.vxg"
            logger.info("Writing %s (grid cache) ...", out_grid)
            export_grid(store, out_grid, max_boxes=max_boxes)
            written.append(out_grid)

    if do_roi:
        cx, cy = store_occupied_centre(store)
        rcx = cx if roi_cx is None else roi_cx
        rcy = cy if roi_cy is None else roi_cy
        half = roi_size / 2.0
        region = (rcx - half, rcy - half, rcx + half, rcy + half)
        size_str = (f"{int(roi_size)}" if float(roi_size).is_integer()
                    else f"{roi_size:g}")
        out_roi = out_dir / f"{file_label}_roi.html"
        logger.info("Writing %s (region=(%.1f, %.1f, %.1f, %.1f), no thinning) ...",
                    out_roi, *region)
        # max_boxes=None: the region itself is the budget, rendered full detail.
        export_html(store, out_roi, title=f"{title_label} - {size_str}m ROI",
                    region=region, max_boxes=None)
        written.append(out_roi)
        if do_grid:
            out_grid = out_dir / f"{file_label}_roi.vxg"
            logger.info("Writing %s (grid cache) ...", out_grid)
            export_grid(store, out_grid, region=region, max_boxes=None)
            written.append(out_grid)

    return written
