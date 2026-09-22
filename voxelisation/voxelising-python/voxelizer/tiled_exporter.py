"""
@ingroup t1_donnees


Tiled, view-dependent streaming exporter: maximum detail, never thinned.

EXPORT (memory-bounded, never thinned)
--------------------------------------
The store's columns are in canonical order (packed keys are bias-shifted, so
ascending key order is ascending ``(ix, iy)``), so all columns of one tile row
(a band of ``tile_cols`` consecutive ``ix``) are a contiguous slice of the
store and their intervals are a contiguous slice of ``_zs``/``_ze``/``_cl``.
The store is walked one band at a time, that band's records are bucketed by
``tile_iy`` with a stable sort (so order within a tile stays canonical), and
appended to the ``.bin`` tile-contiguous. Peak resident memory is one band,
not the payload:

    band_records ~= density * area_side * tile_m

With ``ColumnStore.load_dir(mmap=True)`` the store itself costs almost nothing
resident either. Nothing is strided, sampled or capped: the ``.bin`` holds
every interval in the store.

The result is a ``.bin`` whose byte layout is spatial, plus a ``.idx.json``
giving, per tile, its metric AABB and its ``(byte offset, record count)``,
which makes "give me only the boxes over there" an expressible HTTP request
(``Range: bytes=off-off+len-1``; see ``serve_voxel_html``, which answers 206).

PAGE (view-dependent residency: spawn near, evict far)
------------------------------------------------------
``max_instances`` is a working-set budget, not a fill limit and not a
decimation ratio. Every frame-ish the page scores each tile by the nearer of
two distances: from the camera, and from a probe point (the camera pushed
forward along the view direction by a speed-scaled look-ahead, capped at
``TILE_M * 4``). The probe alone would prefetch what you are flying into but
would score the tile you are standing in at the full look-ahead distance, so
closing in to inspect it demoted it behind everything ahead and eventually
evicted it. Taking the minimum keeps the prefetch while pinning the tile under
the camera near score 0. Tiles outside the frustum are demoted, not rejected,
so turning the head does not blank the periphery. Scores are sorted and the
budget is filled with the best-scoring tiles; tiles falling out are evicted
with hysteresis, and incoming tiles are Range-fetched, decoded and spawned as
their own ``InstancedMesh``, a real spatial cull unit. Everything the budget
cannot hold is drawn as a cheap per-tile impostor box from the index's
per-tile z-range, so the far field is a silhouette rather than a hole.
Press P to toggle it.

Public surface
--------------
    write_tiled_payload(store, bin_path, ...) -> dict   # index (also the meta)
    export_tiled_from_store(store, out_path, ...) -> Path

Record layout (32 B):
    0..11  float32 x,y,z   (centred frame)
   12..23  float32 sx,sy,sz
   24..26  uint8   r,g,b
   27      uint8   class
   28..31  reserved (face-exposure mask goes here when that lands)
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

import numpy as np

from .classes_config import CLASS_COLORS, CLASS_NAMES
from .data_structures import ColumnStore, _KOFF, _unpack_keys
from .viz_common import REC_BYTES, REC_DTYPE, class_color_lut

logger = logging.getLogger(__name__)

# The record layout is shared with the 3D Tiles exporter, which re-reads the
# same bytes; see viz_common.REC_DTYPE.
_REC = REC_DTYPE
_REC_BYTES = REC_BYTES               # 32
assert _REC_BYTES == 32

DEFAULT_TILE_M = 64.0


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def _class_lut() -> np.ndarray:
    """256 x 3 uint8 colour table indexed by class code: ``CLASS_COLORS`` where defined, mid-grey (128, 128, 128) elsewhere.

    Delegates to viz_common.class_color_lut(), shared with visualizer3d
    and with the 2-D map renderer in visualization, so a class renders the
    same colour everywhere.
    """
    return class_color_lut()


def _key_lo(ix: int) -> np.uint64:
    """Smallest packed key with this ix (iy at its minimum)."""
    return np.uint64((int(ix) + int(_KOFF)) << 32)


def write_tiled_payload(
    store: ColumnStore,
    bin_path: str | Path,
    *,
    tile_m: float = DEFAULT_TILE_M,
    keep_classes: set[int] | None = None,
    region: tuple[float, float, float, float] | None = None,
    progress: bool = True,
) -> dict:
    """Write a tile-contiguous, FULL-DETAIL record stream. Returns the index.

    One tile row (band) is resident at a time; nothing is strided or capped.

    @param store        Source ColumnStore. Its cell sizes, grid origin and column
                        keys drive the tiling; the store itself is not modified.
    @param bin_path     Destination ``.bin`` for the packed records; parent
                        directories are created as needed and the file is truncated
                        on open.
    @param tile_m       Nominal tile edge in metres (default ``DEFAULT_TILE_M``);
                        snapped to a whole number of ``cell_xy`` columns, so the
                        effective tile edge may differ.
    @param keep_classes Classes to keep, as a set of integer class codes; ``None``
                        keeps every class.
    @param region       ``(x0, y0, x1, y1)`` metres, area-local; columns whose
                        centre falls outside are dropped. ``None`` keeps them all.
    @param progress     Log a band progress line every 8 bands and on the last one
                        (default True).
    @return The version-2 index dict describing the payload: cell sizes, centring
            origin, bbox in metres, class tables, per-class counts and the tile
            offset/count arrays. A store with no kept columns yields the minimal
            index from ``_empty_index`` and a zero-byte ``.bin``.
    """
    bin_path = Path(bin_path)
    bin_path.parent.mkdir(parents=True, exist_ok=True)

    cell_xy = float(store.cell_xy)
    cell_z = float(store.cell_z)
    tile_cols = max(1, int(round(float(tile_m) / cell_xy)))
    tile_m = tile_cols * cell_xy          # snapped to the voxel grid

    keys = store._keys
    off = store._off
    n_cols = int(keys.shape[0])

    # -- pass 0: column index extent + centring origin (batched; never
    #    materialises 116M boxed keys, and never even a full ix/iy array).
    if n_cols == 0:
        idx = _empty_index(store, tile_m, tile_cols)
        bin_path.write_bytes(b"")
        return idx

    B = 4_000_000
    sx = sy = 0
    ix_lo = iy_lo = 2 ** 31 - 1
    ix_hi = iy_hi = -(2 ** 31)
    kept_cols = 0
    for s in range(0, n_cols, B):
        ix, iy = _unpack_keys(np.asarray(keys[s:s + B]))
        if region is not None:
            x0, y0, x1, y1 = region
            cx = store.x_min + (ix.astype(np.float64) + 0.5) * cell_xy
            cy = store.y_min + (iy.astype(np.float64) + 0.5) * cell_xy
            m = (cx >= x0) & (cx <= x1) & (cy >= y0) & (cy <= y1)
            ix, iy = ix[m], iy[m]
            if ix.size == 0:
                continue
        kept_cols += int(ix.size)
        sx += int(ix.astype(np.int64).sum())
        sy += int(iy.astype(np.int64).sum())
        ix_lo = min(ix_lo, int(ix.min())); ix_hi = max(ix_hi, int(ix.max()))
        iy_lo = min(iy_lo, int(iy.min())); iy_hi = max(iy_hi, int(iy.max()))
    if kept_cols == 0:
        idx = _empty_index(store, tile_m, tile_cols)
        bin_path.write_bytes(b"")
        return idx

    # Same centring convention as _collect_voxel_geometry: mean column centre.
    cx0 = store.x_min + (sx / kept_cols + 0.5) * cell_xy
    cy0 = store.y_min + (sy / kept_cols + 0.5) * cell_xy
    cz0 = float(store.z_min)
    z_shift = float(store.z_min) - cz0        # 0.0, kept explicit for clarity

    tix_lo = int(np.floor_divide(ix_lo, tile_cols))
    tix_hi = int(np.floor_divide(ix_hi, tile_cols))
    tiy_lo = int(np.floor_divide(iy_lo, tile_cols))
    tiy_hi = int(np.floor_divide(iy_hi, tile_cols))

    lut = _class_lut()
    tiles: list[dict] = []
    counts_total = np.zeros(256, dtype=np.int64)
    n_written = 0
    byte_off = 0
    bb = [np.inf, np.inf, np.inf, -np.inf, -np.inf, -np.inf]
    n_bands = tix_hi - tix_lo + 1

    with open(bin_path, "wb") as f:
        for bi, tix in enumerate(range(tix_lo, tix_hi + 1)):
            c0 = int(np.searchsorted(keys, _key_lo(tix * tile_cols), "left"))
            c1 = int(np.searchsorted(keys, _key_lo((tix + 1) * tile_cols), "left"))
            if c1 <= c0:
                continue

            i0, i1 = int(off[c0]), int(off[c1])
            if i1 <= i0:
                continue

            zs = np.asarray(store._zs[i0:i1])
            ze = np.asarray(store._ze[i0:i1])
            cl = np.asarray(store._cl[i0:i1])
            niv = np.diff(np.asarray(off[c0:c1 + 1]))
            bix_c, biy_c = _unpack_keys(np.asarray(keys[c0:c1]))
            bix = np.repeat(bix_c, niv)
            biy = np.repeat(biy_c, niv)
            del bix_c, biy_c, niv

            m = None
            if region is not None:
                x0, y0, x1, y1 = region
                cxm = store.x_min + (bix.astype(np.float64) + 0.5) * cell_xy
                cym = store.y_min + (biy.astype(np.float64) + 0.5) * cell_xy
                m = (cxm >= x0) & (cxm <= x1) & (cym >= y0) & (cym <= y1)
                del cxm, cym
            if keep_classes is not None:
                mc = np.isin(cl, np.fromiter(keep_classes, dtype=np.uint8,
                                             count=len(keep_classes)))
                m = mc if m is None else (m & mc)
            if m is not None:
                zs, ze, cl, bix, biy = zs[m], ze[m], cl[m], bix[m], biy[m]
                del m
            n = int(zs.shape[0])
            if n == 0:
                continue

            # -- bucket the band by tile_iy; stable => canonical order inside
            tiy = np.floor_divide(biy, tile_cols)
            order = np.argsort(tiy, kind="stable")
            zs, ze, cl = zs[order], ze[order], cl[order]
            bix, biy, tiy = bix[order], biy[order], tiy[order]
            del order

            # -- geometry (dtype chains cloned from _collect_voxel_geometry)
            # dtype chain cloned EXACTLY from _collect_voxel_geometry, so the
            # records are bit-identical to the reference collector's output
            # (checked during development against a frozen legacy collector;
            # that parity bench was not kept in the test suite, so re-verify
            # against _collect_voxel_geometry on a synthetic store if this
            # chain changes): centre = (zs+ze)/2*cell_z, size =
            # (ze-zs)*cell_z - NOT bottom/top computed separately and averaged,
            # which rounds differently in the last bit.
            rec = np.empty(n, dtype=_REC)
            rec["xyz"][:, 0] = store.x_min + (bix + 0.5) * cell_xy - cx0
            rec["xyz"][:, 1] = store.y_min + (biy + 0.5) * cell_xy - cy0
            zc = (zs.astype(np.float32) + ze.astype(np.float32)) * 0.5 * cell_z \
                 + (store.z_min - cz0)
            sz = (ze - zs).astype(np.float32) * cell_z
            rec["xyz"][:, 2] = zc
            rec["siz"][:, 0] = cell_xy
            rec["siz"][:, 1] = cell_xy
            rec["siz"][:, 2] = sz
            # Tile z-bounds are derived from the RECORDS (what the page draws),
            # not from the grid, so the index AABB can never disagree with the
            # geometry inside it.
            zb = zc - sz * 0.5
            zt = zc + sz * 0.5
            rec["rgb"] = lut[cl]
            rec["cls"][:, :] = 0
            rec["cls"][:, 0] = cl
            del bix, zs, ze

            # -- per-tile spans within this band
            uniq, starts = np.unique(tiy, return_index=True)
            ends = np.append(starts[1:], n)
            zmin_t = np.minimum.reduceat(zb, starts)
            zmax_t = np.maximum.reduceat(zt, starts)
            for k in range(uniq.shape[0]):
                cnt = int(ends[k] - starts[k])
                ty = int(uniq[k])
                tiles.append({
                    "tx": int(tix), "ty": ty,
                    "x0": float(store.x_min + tix * tile_cols * cell_xy - cx0),
                    "y0": float(store.y_min + ty * tile_cols * cell_xy - cy0),
                    "z0": float(zmin_t[k]), "z1": float(zmax_t[k]),
                    "off": int(byte_off + starts[k] * _REC_BYTES),
                    "cnt": cnt,
                })
            del uniq, starts, ends, zmin_t, zmax_t, tiy, biy

            counts_total += np.bincount(cl, minlength=256).astype(np.int64)
            del cl

            half = cell_xy * 0.5
            bb[0] = min(bb[0], float(rec["xyz"][:, 0].min()) - half)
            bb[1] = min(bb[1], float(rec["xyz"][:, 1].min()) - half)
            bb[2] = min(bb[2], float(zb.min()))
            bb[3] = max(bb[3], float(rec["xyz"][:, 0].max()) + half)
            bb[4] = max(bb[4], float(rec["xyz"][:, 1].max()) + half)
            bb[5] = max(bb[5], float(zt.max()))
            del zb, zt

            f.write(rec.tobytes(order="C"))
            byte_off += n * _REC_BYTES
            n_written += n
            del rec

            if progress and (bi % 8 == 0 or bi == n_bands - 1):
                logger.info("  band %d/%d  tiles=%d  records=%s (%.1f MB)",
                            bi + 1, n_bands, len(tiles), f"{n_written:,}",
                            byte_off / 1e6)

    tiles.sort(key=lambda t: (t["tx"], t["ty"]))     # row-major, matches file
    index = {
        "version": 2,
        "record_bytes": _REC_BYTES,
        "n_records": int(n_written),
        "cell_xy": cell_xy,
        "cell_z": cell_z,
        "tile_m": float(tile_m),
        "tile_cols": int(tile_cols),
        "origin": [float(cx0), float(cy0), float(cz0)],
        "grid_origin": [float(store.x_min), float(store.y_min), float(store.z_min)],
        "bbox": [float(v) for v in bb] if n_written else [0.0] * 6,
        "tile_range": [tix_lo, tiy_lo, tix_hi, tiy_hi],
        "class_names": {int(k): v for k, v in CLASS_NAMES.items()},
        "class_colors": {int(k): list(v) for k, v in CLASS_COLORS.items()},
        "class_counts": {int(c): int(counts_total[c])
                         for c in np.nonzero(counts_total)[0]},
        "tiles": {
            "x0":  [t["x0"] for t in tiles],
            "y0":  [t["y0"] for t in tiles],
            "z0":  [t["z0"] for t in tiles],
            "z1":  [t["z1"] for t in tiles],
            "off": [t["off"] for t in tiles],
            "cnt": [t["cnt"] for t in tiles],
        },
    }
    logger.info("tiled payload: %s records, %d tiles, %.1f MB -> %s",
                f"{n_written:,}", len(tiles), byte_off / 1e6, bin_path)
    return index


def _empty_index(store: ColumnStore, tile_m: float, tile_cols: int) -> dict:
    """Index dict in the version-2 layout for a payload with no records: the store's cell sizes and origin, a zero bbox and tile range, the class tables, no class counts and empty tile arrays."""
    return {
        "version": 2, "record_bytes": _REC_BYTES, "n_records": 0,
        "cell_xy": float(store.cell_xy), "cell_z": float(store.cell_z),
        "tile_m": float(tile_m), "tile_cols": int(tile_cols),
        "origin": [float(store.x_min), float(store.y_min), float(store.z_min)],
        "grid_origin": [float(store.x_min), float(store.y_min), float(store.z_min)],
        "bbox": [0.0] * 6, "tile_range": [0, 0, 0, 0],
        "class_names": {int(k): v for k, v in CLASS_NAMES.items()},
        "class_colors": {int(k): list(v) for k, v in CLASS_COLORS.items()},
        "class_counts": {},
        "tiles": {"x0": [], "y0": [], "z0": [], "z1": [], "off": [], "cnt": []},
    }


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
_TILED_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>__TITLE__</title>
<style>
  html,body{margin:0;height:100%;background:#0e1014;color:#d8d8d8;
            font-family:-apple-system,system-ui,"Segoe UI",sans-serif;}
  #c{display:block;width:100%;height:100%;cursor:crosshair;}
  .panel{position:fixed;background:rgba(14,16,20,.78);border:1px solid #2a2f38;
         border-radius:6px;padding:10px 12px;font-size:12px;line-height:1.5;
         backdrop-filter:blur(6px);}
  #info{top:10px;left:10px;pointer-events:none;min-width:250px;}
  #info .k{color:#8a93a4;} #info .v{color:#fff;font-variant-numeric:tabular-nums;}
  #legend{top:10px;right:10px;max-height:calc(100vh - 20px);overflow-y:auto;min-width:210px;}
  #legend h3{margin:0 0 6px;font-size:12px;color:#fff;font-weight:600;}
  #legend label{display:flex;align-items:center;padding:3px 0;cursor:pointer;user-select:none;}
  #legend .sw{display:inline-block;width:14px;height:14px;margin-right:8px;
              border-radius:3px;border:1px solid #00000055;}
  #legend input{margin:0 6px 0 0;accent-color:#6aa9ff;}
  #legend .count{color:#6c7383;margin-left:auto;font-variant-numeric:tabular-nums;}
  #help{bottom:16px;left:10px;max-width:640px;}
  #help b{color:#fff;}
  #err{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);max-width:620px;
       padding:18px 22px;border-radius:8px;background:#1a1216;border:1px solid #5a2a35;
       color:#ffd7de;font-size:13px;line-height:1.6;display:none;}
  #err code{background:#00000055;padding:1px 5px;border-radius:3px;color:#fff;}
  #search{top:155px;left:10px;min-width:248px;z-index:10;}
  #search .sec{color:#8a93a4;font-size:11px;margin-bottom:2px;}
  #search .row{display:flex;align-items:center;gap:4px;flex-wrap:wrap;margin-bottom:5px;}
  #search input[type=number],#search input[type=text]{background:#1a1e26;border:1px solid #2a2f38;
    border-radius:3px;color:#fff;padding:2px 4px;font-size:11px;outline:none;width:56px;}
  #search input:focus{border-color:#6aa9ff;}
  #search button{background:#2a2f38;border:1px solid #3a3f48;border-radius:3px;
    color:#d8d8d8;padding:2px 8px;font-size:11px;cursor:pointer;}
  #search button:hover{background:#3a3f48;}
  #search .sep{border:none;border-top:1px solid #2a2f38;margin:6px 0;}
  #search input[type=range]{-webkit-appearance:none;appearance:none;height:4px;
    background:#2a2f38;border-radius:2px;outline:none;flex:1;min-width:60px;}
  #search input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;
    width:12px;height:12px;background:#6aa9ff;border-radius:50%;cursor:pointer;}
  #search input[type=range]::-moz-range-thumb{width:12px;height:12px;
    background:#6aa9ff;border-radius:50%;cursor:pointer;border:none;}
  #s-tile-id{width:140px!important;}
  #s-dim-val{color:#fff;font-size:11px;min-width:28px;text-align:right;}
  #s-status{color:#8a93a4;font-size:11px;margin-top:4px;min-height:14px;}
  #search .btn-full{width:100%;margin-top:4px;}
</style>
</head>
<body>
<canvas id="c"></canvas>
<div id="info" class="panel">
  <div><span class="k">tile:&nbsp;</span><span class="v" id="i-tile">__TILE_LABEL__</span></div>
  <div><span class="k">drawn:&nbsp;</span><span class="v" id="i-drawn">0</span></div>
  <div><span class="k">resident:&nbsp;</span><span class="v" id="i-res">0</span></div>
  <div><span class="k">fetch:&nbsp;</span><span class="v" id="i-fetch">idle</span></div>
  <div><span class="k">FPS:&nbsp;</span><span class="v" id="i-fps">0</span></div>
  <div><span class="k">cam:&nbsp;</span><span class="v" id="i-cam"> - </span></div>
  <div><span class="k">speed:&nbsp;</span><span class="v" id="i-speed"> - </span></div>
</div>
<div id="legend" class="panel"><h3>Classes</h3><div id="legend-rows"></div></div>
<div id="help" class="panel">
  <b>Click canvas</b> to capture mouse &nbsp;|&nbsp; <b>WASD</b> move &nbsp;|&nbsp;
  <b>Q/E</b> down/up &nbsp;|&nbsp; <b>Shift</b> sprint &nbsp;|&nbsp; <b>Wheel</b> speed &nbsp;|&nbsp;
  <b>R</b> reset &nbsp;|&nbsp; <b>T</b> orbit &nbsp;|&nbsp; <b>P</b> impostors &nbsp;|&nbsp;
  <b>Esc</b> release<br>
  Full detail, no thinning: the payload holds every voxel. The budget decides
  <i>which</i> tiles are on the GPU, never <i>how many</i> boxes inside them.
</div>
<div id="err"></div>

<div id="search" class="panel">
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
{"imports":{"three":"https://unpkg.com/three@0.160.0/build/three.module.js",
            "three/addons/":"https://unpkg.com/three@0.160.0/examples/jsm/"}}
</script>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const IDX           = __IDX_JSON__;
const PAYLOAD_URL   = "__PAYLOAD_URL__";
const INLINE_B64    = "__INLINE_B64__";      // "" when a sidecar is used
const MAX_INSTANCES = __MAX_INSTANCES__;     // WORKING-SET budget
const REC           = IDX.record_bytes;      // 32
const TILE_M        = IDX.tile_m;
const MAX_CONC      = 6;                     // concurrent range fetches
const BYTE_CACHE_MB = 512;
const [CX0,CY0,CZ0] = IDX.origin;
const [GX0,GY0,GZ0_] = IDX.grid_origin;
const cellXY        = IDX.cell_xy;

// ---------------- inline payload (small exports stay single-file) -----------
let INLINE_BUF = null;
if (INLINE_B64) {
  const bin = atob(INLINE_B64);
  INLINE_BUF = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) INLINE_BUF[i] = bin.charCodeAt(i);
}

// ---------------- scene ------------------------------------------------------
const canvas = document.getElementById('c');
const renderer = new THREE.WebGLRenderer({canvas, antialias:true,
                                          powerPreference:'high-performance'});
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0e1014);

const [xmin,ymin,zmin,xmax,ymax,zmax] = IDX.bbox;
const cx=(xmin+xmax)/2, cy=(ymin+ymax)/2, cz=(zmin+zmax)/2;
const span = Math.max(xmax-xmin, ymax-ymin, 1);

// Far plane is pulled in to the working-set horizon (the old 0.5/1e6 pair
// spent the whole depth buffer on emptiness); impostors cover beyond it.
const camera = new THREE.PerspectiveCamera(60, innerWidth/innerHeight, 1.0,
                                           Math.max(4000, span*1.6));
camera.up.set(0,0,1);
scene.fog = new THREE.Fog(0x0e1014, 400, Math.max(2500, span*1.2));
addEventListener('resize', ()=>{
  camera.aspect = innerWidth/innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

scene.add(new THREE.AmbientLight(0xffffff, .55));
const sun = new THREE.DirectionalLight(0xfff0d8, .85); sun.position.set(.6,.4,1); scene.add(sun);
const fill = new THREE.DirectionalLight(0x90b0ff, .35); fill.position.set(-.8,-.2,.5); scene.add(fill);
{
  const plane = new THREE.Mesh(new THREE.PlaneGeometry(xmax-xmin+400, ymax-ymin+400),
                               new THREE.MeshBasicMaterial({color:0x14181f}));
  plane.position.set(cx, cy, zmin-0.5); scene.add(plane);
}

// ---------------- controls (unchanged scheme) --------------------------------
const orbit = new OrbitControls(camera, canvas);
orbit.enableDamping = true; orbit.dampingFactor = .07;
orbit.target.set(cx, cy, cz);
let mode='orbit';
let cameraAnim=null;          // hoisted above resetCamera(), which is called below
const fly = {yaw:0, pitch:0, keys:new Set(), speed:Math.max(20, span/25)};
const defaultCam = {pos:new THREE.Vector3(cx+span*.35, cy-span*.35, cz+span*.30),
                    look:new THREE.Vector3(cx, cy, cz)};
function resetCamera(){
  cameraAnim=null;
  camera.position.copy(defaultCam.pos); camera.lookAt(defaultCam.look);
  const d = new THREE.Vector3().subVectors(defaultCam.look, camera.position).normalize();
  fly.yaw = Math.atan2(d.y, d.x); fly.pitch = Math.asin(d.z);
  orbit.target.copy(defaultCam.look); orbit.update();
}
resetCamera();
function setMode(n){ if(n===mode) return; mode=n; orbit.enabled=(mode==='orbit');
  canvas.style.cursor = mode==='fly' ? 'crosshair' : 'grab'; }
canvas.addEventListener('click', ()=>{ if(mode==='fly') canvas.requestPointerLock(); });
addEventListener('mousemove', e=>{
  if(mode!=='fly' || document.pointerLockElement!==canvas) return;
  fly.yaw -= e.movementX*0.0022; fly.pitch -= e.movementY*0.0022;
  const L = Math.PI/2 - 0.001; fly.pitch = Math.max(-L, Math.min(L, fly.pitch));
});
addEventListener('keydown', e=>{
  // Don't steal keys typed into the search panel's inputs.
  if(e.target && (e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA')) return;
  if(e.key==='r'||e.key==='R'){ resetCamera(); return; }
  if(e.key==='t'||e.key==='T'){ setMode(mode==='orbit'?'fly':'orbit'); return; }
  if(e.key==='p'||e.key==='P'){ impostors.visible = !impostors.visible; return; }
  fly.keys.add(e.code);
});
addEventListener('keyup', e=>fly.keys.delete(e.code));  // always clear - harmless
canvas.addEventListener('wheel', e=>{
  if(mode!=='fly') return; e.preventDefault();
  fly.speed = Math.max(1, Math.min(5000, fly.speed*Math.pow(0.9, Math.sign(e.deltaY))));
  document.getElementById('i-speed').textContent = fly.speed.toFixed(0)+' m/s';
}, {passive:false});

// ---------------- tiles ------------------------------------------------------
const T = IDX.tiles;
const NT = T.off.length;
const tiles = new Array(NT);
for (let i=0;i<NT;i++){
  const x0=T.x0[i], y0=T.y0[i], z0=T.z0[i], z1=T.z1[i];
  tiles[i] = {
    i, x0, y0, z0, z1, off:T.off[i], cnt:T.cnt[i],
    box: new THREE.Box3(new THREE.Vector3(x0,y0,z0),
                        new THREE.Vector3(x0+TILE_M, y0+TILE_M, z1)),
    c: new THREE.Vector3(x0+TILE_M/2, y0+TILE_M/2, (z0+z1)/2),
    mesh:null, state:'out', score:0, abort:null,
  };
}

// far-field impostors: one box per tile, spanning its z-range. Not detail -
// a silhouette, so "too far to draw" is a horizon and not a black hole.
const impostors = new THREE.InstancedMesh(
  new THREE.BoxGeometry(1,1,1),
  new THREE.MeshLambertMaterial({color:0x2b3340, transparent:true, opacity:0.85}),
  Math.max(1, NT));
impostors.frustumCulled = false;
{
  const m = new THREE.Matrix4();
  for (let i=0;i<NT;i++){
    const t = tiles[i];
    m.makeScale(TILE_M, TILE_M, Math.max(0.5, t.z1-t.z0));
    m.setPosition(t.c.x, t.c.y, (t.z0+t.z1)/2);
    impostors.setMatrixAt(i, m);
  }
  impostors.count = NT;
  impostors.instanceMatrix.needsUpdate = true;
  scene.add(impostors);
}
const impostorOn  = new THREE.Matrix4();
function setImpostor(t, on){
  const m = new THREE.Matrix4();
  if (on) { m.makeScale(TILE_M, TILE_M, Math.max(0.5, t.z1-t.z0));
            m.setPosition(t.c.x, t.c.y, (t.z0+t.z1)/2); }
  else    { m.makeScale(0,0,0); }
  impostors.setMatrixAt(t.i, m);
  impostors.instanceMatrix.needsUpdate = true;
}

// ---------------- byte cache (LRU) -------------------------------------------
const byteCache = new Map();          // i -> Uint8Array
let byteCacheBytes = 0;
function cacheGet(i){
  const v = byteCache.get(i);
  if (v){ byteCache.delete(i); byteCache.set(i, v); }   // touch
  return v;
}
function cachePut(i, buf){
  byteCache.set(i, buf); byteCacheBytes += buf.byteLength;
  while (byteCacheBytes > BYTE_CACHE_MB*1024*1024 && byteCache.size > 1){
    const k = byteCache.keys().next().value;
    byteCacheBytes -= byteCache.get(k).byteLength;
    byteCache.delete(k);
  }
}

// ---------------- fetch ------------------------------------------------------
let inFlight = 0, fetchedMB = 0;
const errEl = document.getElementById('err');
function fatal(msg){ errEl.style.display='block'; errEl.innerHTML = msg; }

async function fetchTile(t){
  if (INLINE_BUF) return INLINE_BUF.subarray(t.off, t.off + t.cnt*REC);
  const cached = cacheGet(t.i);
  if (cached) return cached;
  const first = t.off, last = t.off + t.cnt*REC - 1;
  t.abort = new AbortController();
  const res = await fetch(PAYLOAD_URL, {
    headers:{'Range':`bytes=${first}-${last}`}, signal:t.abort.signal});
  if (res.status !== 206 && res.status !== 200)
    throw new Error('HTTP '+res.status);
  let buf = new Uint8Array(await res.arrayBuffer());
  if (res.status === 200 && buf.byteLength > t.cnt*REC)   // server ignored Range
    buf = buf.subarray(first, first + t.cnt*REC);
  fetchedMB += buf.byteLength/1e6;
  cachePut(t.i, buf);
  return buf;
}

// ---------------- spawn / evict ----------------------------------------------
const boxGeom = new THREE.BoxGeometry(1,1,1);
const boxMat  = new THREE.MeshLambertMaterial({color:0xffffff});
const classVisible = {};
let drawn = 0, resident = 0;

function spawn(t, bytes){
  const n = t.cnt;
  const mesh = new THREE.InstancedMesh(boxGeom, boxMat, n);
  mesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(n*3), 3);
  const arr = mesh.instanceMatrix.array, ca = mesh.instanceColor.array;
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const sz = new Float32Array(n), cls = new Uint8Array(n);
  const ixA = new Int32Array(n), iyA = new Int32Array(n);
  const cxy = cellXY;
  for (let j=0;j<n;j++){
    const o = j*REC, k = j*16;
    const x = dv.getFloat32(o, true);
    const y = dv.getFloat32(o+4, true);
    const s = dv.getFloat32(o+20, true);
    const code = dv.getUint8(o+27);
    const vis = classVisible[code] !== false;
    arr[k]    = vis ? dv.getFloat32(o+12, true) : 0;
    arr[k+5]  = vis ? dv.getFloat32(o+16, true) : 0;
    arr[k+10] = vis ? s : 0;
    arr[k+12] = x; arr[k+13] = y;
    arr[k+14] = dv.getFloat32(o+8, true);
    arr[k+15] = 1;
    ca[j*3]   = dv.getUint8(o+24)/255;
    ca[j*3+1] = dv.getUint8(o+25)/255;
    ca[j*3+2] = dv.getUint8(o+26)/255;
    sz[j] = s; cls[j] = code;
    ixA[j] = Math.round((x + CX0 - GX0) / cxy - 0.5);
    iyA[j] = Math.round((y + CY0 - GY0) / cxy - 0.5);
  }
  mesh.instanceMatrix.needsUpdate = true;
  mesh.instanceColor.needsUpdate = true;
  mesh.boundingBox = t.box.clone();
  mesh.boundingSphere = t.box.getBoundingSphere(new THREE.Sphere());
  mesh.frustumCulled = true;
  t.mesh = mesh; t.sz = sz; t.cls = cls; t.ix = ixA; t.iy = iyA; t.state = 'in';
  scene.add(mesh);
  drawn += n; resident++;
  setImpostor(t, false);
}

function evict(t){
  if (t.abort){ t.abort.abort(); t.abort = null; }
  if (t.mesh){
    scene.remove(t.mesh);
    t.mesh.dispose();
    drawn -= t.cnt; resident--;
    // Null EVERY per-tile array, not just mesh/sz/cls: ix/iy are
    // Int32Arrays (8 B/box) that used to survive eviction forever.
    t.mesh = null; t.sz = null; t.cls = null; t.ix = null; t.iy = null;
    setImpostor(t, true);
  }
  t.state = 'out';
}

// ---------------- residency manager ------------------------------------------
// Score = distance to the tile's AABB from whichever is NEARER, the camera or
// a PROBE POINT (the camera pushed forward along the view direction), so the
// working set follows where you ARE and where you LOOK. The reason the camera
// is in that min is spelled out at the scoring loop below. Tiles outside the
// frustum are demoted, not dropped: the periphery you are about to turn into
// stays warm.
const frustum = new THREE.Frustum();
const projView = new THREE.Matrix4();
const probe = new THREE.Vector3();
const fwd = new THREE.Vector3();
const OUT_OF_FRUSTUM_PENALTY = 3.0;
const HYSTERESIS = 1.30;              // keep-resident budget multiplier

let lastPos = new THREE.Vector3(1e9,1e9,1e9), lastYaw = 1e9, lastPitch = 1e9;
let updating = false;

function cameraForward(v){
  if (mode==='fly'){
    const cy_=Math.cos(fly.yaw), sy_=Math.sin(fly.yaw);
    const cp=Math.cos(fly.pitch), sp=Math.sin(fly.pitch);
    v.set(cp*cy_, cp*sy_, sp);
  } else {
    v.subVectors(orbit.target, camera.position).normalize();
  }
  return v;
}

async function updateResidency(){
  if (updating) return;
  updating = true;
  try {
    camera.updateMatrixWorld();
    projView.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    frustum.setFromProjectionMatrix(projView);

    cameraForward(fwd);
    // Look-ahead scales with speed: prefetch what you are flying into.
    const ahead = Math.min(TILE_M*4, fly.speed*1.2);
    probe.copy(camera.position).addScaledVector(fwd, ahead);

    const cand = [];
    const camPos = camera.position;
    for (let i=0;i<NT;i++){
      const t = tiles[i];
      // Score by the NEARER of camera and probe. Probe-only scoring ranked the
      // tile you are standing in at `ahead` metres (up to TILE_M*4), so closing
      // in to inspect a tile pushed it DOWN the priority list behind everything
      // in front of you - it then fell out of the budget and was evicted, which
      // read as "I cannot zoom in far enough to see detail". Taking the min
      // keeps the prefetch (tiles ahead still score ~0) while guaranteeing the
      // tile under the camera is never demoted by the look-ahead.
      const d = Math.max(1, Math.min(t.box.distanceToPoint(camPos),
                                     t.box.distanceToPoint(probe)));
      t.score = frustum.intersectsBox(t.box) ? d : d*OUT_OF_FRUSTUM_PENALTY;
      cand.push(t);
    }
    cand.sort((a,b)=>a.score-b.score);

    const want = new Set();
    let acc = 0;
    for (const t of cand){
      if (acc + t.cnt <= MAX_INSTANCES || want.size === 0){
        want.add(t.i); acc += t.cnt;
      }
      if (acc >= MAX_INSTANCES) break;
    }
    // keep must be a superset of want, or a tile want just fetched/spawned
    // gets evicted on the very next pass - defeating the hysteresis below.
    const keep = new Set(want);
    let acc2 = acc;
    for (const t of cand){
      if (keep.has(t.i)) continue;
      if (acc2 + t.cnt > MAX_INSTANCES*HYSTERESIS) break;
      keep.add(t.i); acc2 += t.cnt;
    }

    for (const t of tiles)
      // searchPinned: an active search is mid-fetch on this tile; evicting it
      // here would abort that fetch out from under the search and make it
      // silently drop a real match (see doStreamSearch).
      if (t.state !== 'out' && !keep.has(t.i) && !t.searchPinned) evict(t);

    const todo = [];
    for (const t of cand)
      if (want.has(t.i) && t.state === 'out') todo.push(t);

    let p = 0;
    const workers = new Array(Math.min(MAX_CONC, todo.length)).fill(0).map(async ()=>{
      while (p < todo.length){
        const t = todo[p++];
        if (t.state !== 'out') continue;
        t.state = 'loading'; inFlight++;
        try {
          const bytes = await fetchTile(t);
          if (t.state === 'loading') spawn(t, bytes);
        } catch (e) {
          if (e.name !== 'AbortError'){
            t.state = 'out';
            if (!INLINE_BUF && !errEl.style.display.includes('block'))
              fatal('Could not range-fetch <code>'+PAYLOAD_URL+'</code>: '+e.message+
                    '<br><br>A tiled viewer needs an HTTP origin that answers ' +
                    '<code>Range</code> requests. Serve the folder with:<br><br>' +
                    '<code>python -m voxelizer.serve_voxel_html &lt;dir&gt;</code>' +
                    '<br><br>then open it over <code>http://</code> (not <code>file://</code>).');
          } else { t.state = 'out'; }
        } finally { inFlight--; }
      }
    });
    await Promise.all(workers);
  } finally { updating = false; }
}

// ---------------- legend ------------------------------------------------------
{
  const counts = IDX.class_counts;
  const rows = document.getElementById('legend-rows');
  const sorted = Object.keys(counts).map(Number).sort((a,b)=>counts[b]-counts[a]);
  for (const code of sorted){
    classVisible[code] = true;
    const rgb = IDX.class_colors[code] || [128,128,128];
    const name = IDX.class_names[code] || ('class_'+code);
    const l = document.createElement('label');
    l.innerHTML = `<input type="checkbox" checked data-code="${code}">
      <span class="sw" style="background:rgb(${rgb.join(',')})"></span>
      <span>${name}</span>
      <span class="count">${counts[code].toLocaleString()}</span>`;
    rows.appendChild(l);
  }
  rows.addEventListener('change', e=>{
    if (e.target.tagName!=='INPUT') return;
    const code = parseInt(e.target.dataset.code);
    classVisible[code] = e.target.checked;
    const cxy = IDX.cell_xy;
    for (const t of tiles){
      if (!t.mesh) continue;
      const arr = t.mesh.instanceMatrix.array;
      let touched = false;
      for (let j=0;j<t.cnt;j++){
        if (t.cls[j] !== code) continue;
        const k = j*16;
        if (e.target.checked){ arr[k]=cxy; arr[k+5]=cxy; arr[k+10]=t.sz[j]; }
        else { arr[k]=0; arr[k+5]=0; arr[k+10]=0; }
        touched = true;
      }
      if (touched) t.mesh.instanceMatrix.needsUpdate = true;
    }
  });
}

// ---------------- search (column, range, tile) --------------------------------
function parseTileStem(input){
  let s=input.trim();
  s=s.replace(/\.laz$/i,'').replace(/^tile_/i,'');
  const p=s.split('_');
  if(p.length<2) return null;
  const x=parseInt(p[0]),y=parseInt(p[1]);
  if(isNaN(x)||isNaN(y)) return null;
  return {x0:x*100,y0:y*100,x1:x*100+500,y1:y*100+500};
}
let highlightGroup=null, searchActive=false, searchGen=0;
function clearSearch(){
  searchGen++;             // invalidate any in-flight doStreamSearch run
  cameraAnim=null;         // cancel a pending fly-to; camera POSITION is left as-is
  if(highlightGroup){ scene.remove(highlightGroup);
    highlightGroup.traverse(c=>{if(c.isInstancedMesh){c.dispose();if(c.geometry)c.geometry.dispose();}});
    highlightGroup=null; }
  boxMat.transparent=false; boxMat.opacity=1; boxMat.needsUpdate=true;
  impostors.material.opacity=0.85; impostors.material.needsUpdate=true;
  searchActive=false; document.getElementById('s-status').textContent='';
}
function flyToScene(cx,cy,cz,extent){
  // Floor is size-proportional (not tile-scale): a single-column search has a
  // ~cellXY footprint and must still zoom in close enough to read its stack of
  // boxes, not park TILE_M away where a handful of boxes is an invisible speck.
  const dist=Math.max(extent*0.8, cellXY*20);
  const endPos=new THREE.Vector3(cx+dist*0.5, cy-dist*0.5, cz+dist*0.4);
  const endTarget=new THREE.Vector3(cx,cy,cz);
  cameraAnim={t0:performance.now(), p0:camera.position.clone(), t0t:orbit.target.clone(), p1:endPos, t1t:endTarget};
}
// Grid-index AABB of a tile. t.x0 is the centred metric corner and by
// construction t.x0+CX0-GX0 == tx*tile_cols*cellXY exactly, so this is lossless.
const TILE_COLS = IDX.tile_cols;
function tileGridBox(t){
  const ixLo=Math.round((t.x0+CX0-GX0)/cellXY);
  const iyLo=Math.round((t.y0+CY0-GY0)/cellXY);
  return {ixLo, iyLo, ixHi:ixLo+TILE_COLS-1, iyHi:iyLo+TILE_COLS-1};
}
// gridBounds ({ix0,iy0,ix1,iy1}, inclusive) is a conservative superset of the
// columns a query can match. A tile whose footprint misses it cannot contain a
// match, so we skip it WITHOUT fetching - the whole reason a single-column
// search shouldn't drag the entire 10 GB payload over the wire.
function tileMaybeMatches(t, gb){
  if(!gb) return true;
  const b=tileGridBox(t);
  return gb.ix1>=b.ixLo && gb.ix0<=b.ixHi && gb.iy1>=b.iyLo && gb.iy0<=b.iyHi;
}
async function doStreamSearch(matchFn, flyTo=null, gridBounds=null){
  clearSearch();               // also bumps searchGen, invalidating any prior run
  const myGen=searchGen;       // this run's identity, captured AFTER the clear above
  const st=document.getElementById('s-status');
  st.textContent='Searching ...';
  const md=[]; // {row:Float32Array(16), color:[r,g,b]}
  const scanned=new Set();   // tile indices already contributing to md
  function scanResident(t){
    scanned.add(t.i);
    const arr=t.mesh.instanceMatrix.array, ca=t.mesh.instanceColor.array;
    for(let j=0;j<t.cnt;j++){
      if(!matchFn(t.ix[j],t.iy[j])) continue;
      const k=j*16;
      const sx=arr[k]!==0?arr[k]:cellXY, sy=arr[k+5]!==0?arr[k+5]:cellXY;
      const sz=arr[k+10]!==0?arr[k+10]:t.sz[j];
      const row=new Float32Array(16);
      row[0]=sx;row[1]=0;row[2]=0;row[3]=0;
      row[4]=0;row[5]=sy;row[6]=0;row[7]=0;
      row[8]=0;row[9]=0;row[10]=sz;row[11]=0;
      row[12]=arr[k+12];row[13]=arr[k+13];row[14]=arr[k+14];row[15]=1;
      md.push({row,color:[ca[j*3],ca[j*3+1],ca[j*3+2]]});
    }
  }
  // Resident tiles first
  for(let ti=0;ti<NT;ti++){
    if(myGen!==searchGen) return;   // superseded by a newer search or Clear Search
    const t=tiles[ti];
    if(t.state!=='in'||!t.mesh||!tileMaybeMatches(t,gridBounds)) continue;
    scanResident(t);
  }
  if(myGen!==searchGen) return;
  st.textContent='Searching ... ('+md.length+' found so far)';
  // Everything the resident pass didn't cover, one tile at a time
  for(let ti=0;ti<NT;ti++){
    if(myGen!==searchGen) return;   // superseded - stop touching tiles/network
    const t=tiles[ti];
    if(scanned.has(t.i)||!tileMaybeMatches(t,gridBounds)) continue;   // never fetch a tile that can't match
    // A concurrent updateResidency() may already be fetching this tile.
    // There is no handle to that fetch's promise, so wait for its state
    // machine to settle ('in' or 'out') instead of skipping it - skipping
    // silently drops the tile's matches and under-reports "Found N".
    while(t.state==='loading'){
      await new Promise(r=>setTimeout(r,60));
      if(myGen!==searchGen) return;
    }
    if(t.state==='in'){ if(t.mesh) scanResident(t); continue; }
    if(t.state!=='out') continue;
    t.state='loading';
    t.searchPinned=true;   // keep updateResidency() from evicting/aborting this fetch
    try{
      const bytes=await fetchTile(t);
      if(myGen!==searchGen){ t.state='out'; return; }   // superseded mid-fetch - don't strand it
      if(t.state!=='loading') continue;    // pin was overridden somehow - skip this tile
      const dv=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
      let has=false; const tm=[];
      for(let j=0;j<t.cnt;j++){
        const o=j*REC, code=dv.getUint8(o+27);
        const x=dv.getFloat32(o,true), y=dv.getFloat32(o+4,true), z=dv.getFloat32(o+8,true);
        const sx=dv.getFloat32(o+12,true), sy=dv.getFloat32(o+16,true), sz=dv.getFloat32(o+20,true);
        const ix=Math.round((x+CX0-GX0)/cellXY-0.5), iy=Math.round((y+CY0-GY0)/cellXY-0.5);
        if(!matchFn(ix,iy)) continue;
        has=true;
        const row=new Float32Array(16);
        row[0]=sx;row[1]=0;row[2]=0;row[3]=0;
        row[4]=0;row[5]=sy;row[6]=0;row[7]=0;
        row[8]=0;row[9]=0;row[10]=sz;row[11]=0;
        row[12]=x;row[13]=y;row[14]=z;row[15]=1;
        tm.push({row,color:[dv.getUint8(o+24)/255,dv.getUint8(o+25)/255,dv.getUint8(o+26)/255]});
      }
      if(has){ spawn(t,bytes); for(const m of tm) md.push(m); }
      else { byteCache.delete(t.i); t.state='out'; }
    } catch(e){ t.state='out'; }
    finally { t.searchPinned=false; }
  }
  if(myGen!==searchGen) return;   // finish exactly once, only for the current run
  if(md.length){
    // Frame on the ACTUAL matched geometry, not the caller's pre-search guess -
    // a column search's real shape is tall and thin (one footprint, many z
    // intervals), which a fixed cellXY/global-z-center guess cannot capture.
    let bx0=Infinity,by0=Infinity,bz0=Infinity,bx1=-Infinity,by1=-Infinity,bz1=-Infinity;
    for(const d of md){
      const px=d.row[12], py=d.row[13], pz=d.row[14];
      if(px<bx0)bx0=px; if(px>bx1)bx1=px;
      if(py<by0)by0=py; if(py>by1)by1=py;
      if(pz<bz0)bz0=pz; if(pz>bz1)bz1=pz;
    }
    flyToScene((bx0+bx1)/2, (by0+by1)/2, (bz0+bz1)/2,
               Math.max(bx1-bx0, by1-by0, bz1-bz0, cellXY));
  } else if(flyTo){
    flyToScene(flyTo.cx, flyTo.cy, flyTo.cz, flyTo.extent);   // nothing found - show where we looked
  }
  if(!md.length){ st.textContent='No matching boxes found.'; searchActive=true; return; }
  const dimVal=parseInt(document.getElementById('s-dim').value)/100;
  boxMat.transparent=true; boxMat.opacity=dimVal; boxMat.needsUpdate=true;
  // Impostors are "the rest of the area" too - without this a close-up on a
  // single column (its real footprint is one cellXY) is surrounded by
  // TILE_M-sized impostor cubes that a dim-to-0 slider never touched, drowning
  // the highlight out entirely.
  impostors.material.opacity=dimVal; impostors.material.needsUpdate=true;
  const n=md.length, geom=new THREE.BoxGeometry(1,1,1);
  // NOT vertexColors:true - the shared BoxGeometry carries no per-vertex
  // "color" attribute, so WebGL would supply the default (0,0,0) for it and
  // zero out every instance color before instanceColor even gets multiplied
  // in. instanceColor alone (the same path spawn()'s tile meshes use) is
  // sufficient and is what actually reads d.color per box.
  const mat=new THREE.MeshLambertMaterial({depthWrite:true});
  const mesh=new THREE.InstancedMesh(geom,mat,n);
  mesh.instanceColor=new THREE.InstancedBufferAttribute(new Float32Array(n*3),3);
  const ma=mesh.instanceMatrix.array, mca=mesh.instanceColor.array;
  for(let j=0;j<n;j++){
    const d=md[j], o=j*16;
    for(let m=0;m<16;m++) ma[o+m]=d.row[m];
    mca[j*3]=d.color[0]; mca[j*3+1]=d.color[1]; mca[j*3+2]=d.color[2];
  }
  mesh.instanceMatrix.needsUpdate=true; mesh.instanceColor.needsUpdate=true;
  mesh.frustumCulled=false; mesh.computeBoundingSphere();
  highlightGroup=new THREE.Group(); highlightGroup.add(mesh); scene.add(highlightGroup);
  searchActive=true; st.textContent='Found '+n+' box(es).';
}
document.getElementById('s-col-go').addEventListener('click',()=>{
  const ix=parseInt(document.getElementById('s-col-ix').value);
  const iy=parseInt(document.getElementById('s-col-iy').value);
  if(isNaN(ix)||isNaN(iy)) return;
  const cx=GX0+(ix+0.5)*cellXY-CX0, cy=GY0+(iy+0.5)*cellXY-CY0;
  const [,,,,,zmax]=IDX.bbox, zmin=IDX.bbox[2];
  doStreamSearch((ti,tj)=>ti===ix&&tj===iy, {cx,cy,cz:(zmin+zmax)/2,extent:cellXY},
                 {ix0:ix, iy0:iy, ix1:ix, iy1:iy});   // one column -> one tile
});
document.getElementById('s-rng-go').addEventListener('click',()=>{
  const ix0=parseInt(document.getElementById('s-rng-ix0').value);
  const ix1=parseInt(document.getElementById('s-rng-ix1').value);
  const iy0=parseInt(document.getElementById('s-rng-iy0').value);
  const iy1=parseInt(document.getElementById('s-rng-iy1').value);
  if(isNaN(ix0)||isNaN(ix1)||isNaN(iy0)||isNaN(iy1)) return;
  const cx=GX0+((ix0+ix1+1)/2)*cellXY-CX0, cy=GY0+((iy0+iy1+1)/2)*cellXY-CY0;
  const [,,,,,zmax]=IDX.bbox, zmin=IDX.bbox[2];
  const extent=Math.max((ix1-ix0+1)*cellXY, (iy1-iy0+1)*cellXY);
  // min/max so a reversed range still yields a valid (conservative) prune box.
  doStreamSearch((ti,tj)=>ti>=ix0&&ti<=ix1&&tj>=iy0&&tj<=iy1, {cx,cy,cz:(zmin+zmax)/2,extent},
                 {ix0:Math.min(ix0,ix1), iy0:Math.min(iy0,iy1),
                  ix1:Math.max(ix0,ix1), iy1:Math.max(iy0,iy1)});
});
document.getElementById('s-tile-go').addEventListener('click',()=>{
  const bbox=parseTileStem(document.getElementById('s-tile-id').value);
  if(!bbox){document.getElementById('s-status').textContent='Invalid tile name.';return;}
  const [,,,,,zmax]=IDX.bbox, zmin=IDX.bbox[2];
  // bbox is world metric; convert to a grid-index prune box (floor/ceil widen
  // it so a partially-covered edge column is never pruned away).
  const gb={ix0:Math.floor((bbox.x0-GX0)/cellXY-0.5), iy0:Math.floor((bbox.y0-GY0)/cellXY-0.5),
             ix1:Math.ceil((bbox.x1-GX0)/cellXY-0.5), iy1:Math.ceil((bbox.y1-GY0)/cellXY-0.5)};
  doStreamSearch((ti,tj)=>{
    const cx=GX0+(ti+0.5)*cellXY, cy=GY0+(tj+0.5)*cellXY;
    return cx>=bbox.x0&&cx<=bbox.x1&&cy>=bbox.y0&&cy<=bbox.y1;
  }, {cx:(bbox.x0+bbox.x1)/2-CX0, cy:(bbox.y0+bbox.y1)/2-CY0, cz:(zmin+zmax)/2, extent:bbox.x1-bbox.x0}, gb);
});
document.getElementById('s-dim').addEventListener('input',()=>{
  const v=parseInt(document.getElementById('s-dim').value);
  document.getElementById('s-dim-val').textContent=v+'%';
  if(!searchActive) return;
  boxMat.opacity=v/100; boxMat.needsUpdate=true;
  impostors.material.opacity=v/100; impostors.material.needsUpdate=true;
});
document.getElementById('s-clear').addEventListener('click',clearSearch);
document.getElementById('search-toggle').addEventListener('click',()=>{
  const b=document.getElementById('search-body'), ic=document.getElementById('search-icon');
  const e=b.style.display!=='none';
  b.style.display=e?'none':'block'; ic.textContent=e?'\u25B6':'\u25BC';
});

// ---------------- loop --------------------------------------------------------
const drawnEl=document.getElementById('i-drawn'), resEl=document.getElementById('i-res');
const fetEl=document.getElementById('i-fetch'), fpsEl=document.getElementById('i-fps');
const camEl=document.getElementById('i-cam'), spdEl=document.getElementById('i-speed');
spdEl.textContent = fly.speed.toFixed(0)+' m/s';

let last=performance.now(), frames=0, acc=0, sinceUpdate=0;
function tick(now){
  const dt = Math.min(.1, (now-last)/1000); last=now;

  if (cameraAnim){
    const t=(now-cameraAnim.t0)/500;
    if(t>=1){
      camera.position.copy(cameraAnim.p1);
      orbit.target.copy(cameraAnim.t1t);
      camera.lookAt(orbit.target);
      const d=new THREE.Vector3().subVectors(cameraAnim.t1t,cameraAnim.p1).normalize();
      fly.yaw=Math.atan2(d.y,d.x); fly.pitch=Math.asin(d.z);
      cameraAnim=null;
    }else{
      const s=t*t*(3-2*t);
      camera.position.lerpVectors(cameraAnim.p0,cameraAnim.p1,s);
      orbit.target.lerpVectors(cameraAnim.t0t,cameraAnim.t1t,s);
      camera.lookAt(orbit.target);
    }
  } else if (mode==='fly'){
    const f = cameraForward(new THREE.Vector3());
    const right = new THREE.Vector3(-Math.sin(fly.yaw), Math.cos(fly.yaw), 0);
    const up = new THREE.Vector3(0,0,1);
    let sp = fly.speed;
    if (fly.keys.has('ShiftLeft')||fly.keys.has('ShiftRight')) sp*=4;
    const dv = new THREE.Vector3();
    if (fly.keys.has('KeyW')) dv.add(f);
    if (fly.keys.has('KeyS')) dv.sub(f);
    if (fly.keys.has('KeyA')) dv.add(right);
    if (fly.keys.has('KeyD')) dv.sub(right);
    if (fly.keys.has('KeyE')) dv.add(up);
    if (fly.keys.has('KeyQ')) dv.sub(up);
    if (dv.lengthSq()) dv.normalize().multiplyScalar(sp*dt);
    camera.position.add(dv);
    camera.lookAt(camera.position.clone().add(f));
  } else orbit.update();

  // Re-plan residency when the view has actually changed, at most ~6x/s.
  sinceUpdate += dt;
  const moved = camera.position.distanceTo(lastPos);
  const turned = Math.abs(fly.yaw-lastYaw)+Math.abs(fly.pitch-lastPitch);
  if (sinceUpdate > 0.16 && (moved > TILE_M*0.25 || turned > 0.12 || resident===0)){
    sinceUpdate = 0;
    lastPos.copy(camera.position); lastYaw=fly.yaw; lastPitch=fly.pitch;
    updateResidency();
  }

  frames++; acc += dt;
  if (acc > .5){
    fpsEl.textContent = (frames/acc).toFixed(0); frames=0; acc=0;
    const p = camera.position;
    camEl.textContent = `${p.x.toFixed(0)} / ${p.y.toFixed(0)} / ${p.z.toFixed(0)} m`;
    drawnEl.textContent = drawn.toLocaleString()+' / '+MAX_INSTANCES.toLocaleString()
                        + ' (of '+IDX.n_records.toLocaleString()+' full detail)';
    resEl.textContent = resident+' / '+NT+' tiles';
    fetEl.textContent = inFlight ? (inFlight+' in flight, '+fetchedMB.toFixed(1)+' MB')
                                 : (fetchedMB.toFixed(1)+' MB fetched');
  }
  renderer.render(scene, camera);
  requestAnimationFrame(tick);
}
setMode('fly');
updateResidency();
requestAnimationFrame(tick);
</script>
</body>
</html>
"""


def export_tiled_from_store(
    store: ColumnStore,
    out_path: str | Path,
    *,
    title: str = "Voxel area",
    max_instances: int = 4_000_000,
    tile_m: float = DEFAULT_TILE_M,
    keep_classes: set[int] | None = None,
    region: tuple[float, float, float, float] | None = None,
    inline_threshold: int = 64 * 1024 * 1024,
) -> Path:
    """Full-detail tiled export + view-dependent viewer.

    Writes either one file or three, depending on the payload size. At or
    under ``inline_threshold`` the payload is base64-embedded in
    ``<out>.html``, the ``<out>.bin`` sidecar is deleted and
    ``<out>.idx.json`` is NEVER WRITTEN - the index rides inside the page, so
    a caller looking for it beside a small export will not find one. Above
    the threshold all three exist and the page Range-fetches tiles, which
    needs an HTTP origin that answers 206 (``serve_voxel_html`` does); only
    the inlined form works from ``file://``.

    An ``inline_threshold`` of 0 means "never inline": the sidecars are
    always written, even when a region/class filter leaves the payload
    empty (a 0-byte ``.bin`` plus a valid empty-tiles ``.idx.json``), so
    callers that depend on the sidecars existing (``tileset_cli``) never
    have to guess which form they got.

    ``max_instances`` is a GPU WORKING-SET budget. It never removes a voxel
    from the payload and never strides the draw; it decides how many TILES
    are resident.

    @param store             Source ColumnStore, passed through to
                             ``write_tiled_payload``; it is not modified.
    @param out_path          Destination ``.html``; the ``.bin`` and
                             ``.idx.json`` sidecars take the same stem.
    @param title              Page title, substituted into the viewer's title bar
                             and its tile label (default "Voxel area").
    @param max_instances     GPU working-set budget, in instances, written into
                             the page; decides how many tiles stay resident, never
                             which voxels are exported (default 4,000,000).
    @param tile_m            Nominal tile edge in metres (default ``DEFAULT_TILE_M``).
    @param keep_classes      Classes to keep, as a set of integer class codes;
                             ``None`` keeps every class.
    @param region            ``(x0, y0, x1, y1)`` metres, area-local; columns whose
                             centre falls outside are dropped. ``None`` keeps all.
    @param inline_threshold  Payload size in bytes at or under which the ``.bin`` is
                             base64-inlined in the HTML and deleted, and the
                             ``.idx.json`` is skipped (default 64 MiB). 0 means
                             never inline: both sidecars are always written.
    @return Path to the written HTML page.
    """
    out_path = Path(out_path)
    bin_path = out_path.with_suffix(".bin")
    idx_path = out_path.with_suffix(".idx.json")

    index = write_tiled_payload(store, bin_path, tile_m=tile_m,
                                keep_classes=keep_classes, region=region)
    nbytes = bin_path.stat().st_size

    inline_b64 = ""
    # Version tag so the sidecar can be cached hard AND still be busted by a
    # re-export. The payload name is stable (`<out>.bin`), so without this the
    # browser keeps serving the previous run's bytes from an `immutable` entry
    # (see serve_voxel_html, which only promises immutability for a `?v=` url).
    payload_url = f"{bin_path.name}?v={nbytes}-{int(bin_path.stat().st_mtime)}"
    # A threshold of 0 is a contract, not a size: "never inline". Without the
    # strict comparison an empty payload (region/class filter matching
    # nothing) would take the inline branch, delete the .bin and skip the
    # .idx.json - and a sidecar-only caller then dies on FileNotFoundError
    # instead of seeing a clean empty export.
    if 0 < inline_threshold >= nbytes:
        inline_b64 = base64.b64encode(bin_path.read_bytes()).decode("ascii")
        bin_path.unlink(missing_ok=True)
        payload_url = ""
    else:
        idx_path.write_text(json.dumps(index), encoding="utf-8")

    html = (_TILED_HTML
            .replace("__TITLE__", title)
            .replace("__TILE_LABEL__", title)
            .replace("__IDX_JSON__", json.dumps(index))
            .replace("__PAYLOAD_URL__", payload_url)
            .replace("__INLINE_B64__", inline_b64)
            .replace("__MAX_INSTANCES__", str(int(max_instances))))
    out_path.write_text(html, encoding="utf-8")

    logger.info(
        "wrote %s (%s records, %d tiles, payload %.1f MB, %s)",
        out_path, f"{index['n_records']:,}", len(index["tiles"]["off"]),
        nbytes / 1e6, "inlined" if inline_b64 else f"sidecar {bin_path.name}")
    return out_path
