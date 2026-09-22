"""
@ingroup t1_donnees


The data structures that make up the column-compressed voxel grid:

    Interval     - one vertical run of voxels with the same semantic class
    Column       - all intervals of one (ix, iy) column, as parallel numpy arrays
    ColumnStore  - the whole tile/area: geometric metadata + the columns

Struct-of-arrays layout
-----------------------
A ``dict`` of ``Column`` objects costs a fixed Python overhead per occupied
column (dict slot, tuple key, boxed ints, dataclass, ndarray headers)
whatever the column holds. The store is six flat numpy arrays instead:

    _keys : uint64 (n_cols,)    packed (ix, iy), sorted ascending; the packing
                                is order-preserving so lexicographic (ix, iy)
                                order == numeric key order (binary-searchable).
    _off  : int64  (n_cols+1,)  per-column offsets into the interval arrays.
    _zs, _ze : int32 (n_iv,)    interval z_start / exclusive z_end.
    _cl   : uint8  (n_iv,)      ASPRS class code per interval.
    _ct   : int32  (n_iv,)      point count per interval.

Cost: 16 B per column plus 13 B per interval.

``store.columns`` is a read-only Mapping view over the flat arrays. It
supports ``len()``, truthiness, ``in``, ``[]``, ``.get()`` and
``.keys()/.values()/.items()``; ``__getitem__`` returns a ``Column`` whose
arrays are zero-copy slices of the flat arrays, so mutations write through.
Constructing ``ColumnStore(..., columns={...})`` with a plain dict still
works and is converted to the flat layout.

Interval order inside a column is canonical: ascending (z_start, class).
"""

from __future__ import annotations
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import numpy as np

from .classes_config import (
    ALL_CLASS_CODES, CLASS_NAMES,
    GROUND, BUILDING, VEGETATION_CLASSES,
)

# Offset that maps signed 32-bit ix/iy into unsigned space so the packed
# uint64 key preserves lexicographic (ix, iy) order.
_KOFF = np.int64(1 << 31)
_LO32 = np.uint64(0xFFFFFFFF)
_SH32 = np.uint64(32)


def _pack_keys(ix, iy) -> np.ndarray:
    """Pack int arrays (ix, iy) into order-preserving uint64 keys."""
    ixu = (np.asarray(ix, dtype=np.int64) + _KOFF).astype(np.uint64)
    iyu = (np.asarray(iy, dtype=np.int64) + _KOFF).astype(np.uint64)
    return (ixu << _SH32) | iyu


def _unpack_keys(keys: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Inverse of _pack_keys() -> (ix int32, iy int32)."""
    ix = ((keys >> _SH32).astype(np.int64) - _KOFF).astype(np.int32)
    iy = ((keys & _LO32).astype(np.int64) - _KOFF).astype(np.int32)
    return ix, iy


def _pack_one(key) -> np.uint64:
    """Scalar form of _pack_keys() for a single ``(ix, iy)`` tuple, computed in plain Python ints so a lookup does not pay for array construction."""
    ix, iy = key
    return np.uint64(((int(ix) + (1 << 31)) << 32) | (int(iy) + (1 << 31)))


@dataclass
class Interval:
    """
    One vertical run of voxels with the same semantic class.

    For example, a tree in a column might be represented as:
        Interval(z_start_idx=20, z_end_idx=55, class_id=5, point_count=72)
    meaning: voxels 20 to 54 (inclusive) in this column are 'high_vegetation',
    and 72 LiDAR points fell into that vertical run.

    We deliberately store voxel *indices* (integers), not metric coordinates.
    The metric position can always be recovered if needed: z = z_min + iz*dz.
    Indices keep the structure compact and the comparisons trivial.
    """
    z_start_idx: int       # first voxel index in the run (inclusive)
    z_end_idx: int         # one past the last voxel index (exclusive - Python style)
    class_id: int          # ASPRS class code
    point_count: int       # how many raw LiDAR points contributed to this run

    @property
    def height_voxels(self) -> int:
        """Number of voxels covered by this interval."""
        return self.z_end_idx - self.z_start_idx

    @property
    def class_name(self) -> str:
        """Human-readable class name, falling back to the raw code."""
        return CLASS_NAMES.get(self.class_id, f"class_{self.class_id}")


@dataclass
class Column:
    """
    All intervals of one (ix, iy) column, stored as parallel numpy arrays.

    When obtained from ``store.columns[...]`` the four arrays are zero-copy
    views into the store's flat interval arrays (so a Column costs nothing
    beyond the transient object). Standalone Columns (e.g. built by tests or
    by _merge_two_columns()) own their arrays; both behave identically.
    """
    z_start: np.ndarray   # int32, shape (n,) - first voxel index of each interval
    z_end:   np.ndarray   # int32, shape (n,) - exclusive end voxel index
    cls:     np.ndarray   # uint8, shape (n,) - ASPRS class code per interval
    count:   np.ndarray   # int32, shape (n,) - point count per interval

    def __len__(self) -> int:
        """Number of intervals in this column."""
        return len(self.z_start)

    def iter_intervals(self):
        """Yield Interval objects on demand. Use only when you need them."""
        for k in range(len(self.z_start)):
            yield Interval(
                z_start_idx=int(self.z_start[k]),
                z_end_idx=int(self.z_end[k]),
                class_id=int(self.cls[k]),
                point_count=int(self.count[k]),
            )


def _coalesce_column_intervals(zs: np.ndarray, ze: np.ndarray, cc: np.ndarray,
                               cn: np.ndarray) -> tuple:
    """
    Merge same-class runs that touch or overlap in z, from ANY number of
    contributors for one (ix, iy) column; different classes stay independent
    (a column may hold several classes at the same height). Exact when the
    contributing columns' occupied voxels are disjoint - the case the area
    pipeline actually hits, because a grid-aligned origin over clipped,
    non-overlapping tiles never routes two tiles' points into the same
    physical column. For genuinely overlapping same-class runs the summed
    count is exact but the per-voxel split inside the overlap is not
    recoverable (the interval store is lossy there).

    Shared by _merge_two_columns() (pairwise) and
    ColumnStore.merge_many() (N-way batch merge) - the coalescing rule
    does not care how many sources an interval came from.
    """
    out_s: list[int] = []
    out_e: list[int] = []
    out_c: list[int] = []
    out_n: list[int] = []
    for c in np.unique(cc):
        m = cc == c
        s, e, n = zs[m], ze[m], cn[m]
        order = np.argsort(s, kind="stable")
        s, e, n = s[order], e[order], n[order]
        cur_s, cur_e, cur_n = int(s[0]), int(e[0]), int(n[0])
        for i in range(1, len(s)):
            if int(s[i]) <= cur_e:          # overlap or touch (e is exclusive)
                cur_e = max(cur_e, int(e[i]))
                cur_n += int(n[i])
            else:
                out_s.append(cur_s); out_e.append(cur_e)
                out_c.append(int(c)); out_n.append(cur_n)
                cur_s, cur_e, cur_n = int(s[i]), int(e[i]), int(n[i])
        out_s.append(cur_s); out_e.append(cur_e)
        out_c.append(int(c)); out_n.append(cur_n)

    # Canonical column order: by z_start, then class.
    order = np.lexsort((np.array(out_c), np.array(out_s)))
    return (np.array(out_s, dtype=np.int32)[order],
            np.array(out_e, dtype=np.int32)[order],
            np.array(out_c, dtype=np.uint8)[order],
            np.array(out_n, dtype=np.int32)[order])


def _merge_two_columns(a: "Column", b: "Column") -> "Column":
    """Merge two columns that share the same (ix, iy) on a common grid."""
    zs = np.concatenate((a.z_start, b.z_start))
    ze = np.concatenate((a.z_end,   b.z_end))
    cc = np.concatenate((a.cls,     b.cls))
    cn = np.concatenate((a.count,   b.count))
    s, e, c, n = _coalesce_column_intervals(zs, ze, cc, cn)
    return Column(z_start=s, z_end=e, cls=c, count=n)


class _ColumnsView(Mapping):
    """
    Read-only dict-like view over a ColumnStore's flat arrays.

    Keys are ``(ix, iy)`` tuples; values are Column objects whose
    arrays are zero-copy slices. Lookup is a binary search on the packed
    keys (O(log n)); iteration is linear and materializes one Column at a
    time, so full scans never inflate memory.
    """
    __slots__ = ("_s",)

    def __init__(self, store: "ColumnStore"):
        """Bind the view to *store*; nothing is copied, every access reads the store's current flat arrays."""
        self._s = store

    def __len__(self) -> int:
        """Number of occupied columns in the store."""
        return int(self._s._keys.shape[0])

    # Chunk size for lazy iteration. Iterating the view used to do
    # ``_unpack_keys(all_keys).tolist()`` up front - two Python int lists of
    # length n_columns (~9 GB at 116.5M columns). Unpacking in blocks caps
    # the transient at tens of MB (~75 MB: two Python int lists of
    # _ITER_BLOCK boxed ints) regardless of store size, while yielding the
    # identical sequence in the identical (canonical) order.
    _ITER_BLOCK = 1_000_000

    def __iter__(self):
        """Yield the ``(ix, iy)`` keys in canonical (sorted) order, unpacking the packed key array one ``_ITER_BLOCK`` slice at a time."""
        keys = self._s._keys
        n = keys.shape[0]
        B = self._ITER_BLOCK
        for start in range(0, n, B):
            ix, iy = _unpack_keys(keys[start:start + B])
            for a, b in zip(ix.tolist(), iy.tolist()):
                yield (a, b)

    def __contains__(self, key) -> bool:
        """True when *key* names an occupied column; a key that cannot be packed (wrong shape, type or out of range) counts as absent rather than raising."""
        # OverflowError joins the list because packing shifts the index left
        # by 32 bits: an int beyond the uint64 range raises there, and an
        # unrepresentable key is a miss like any other.
        try:
            return self._s._find(key) >= 0
        except (TypeError, ValueError, IndexError, OverflowError):
            return False

    def __getitem__(self, key) -> Column:
        """Return the Column at ``(ix, iy)`` as zero-copy slices of the flat arrays, or raise ``KeyError`` when the column is unoccupied."""
        i = self._s._find(key)
        if i < 0:
            raise KeyError(key)
        return self._s._column_at(i)

    def items(self):
        """Yield ``((ix, iy), Column)`` pairs in canonical order, unpacking keys in ``_ITER_BLOCK`` slices and materializing one Column at a time."""
        s = self._s
        keys = s._keys
        n = keys.shape[0]
        B = self._ITER_BLOCK
        for start in range(0, n, B):
            ix, iy = _unpack_keys(keys[start:start + B])
            ixl, iyl = ix.tolist(), iy.tolist()
            for j in range(len(ixl)):
                yield (ixl[j], iyl[j]), s._column_at(start + j)

    def values(self):
        """Yield every Column in canonical order without unpacking the keys."""
        s = self._s
        for i in range(len(s._keys)):
            yield s._column_at(i)

    def keys(self):
        """Return a lazy ``KeysView`` over this view (length, iteration and membership) rather than a materialized list of tuples."""
        # Lazy KeysView, NOT a materialized list: at metropolis scale the
        # list of (ix, iy) tuples is ~15 GB on the 116.5M-column store - the
        # allocation this class exists to avoid. (The ~9 GB figure quoted at
        # _ITER_BLOCK above is a different object: the two Python int lists
        # the old eager unpacking built, without the tuples.) KeysView
        # supports len/iteration/membership.
        from collections.abc import KeysView
        return KeysView(self)

    def get(self, key, default=None):
        """Return the Column at *key* or *default* when the column is unoccupied or the key cannot be packed."""
        # Same tolerance as __contains__: a key that cannot name a column
        # returns the default instead of propagating the packing error.
        try:
            i = self._s._find(key)
        except (TypeError, ValueError, IndexError, OverflowError):
            return default
        return default if i < 0 else self._s._column_at(i)

    def __repr__(self) -> str:  # keep huge stores printable
        """Short summary naming only the column count, so printing a metropolis-scale view never enumerates it."""
        return f"<ColumnsView: {len(self)} columns>"


class ColumnStore:
    """
    The whole tile/area, as a column-compressed voxel grid (struct-of-arrays).

    Geometric metadata (origin, voxel size) is stored here so that any
    end user can convert column indices back to real-world coordinates.

    Design note: columns know about their own contents (vertically) but
    NOT about their horizontal neighbours; no left-right adjacency is
    stored, since every question asked of the grid is vertical (ground
    here? tree above? building cover?).

    ``x_min, y_min, z_min`` - grid origin in metric (CRS) coordinates,
    typically RGF93/CC46 (EPSG:3946). Indices (ix, iy, iz) are offsets from it.
    ``cell_xy, cell_z``     - voxel size in metres.
    ``columns``             - read-only Mapping view (see module docstring);
                              pass a plain dict here to build a store from
                              per-column data (converted to flat arrays).

    Flat arrays (struct-of-arrays layout)
    -------------------------------------
    The store is six flat numpy arrays, reset by ``_set_empty()``:

    | Array     | dtype  | Shape        | Meaning                                    |
    |-----------|--------|--------------|--------------------------------------------|
    | ``_keys`` | uint64 | (n_cols,)    | packed (ix, iy) key, sorted ascending       |
    | ``_off``  | int64  | (n_cols+1,)  | CSR offsets into the per-interval arrays    |
    | ``_zs``   | int32  | (n_iv,)      | first voxel index of each run               |
    | ``_ze``   | int32  | (n_iv,)      | exclusive end voxel index of each run       |
    | ``_cl``   | uint8  | (n_iv,)      | ASPRS class code of the run                 |
    | ``_ct``   | int32  | (n_iv,)      | echo (point) count of the run               |

    Cost: 16 bytes per column (key plus offset) and 13 bytes per interval.
    Columns are sorted by key; inside a column the intervals are sorted by
    height (z_start, then class).

    Public API
    ----------
    - ``from_intervals()``: build a store from flat per-interval arrays (the
      voxelizer's constructor).
    - ``grouped()``: new store with consecutive same-class intervals merged.
    - ``merge_many()``: one-pass union of several stores on one grid
      (``merge()`` is the pairwise form).
    - ``save()`` / ``load()``: compressed ``.npz`` round-trip, the portable
      artifact.
    - ``save_dir()`` / ``load_dir()``: raw directory of ``.npy`` files that
      can be memory-mapped.
    - ``swap_to_dir_mmap()``: replace the heap arrays with mmap views of an
      already written directory.
    - ``stats()``: summary dict (counts, per-class totals, column heights).
    - ``ensure_dense_index()``: optional O(1) column-lookup grid behind the
      binary search.
    - ``columns``: read-only Mapping view, ``(ix, iy)`` to ``Column``.
    - ``n_intervals``: total interval count.

    Underscore-prefixed members are internal layout machinery, kept visible
    on purpose because the architecture document names some of them.
    """

    __slots__ = ("x_min", "y_min", "z_min", "cell_xy", "cell_z",
                 "_keys", "_off", "_zs", "_ze", "_cl", "_ct", "_gi", "_dense")

    def __init__(self, x_min: float, y_min: float, z_min: float,
                 cell_xy: float, cell_z: float, columns=None):
        """Create a store with the given grid origin and voxel size, empty unless *columns* is a non-empty ``{(ix, iy): Column}`` mapping, which is converted to the flat layout."""
        self.x_min = float(x_min)
        self.y_min = float(y_min)
        self.z_min = float(z_min)
        self.cell_xy = float(cell_xy)
        self.cell_z = float(cell_z)
        self._set_empty()
        if columns:
            self._set_from_dict(columns)

    # -- construction ---------------------------------------------------------
    def _set_empty(self) -> None:
        """Reset the six flat arrays to their empty shapes (``_off`` keeps its single leading zero) and drop the cached ground index and dense column index."""
        self._keys = np.empty(0, dtype=np.uint64)
        self._off = np.zeros(1, dtype=np.int64)
        self._zs = np.empty(0, dtype=np.int32)
        self._ze = np.empty(0, dtype=np.int32)
        self._cl = np.empty(0, dtype=np.uint8)
        self._ct = np.empty(0, dtype=np.int32)
        self._gi = None
        self._dense = None

    def _set_from_dict(self, columns: dict) -> None:
        """Build the flat arrays from a ``{(ix, iy): Column}`` mapping.

        NOTE: a key whose Column holds ZERO intervals is silently dropped -
        in the flat layout an interval-less column is indistinguishable
        from an unoccupied one, so such keys do not survive the round-trip
        (``len(store.columns)`` will be smaller than ``len(columns)``).
        """
        keys = list(columns.keys())
        n_iv = np.fromiter((len(columns[k]) for k in keys), dtype=np.int64,
                           count=len(keys))
        iv_ix = np.repeat(np.fromiter((k[0] for k in keys), dtype=np.int64,
                                      count=len(keys)), n_iv)
        iv_iy = np.repeat(np.fromiter((k[1] for k in keys), dtype=np.int64,
                                      count=len(keys)), n_iv)
        if len(keys):
            zs = np.concatenate([np.asarray(columns[k].z_start) for k in keys])
            ze = np.concatenate([np.asarray(columns[k].z_end) for k in keys])
            cl = np.concatenate([np.asarray(columns[k].cls) for k in keys])
            ct = np.concatenate([np.asarray(columns[k].count) for k in keys])
        else:
            zs = ze = np.empty(0, np.int32); cl = np.empty(0, np.uint8)
            ct = np.empty(0, np.int32)
        self._adopt_intervals(iv_ix, iv_iy, zs, ze, cl, ct,
                              assume_canonical=False)

    def _adopt_intervals(self, iv_ix, iv_iy, zs, ze, cl, ct, *,
                         assume_canonical: bool) -> None:
        """Set this store's contents from flat per-interval arrays."""
        self._dense = None
        # The ground index is derived from the intervals, so replacing them
        # invalidates it exactly as it invalidates the dense column index.
        self._gi = None
        if len(zs) == 0:
            self._set_empty()
            return
        keys_iv = _pack_keys(iv_ix, iv_iy)
        zs = np.asarray(zs, dtype=np.int32)
        ze = np.asarray(ze, dtype=np.int32)
        cl = np.asarray(cl, dtype=np.uint8)
        ct = np.asarray(ct, dtype=np.int32)
        if not assume_canonical:
            order = np.lexsort((cl, zs, keys_iv))
            keys_iv = keys_iv[order]
            zs, ze, cl, ct = zs[order], ze[order], cl[order], ct[order]
        col_starts = np.concatenate(
            ([0], np.where(keys_iv[1:] != keys_iv[:-1])[0] + 1))
        self._keys = np.ascontiguousarray(keys_iv[col_starts], dtype=np.uint64)
        self._off = np.concatenate(
            (col_starts, [len(keys_iv)])).astype(np.int64)
        # Pin the documented dtypes (class docstring: zs/ze int32, cl uint8,
        # ct int32). Without the explicit dtype an int64 count array handed in
        # by the direct area path survived to disk, 4 extra bytes per interval
        # against the same values; load() normalised it on read, which is why
        # no consumer ever saw the difference.
        self._zs = np.ascontiguousarray(zs, dtype=np.int32)
        self._ze = np.ascontiguousarray(ze, dtype=np.int32)
        self._cl = np.ascontiguousarray(cl, dtype=np.uint8)
        self._ct = np.ascontiguousarray(ct, dtype=np.int32)

    @classmethod
    def from_intervals(cls, x_min, y_min, z_min, cell_xy, cell_z,
                       iv_ix, iv_iy, z_start, z_end, cls_arr, count, *,
                       assume_canonical: bool = False) -> "ColumnStore":
        """
        Build a store directly from flat per-interval arrays. This is the
        zero-Python-loop constructor used by ``voxelize._store_from_cells``.
        ``assume_canonical=True`` promises the intervals are already sorted
        by (ix, iy, z_start, cls) - the natural output of the voxelizer.

        @param x_min Grid origin x in metres (CRS coordinates).
        @param y_min Grid origin y in metres.
        @param z_min Grid origin z in metres; voxel index iz = 0 starts here.
        @param cell_xy Horizontal voxel size in metres.
        @param cell_z Vertical voxel size in metres.
        @param iv_ix Column index ix of each interval (integer array, one
                     entry per interval).
        @param iv_iy Column index iy of each interval, parallel to ``iv_ix``.
        @param z_start First voxel index of each interval (stored as int32).
        @param z_end Exclusive end voxel index of each interval (stored as
                     int32).
        @param cls_arr ASPRS class code of each interval (stored as uint8).
        @param count Point count of each interval (stored as int32).
        @param assume_canonical When False (default) the intervals are
                     lexsorted by (packed key, z_start, cls) before adoption;
                     True skips that sort and trusts the caller's order.
        @return The new store; empty (six empty arrays, no cached indices)
                when ``z_start`` has length zero.
        """
        st = cls(x_min, y_min, z_min, cell_xy, cell_z)
        st._adopt_intervals(iv_ix, iv_iy, z_start, z_end, cls_arr, count,
                            assume_canonical=assume_canonical)
        return st

    # -- basic accessors -------------------------------------------------------
    @property
    def columns(self) -> _ColumnsView:
        """Read-only Mapping view over the flat arrays; a fresh lightweight view object on every access."""
        return _ColumnsView(self)

    @columns.setter
    def columns(self, value) -> None:
        """Replace the store contents from a ``{(ix, iy): Column}`` mapping (any Mapping is accepted and copied to a dict); an empty or falsy value empties the store."""
        if value:
            self._set_from_dict(dict(value))
        else:
            self._set_empty()

    @property
    def n_intervals(self) -> int:
        """Total number of intervals across all columns."""
        return int(self._zs.shape[0])

    @property
    def ground_idx(self) -> np.ndarray:
        """int32 per occupied column: exclusive z_end of the highest ground interval (one past its top voxel), or NODATA.

        Computed lazily on first access and cached in ``_gi``. To force
        recomputation, set ``store._gi = None``.

        Returns an empty array if the store has no columns.
        """
        if self._gi is None:
            from .ground_index import compute_ground_indices
            self._gi = compute_ground_indices(self)
        return self._gi

    def nbytes(self) -> int:
        """In-memory size of the flat arrays (bytes).

        Includes the ground index and the dense column index whenever those
        are built; the dense grid covers the occupied bounding rectangle at
        4 bytes a cell, so it can dominate the total on a sparse store.
        """
        gi_bytes = self._gi.nbytes if self._gi is not None else 0
        dense_bytes = self._dense[0].nbytes if self._dense else 0
        return int(self._keys.nbytes + self._off.nbytes + self._zs.nbytes
                   + self._ze.nbytes + self._cl.nbytes + self._ct.nbytes
                   + gi_bytes + dense_bytes)

    def _find(self, key) -> int:
        """Index of (ix, iy) in the sorted key array, or -1."""
        if self._dense:
            grid, ix0, iy0 = self._dense
            gx = int(key[0]) - ix0
            gy = int(key[1]) - iy0
            if 0 <= gx < grid.shape[1] and 0 <= gy < grid.shape[0]:
                return int(grid[gy, gx])
            return -1
        if self._keys.shape[0] == 0:
            return -1
        k = _pack_one(key)
        i = int(np.searchsorted(self._keys, k))
        if i < self._keys.shape[0] and self._keys[i] == k:
            return i
        return -1

    def ensure_dense_index(self, max_bytes: int = 256 * 1024 * 1024) -> bool:
        """Build the O(1) (ix, iy) -> column-id grid behind _find().

        The grid covers the occupied columns' bounding rectangle at 4 bytes
        a cell, filled with -1 for empty cells, so hits and misses answer
        exactly as the binary search does; the ids are positions in the same
        sorted key array. Returns True when the index is present. A store
        whose rectangle exceeds *max_bytes* keeps the binary search and
        returns False: a merged metropolis-scale store at 0.5 m would need a
        multi-gigabyte grid, and correctness never depends on the index.
        Batch consumers that look up many columns (the absorb pass, the ray
        walkers' cache-miss path) call this once before their loop. A
        declined build is remembered (empty-tuple sentinel), so calling this
        per ray costs one attribute check, never a re-scan of the keys.

        @param max_bytes Largest grid the index may occupy, in bytes (default
                     256 MiB); the grid costs 4 bytes per cell of the occupied
                     bounding rectangle.
        @return True when the dense index is present after the call, False
                when it was declined (empty store or rectangle over budget).
        """
        if self._dense is not None:
            return bool(self._dense)
        n = int(self._keys.shape[0])
        if n == 0:
            self._dense = ()
            return False
        ix, iy = _unpack_keys(self._keys)
        ix0 = int(ix.min())
        iy0 = int(iy.min())
        w = int(ix.max()) - ix0 + 1
        h = int(iy.max()) - iy0 + 1
        if 4 * w * h > max_bytes:
            self._dense = ()
            return False
        grid = np.full((h, w), -1, dtype=np.int32)
        grid[(iy - iy0).astype(np.int64),
             (ix - ix0).astype(np.int64)] = np.arange(n, dtype=np.int32)
        self._dense = (grid, ix0, iy0)
        return True

    def _column_at(self, i: int) -> Column:
        """Wrap column *i* (a position in the sorted key array) as a Column whose four arrays are zero-copy slices of the flat interval arrays."""
        s, e = int(self._off[i]), int(self._off[i + 1])
        return Column(z_start=self._zs[s:e], z_end=self._ze[s:e],
                      cls=self._cl[s:e], count=self._ct[s:e])

    def _slice_at(self, i: int) -> tuple[int, int]:
        """The (start, end) offsets of column *i* into the flat arrays.

        Same two integers ``_column_at`` computes, but WITHOUT wrapping them in
        a Column dataclass. Hot per-voxel callers that only need to scan
        ``_zs``/``_ze``/``_cl`` for one voxel use this to avoid allocating a
        throwaway Column per lookup. The one caller is
        decoder.class_at_slice(), which both ray walkers go through
        (``ray_trace._class_at_voxel_fast`` is a one-line wrapper over it),
        so this method has a single hot path rather than two.
        """
        return int(self._off[i]), int(self._off[i + 1])

    def index_to_xyz(self, ix: int, iy: int, iz: int) -> tuple[float, float, float]:
        """Convert voxel indices back to the *centre* of the voxel in metres."""
        x = self.x_min + (ix + 0.5) * self.cell_xy
        y = self.y_min + (iy + 0.5) * self.cell_xy
        z = self.z_min + (iz + 0.5) * self.cell_z
        return x, y, z

    def _clone(self) -> "ColumnStore":
        """Deep copy of the store: same metadata, copies of the six flat arrays and of the ground index when cached; the dense column index is not carried over and is rebuilt on demand."""
        st = ColumnStore(self.x_min, self.y_min, self.z_min,
                         self.cell_xy, self.cell_z)
        st._keys = self._keys.copy(); st._off = self._off.copy()
        st._zs = self._zs.copy(); st._ze = self._ze.copy()
        st._cl = self._cl.copy(); st._ct = self._ct.copy()
        if self._gi is not None:
            st._gi = self._gi.copy()
        return st

    # -- persistence (used by the sharded area pipeline) -----------------------
    def save(self, path) -> None:
        """Write the store as a compressed .npz (flat arrays + metadata).

        The cached ground index is written too when present.

        @param path Destination file (str or Path); ``np.savez_compressed``
                    appends ``.npz`` when the name lacks it.
        """
        kwargs = dict(keys=self._keys, off=self._off, zs=self._zs,
                       ze=self._ze, cl=self._cl, ct=self._ct,
                       meta=np.array([self.x_min, self.y_min, self.z_min,
                                      self.cell_xy, self.cell_z],
                                     dtype=np.float64))
        if self._gi is not None:
            kwargs["gi"] = self._gi
        np.savez_compressed(str(path), **kwargs)

    @classmethod
    def load(cls, path) -> "ColumnStore":
        """Inverse of save().

        Accepts either the exact file path or, when that does not exist,
        the same path with ``.npz`` appended - because ``save()`` goes
        through ``np.savez_compressed``, which appends ``.npz`` itself,
        ``save(p)`` followed by ``load(p)`` round-trips for any spelling.

        @param path File written by save(), with or without the ``.npz``
                    suffix (str or Path).
        @return A new store holding heap copies of the arrays, normalised to
                the documented dtypes; the ground index is restored (widened
                to int32) when the file has one.
        """
        p = Path(str(path))
        if not p.exists() and p.suffix != ".npz":
            alt = p.with_name(p.name + ".npz")
            if alt.exists():
                path = alt
        with np.load(str(path)) as d:
            meta = d["meta"]
            st = cls(*(float(v) for v in meta))
            st._keys = d["keys"].astype(np.uint64, copy=True)
            st._off = d["off"].astype(np.int64, copy=True)
            st._zs = d["zs"].astype(np.int32, copy=True)
            st._ze = d["ze"].astype(np.int32, copy=True)
            st._cl = d["cl"].astype(np.uint8, copy=True)
            st._ct = d["ct"].astype(np.int32, copy=True)
            if "gi" in d:
                # int32 (was int16, which silently wrapped at fine cell_z).
                # Files
                # persisted with the old int16 array widen losslessly here.
                st._gi = d["gi"].astype(np.int32, copy=True)
        return st

    # -- raw (memory-mappable) persistence: the stage-exchange format ----------
    # ``save()``'s compressed .npz stays the portable artifact, but it cannot
    # be memory-mapped: np.load inflates every member fully into the heap, so
    # each consumer re-pays the whole store (~6 GB on a dense 3 km area). The
    # raw directory (one plain .npy per flat array + meta.json) exists so that
    # per-stage child processes can attach with ``np.load(..., mmap_mode="r")``:
    # zero-copy, lazy page-in, and the pages are CLEAN file-backed pages the OS
    # may discard under memory pressure instead of swapping. meta.json is
    # written LAST as the commit marker - a directory without it is an aborted
    # write and load_dir refuses it.
    _RAW_SCHEMA = "voxelizer.store_raw/1"
    _RAW_ARRAYS = ("keys", "off", "zs", "ze", "cl", "ct")
    _RAW_ARRAYS_OPT = ("gi",)  # optional arrays (may not exist in older stores)

    def save_dir(self, path) -> None:
        """Persist as a raw, mmap-able directory (see block comment above).

        @param path Target directory (str or Path); created with its parents
                    when missing. The six ``.npy`` files (plus ``gi.npy`` when
                    the ground index is cached) overwrite any existing files
                    of the same names, and ``meta.json`` is written last as
                    the commit marker after the arrays are fsynced.
        """
        import json as _json
        import os as _os
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        arrays = (self._keys, self._off, self._zs, self._ze, self._cl, self._ct)
        written = []
        for name, arr in zip(self._RAW_ARRAYS, arrays):
            p = path / f"{name}.npy"
            np.save(str(p), arr)
            written.append(p)
        if self._gi is not None:
            p = path / "gi.npy"
            np.save(str(p), self._gi)
            written.append(p)
        # The commit marker only means something once the arrays it vouches
        # for are on the platter. Written but unflushed, a power loss can
        # leave a complete meta.json over short or empty .npy files, which
        # load_dir would then accept. Flush the arrays, then the directory
        # entries that name them, before the marker is created. The files are
        # reopened "rb+" because Windows' fsync is _commit(), which needs a
        # WRITABLE handle and fails EBADF on a read-only one. The directory
        # flush is POSIX-only: Windows has no directory handle to fsync, and
        # its metadata ordering is the kernel's business.
        for p in written:
            with open(p, "rb+") as fh:
                _os.fsync(fh.fileno())
        if _os.name != "nt":
            fd = _os.open(str(path), _os.O_RDONLY)
            try:
                _os.fsync(fd)
            finally:
                _os.close(fd)
        meta = {
            "schema": self._RAW_SCHEMA,
            "x_min": float(self.x_min), "y_min": float(self.y_min),
            "z_min": float(self.z_min),
            "cell_xy": float(self.cell_xy), "cell_z": float(self.cell_z),
            "n_columns": int(self._keys.shape[0]),
            "n_intervals": int(self._zs.shape[0]),
            "has_gi": self._gi is not None,
        }
        (path / "meta.json").write_text(_json.dumps(meta, indent=2),
                                        encoding="utf-8")

    @classmethod
    def load_dir(cls, path, *, mmap: bool = True) -> "ColumnStore":
        """Load a save_dir() directory.

        ``mmap=True`` returns arrays as read-only ``np.memmap`` views - lazy,
        zero-copy, OS-evictable. Consumers MUST treat the store as read-only
        (all rendering/diagnostic stages do). ``mmap=False`` reads full copies.

        @param path Directory written by save_dir() (str or Path).
        @param mmap When True (default) the arrays are read-only ``np.memmap``
                    views; when False full heap copies are read.
        @return A new store on the grid recorded in ``meta.json``, with the
                ground index attached when ``gi.npy`` is present.
        @throws FileNotFoundError When ``meta.json`` is missing (an aborted
                save_dir() or a foreign directory).
        @throws ValueError When the schema string is not
                ``voxelizer.store_raw/1`` or the array lengths disagree with
                the column and interval counts in ``meta.json``.
        """
        import json as _json
        path = Path(path)
        meta_p = path / "meta.json"
        if not meta_p.exists():
            raise FileNotFoundError(
                f"{path} is not a complete raw store (missing meta.json - "
                "possibly an aborted save_dir).")
        meta = _json.loads(meta_p.read_text(encoding="utf-8"))
        if meta.get("schema") != cls._RAW_SCHEMA:
            raise ValueError(f"unsupported raw-store schema: {meta.get('schema')!r}")
        st = cls(meta["x_min"], meta["y_min"], meta["z_min"],
                 meta["cell_xy"], meta["cell_z"])
        mode = "r" if mmap else None
        (st._keys, st._off, st._zs, st._ze, st._cl, st._ct) = (
            np.load(str(path / f"{n}.npy"), mmap_mode=mode)
            for n in cls._RAW_ARRAYS)
        if meta.get("has_gi", False):
            gi_path = path / "gi.npy"
            if gi_path.exists():
                st._gi = np.load(str(gi_path), mmap_mode=mode)
        n_cols, n_ivs = int(st._keys.shape[0]), int(st._zs.shape[0])
        if (n_cols, n_ivs) != (meta["n_columns"], meta["n_intervals"]):
            raise ValueError(
                f"raw store {path} is inconsistent: meta says "
                f"{meta['n_columns']} cols / {meta['n_intervals']} intervals, "
                f"arrays hold {n_cols} / {n_ivs}.")
        return st

    def swap_to_dir_mmap(self, path) -> None:
        """Replace this store's heap arrays with read-only mmap views of an
        already-written save_dir() directory, releasing the heap copies.

        Used by the stage orchestrator right after persisting: the parent keeps
        a fully readable store (API contract preserved for callers like the
        GUI) while its dirty heap pages become clean file-backed
        pages. NOTE: on Windows the directory cannot be deleted while these
        views are alive - call release_arrays() first.

        @param path Directory written by save_dir(); it is opened through
                    ``load_dir(path, mmap=True)``, whose FileNotFoundError and
                    ValueError propagate unchanged. On success the heap
                    arrays, the cached ground index and any dense index are
                    dropped in favour of the mapped views.
        """
        other = ColumnStore.load_dir(path, mmap=True)
        (self._keys, self._off, self._zs, self._ze, self._cl, self._ct) = (
            other._keys, other._off, other._zs, other._ze, other._cl, other._ct)
        self._gi = other._gi
        # Any dense index describes the previous arrays; drop it rather than
        # trust the caller to point this at the same directory.
        self._dense = None

    def release_arrays(self) -> None:
        """Drop all array references (replacing them with empty arrays) so a
        backing raw-store directory can be deleted, even on Windows where
        mapped files are undeletable while a view is open."""
        self._keys = np.empty(0, dtype=self._keys.dtype)
        self._off = np.zeros(1, dtype=self._off.dtype)
        self._zs = np.empty(0, dtype=self._zs.dtype)
        self._ze = np.empty(0, dtype=self._ze.dtype)
        self._cl = np.empty(0, dtype=self._cl.dtype)
        self._ct = np.empty(0, dtype=self._ct.dtype)
        self._dense = None
        self._gi = None

    # -- vectorized per-column reductions ---------------------------------------

    def grouped(self, max_gap_cells: int | None = None) -> "ColumnStore":
        """Return a NEW store where consecutive same-class intervals in each
        column are merged into one interval (the 'grouping/unison' step).

        At fine cell_z, sparse returns fragment one physical object (a tree
        canopy) into dozens of 1-point slivers separated by empty cells; the
        diagnostics then report 40-interval "complex" columns that are really
        one object, and every interval costs store bytes, viz3d boxes and
        figure artists. Merging is CONSECUTIVE-ONLY: a same-class run broken
        by another class stays split, so intervals remain sorted and disjoint
        (full same-class spans would overlap where classes interleave, which
        would break every downstream consumer's invariant).

        ``max_gap_cells``: merge only across gaps of at most this many empty
        z-cells (None = any gap). Merged counts are summed; per-column and
        per-class point totals are exactly preserved; the top interval's class
        and z_end are unchanged, so max-height and orthophoto maps are
        unaffected. The raw store is not modified.

        @param max_gap_cells Largest gap, counted in empty z-cells (voxels,
                     not metres), that a merge may bridge between two
                     consecutive same-class intervals; None (default) merges
                     across any gap.
        @return A new ColumnStore on the same grid with the same column keys;
                the receiver is left untouched, and no ground index or dense
                index is carried over.
        """
        new = ColumnStore(self.x_min, self.y_min, self.z_min,
                          self.cell_xy, self.cell_z)
        m = int(self._zs.shape[0])
        if m == 0:
            new._keys = self._keys.copy()
            new._off = self._off.copy()
            return new
        zs, ze, cl, ct, off = self._zs, self._ze, self._cl, self._ct, self._off
        start = np.ones(m, dtype=bool)
        merge = cl[1:] == cl[:-1]
        if max_gap_cells is not None:
            merge &= (zs[1:] - ze[:-1]) <= int(max_gap_cells)
        start[1:] = ~merge
        start[off[:-1]] = True          # a column boundary always starts a run
        idx = np.flatnonzero(start)
        new._keys = self._keys.copy()
        new._zs = zs[idx].copy()
        new._cl = cl[idx].copy()
        new._ze = np.maximum.reduceat(ze, idx)
        # add.reduceat upcasts sub-platform ints to int64 on 64-bit numpy;
        # pin the documented int32 so grouped stores serialize at spec width
        # (largest observed count is far below the int32 ceiling).
        new._ct = np.add.reduceat(ct, idx).astype(np.int32, copy=False)
        per_col = np.add.reduceat(start.astype(np.int64), off[:-1])
        new._off = np.concatenate(([0], np.cumsum(per_col)))
        return new

    def key_bounds(self) -> tuple[int, int, int, int] | None:
        """(ix_min, iy_min, ix_max, iy_max) over occupied columns, or None."""
        if self._keys.shape[0] == 0:
            return None
        ix, iy = _unpack_keys(self._keys)
        return int(ix.min()), int(iy.min()), int(ix.max()), int(iy.max())

    def interval_counts(self) -> np.ndarray:
        """
        Per-column interval count (column complexity), one int32 per occupied
        column, in the store's canonical column order. Computed straight off
        the offset array (``np.diff(self._off)``) with no Python loop and no
        materialization of per-column ``Column`` objects.

        This is the single most common per-column reduction, so it is exposed
        as a first-class method: consumers that only need "how many intervals
        does each column have" (histograms, top-N-by-complexity) should use
        this instead of iterating ``self.columns`` - iterating the Mapping
        view builds one transient Column per column (millions on a dense
        500 m tile), which is both slow and needless for a length query.
        """
        return np.diff(self._off).astype(np.int32)

    def column_class_profile(self, *, chunk_intervals: int | None = None,
                             ground_class: int = GROUND, building_class: int = BUILDING,
                             veg_classes=VEGETATION_CLASSES) -> dict:
        """
        Per-column class facts needed by the sample-column categoriser, one
        entry per occupied column in canonical order, computed fully in C:

            nivs         int32  intervals per column (== interval_counts())
            first_cls    uint8  class of the column's first interval
            has_ground   bool   any interval is class 2
            has_building bool   any interval is class 6
            has_veg      bool   any interval is class 3, 4, 5 or 8
            n_distinct   int32  number of distinct class codes in the column

        This replaces a per-column Python loop in
        column_diagnostics._categorize(), which built one ``set``, one
        transient ``Column`` and, via ``items()``, one key tuple per occupied
        column: memory proportional to the column count in Python objects.

        ``n_distinct`` is exact for *any* raw class code (0-255), not just the
        codes in ``classes_config``: each column's class set is accumulated as a
        256-bit mask (four uint64 lanes) and popcounted. Work is done in
        interval-count-bounded batches (default ``_DOM_CHUNK_INTERVALS``),
        splitting only on whole columns, so peak temporary memory is bounded by
        the batch (four uint64 lanes over the batch) rather than by the store's
        total interval count - and the result is independent of the batch size.
        """
        n = int(self._keys.shape[0])
        off = self._off
        nivs = np.diff(off).astype(np.int32)
        if n == 0:
            e = np.empty(0, np.int32)
            b = np.empty(0, bool)
            return dict(nivs=e, first_cls=np.empty(0, np.uint8),
                        has_ground=b, has_building=b.copy(), has_veg=b.copy(),
                        n_distinct=e.copy())

        first_cls = self._cl[off[:-1]].copy()

        # Only lane 0 (class codes 0-63, which hold ground/building/veg) is kept
        # resident, for the membership tests. Lanes 1-3 (codes 64-255) exist only
        # to make ``n_distinct`` exact for exotic codes; their contribution is
        # folded into the popcount as we go and never stored, so the sole full-
        # length uint64 array is lane 0 rather than a (4, n) block.
        lane0 = np.zeros(n, dtype=np.uint64)
        n_distinct = np.zeros(n, dtype=np.int32)
        budget = (self._DOM_CHUNK_INTERVALS if chunk_intervals is None
                  else max(1, int(chunk_intervals)))
        csum = np.cumsum(nivs.astype(np.int64))
        c0, prev = 0, 0
        while c0 < n:
            c1 = int(np.searchsorted(csum, prev + budget, side="right"))
            if c1 <= c0:
                c1 = c0 + 1
            s, e = int(off[c0]), int(off[c1])
            code = self._cl[s:e].astype(np.uint16)
            lane_idx = (code >> 6).astype(np.int64)      # 0..3
            bitval = (np.uint64(1) << (code & np.uint16(63)).astype(np.uint64))
            local_starts = (off[c0:c1] - s)
            for L in range(4):
                contrib = np.where(lane_idx == L, bitval, np.uint64(0))
                col_lane = np.bitwise_or.reduceat(contrib, local_starts)
                if L == 0:
                    lane0[c0:c1] = col_lane
                n_distinct[c0:c1] += np.bitwise_count(col_lane).astype(np.int32)
            prev = int(csum[c1 - 1])
            c0 = c1

        # Defaults sourced from classes_config - all modules stay in sync.
        if not all(0 <= int(c) < 64 for c in (ground_class, building_class,
                                              *veg_classes)):
            raise ValueError("class codes for the membership masks must be "
                             "in [0, 64) (lane 0 of the bitmask)")
        veg_mask = np.uint64(sum(1 << int(c) for c in set(veg_classes)))
        has_ground = (lane0 & np.uint64(1 << int(ground_class))) != 0
        has_building = (lane0 & np.uint64(1 << int(building_class))) != 0
        has_veg = (lane0 & veg_mask) != 0

        return dict(nivs=nivs, first_cls=first_cls, has_ground=has_ground,
                    has_building=has_building, has_veg=has_veg,
                    n_distinct=n_distinct)

    def class_presence(self, codes=None, *,
                       chunk_intervals: int | None = None) -> dict:
        """
        Per-column presence mask for each requested class code: one bool
        array (canonical column order) per code. This is the store-side
        reduction behind "every class in classes_config is represented in
        the diagnostics": with the default ``codes=None`` it covers the
        WHOLE table (``classes_config.ALL_CLASS_CODES``: 0-11, 13-22
        and 64-67 - 12 is reserved and unnamed), not just the
        ground/building/vegetation buckets of
        column_class_profile(). Callers may narrow it: the
        by-class figure passes only the codes <= 31, since the IGN range
        cannot occur under point format 1.

        Codes must lie in [0, 128): presence is derived from two resident
        per-column uint64 bitmask lanes (codes 0-63 and 64-127, 16 B per
        column total), accumulated in interval-count-bounded batches over
        whole columns exactly like column_class_profile(), so peak
        temporary memory is bounded by the batch, not the store, and the
        result is independent of the batch size. Raises ValueError for a
        code >= 128 (add lanes 2-3 the same way if a vendor range above
        127 ever matters).
        """
        codes = tuple(ALL_CLASS_CODES) if codes is None else tuple(codes)
        if not all(0 <= int(c) < 128 for c in codes):
            raise ValueError("class_presence codes must be in [0, 128)")
        n = int(self._keys.shape[0])
        if n == 0:
            return {int(c): np.empty(0, bool) for c in codes}
        off = self._off
        nivs = np.diff(off).astype(np.int32)
        lanes = np.zeros((2, n), dtype=np.uint64)   # codes 0-63 / 64-127
        budget = (self._DOM_CHUNK_INTERVALS if chunk_intervals is None
                  else max(1, int(chunk_intervals)))
        csum = np.cumsum(nivs.astype(np.int64))
        c0, prev = 0, 0
        while c0 < n:
            c1 = int(np.searchsorted(csum, prev + budget, side="right"))
            if c1 <= c0:
                c1 = c0 + 1
            s, e = int(off[c0]), int(off[c1])
            code = self._cl[s:e].astype(np.uint16)
            lane_idx = (code >> 6).astype(np.int64)      # 0..3
            bitval = (np.uint64(1)
                      << (code & np.uint16(63)).astype(np.uint64))
            local_starts = (off[c0:c1] - s)
            for L in (0, 1):
                contrib = np.where(lane_idx == L, bitval, np.uint64(0))
                lanes[L, c0:c1] = np.bitwise_or.reduceat(contrib,
                                                         local_starts)
            prev = int(csum[c1 - 1])
            c0 = c1
        out = {}
        for c in codes:
            c = int(c)
            lane = lanes[c >> 6]
            out[c] = (lane & (np.uint64(1) << np.uint64(c & 63))) != 0
        return out

    def class_overlap_stats(self, *,
                            chunk_intervals: int | None = None) -> dict:
        """
        Detect SHARED VOXELS: z-ranges where two intervals of one column
        overlap. In practice every such overlap is cross-class (a 16.6 M-
        interval urban merge measured zero same-class overlaps).
        Same-class runs are NOT always gapped, though: the voxelizer
        forms runs along a z-major cell walk, so a same-class run is
        split into TOUCHING pieces wherever another class interleaves in
        the same voxels (0.19 % of intervals on that merge). The two
        coalescing paths treat those pieces differently: ``grouped()``
        merges same-class intervals only where they are CONSECUTIVE in
        the column's z order, so an interleaved pair stays split, while
        ``merge()`` groups by class before sorting and does join a
        touching same-class pair that another class separates.
        ``absorb_interior`` coalesces the absorber runs its relabel
        leaves touching.
        Cross-class intervals are deliberately kept independent (one
        physical voxel CAN hold e.g. ground points and vegetation points),
        so the store retains the full per-(voxel-run, class) point counts:
        NOTHING is lost in the data. The one-class-per-cell collapse
        people observe happens only downstream, in projections (the 2-D
        maps put one class per pixel: majority for ``max_points_class``,
        topmost for ``orthophoto_class``) and in rendering (co-located
        boxes overpaint in 2-D and z-fight in 3-D). This method makes the
        phenomenon measurable so runs can report it.

        Returns ``dict(n_overlap_intervals, n_overlap_columns)``: intervals
        whose z_start lies below a previous interval's exclusive z_end
        within the same column, and columns containing at least one such
        interval. Fully vectorized (a per-batch segment cumulative max via
        the offset trick), batched over whole columns so temporaries stay
        bounded; the result is independent of the batch size.
        """
        n = int(self._keys.shape[0])
        if n == 0 or self._zs.shape[0] == 0:
            return {"n_overlap_intervals": 0, "n_overlap_columns": 0}
        off = self._off
        nivs = np.diff(off).astype(np.int64)
        budget = (self._DOM_CHUNK_INTERVALS if chunk_intervals is None
                  else max(1, int(chunk_intervals)))
        csum = np.cumsum(nivs)
        n_ov_iv = 0
        n_ov_col = 0
        c0, prev = 0, 0
        while c0 < n:
            c1 = int(np.searchsorted(csum, prev + budget, side="right"))
            if c1 <= c0:
                c1 = c0 + 1
            s, e = int(off[c0]), int(off[c1])
            zs = self._zs[s:e].astype(np.int64)
            ze = self._ze[s:e].astype(np.int64)
            local_starts = (off[c0:c1] - s).astype(np.int64)
            k = e - s
            # segment id per interval (0..batch_cols-1) without np.repeat:
            seg = np.zeros(k, dtype=np.int64)
            seg[local_starts[1:]] = 1
            seg = np.cumsum(seg)
            # Segment-wise cumulative max via a per-segment offset large
            # enough that earlier segments can never win the max.
            K = int(ze.max() - zs.min()) + 2
            shifted = ze + seg * K
            cm = np.maximum.accumulate(shifted)
            prev_max = np.empty_like(cm)
            prev_max[0] = np.iinfo(np.int64).min
            prev_max[1:] = cm[:-1]
            overlap = (zs + seg * K) < prev_max
            overlap[local_starts] = False   # a column's first interval
            n_ov_iv += int(overlap.sum())
            per_col = np.add.reduceat(overlap.astype(np.int64),
                                      local_starts)
            n_ov_col += int((per_col > 0).sum())
            prev = int(csum[c1 - 1])
            c0 = c1
        return {"n_overlap_intervals": int(n_ov_iv),
                "n_overlap_columns": int(n_ov_col)}

    # Default per-batch interval budget for the dominant-class step. ~8M
    # intervals keeps each batch's int64 temporaries (col_idx, take, sorted
    # copies, the np.diff temp) at ~64 MB apiece regardless of how many
    # hundreds of millions of intervals the whole store holds.
    _DOM_CHUNK_INTERVALS = 8_000_000

    def column_summaries(self, *, need_dom: bool = True,
                         dom_chunk_intervals: int | None = None) -> dict:
        """
        One number per occupied column, with no per-column Python work
        (the dominant step runs a short interval-bounded batch loop):

            ix, iy      int32  column indices
            n_intervals int32  interval count (complexity)
            top         int32  exclusive top voxel index (z_end of last run)
            bot         int32  lowest occupied voxel index (z_start of first)
            top_cls     uint8  class of the topmost interval ("orthophoto")
            dom_cls     uint8  class with the most points in the column

        Feeds the 2-D map renderers and the shard mosaic without ever
        materializing per-column Python objects.

        ``need_dom`` - when False, the dominant-class step (the only expensive
        part) is skipped and
        ``dom_cls`` is returned as all-zeros. The three map modes that never
        read ``dom`` (max_height / orthophoto_class / n_intervals) pass this so
        they can render even on a store whose dominant-class reduction would
        not fit in RAM. Every other field is identical either way.

        ``dom_chunk_intervals`` - per-batch interval budget for the dominant
        step (default ``_DOM_CHUNK_INTERVALS``). The multi-interval columns
        are reduced in batches whose cumulative interval count stays under this,
        so peak temporary memory is bounded by the batch rather than by the
        store's total interval count. The result is independent of the value
        (batching splits on whole columns, never mid-column), so this is purely
        a memory/speed knob, not a semantics one.
        """
        n = int(self._keys.shape[0])
        ix, iy = _unpack_keys(self._keys)
        if n == 0:
            e32 = np.empty(0, np.int32)
            return dict(ix=e32, iy=e32.copy(), n_intervals=e32.copy(),
                        top=e32.copy(), bot=e32.copy(),
                        top_cls=np.empty(0, np.uint8),
                        dom_cls=np.empty(0, np.uint8))
        off = self._off
        nivs = np.diff(off).astype(np.int64)
        # "Top" is the highest z_end over the column, not the last interval's:
        # z_start is the sort key, and under cross-class overlap (class_first
        # run order, absorb_interior, merges of overlapping inputs) an earlier
        # interval can end above the last one.
        top = np.maximum.reduceat(self._ze, off[:-1]).astype(np.int32)
        bot = self._zs[off[:-1]].astype(np.int32)
        # "Topmost class" is a pure function of the SET of intervals: among
        # those reaching the top, the smallest class code wins (the same tie
        # rule as the dominant class). A positional rule (last in list, or
        # argmax's first hit) makes two stores holding identical content in
        # different run structures disagree wherever two classes tie at the
        # top voxel - measured at 0.27 % of columns on a 0.5 m area store.
        cl_masked = np.where(
            np.asarray(self._ze) == np.repeat(top, nivs),
            np.asarray(self._cl), np.uint8(255))
        top_cls = np.minimum.reduceat(cl_masked, off[:-1]).astype(np.uint8)

        dom = np.zeros(n, dtype=np.uint8)
        if need_dom:
            budget = (self._DOM_CHUNK_INTERVALS if dom_chunk_intervals is None
                      else dom_chunk_intervals)
            self._fill_dominant(dom, off, nivs, chunk_intervals=budget)

        return dict(ix=ix, iy=iy, n_intervals=nivs.astype(np.int32),
                    top=top, bot=bot, top_cls=top_cls, dom_cls=dom)

    def _fill_dominant(self, dom, off, nivs, *, chunk_intervals: int) -> None:
        """
        Write the dominant class (argmax over per-(column, class) summed point
        counts; ties -> smallest code, matching ``np.bincount(...).argmax()``)
        into ``dom`` for every occupied column.

        Single-interval columns are trivial (their sole class). Multi-interval
        columns are reduced in interval-count-bounded batches: a dense area can
        reach hundreds of millions of intervals, and an un-batched reduction
        would need several int64 temporaries of that whole length at
        once. Because a column's intervals are contiguous in the flat
        arrays and every batch takes *whole* columns, no column is split across
        a batch and each column's dominant class is written exactly once - so
        the output is identical to the single-pass computation.
        """
        # Single-interval columns: dominant == their only class.
        single = nivs == 1
        dom[single] = self._cl[off[:-1][single]]

        multi_idx = np.where(~single)[0]
        if not multi_idx.size:
            return

        nivs_m = nivs[multi_idx]        # intervals per multi column (int64)
        starts_m = off[:-1][multi_idx]  # absolute interval start per multi col
        csum = np.cumsum(nivs_m)        # running interval total across columns

        budget = max(1, int(chunk_intervals))
        c0, prev, n_multi = 0, 0, multi_idx.shape[0]
        while c0 < n_multi:
            # Furthest column c1 whose batch stays within `budget` intervals;
            # always advance by >= 1 column so a single column larger than the
            # budget still makes progress (it becomes its own oversized batch).
            c1 = int(np.searchsorted(csum, prev + budget, side="right"))
            if c1 <= c0:
                c1 = c0 + 1
            self._dominant_batch(dom, multi_idx[c0:c1],
                                 nivs_m[c0:c1], starts_m[c0:c1])
            prev = int(csum[c1 - 1])
            c0 = c1

    def _dominant_batch(self, dom, cols, k, starts) -> None:
        """
        Dominant class for one batch of multi-interval columns (the original
        single-pass reduction, restricted to the batch).

        ``cols``   absolute column indices (into ``dom``);
        ``k``      interval count per column (int64);
        ``starts`` absolute start offset of each column's intervals.
        """
        col_idx = np.repeat(cols, k)
        # Batch-local interval positions (arange starts at 0) mapped back to
        # absolute indices into the flat _cl/_ct arrays via the per-column
        # shift (start - local_prefix); identical identity to the un-batched
        # path, made batch-local so the arange never spans the whole store.
        take = np.repeat(starts - np.concatenate(([0], np.cumsum(k)))[:-1], k) \
            + np.arange(int(k.sum()))
        cl = self._cl[take]
        ct = self._ct[take].astype(np.int64)
        order = np.lexsort((cl, col_idx))
        ci, cl, ct = col_idx[order], cl[order], ct[order]
        grp = np.concatenate(
            ([0], np.where((np.diff(ci) != 0) | (np.diff(cl) != 0))[0] + 1))
        g_ci, g_cl = ci[grp], cl[grp]
        g_sum = np.add.reduceat(ct, grp)
        # Within each column: ascending sum, ties by DESCENDING class code, so
        # the last row of each column group is (max sum, smallest code).
        o2 = np.lexsort((255 - g_cl.astype(np.int32), g_sum, g_ci))
        g_ci2, g_cl2 = g_ci[o2], g_cl[o2]
        last = np.concatenate(
            (np.where(np.diff(g_ci2) != 0)[0], [len(g_ci2) - 1]))
        dom[g_ci2[last]] = g_cl2[last]

    # -- merge ------------------------------------------------------------------
    def merge(
        self,
        other: "ColumnStore",
        *,
        inplace: bool = False,
        atol: float = 1e-6,
    ) -> "ColumnStore":
        """
        Combine ``other`` into this store. Both must share one grid: same
        cell sizes and the same origin ``(x_min, y_min, z_min)``. Columns
        present in only one store are carried over as-is; columns present in
        both are stitched by _merge_two_columns().

        This is the across-tile assembler used by ``process_area``: every
        tile is voxelized against the *same* shared origin (one z0 for the
        whole area), so merging is index-compatible and, with a grid-aligned
        origin over clipped non-overlapping tiles, reduces to a disjoint
        union - which is now a pure array concatenation + one gather (no
        per-column Python work at all).

        @param other The store to fold in; it is only read, never modified.
        @param inplace When True the receiver adopts the merged arrays (its
                     cached ground index and dense index are dropped) and is
                     returned; when False (default) a new store is returned
                     and the receiver is untouched.
        @param atol Absolute tolerance in metres for the cell-size and origin
                     equality checks (default 1e-6).
        @return The merged store: ``self`` when ``inplace`` is True, otherwise
                a new store. Merging a store with itself is a no-op (``self``
                untouched, or an independent copy).
        @throws ValueError If the two stores are on incompatible grids
                (different cell size or origin) - merging those would
                silently misalign voxels.
        """
        if other is self:
            # Merge is a *union* of tile contents, so merging a store with
            # itself is a no-op: inplace returns self untouched, non-inplace
            # returns an independent copy. Without this guard every column
            # would be concatenated with itself and its touching runs
            # coalesced, silently doubling all point counts.
            return self if inplace else self._clone()

        if not (abs(self.cell_xy - other.cell_xy) < atol
                and abs(self.cell_z - other.cell_z) < atol):
            raise ValueError(
                f"cell-size mismatch: self=({self.cell_xy},{self.cell_z}) "
                f"other=({other.cell_xy},{other.cell_z})"
            )
        if not (abs(self.x_min - other.x_min) < atol
                and abs(self.y_min - other.y_min) < atol
                and abs(self.z_min - other.z_min) < atol):
            raise ValueError(
                "origin mismatch - stores must share one origin to merge "
                f"(self=({self.x_min},{self.y_min},{self.z_min}) "
                f"other=({other.x_min},{other.y_min},{other.z_min})). "
                "Voxelize every tile with the same explicit `origin`."
            )

        merged = _merge_stores(self, other)
        if inplace:
            self._keys, self._off = merged._keys, merged._off
            self._zs, self._ze = merged._zs, merged._ze
            self._cl, self._ct = merged._cl, merged._ct
            # The cached ground index and dense index (if any) describe the
            # PRE-merge column set; keeping either would hand consumers a
            # wrong answer silently.
            self._gi = None
            self._dense = None
            return self
        return merged

    # -- merge_many --------------------------------------------------------------
    @classmethod
    def merge_many(cls, stores: list["ColumnStore"],
                   *, atol: float = 1e-6, consume: bool = False,
                   iv_chunk: int = 40_000_000) -> "ColumnStore":
        """
        Merge a list of stores in ONE pass: concatenate every store's flat
        arrays, sort the combined columns by key once, and coalesce only the
        keys that actually collide.

        All stores must share one grid (same cell sizes and origin). For
        spatially-disjoint tiles (the common ``process_area`` / shard case)
        no two stores share a column key, so this is a pure concatenate +
        single ``argsort`` + gather with zero per-column Python work -
        O(N log N) in the total column count. Where the same (ix, iy) column
        is present in more than one store (overlapping inputs - rare), those
        few groups are stitched with _coalesce_column_intervals(),
        giving the identical result to pairwise merge().

        This replaces the previous incremental ``reduce(merge)`` form, whose
        cost was O(n_stores x N) - it re-sorted the whole growing accumulator
        on every store, quadratic in the number of shards on a large area.

        Memory discipline (this is the whole-Lyon-scale path, 160M+ columns /
        400M+ intervals):
          * ``consume=True`` releases each input store's arrays the moment its
            data has been copied into the combined buffers, so peak RAM never
            holds the sources AND the concatenated copy at once. The passed
            stores are emptied in place; do not reuse them afterwards.
          * offsets and the sort permutation are int32 (interval and column
            totals are well under 2^31), halving the column-indexed metadata.
          * the reorder gather runs in interval-bounded chunks (``iv_chunk``),
            so no single index array spans all intervals.

        For the large-area case where a single in-memory store is
        inappropriate at all, see voxelizer.sharding.run_area_sharded().

        @param stores The stores to merge (None entries are ignored). All
            must share one grid: same ``cell_xy``/``cell_z`` and one origin.
            At least one store must be given; an empty store is accepted and
            supplies the grid metadata on its own.
        @param atol Absolute tolerance in metres for the cell-size and origin
            equality checks (default 1e-6).
        @param consume DESTRUCTIVE (default False). Release each input
            store's arrays as soon as its data has been copied into the
            combined buffers, so peak RAM never holds both the sources and
            the concatenated copy. The passed stores are emptied in place;
            do not reuse them afterwards.
        @param iv_chunk Interval count per gather block when reordering the
            combined columns (default 40,000,000), so no single index array
            spans all intervals.
        @return A new ColumnStore on the shared grid, holding the union of
            the inputs with colliding (ix, iy) columns coalesced.
        @throws ValueError When no store (or only None) is given, when the
            inputs disagree on cell size or origin (beyond ``atol``), or when
            the total interval count reaches 2^31 and would overflow the
            int32 offset limit.
        """
        stores = [s for s in stores if s is not None]
        if not stores:
            raise ValueError(
                "merge_many: no stores given (or all None) - the merged "
                "grid metadata would be unknowable. Pass at least one "
                "store; an EMPTY store is fine (its grid metadata is "
                "used).")
        nonempty = [s for s in stores if s._keys.shape[0] > 0]
        if not nonempty:
            # All inputs empty: return an empty store ON THE INPUTS' GRID.
            # (Falling back to a default origin here - (0,0,0) / 1 m
            # cells - would surface much later as an unrelated-looking
            # 'cell-size mismatch' when the empty store is merged
            # onward.)
            b = stores[0]
            return cls(b.x_min, b.y_min, b.z_min, b.cell_xy, b.cell_z)
        stores = nonempty
        base = stores[0]
        origin = (base.x_min, base.y_min, base.z_min, base.cell_xy, base.cell_z)
        for s in stores[1:]:
            if not (abs(base.cell_xy - s.cell_xy) < atol
                    and abs(base.cell_z - s.cell_z) < atol):
                raise ValueError(
                    f"cell-size mismatch in merge_many: base="
                    f"({base.cell_xy},{base.cell_z}) other="
                    f"({s.cell_xy},{s.cell_z})")
            if not (abs(base.x_min - s.x_min) < atol
                    and abs(base.y_min - s.y_min) < atol
                    and abs(base.z_min - s.z_min) < atol):
                raise ValueError(
                    "origin mismatch in merge_many - stores must share one "
                    f"origin (base=({base.x_min},{base.y_min},{base.z_min}) "
                    f"other=({s.x_min},{s.y_min},{s.z_min})). Voxelize every "
                    "tile with the same explicit `origin`.")

        n_col = sum(int(s._keys.shape[0]) for s in stores)
        n_iv = sum(int(s._zs.shape[0]) for s in stores)
        if n_iv >= (1 << 31):
            raise ValueError(
                f"merge_many: {n_iv:,} total intervals exceeds the int32 "
                "offset limit; merge in sub-batches (see sharding.py).")

        # Pre-allocated combined buffers; copy each store in, then (optionally)
        # free it so the sources never coexist with a second full copy.
        keys = np.empty(n_col, dtype=np.uint64)
        niv = np.empty(n_col, dtype=np.int32)
        zs = np.empty(n_iv, dtype=np.int32)
        ze = np.empty(n_iv, dtype=np.int32)
        cl = np.empty(n_iv, dtype=np.uint8)
        ct = np.empty(n_iv, dtype=np.int32)
        co = io = 0
        for s in stores:
            nc, ni = int(s._keys.shape[0]), int(s._zs.shape[0])
            keys[co:co + nc] = s._keys
            niv[co:co + nc] = np.diff(s._off)
            zs[io:io + ni] = s._zs
            ze[io:io + ni] = s._ze
            cl[io:io + ni] = s._cl
            ct[io:io + ni] = s._ct
            co += nc
            io += ni
            if consume:
                s.release_arrays()

        off = np.empty(n_col + 1, dtype=np.int32)
        off[0] = 0
        np.cumsum(niv, out=off[1:])
        del niv

        order = np.argsort(keys, kind="stable").astype(np.int32, copy=False)
        keys, off, zs, ze, cl, ct = _gather_columns_chunked(
            keys, off, zs, ze, cl, ct, order, iv_chunk)
        del order
        keys, off, zs, ze, cl, ct = _coalesce_sorted_duplicate_columns(
            keys, off, zs, ze, cl, ct)

        out = cls(*origin)
        out._keys = np.ascontiguousarray(keys)
        out._off = np.ascontiguousarray(off.astype(np.int64, copy=False))
        out._zs = zs.astype(np.int32, copy=False)
        out._ze = ze.astype(np.int32, copy=False)
        out._cl = cl.astype(np.uint8, copy=False)
        out._ct = ct.astype(np.int32, copy=False)
        return out

    # -- stats -------------------------------------------------------------------
    def stats(self) -> dict:
        """
        Return a small dict of summary numbers, useful and necessary
        for sanity-checking after voxelization. Fully vectorized on the
        flat arrays (no per-column Python loop).

        Includes a per-class breakdown so you can see at a glance whether
        the tile has the class distribution you expected (e.g. mostly
        ground + some vegetation, or building-heavy urban core).

        @return A dict with the column, interval and point totals, the
            average intervals per column, per-class point and interval
            counts (keyed by class name, dominant class first), the tile's
            vertical extent in metres (``z_min_m``/``z_max_m``) and the
            per-column height statistics (min/max/mean/median/sum in
            metres), the shortest and tallest columns, and the cross-class
            overlap counts from ``class_overlap_stats``. The geometry and
            height fields are None for an empty store.
        """
        n_columns = int(self._keys.shape[0])
        if n_columns == 0:
            return {
                "n_columns": 0, "n_intervals": 0, "n_points": 0,
                "avg_intervals_per_column": 0.0,
                "points_per_class": {}, "intervals_per_class": {},
                # Vertical / column geometry (empty tile -> all null).
                "z_min_m": None, "z_max_m": None,
                "column_height_min_m": None, "column_height_max_m": None,
                "column_height_mean_m": None, "column_height_median_m": None,
                "column_height_sum_m": 0.0,
                "shortest_column": None, "tallest_column": None,
                "intervals_per_column_min": 0, "intervals_per_column_max": 0,
                "n_single_interval_columns": 0,
                # Shared voxels (cross-class z-overlaps) - see
                # class_overlap_stats().
                "n_overlap_intervals": 0, "n_overlap_columns": 0,
            }

        # Per-class aggregation: bincount does the grouping in C.
        #  - cls is uint8, so values land in [0, 255]; minlength=256 gives a
        #    fixed-size output regardless of which codes actually occur.
        #  - with weights=count, bincount returns total LiDAR points/class;
        #    without weights, it returns interval occurrences per class.
        points_by_code = np.bincount(
            self._cl, weights=self._ct.astype(np.float64),
            minlength=256).astype(np.int64)
        intervals_by_code = np.bincount(self._cl, minlength=256)

        n_intervals = int(intervals_by_code.sum())
        n_points = int(points_by_code.sum())

        seen = np.where(intervals_by_code > 0)[0]
        per_class_points = {
            CLASS_NAMES.get(int(c), f"class_{c}"): int(points_by_code[c])
            for c in seen
        }
        per_class_intervals = {
            CLASS_NAMES.get(int(c), f"class_{c}"): int(intervals_by_code[c])
            for c in seen
        }
        # Sort descending by value so the dominant classes show first.
        per_class_points = dict(sorted(per_class_points.items(),
                                       key=lambda kv: -kv[1]))
        per_class_intervals = dict(sorted(per_class_intervals.items(),
                                          key=lambda kv: -kv[1]))

        # Vertical geometry, straight off the flat arrays:
        #   bottom = z_start of each column's first interval
        #   top    = max z_end over the column (exclusive; equals the last
        #            interval's z_end in canonical height-first order, but an
        #            earlier interval can end higher under cross-class overlap)
        #   n_iv   = interval count per column (complexity)
        off = self._off
        bots = self._zs[off[:-1]].astype(np.int64)
        tops = np.maximum.reduceat(self._ze, off[:-1]).astype(np.int64)
        nivs = np.diff(off)

        extents_vox = tops - bots
        extents_m = extents_vox.astype(np.float64) * self.cell_z

        # Absolute altitudes (metres in the tile CRS). z_min anchors iz=0.
        z_low_m = float(self.z_min + int(bots.min()) * self.cell_z)
        z_high_m = float(self.z_min + int(tops.max()) * self.cell_z)

        i_short = int(extents_vox.argmin())
        i_tall = int(extents_vox.argmax())
        ix_s, iy_s = _unpack_keys(self._keys[i_short:i_short + 1])
        ix_t, iy_t = _unpack_keys(self._keys[i_tall:i_tall + 1])
        shortest_column = {
            "ix": int(ix_s[0]), "iy": int(iy_s[0]),
            "height_m": float(extents_m[i_short]),
        }
        tallest_column = {
            "ix": int(ix_t[0]), "iy": int(iy_t[0]),
            "height_m": float(extents_m[i_tall]),
        }

        # avg_intervals_per_column is a quick RLE-compression diagnostic. A
        # clean column (ground + canopy, nothing else) is 2; LiDAR returns on
        # vertical surfaces fragment building walls into more, smaller
        # intervals and push the average up. Urban averages measured in this
        # package run from 1.48 to 2.72, and they FALL as the grid refines:
        # the ten-area series is 2.116 at 1.0 m, 1.776 at 0.5 m and 1.480 at
        # 0.25 m, the same series preflight's calibration constants are fitted
        # to (calibrated on the 2026-08-08 campaign, archived outside the
        # delivery), against 2.72 on the metropolis-scale store cited in
        # sharding and 2.2 as preflight's planning default.
        return {
            "n_columns": n_columns,
            "n_intervals": n_intervals,
            "n_points": n_points,
            "avg_intervals_per_column": round(n_intervals / n_columns, 2),
            "points_per_class": per_class_points,
            "intervals_per_class": per_class_intervals,
            # Vertical extent of the tile (absolute altitude, metres).
            "z_min_m": z_low_m,
            "z_max_m": z_high_m,
            # Per-column height (relative, metres) - shortest/tallest + spread.
            "column_height_min_m": float(extents_m.min()),
            "column_height_max_m": float(extents_m.max()),
            "column_height_mean_m": round(float(extents_m.mean()), 3),
            "column_height_median_m": round(float(np.median(extents_m)), 3),
            "column_height_sum_m": float(extents_m.sum()),  # for combined mean
            "shortest_column": shortest_column,
            "tallest_column": tallest_column,
            # Column complexity (interval count).
            "intervals_per_column_min": int(nivs.min()),
            "intervals_per_column_max": int(nivs.max()),
            "n_single_interval_columns": int((nivs == 1).sum()),
            # Shared voxels: cross-class z-overlaps inside single columns
            # (the data keeps every class; see class_overlap_stats()).
            **self.class_overlap_stats(),
        }


# ---------------------------------------------------------------------------
# Merge machinery (module-level so it stays testable in isolation)
# ---------------------------------------------------------------------------
def _gather_by_column_order(keys, off, zs, ze, cl, ct, order):
    """
    Reorder whole column blocks according to ``order`` (an index array over
    columns). Returns (keys, off, zs, ze, cl, ct) in the new order. Fully
    vectorized: the per-interval gather index is built by repeating each
    block's source start and adding a ramp.
    """
    niv = np.diff(off)
    new_niv = niv[order]
    new_off = np.concatenate(([0], np.cumsum(new_niv))).astype(np.int64)
    if new_off[-1] == 0:
        return (keys[order], new_off, zs[:0], ze[:0], cl[:0], ct[:0])
    src_starts = off[:-1][order]
    idx = (np.repeat(src_starts - new_off[:-1], new_niv)
           + np.arange(int(new_off[-1]), dtype=np.int64))
    return (keys[order], new_off, zs[idx], ze[idx], cl[idx], ct[idx])


def _gather_columns_chunked(keys, off, zs, ze, cl, ct, order, iv_chunk):
    """
    Reorder whole column blocks by ``order`` (like _gather_by_column_order())
    but writing the interval arrays in interval-bounded chunks, so the source-index
    array never spans all intervals at once. Used by ColumnStore.merge_many()
    on whole-area stores (hundreds of millions of intervals), where a single-shot
    gather index would itself be gigabytes. Offsets are int32 (the caller guarantees
    the interval total is below 2^31).
    """
    n_col = int(order.shape[0])
    n_iv = int(zs.shape[0])
    src_niv = (off[1:] - off[:-1])           # int32 intervals per source column
    new_niv = src_niv[order]
    src_starts = off[:-1][order]             # source interval start per out column
    new_off = np.empty(n_col + 1, dtype=np.int32)
    new_off[0] = 0
    np.cumsum(new_niv, out=new_off[1:])
    keys_out = keys[order]

    zs2 = np.empty(n_iv, dtype=zs.dtype)
    ze2 = np.empty(n_iv, dtype=ze.dtype)
    cl2 = np.empty(n_iv, dtype=cl.dtype)
    ct2 = np.empty(n_iv, dtype=ct.dtype)

    budget = max(1, int(iv_chunk))
    c0 = 0
    while c0 < n_col:
        c1 = int(np.searchsorted(new_off, int(new_off[c0]) + budget,
                                 side="right")) - 1
        if c1 <= c0:
            c1 = c0 + 1                       # a single oversized column stands alone
        out_a, out_b = int(new_off[c0]), int(new_off[c1])
        if out_b > out_a:
            # source index per output interval: per-column base + local ramp.
            base = (src_starts[c0:c1].astype(np.int64)
                    - (new_off[c0:c1].astype(np.int64) - out_a))
            idx = (np.repeat(base, new_niv[c0:c1])
                   + np.arange(out_b - out_a, dtype=np.int64))
            zs2[out_a:out_b] = zs[idx]
            ze2[out_a:out_b] = ze[idx]
            cl2[out_a:out_b] = cl[idx]
            ct2[out_a:out_b] = ct[idx]
        c0 = c1
    return keys_out, new_off, zs2, ze2, cl2, ct2


def _subset_columns(store: ColumnStore, mask: np.ndarray):
    """Extract (keys, off, zs, ze, cl, ct) for the columns where mask=True."""
    order = np.where(mask)[0]
    return _gather_by_column_order(store._keys, store._off, store._zs,
                                   store._ze, store._cl, store._ct, order)


def _coalesce_sorted_duplicate_columns(keys, off, zs, ze, cl, ct):
    """
    Collapse adjacent equal keys in an already key-sorted store into one
    column each, coalescing their intervals with the same rule as pairwise
    merge (_coalesce_column_intervals()).

    Used by ColumnStore.merge_many() after its single global sort. The
    common case - every key unique (disjoint tiles) - is detected up front
    and returns the inputs untouched with ZERO Python-level per-column work.
    Only when the same (ix, iy) appears in more than one source store (rare,
    overlapping inputs) is any coalescing done, and then only for the
    colliding groups; the many singleton columns between them are copied in
    bulk contiguous slices, so the Python loop runs O(number of colliding
    groups) times, never O(number of columns).
    """
    n_col = keys.shape[0]
    if n_col == 0:
        return keys, off, zs, ze, cl, ct
    is_first = np.empty(n_col, dtype=bool)
    is_first[0] = True
    is_first[1:] = keys[1:] != keys[:-1]
    g_col_start = np.flatnonzero(is_first)          # column start of each group
    n_u = g_col_start.shape[0]
    if n_u == n_col:
        return keys, off, zs, ze, cl, ct            # all unique: nothing to do

    g_col_end = np.empty(n_u, dtype=np.int64)       # exclusive column end
    g_col_end[:-1] = g_col_start[1:]
    g_col_end[-1] = n_col
    unique_keys = keys[g_col_start]
    # Pre-coalesce interval span per group (exact for singletons; an upper
    # bound for colliding groups, overwritten below with the coalesced count).
    new_niv = (off[g_col_end] - off[g_col_start]).astype(np.int64)

    dup_g = np.flatnonzero((g_col_end - g_col_start) > 1)

    s_parts, e_parts, c_parts, n_parts = [], [], [], []
    cursor = 0                                      # next group index to emit
    for g in dup_g:
        g = int(g)
        if g > cursor:                              # bulk-copy singleton run
            iv_a = int(off[g_col_start[cursor]])
            iv_b = int(off[g_col_start[g]])
            s_parts.append(zs[iv_a:iv_b]); e_parts.append(ze[iv_a:iv_b])
            c_parts.append(cl[iv_a:iv_b]); n_parts.append(ct[iv_a:iv_b])
        iv_a = int(off[g_col_start[g]]); iv_b = int(off[g_col_end[g]])
        cs, ce, cc, cn = _coalesce_column_intervals(
            zs[iv_a:iv_b], ze[iv_a:iv_b], cl[iv_a:iv_b], ct[iv_a:iv_b])
        s_parts.append(cs); e_parts.append(ce)
        c_parts.append(cc); n_parts.append(cn)
        new_niv[g] = cs.shape[0]
        cursor = g + 1
    if cursor < n_u:                                # trailing singleton run
        iv_a = int(off[g_col_start[cursor]])
        iv_b = int(off[-1])
        s_parts.append(zs[iv_a:iv_b]); e_parts.append(ze[iv_a:iv_b])
        c_parts.append(cl[iv_a:iv_b]); n_parts.append(ct[iv_a:iv_b])

    new_off = np.concatenate(([0], np.cumsum(new_niv))).astype(np.int64)
    return (unique_keys,
            new_off,
            np.concatenate(s_parts),
            np.concatenate(e_parts),
            np.concatenate(c_parts),
            np.concatenate(n_parts))


def _merge_stores(a: ColumnStore, b: ColumnStore) -> ColumnStore:
    """Grid-checked union of two stores (see ColumnStore.merge)."""
    out = ColumnStore(a.x_min, a.y_min, a.z_min, a.cell_xy, a.cell_z)
    if b._keys.shape[0] == 0:
        return a._clone()
    if a._keys.shape[0] == 0:
        c = b._clone()
        c.x_min, c.y_min, c.z_min = a.x_min, a.y_min, a.z_min
        return c

    common = np.intersect1d(a._keys, b._keys, assume_unique=True)

    if common.size == 0:
        # Fast path (the real area workload): disjoint tiles -> concatenate
        # column blocks from both stores and sort by key. One argsort + one
        # gather; zero per-column Python work.
        keys = np.concatenate((a._keys, b._keys))
        off = np.concatenate((a._off, a._off[-1] + b._off[1:])).astype(np.int64)
        zs = np.concatenate((a._zs, b._zs))
        ze = np.concatenate((a._ze, b._ze))
        cl = np.concatenate((a._cl, b._cl))
        ct = np.concatenate((a._ct, b._ct))
        order = np.argsort(keys, kind="stable")
        keys, off, zs, ze, cl, ct = _gather_by_column_order(
            keys, off, zs, ze, cl, ct, order)
        out._keys, out._off = keys, off
        out._zs, out._ze, out._cl, out._ct = (
            zs.astype(np.int32, copy=False), ze.astype(np.int32, copy=False),
            cl.astype(np.uint8, copy=False), ct.astype(np.int32, copy=False))
        return out

    # Shared columns exist (overlapping inputs - rare). Keep the disjoint
    # parts on the fast path and stitch only the shared columns with the
    # exact per-column coalescer.
    a_shared = np.isin(a._keys, common, assume_unique=True)
    b_shared = np.isin(b._keys, common, assume_unique=True)
    parts = [_subset_columns(a, ~a_shared), _subset_columns(b, ~b_shared)]

    m_keys, m_zs, m_ze, m_cl, m_ct, m_niv = [], [], [], [], [], []
    a_idx = np.where(a_shared)[0]
    for i in a_idx:
        key = a._keys[i]
        j = int(np.searchsorted(b._keys, key))
        col = _merge_two_columns(a._column_at(int(i)), b._column_at(j))
        m_keys.append(key)
        m_zs.append(col.z_start); m_ze.append(col.z_end)
        m_cl.append(col.cls);     m_ct.append(col.count)
        m_niv.append(len(col))
    shared_keys = np.array(m_keys, dtype=np.uint64)
    shared_off = np.concatenate(([0], np.cumsum(m_niv))).astype(np.int64)
    parts.append((shared_keys, shared_off,
                  np.concatenate(m_zs).astype(np.int32),
                  np.concatenate(m_ze).astype(np.int32),
                  np.concatenate(m_cl).astype(np.uint8),
                  np.concatenate(m_ct).astype(np.int32)))

    keys = np.concatenate([p[0] for p in parts])
    off = np.zeros(len(keys) + 1, dtype=np.int64)
    pos = 0
    zs_l, ze_l, cl_l, ct_l = [], [], [], []
    for p in parts:
        n = len(p[0])
        if n:
            off[pos + 1: pos + 1 + n] = off[pos] + p[1][1:]
        pos += n
        zs_l.append(p[2]); ze_l.append(p[3]); cl_l.append(p[4]); ct_l.append(p[5])
    zs = np.concatenate(zs_l); ze = np.concatenate(ze_l)
    cl = np.concatenate(cl_l); ct = np.concatenate(ct_l)

    order = np.argsort(keys, kind="stable")
    keys, off, zs, ze, cl, ct = _gather_by_column_order(
        keys, off, zs, ze, cl, ct, order)
    out._keys, out._off = keys, off
    out._zs, out._ze = zs.astype(np.int32, copy=False), ze.astype(np.int32, copy=False)
    out._cl, out._ct = cl.astype(np.uint8, copy=False), ct.astype(np.int32, copy=False)
    return out
