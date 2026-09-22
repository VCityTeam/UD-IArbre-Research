"""
@ingroup t3_orchestr


Out-of-core shard merge: assemble the merged raw store ON DISK, band by band.

    python -m voxelizer.merge_streaming --shards-dir DIR --out-store-dir DIR
        [--group-intervals] [--group-gap METRES] [--band-intervals N]
        [--overwrite] [--plan-only]

``ColumnStore.merge_many`` needs every shard and the whole merged store
resident at once, which a metropolis store at a fine grid does not fit. The
result does not need that much RAM: it is written to disk immediately and
every consumer memory-maps it. Only the assembly step insists on holding it
whole, so this module assembles the store on disk instead.

Output contract, derived from ``merge_many`` rather than assumed:

  * columns come out globally sorted by the packed uint64 key, never in shard
    order, so appending shards in turn is wrong;
  * a column that lives in exactly one shard keeps that shard's interval block
    verbatim;
  * a key present in several shards collapses into one column, its intervals
    stitched by ``_coalesce_column_intervals`` (class-wise sweep, then a
    canonical ``(z_start, class)`` order), independent of source order.

Because every shard's ``_keys`` is already ascending, the key space can be
partitioned into bands of whole ``ix`` slabs and each band takes one
contiguous slice of each shard. A stable sort restricted to a key range is the
stable sort of exactly the elements in that range, so running ``merge_many``
on one band's slices and concatenating the band results in ascending band
order reproduces the global merge byte for byte; equal keys never straddle a
band boundary, so duplicate coalescing is reproduced too. The merge semantics
are not reimplemented: ``merge_many`` is called once per band, on inputs small
enough to fit, and this module only decides band membership and where the
answer lands on disk.

The final arrays are created once, at full size, with
``np.lib.format.open_memmap`` inside ``<out>.partial/`` and filled band by
band; ``meta.json`` is written last and the directory is renamed into place,
so an interrupted merge leaves a ``.partial`` that no consumer can mistake for
a complete store (``ColumnStore.load_dir`` refuses a directory without
``meta.json``).

Peak RAM is one band plus the shard currently being decompressed. With ``r``
intervals per column, a band of ``B`` intervals costs roughly
``B * (26 + 20/r)`` bytes. For a typical mix (r ~ 2.1) the default
``B = 40 M`` is about 1.4 GB, whatever the size of the store being written.

``--band-intervals`` is the one tuning knob, trading RAM against re-reads: a
shard is decompressed once per band its ``ix`` range touches, so a band
narrower than a tile (500 m / cell_xy) reads the shard corpus more than once.

Sizes come from ``shards/manifest.json`` (per-shard ``n_columns`` /
``n_intervals``), an upper bound: duplicate keys and grouping both shrink the
result. The arrays are allocated at that bound and truncated in place once
the true totals are known, so the store is never copied to be right-sized.
"""

from __future__ import annotations

import argparse
import gc
import json
import logging
import os
import shutil
import time
from pathlib import Path

import numpy as np

from .data_structures import ColumnStore, _pack_keys

logger = logging.getLogger(__name__)

# Intervals per band. ~1.4 GB peak at the metropolis interval-per-column mix;
# see the module docstring for the arithmetic and the re-read trade-off.
DEFAULT_BAND_INTERVALS = 40_000_000

# Bytes of peak RAM per band interval / per band column, measured against
# merge_many's allocation sequence (combined buffers + gathered copy). Used
# only to report the expected peak in the band plan.
_PEAK_B_PER_IV = 26
_PEAK_B_PER_COL = 20


def _slab_key(ix: int) -> np.uint64:
    """Smallest packed key of the ``ix`` slab, that is the key of ``(ix, -2^31)``.

    Band bounds are whole ``ix`` slabs, so this is the only key arithmetic
    needed. It goes through _pack_keys() rather than open-coding the
    offset, so the packing stays defined in exactly one place.
    """
    return _pack_keys(np.int64(ix), np.int64(-(1 << 31)))[()]


def _slice_to_band(store: ColumnStore, key_lo, key_hi) -> ColumnStore:
    """Copy the columns of ``store`` whose key is in ``[key_lo, key_hi)``.

    ``None`` means unbounded on that side. The arrays are copied, not sliced,
    so the caller can drop the source shard immediately - a view would pin the
    whole decompressed shard for the lifetime of the band.
    """
    keys = store._keys
    a = 0 if key_lo is None else int(np.searchsorted(keys, key_lo))
    b = keys.shape[0] if key_hi is None else int(np.searchsorted(keys, key_hi))
    out = ColumnStore(store.x_min, store.y_min, store.z_min,
                      store.cell_xy, store.cell_z)
    if b <= a:
        return out
    i0, i1 = int(store._off[a]), int(store._off[b])
    out._keys = store._keys[a:b].copy()
    out._off = (store._off[a:b + 1] - store._off[a]).copy()
    out._zs = store._zs[i0:i1].copy()
    out._ze = store._ze[i0:i1].copy()
    out._cl = store._cl[i0:i1].copy()
    out._ct = store._ct[i0:i1].copy()
    return out


def _read_manifest(shards_dir: Path):
    """Manifest shard records that exist on disk, in manifest order.

    The manifest list order is the merge input order, and a record whose file
    is gone is warned about and skipped rather than being fatal.
    """
    mf_path = shards_dir / "manifest.json"
    if not mf_path.is_file():
        logger.warning("merge: no manifest at %s.", mf_path)
        return None
    mf = json.loads(mf_path.read_text(encoding="utf-8"))
    records = mf.get("shards", [])
    if not records:
        logger.warning("merge: manifest lists no shards.")
        return None
    kept = []
    for rec in records:
        p = shards_dir / rec["file"]
        if not p.is_file():
            logger.warning("merge: shard missing on disk: %s", p.name)
            continue
        # iy_min/iy_max are not read by the merge planner itself (it slabs by
        # ix only) but shard_diagnostics' owner_of/assert_disjoint - which
        # share this validator - index them unconditionally; requiring the
        # full extent here keeps "manifest too old" the one curated error
        # instead of a bare KeyError downstream.
        for field in ("n_columns", "n_intervals",
                      "ix_min", "ix_max", "iy_min", "iy_max"):
            if field not in rec:
                raise ValueError(
                    f"the shard tooling needs per-shard {field} in "
                    f"{mf_path} (schema voxelizer.shards/2 or newer); this "
                    "manifest is too old to plan an out-of-core merge or "
                    "shard diagnostics from.")
        kept.append(rec)
    if not kept:
        logger.warning("merge: no shard file from the manifest is on disk.")
        return None
    return kept


def plan_bands(records, band_intervals: int = DEFAULT_BAND_INTERVALS):
    """Cut the global ``ix`` range into bands of at most ~``band_intervals``.

    The per-ix interval density is estimated by spreading each shard's
    ``n_intervals`` evenly over its ``ix`` span - the manifest carries no finer
    distribution, and none is needed: the estimate only sizes a RAM budget, and
    a band that comes out twice as big as planned is still two orders of
    magnitude below the in-RAM merge it replaces. One ``ix`` slab is the
    finest band (at the 0.5 m metropolis that is ~90 k intervals, ~400x under
    the default budget, so the granularity never binds).

    Returns a list of ``(ix_lo, ix_hi)`` half-open slab ranges covering
    ``[min ix_min, max ix_max + 1)``.

    @param records Manifest records (one dict per shard, carrying at least
        ``ix_min``, ``ix_max`` and ``n_intervals``).
    @param band_intervals Target interval count per band (default
        ``DEFAULT_BAND_INTERVALS``); clamped to at least 1.
    @return A list of ``(ix_lo, ix_hi)`` half-open ``ix`` slab ranges
        covering the global ``[min ix_min, max ix_max + 1)``; a single
        spanning band if the budget is reached only at the end.
    """
    ix_lo = min(int(r["ix_min"]) for r in records)
    ix_hi = max(int(r["ix_max"]) for r in records) + 1
    span = ix_hi - ix_lo
    # Density by difference-array accumulation: O(n_shards), not O(span x n).
    diff = np.zeros(span + 1, dtype=np.float64)
    for r in records:
        a, b = int(r["ix_min"]) - ix_lo, int(r["ix_max"]) + 1 - ix_lo
        rate = float(r["n_intervals"]) / max(1, b - a)
        diff[a] += rate
        diff[b] -= rate
    density = np.cumsum(diff[:-1])

    budget = max(1, int(band_intervals))
    bands = []
    start, acc = 0, 0.0
    for i in range(span):
        acc += float(density[i])
        if acc >= budget:
            bands.append((ix_lo + start, ix_lo + i + 1))
            start, acc = i + 1, 0.0
    if start < span:
        bands.append((ix_lo + start, ix_hi))
    return bands or [(ix_lo, ix_hi)]


def _base_grid(shards_dir: Path, records):
    """Grid metadata of the first NON-EMPTY shard, read without inflating it.

    ``merge_many`` takes its output grid from the first non-empty input store
    and validates the rest against it; this reproduces that choice for a few
    hundred bytes of I/O, by reading only the ``meta`` member of the .npz.
    """
    pick = next((r for r in records if int(r["n_columns"]) > 0), records[0])
    with np.load(str(shards_dir / pick["file"])) as d:
        meta = d["meta"]
    return tuple(float(v) for v in meta)


def _check_grid(store: ColumnStore, grid, name: str, atol: float = 1e-6) -> None:
    """Same guard as ``merge_many``, but naming the shard that disagrees."""
    x0, y0, z0, cxy, cz = grid
    if not (abs(store.cell_xy - cxy) < atol and abs(store.cell_z - cz) < atol):
        raise ValueError(
            f"cell-size mismatch in shard {name}: ({store.cell_xy},"
            f"{store.cell_z}) against the run's ({cxy},{cz})")
    if not (abs(store.x_min - x0) < atol and abs(store.y_min - y0) < atol
            and abs(store.z_min - z0) < atol):
        raise ValueError(
            f"origin mismatch in shard {name}: ({store.x_min},{store.y_min},"
            f"{store.z_min}) against the run's ({x0},{y0},{z0}). Every tile "
            "must be voxelized with the same explicit `origin`.")


def _open_out_arrays(target: Path, n_col: int, n_iv: int) -> dict:
    """Create the six store arrays at full size, memory-mapped for writing."""
    target.mkdir(parents=True, exist_ok=True)
    spec = (("keys", np.uint64, n_col), ("off", np.int64, n_col + 1),
            ("zs", np.int32, n_iv), ("ze", np.int32, n_iv),
            ("cl", np.uint8, n_iv), ("ct", np.int32, n_iv))
    out = {}
    for name, dtype, n in spec:
        out[name] = np.lib.format.open_memmap(
            str(target / f"{name}.npy"), mode="w+", dtype=dtype, shape=(n,))
    return out


def _flush_arrays(arrays: dict) -> None:
    """Push the band just written out of the dirty page cache."""
    for arr in arrays.values():
        arr.flush()


def _close_arrays(arrays: dict) -> None:
    """Flush and drop every output mapping.

    Done in its own frame on purpose: a loop variable left bound in the caller
    keeps one memmap alive, and Windows refuses to rename (or truncate) a
    directory holding a mapped file - the store would never leave ``.partial``.
    """
    _flush_arrays(arrays)
    arrays.clear()
    gc.collect()


def _shrink_npy(path: Path, n_rows: int) -> None:
    """Cut an .npy file down to ``n_rows`` without rewriting its data.

    The header is rewritten in place at its original length (a shorter shape
    can always be padded back out to it, and keeping the length keeps the
    64-byte data alignment), then the file is truncated. This is what lets the
    arrays be allocated at the manifest's upper bound and still end up exactly
    the right size when duplicate keys or grouping shrink the result - the
    alternative being a full copy of a store that can weigh 111 GB.
    """
    with open(path, "r+b") as f:
        version = np.lib.format.read_magic(f)
        prefix = f.tell() + (2 if version == (1, 0) else 4)
        if version == (1, 0):
            shape, fortran, dtype = np.lib.format.read_array_header_1_0(f)
        elif version == (2, 0):
            shape, fortran, dtype = np.lib.format.read_array_header_2_0(f)
        else:
            raise ValueError(f"{path}: unsupported .npy version {version}")
        data_off = f.tell()
        if n_rows > shape[0]:
            raise ValueError(f"{path}: cannot grow {shape[0]} -> {n_rows}")
        header = ("{'descr': '%s', 'fortran_order': %s, 'shape': (%d,), }"
                  % (np.lib.format.dtype_to_descr(dtype), bool(fortran),
                     int(n_rows)))
        room = data_off - prefix - 1
        if len(header) > room:
            raise ValueError(f"{path}: rewritten header does not fit")
        f.seek(prefix)
        f.write((header + " " * (room - len(header)) + "\n").encode("latin1"))
    os.truncate(path, data_off + n_rows * dtype.itemsize)


def merge_shards_streaming(shards_dir, out_store_dir, *,
                           group_intervals: bool = False,
                           group_gap: float | None = None,
                           cell_z: float | None = None,
                           band_intervals: int = DEFAULT_BAND_INTERVALS,
                           overwrite: bool = False) -> dict | None:
    """Merge every shard of ``shards_dir`` into a raw store directory on disk.

    The result is exactly what ``ColumnStore.merge_many`` over the same shards
    (in manifest order) followed by ``save_dir`` would have written, array for
    array - see the module docstring for why the band decomposition is exact.
    Returns a summary dict, or ``None`` when there is nothing to merge (no
    manifest, no shards listed, none on disk) - the same "nothing happened"
    signal ``sharding._merge_all_shards`` gives.

    ``group_intervals`` groups each shard's slice of the current key band
    before that band's merge, never the assembled store afterwards; the unit
    is the band slice, and since a band boundary is a column boundary and
    grouping is per-column, that is the same answer as grouping whole shards
    would give. ``group_gap`` is in metres and is converted with ``cell_z``
    when given, otherwise with the shards' own cell height. ``overwrite`` replaces an existing target, and only ever after the
    new store is complete on disk.

    @param shards_dir Directory holding the per-tile ``.npz`` shards and
        their ``manifest.json``.
    @param out_store_dir Target raw store directory; written whole into a
        sibling ``.partial`` directory and renamed into place last.
    @param group_intervals When True, run each band slice's intervals through
        ``ColumnStore.grouped`` before that band's merge (default False).
    @param group_gap Gap tolerance in METRES for that grouping (``None`` =
        no gap limit). Only meaningful with ``group_intervals=True``; it is
        converted to whole voxels with ``cell_z`` when given, otherwise with
        the shards' own cell height.
    @param cell_z Vertical cell size in metres used to quantise ``group_gap``
        (``None`` = take the shards' own cell height).
    @param band_intervals Target interval count per band (default
        ``DEFAULT_BAND_INTERVALS``); smaller bands cost less RAM and re-read
        the compressed shards more often.
    @param overwrite When True, replace an existing ``out_store_dir``, and
        only ever after the new store is complete on disk (default False).
    @return Summary dict with ``n_columns``, ``n_intervals``, ``n_shards``,
        ``n_bands``, ``seconds`` and ``store_dir``; ``None`` when there is
        nothing to merge (no manifest, no shards listed, none on disk).
    @throws ValueError when ``group_gap`` is given without
        ``group_intervals``.
    @throws FileExistsError when ``out_store_dir`` already exists and
        ``overwrite`` is False.
    @throws ValueError when a band would overflow the manifest's column or
        interval totals, or when shards lose columns between the band plan
        and the merge.
    """
    # --group-gap only bounds the grouping pass, so without --group-intervals
    # it silently does nothing. Refuse before touching anything on disk,
    # rather than run a merge the caller will read as gap-limited.
    if group_gap is not None and not group_intervals:
        raise ValueError(
            "group_gap is only meaningful with group_intervals=True: it caps "
            "the vertical gap the grouping pass may merge across, and there "
            "is no grouping pass without it.")

    shards_dir = Path(shards_dir)
    out_store_dir = Path(out_store_dir)
    records = _read_manifest(shards_dir)
    if records is None:
        return None

    if out_store_dir.exists() and not overwrite:
        raise FileExistsError(
            f"{out_store_dir} already exists; pass overwrite=True "
            "(--overwrite) to replace it once the new store is complete.")
    partial = out_store_dir.with_name(out_store_dir.name + ".partial")
    if partial.exists():
        logger.warning("dropping leftover partial merge %s", partial)
        shutil.rmtree(partial)

    grid = _base_grid(shards_dir, records)
    gap_cells = (None if group_gap is None
                 else max(0, int(round(group_gap /
                                       (grid[4] if cell_z is None else cell_z)))))
    bands = plan_bands(records, band_intervals)
    n_col_max = sum(int(r["n_columns"]) for r in records)
    n_iv_max = sum(int(r["n_intervals"]) for r in records)
    logger.info("streaming merge: %d shard(s), <=%s columns / %s intervals, "
                "%d band(s) of <=%s intervals%s",
                len(records), f"{n_col_max:,}", f"{n_iv_max:,}", len(bands),
                f"{band_intervals:,}",
                "" if not group_intervals else
                (f", grouped (gap="
                 f"{'unlimited' if gap_cells is None else f'{gap_cells} cells'})"))

    t0 = time.time()
    arrays = _open_out_arrays(partial, n_col_max, n_iv_max)
    col = iv = 0
    # Every column of every shard must land in exactly one band. The counters
    # turn a manifest whose ix ranges understate a shard's real content into a
    # loud failure at the end instead of a silently truncated store.
    taken = [0] * len(records)
    total = [None] * len(records)
    try:
        arrays["off"][0] = 0
        for b, (ix_lo, ix_hi) in enumerate(bands):
            key_lo = None if b == 0 else _slab_key(ix_lo)
            key_hi = None if b == len(bands) - 1 else _slab_key(ix_hi)
            slices = []
            for k, rec in enumerate(records):
                if int(rec["ix_max"]) < ix_lo or int(rec["ix_min"]) >= ix_hi:
                    continue
                shard = ColumnStore.load(shards_dir / rec["file"])
                total[k] = int(shard._keys.shape[0])
                if shard._keys.shape[0] == 0:
                    continue                      # merge_many drops these too
                _check_grid(shard, grid, rec["file"])
                part = _slice_to_band(shard, key_lo, key_hi)
                del shard
                if part._keys.shape[0] == 0:
                    continue
                taken[k] += int(part._keys.shape[0])
                if group_intervals and part._zs.shape[0]:
                    part = part.grouped(max_gap_cells=gap_cells)
                slices.append(part)
            if not slices:
                continue
            merged = ColumnStore.merge_many(slices, consume=True)
            slices.clear()
            nc = int(merged._keys.shape[0])
            ni = int(merged._zs.shape[0])
            if col + nc > n_col_max or iv + ni > n_iv_max:
                raise ValueError(
                    f"band {b} overflows the manifest's totals "
                    f"({col + nc} columns / {iv + ni} intervals against "
                    f"{n_col_max} / {n_iv_max}) - the manifest does not "
                    "describe these shards.")
            arrays["keys"][col:col + nc] = merged._keys
            arrays["off"][col + 1:col + 1 + nc] = merged._off[1:] + iv
            arrays["zs"][iv:iv + ni] = merged._zs
            arrays["ze"][iv:iv + ni] = merged._ze
            arrays["cl"][iv:iv + ni] = merged._cl
            arrays["ct"][iv:iv + ni] = merged._ct
            col += nc
            iv += ni
            del merged
            _flush_arrays(arrays)
            logger.info("  band %d/%d ix [%d, %d): %s columns, %s intervals "
                        "(total %s / %s, %.0fs)", b + 1, len(bands), ix_lo,
                        ix_hi, f"{nc:,}", f"{ni:,}", f"{col:,}", f"{iv:,}",
                        time.time() - t0)
    finally:
        _close_arrays(arrays)

    short = [k for k in range(len(records))
             if total[k] is None or taken[k] != total[k]]
    if short:
        k = short[0]
        raise ValueError(
            f"{len(short)} shard(s) lost columns between the band plan and "
            f"the merge (first: {records[k]['file']}, {taken[k]} of "
            f"{total[k]}) - their manifest ix range does not cover their "
            "contents.")

    if col < n_col_max:
        _shrink_npy(partial / "keys.npy", col)
        _shrink_npy(partial / "off.npy", col + 1)
    if iv < n_iv_max:
        for name in ("zs", "ze", "cl", "ct"):
            _shrink_npy(partial / f"{name}.npy", iv)
    # meta.json last: it is the commit marker load_dir insists on.
    meta = {"schema": ColumnStore._RAW_SCHEMA,
            "x_min": grid[0], "y_min": grid[1], "z_min": grid[2],
            "cell_xy": grid[3], "cell_z": grid[4],
            "n_columns": col, "n_intervals": iv, "has_gi": False}
    (partial / "meta.json").write_text(json.dumps(meta, indent=2),
                                       encoding="utf-8")
    # os.rename, not os.replace: Windows refuses MOVEFILE_REPLACE_EXISTING on
    # directories, so the old store (if any) is removed first - which is why
    # this is the LAST step, with the complete new store already on disk.
    if out_store_dir.exists():
        shutil.rmtree(out_store_dir)
    os.rename(partial, out_store_dir)

    elapsed = time.time() - t0
    logger.info("streaming merge done: %s columns, %s intervals -> %s "
                "(%d band(s), %.0fs)", f"{col:,}", f"{iv:,}", out_store_dir,
                len(bands), elapsed)
    return {"n_columns": col, "n_intervals": iv, "n_shards": len(records),
            "n_bands": len(bands), "seconds": elapsed,
            "store_dir": str(out_store_dir)}


def _format_plan(records, bands, band_intervals: int) -> str:
    """One-screen summary of a band plan: sizes, re-reads, expected peak RAM."""
    n_col = sum(int(r["n_columns"]) for r in records)
    n_iv = sum(int(r["n_intervals"]) for r in records)
    r_iv = n_iv / max(1, n_col)
    loads = sum(sum(1 for lo, hi in bands
                    if int(rec["ix_max"]) >= lo and int(rec["ix_min"]) < hi)
                for rec in records)
    peak = band_intervals * (_PEAK_B_PER_IV + _PEAK_B_PER_COL / max(1e-9, r_iv))
    store = n_col * 16 + n_iv * 13 + 8
    return (f"  shards          {len(records):,}\n"
            f"  columns         {n_col:,}\n"
            f"  intervals       {n_iv:,} ({r_iv:.2f} per column)\n"
            f"  store on disk   {store / 1e9:,.1f} GB\n"
            f"  bands           {len(bands):,} "
            f"(<= {band_intervals:,} intervals each)\n"
            f"  shard loads     {loads:,} "
            f"({loads / max(1, len(records)):.2f} passes over the corpus)\n"
            f"  expected peak   {peak / 1e9:,.2f} GB of RAM")


def _build_parser() -> argparse.ArgumentParser:
    """The ``python -m voxelizer.merge_streaming`` argument parser: required ``--shards-dir`` and ``--out-store-dir``, the grouping, band-size and overwrite knobs of merge_shards_streaming(), and ``--plan-only``."""
    p = argparse.ArgumentParser(
        prog="python -m voxelizer.merge_streaming",
        description="Merge a shards/ directory into a raw store directory "
                    "out of core (bounded RAM, result identical to "
                    "ColumnStore.merge_many + save_dir).")
    p.add_argument("--shards-dir", required=True,
                   help="Directory holding the .npz shards and manifest.json.")
    p.add_argument("--out-store-dir", required=True,
                   help="Raw store directory to write (ColumnStore.save_dir "
                        "layout: one .npy per array + meta.json).")
    p.add_argument("--group-intervals", action=argparse.BooleanOptionalAction,
                   default=False,
                   help="Merge consecutive same-class intervals per column, "
                        "per shard, before merging - the grouping the sharded "
                        "pipeline applies by default. Off here, so the "
                        "default output is the raw merged store.")
    p.add_argument("--group-gap", type=float, default=None, metavar="METRES",
                   help="With --group-intervals, only merge across vertical "
                        "gaps up to this many metres (default: any gap).")
    p.add_argument("--band-intervals", type=int,
                   default=DEFAULT_BAND_INTERVALS, metavar="N",
                   help=f"Intervals per band, the RAM knob "
                        f"(default {DEFAULT_BAND_INTERVALS:,}; bigger bands "
                        f"use more RAM and re-read the shards less).")
    p.add_argument("--overwrite", action="store_true",
                   help="Replace an existing --out-store-dir (only once the "
                        "new store is complete on disk).")
    p.add_argument("--plan-only", action="store_true",
                   help="Print the band plan and expected peak RAM from the "
                        "manifest alone, then exit without merging.")
    return p


def main(argv=None) -> int:
    """Command-line entry point. With ``--plan-only`` it prints the band plan from the manifest and exits; otherwise it runs merge_shards_streaming() and prints the one-line summary. Returns 0 on success and 1 when there is no manifest or nothing was merged."""
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    shards_dir = Path(args.shards_dir)
    if args.plan_only:
        records = _read_manifest(shards_dir)
        if records is None:
            return 1
        bands = plan_bands(records, args.band_intervals)
        print(f"band plan for {shards_dir}:")
        print(_format_plan(records, bands, args.band_intervals))
        return 0
    summary = merge_shards_streaming(
        shards_dir, args.out_store_dir,
        group_intervals=args.group_intervals, group_gap=args.group_gap,
        band_intervals=args.band_intervals, overwrite=args.overwrite)
    if summary is None:
        print("nothing merged (no manifest, or no shard on disk).")
        return 1
    print(f"merged {summary['n_shards']} shard(s) -> {summary['store_dir']}: "
          f"{summary['n_columns']:,} columns, "
          f"{summary['n_intervals']:,} intervals, "
          f"{summary['n_bands']} band(s), {summary['seconds']:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
