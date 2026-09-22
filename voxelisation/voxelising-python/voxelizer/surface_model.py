"""The 2.5-D arms of objective (v): a surface model and a raster reducer.
@ingroup t2_algos


Objective (v) of the brief asks how the volumetric column store compares with
the 2-D and 2.5-D approaches an urban-vegetation pipeline actually uses. This
module is the 2.5-D side of that comparison; the measurements it produced were
taken on the 2026-08-08 campaign, which is archived outside the delivery. The
comparison is controlled: instead of ingesting an external digital surface
model of unknown provenance, both 2.5-D arms are DERIVED from the same
~voxelizer.data_structures.ColumnStore the 3-D arm uses. Same LiDAR,
same tiling, same origin, same grid, same classification; the representation
is the only variable.

Two arms live here.

**Arm S, the surface model** (surface_store()). Every column collapses to
exactly ONE interval, class ``BUILDING``, spanning the column's lowest
``z_start`` to its highest ``z_end``. The result is still a ``ColumnStore``, so
``sun_hours.compute_sun_hours``, ``ray_columns.CeilingDDA``,
``transmittance.transmittance`` and ``solar`` run on it UNMODIFIED - "identical
rays" is then literally true rather than argued.

One consequence is worth stating because it is the whole point of the
comparison rather than an artefact of it. ``compute_sun_hours`` starts each ray
1.5 m above the top of the column's LOWEST interval. In the 3-D store that is
the ground in a penetrated column, so the reading is at eye level beneath the
canopy. Arm S has only one interval, so the same rule puts the ray 1.5 m above
the surface, i.e. on top of the canopy. That is exactly what a solar-radiation
raster computes and exactly what a heightfield can express: there is no "under"
in a model that stores one value per cell. The divergence this produces is the
measurement, not a bug - but it is confined to columns holding more than one
interval, and surface_store() is required to reproduce the 3-D answer
EXACTLY on columns that hold one (gate G1).

**Arm S2, the terrain-preserving variant** (``surface_store(..., keep_floor=
True)``). Two intervals: the column's lowest interval verbatim, then one opaque
slab from its top to the column's top. This is the MNS/MNT pair of a classical
surface-model pipeline expressed as a store. Its ray origin coincides with arm
V's, so diffing S2 against V isolates the occlusion model from the sample
height, and diffing S against S2 prices the sample height alone. S2 is a
diagnostic; arm S proper is the ``keep_floor=False`` collapse below.

**Arm A, the raster reducer** (surface_rasters(), clean_raster(),
object_height(), vegetation_strata()). MNS, MNT, an object-height
map and a top-class raster, following ``vegetalisation/code/fusion_nuage.py``
semantics (the IA.rbre project's vegetalisation package, a sibling of this
one): MNS is the per-cell maximum elevation, MNT the per-cell minimum
elevation restricted to classes outside ``GROUND_EXCLUDED_CLASSES``, the
class raster carries the class of the topmost return, water is flattened to the
global minimum, holes are filled by iterative 8-neighbour averaging and the
height map is MNS - MNT. The strata binning follows their shipped config
(0.30 m and 5.0 m) with the legacy four-bin variant available.

Elevations are voxel FACES: ``mns = z_min + top * cell_z`` is the top face of
the highest occupied voxel and ``mnt = z_min + z_start * cell_z`` the bottom
face of the lowest non-excluded one. A point raster would place both inside
their voxel, so an object height computed here can differ from a point-derived
one by up to one ``cell_z`` in each direction. That is quantisation, it is
stated with every number derived from it, and it is identical across arms.
"""
from __future__ import annotations

import numpy as np

from .classes_config import BUILDING
from .data_structures import ColumnStore, _unpack_keys

__all__ = [
    "GROUND_EXCLUDED_CLASSES", "VEGETATION_CLASSES_A", "WATER_CLASS",
    "SHIPPED_HEIGHT_THRESHOLDS", "LEGACY_HEIGHT_BINS",
    "surface_store", "surface_rasters", "clean_raster", "object_height",
    "vegetation_strata", "vegetation_strata_legacy",
]

# --- arm A constants, matched to the vegetalisation module ------------------
# fusion_nuage.py line 14. A point of these classes may not define the terrain.
GROUND_EXCLUDED_CLASSES = frozenset({1, 3, 4, 5, 8})
# configs/baseline/configs.yml -> lidar.vegetation_classes
VEGETATION_CLASSES_A = (3, 4, 5, 8)
WATER_CLASS = 9                       # fusion_nuage.py line 15
# configs/baseline/configs.yml -> lidar.height_thresholds
SHIPPED_HEIGHT_THRESHOLDS = (0.30, 5.0)
# calculateVegetationFromLidar.py classify_from_difference (legacy branch,
# disabled by the shipped config)
LEGACY_HEIGHT_BINS = (0.5, 1.5, 5.0, 15.0)

_NO_CLASS = np.int16(-1)


def surface_store(store: ColumnStore, *, keep_floor: bool = False,
                  cls: int = BUILDING) -> ColumnStore:
    """Collapse *store* to its 2.5-D surface model (arm S).

    With ``keep_floor=False`` (arm S) each column becomes one
    interval of class *cls* from its lowest ``z_start`` to its highest
    ``z_end``, carrying the column's total point count. With
    ``keep_floor=True`` (arm S2) the column's lowest interval survives verbatim
    and a single slab of class *cls* covers everything above it, so the ray
    origin rule of ``compute_sun_hours`` picks the same height it picks on the
    3-D store.

    The returned store shares *store*'s origin and cell sizes, so a column key
    means the same ground in both. Columns are preserved one for one: a store
    with one interval per column round-trips to an identical geometry (only the
    class label changes), which is what gate G1 tests.
    """
    off = np.asarray(store._off)
    n = int(store._keys.shape[0])
    if n == 0:
        return ColumnStore(store.x_min, store.y_min, store.z_min,
                           store.cell_xy, store.cell_z)
    starts = off[:-1]
    zs_first = np.asarray(store._zs)[starts].astype(np.int32)
    ze_first = np.asarray(store._ze)[starts].astype(np.int32)
    # Highest z_end over the whole column, not the last interval's: z_start is
    # the sort key, so under cross-class overlap an earlier interval can end
    # above a later one (same reason column_summaries reduces with maximum).
    top = np.maximum.reduceat(np.asarray(store._ze), starts).astype(np.int32)
    counts = np.add.reduceat(np.asarray(store._ct).astype(np.int64), starts)
    ix, iy = _unpack_keys(store._keys)

    if not keep_floor:
        return ColumnStore.from_intervals(
            store.x_min, store.y_min, store.z_min, store.cell_xy, store.cell_z,
            ix, iy, zs_first, top, np.full(n, cls, dtype=np.uint8),
            np.clip(counts, 1, np.iinfo(np.int32).max).astype(np.int32),
            assume_canonical=True)

    # arm S2: floor interval verbatim, one slab above it where there is room.
    has_slab = top > ze_first
    cl_first = np.asarray(store._cl)[starts].astype(np.uint8)
    ct_first = np.asarray(store._ct)[starts].astype(np.int32)
    rest = np.clip(counts - ct_first.astype(np.int64), 1,
                   np.iinfo(np.int32).max).astype(np.int32)
    out_ix = np.concatenate([ix, ix[has_slab]])
    out_iy = np.concatenate([iy, iy[has_slab]])
    out_zs = np.concatenate([zs_first, ze_first[has_slab]])
    out_ze = np.concatenate([ze_first, top[has_slab]])
    out_cl = np.concatenate([cl_first, np.full(int(has_slab.sum()), cls,
                                               dtype=np.uint8)])
    out_ct = np.concatenate([ct_first, rest[has_slab]])
    # not canonical: the slabs are appended, so let the constructor sort.
    return ColumnStore.from_intervals(
        store.x_min, store.y_min, store.z_min, store.cell_xy, store.cell_z,
        out_ix, out_iy, out_zs, out_ze, out_cl, out_ct,
        assume_canonical=False)


def surface_rasters(store: ColumnStore, *, lattice: tuple[int, int] | None = None
                    ) -> dict:
    """MNS, MNT and the top-class raster of *store* (arm A, before cleaning).

    Returns a dict with ``mns`` and ``mnt`` as float32 metric elevations (NaN
    where undefined), ``top_cls`` as int16 (-1 where no column), ``n_intervals``
    as int32, ``ix0``/``iy0`` as the lattice origin in column indices, and
    ``shape``. Rows index iy, columns index ix, so ``a[iy - iy0, ix - ix0]``.

    ``mns`` is defined for every occupied column. ``mnt`` is NaN in a column
    whose intervals all belong to ``GROUND_EXCLUDED_CLASSES`` - the case
    that makes the hole filling necessary, and the case a heightfield has to
    invent a terrain for.
    """
    if store._keys.shape[0] == 0:
        raise ValueError("empty store has no rasters")
    ix, iy = _unpack_keys(store._keys)
    ix0, iy0 = (int(ix.min()), int(iy.min()))
    if lattice is None:
        h = int(iy.max()) - iy0 + 1
        w = int(ix.max()) - ix0 + 1
    else:
        w, h = lattice
    off = np.asarray(store._off)
    starts = off[:-1]
    nivs = np.diff(off).astype(np.int32)
    zs = np.asarray(store._zs)
    ze = np.asarray(store._ze)
    cl = np.asarray(store._cl)

    top = np.maximum.reduceat(ze, starts).astype(np.int64)
    # topmost class: among intervals reaching the column top, the smallest
    # class code wins. Same tie rule as ColumnStore.column_summaries, so the
    # two agree column for column.
    cl_at_top = np.where(ze == np.repeat(top, nivs), cl, np.uint8(255))
    top_cls = np.minimum.reduceat(cl_at_top, starts).astype(np.int16)

    # MNT: lowest z_start among intervals whose class may define terrain.
    allowed = ~np.isin(cl, np.fromiter(GROUND_EXCLUDED_CLASSES, dtype=np.uint8))
    big = np.iinfo(np.int32).max
    zs_allowed = np.where(allowed, zs.astype(np.int64), big)
    bot_allowed = np.minimum.reduceat(zs_allowed, starts)

    rows = (iy - iy0).astype(np.int64)
    cols = (ix - ix0).astype(np.int64)
    mns = np.full((h, w), np.nan, dtype=np.float32)
    mnt = np.full((h, w), np.nan, dtype=np.float32)
    tcl = np.full((h, w), _NO_CLASS, dtype=np.int16)
    niv = np.zeros((h, w), dtype=np.int32)
    mns[rows, cols] = (store.z_min + top * store.cell_z).astype(np.float32)
    ok = bot_allowed < big
    mnt[rows[ok], cols[ok]] = (
        store.z_min + bot_allowed[ok] * store.cell_z).astype(np.float32)
    tcl[rows, cols] = top_cls
    niv[rows, cols] = nivs
    return {"mns": mns, "mnt": mnt, "top_cls": tcl, "n_intervals": niv,
            "ix0": ix0, "iy0": iy0, "shape": (h, w),
            "cell_xy": store.cell_xy, "cell_z": store.cell_z}


_NEIGHBOURS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1),
               (1, -1), (1, 0), (1, 1))


def clean_raster(array: np.ndarray, class_raster: np.ndarray, *,
                 max_iters: int = 4096) -> tuple[np.ndarray, dict]:
    """Water flattening plus iterative 8-neighbour hole filling.

    A vectorised transcription of ``fusion_nuage.clean_mnt_mns``. Their loop
    computes every replacement from the PREVIOUS iteration's array (they build
    ``next_cleaned`` from ``cleaned``), which is a Jacobi sweep, so filling all
    NaNs of a pass simultaneously from the pre-pass values is the same
    computation, not an approximation of it. The fallback for a raster that is
    still NaN after the sweeps, and the value water is flattened to, is the
    minimum of the valid input - theirs exactly.

    Returns the cleaned float32 raster and a dict of what it did.
    """
    cleaned = array.astype(np.float32, copy=True)
    valid = cleaned[~np.isnan(cleaned)]
    if valid.size == 0:
        return (np.zeros_like(cleaned, dtype=np.float32),
                {"fallback": None, "iterations": 0, "filled": 0,
                 "water_cells": 0, "unfilled": int(cleaned.size)})
    fallback = float(valid.min())
    water = class_raster == WATER_CLASS
    cleaned[water] = np.float32(fallback)

    filled_total = 0
    iters = 0
    while iters < max_iters:
        nan = np.isnan(cleaned)
        if not nan.any():
            break
        # float64 accumulation, then one cast to float32 at the end: their
        # loop builds a Python list and calls np.mean on it, which sums in
        # float64 and casts once. Accumulating in float32 instead drifts by
        # about 2e-5 m, which is nothing physically and everything for a test
        # that claims to reproduce their computation exactly.
        vals = np.where(nan, 0.0, cleaned).astype(np.float64)
        good = (~nan).astype(np.float64)
        acc = np.zeros(cleaned.shape, dtype=np.float64)
        cnt = np.zeros(cleaned.shape, dtype=np.float64)
        for dr, dc in _NEIGHBOURS:
            acc += _shift(vals, dr, dc)
            cnt += _shift(good, dr, dc)
        can = nan & (cnt > 0)
        n_can = int(can.sum())
        iters += 1
        if n_can == 0:
            break
        mean = (acc / np.maximum(cnt, 1.0)).astype(np.float32)
        cleaned = np.where(can, mean, cleaned).astype(np.float32)
        filled_total += n_can

    unfilled = int(np.isnan(cleaned).sum())
    cleaned[np.isnan(cleaned)] = np.float32(fallback)
    return cleaned, {"fallback": fallback, "iterations": iters,
                     "filled": filled_total, "water_cells": int(water.sum()),
                     "unfilled": unfilled}


def _shift(a: np.ndarray, dr: int, dc: int) -> np.ndarray:
    """*a* translated by (dr, dc) with zero fill - no wraparound."""
    out = np.zeros_like(a)
    h, w = a.shape
    r0, r1 = max(0, dr), min(h, h + dr)
    c0, c1 = max(0, dc), min(w, w + dc)
    out[r0:r1, c0:c1] = a[r0 - dr:r1 - dr, c0 - dc:c1 - dc]
    return out


def object_height(mns: np.ndarray, mnt: np.ndarray) -> np.ndarray:
    """MNS - MNT with ``fusion_nuage.create_object_height_map``'s NaN rules.

    On cleaned inputs no NaN survives, so the three branches below are inert;
    they are kept because the function is also useful on uncleaned rasters and
    because dropping them would silently change the semantics being reproduced.
    """
    height = (mns - mnt).astype(np.float32)
    only_mns = np.isnan(mnt) & ~np.isnan(mns)
    only_mnt = np.isnan(mns) & ~np.isnan(mnt)
    both = np.isnan(mnt) & np.isnan(mns)
    height[only_mns] = mns[only_mns]
    height[only_mnt] = mnt[only_mnt]
    height[both] = 0.0
    return height


def vegetation_strata(height: np.ndarray, top_cls: np.ndarray, *,
                      thresholds: tuple[float, float] = SHIPPED_HEIGHT_THRESHOLDS,
                      keep_classes=VEGETATION_CLASSES_A) -> np.ndarray:
    """The shipped three-bin vegetation product, from a height map.

    ``fusion_lidar_flair.classify_heights`` with the baseline thresholds: 0
    below ``thresholds[0]``, 1 between the two, 2 at or above ``thresholds[1]``,
    and -1 (their NaN, filled from FLAIR downstream) wherever the cell's top
    class is not vegetation. The baseline config also sets
    ``keep_class_lidar1: true``, which additionally keeps class-1 cells that
    the FLAIR vegetation mask marks valid; that branch needs the FLAIR raster
    and is therefore out of reach of a LiDAR-only reproduction.
    """
    lo, hi = float(thresholds[0]), float(thresholds[1])
    keep = np.isin(top_cls, np.asarray(keep_classes, dtype=top_cls.dtype))
    out = np.full(height.shape, -1, dtype=np.int8)
    out[keep & (height < lo)] = 0
    out[keep & (height >= lo) & (height < hi)] = 1
    out[keep & (height >= hi)] = 2
    return out


def vegetation_strata_legacy(height: np.ndarray, top_cls: np.ndarray, *,
                             bins: tuple[float, ...] = LEGACY_HEIGHT_BINS,
                             keep_classes=VEGETATION_CLASSES_A) -> np.ndarray:
    """The legacy four-bin product of ``classify_from_difference``.

    1 for [0.5, 1.5), 2 for [1.5, 5), 3 for [5, 15), 4 at or above 15, and -1
    below 0.5 or outside the kept classes - their NaN, which the legacy branch
    leaves unclassified rather than calling it grass. The shipped baseline
    config disables this branch (``run_legacy_fusion: false``); it is computed
    here only to price the choice of thresholds.
    """
    b0, b1, b2, b3 = (float(b) for b in bins)
    keep = np.isin(top_cls, np.asarray(keep_classes, dtype=top_cls.dtype))
    out = np.full(height.shape, -1, dtype=np.int8)
    out[keep & (height >= b0) & (height < b1)] = 1
    out[keep & (height >= b1) & (height < b2)] = 2
    out[keep & (height >= b2) & (height < b3)] = 3
    out[keep & (height >= b3)] = 4
    return out


def write_views(store, path, *, clean: bool = True) -> dict:
    """Persist the store's derived 2-D views as memory-mappable ``.npy`` files.

    Writes ``mns.npy``, ``mnt.npy``, ``height.npy`` and ``top_cls.npy`` plus a
    ``views.json`` manifest (lattice origin, shape, cell sizes, column count)
    into *path*. The arrays are the ones surface_rasters() derives, with
    clean_raster() hole-filling and object_height() applied when
    *clean* is true - the raster set the store can reproduce, served from the
    store's own artefact. A memory-mapped view answers a heightfield question
    with one array read; the store's scalar path answers it with a key lookup
    and an interval scan. Purely additive: no existing pipeline output
    changes and the store file is untouched. Returns the manifest dict.
    """
    import json as _json
    from pathlib import Path as _Path

    dst = _Path(path)
    dst.mkdir(parents=True, exist_ok=True)
    ras = surface_rasters(store)
    mns, mnt, top_cls = ras["mns"], ras["mnt"], ras["top_cls"]
    if clean:
        mns, _ = clean_raster(mns, top_cls)
        mnt, _ = clean_raster(mnt, top_cls)
    height = object_height(mns, mnt)
    np.save(dst / "mns.npy", mns)
    np.save(dst / "mnt.npy", mnt)
    np.save(dst / "height.npy", height)
    np.save(dst / "top_cls.npy", top_cls)
    manifest = {
        "schema": "voxelizer.views/1",
        "ix0": int(ras["ix0"]), "iy0": int(ras["iy0"]),
        "shape": [int(s) for s in ras["shape"]],
        "x_min": float(store.x_min), "y_min": float(store.y_min),
        "cell_xy": float(store.cell_xy), "cell_z": float(store.cell_z),
        "n_columns": int(store._keys.shape[0]),
        "clean": bool(clean),
        "arrays": ["mns", "mnt", "height", "top_cls"],
    }
    (dst / "views.json").write_text(_json.dumps(manifest, indent=2),
                                    encoding="utf-8")
    return manifest


def load_views(path, *, mmap: bool = True) -> dict:
    """Load a write_views() directory.

    Returns the manifest dict with the arrays added under their names,
    memory-mapped read-only by default so a lookup touches one page rather
    than loading the raster. ``a[iy - iy0, ix - ix0]`` addresses a column's
    cell, the same convention surface_rasters() documents.
    """
    import json as _json
    from pathlib import Path as _Path

    src = _Path(path)
    manifest = _json.loads((src / "views.json").read_text(encoding="utf-8"))
    mode = "r" if mmap else None
    for name in manifest["arrays"]:
        manifest[name] = np.load(src / f"{name}.npy", mmap_mode=mode)
    return manifest
