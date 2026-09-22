# Exact `ColumnStore` <-> `.laz` round trip - design rationale

Every design choice behind `reconstruct.mode="exact"`, `archive_cli`, and the
manifest provenance block, with the reasoning and the measurement behind each.
For *how to use* them see `how-to-use.md` section 9; for the numbers see
sections 10 (measured cost) and 12 (real-tile parity matrix) below.

---

## 0. The two questions this answers

**Can we recover the original IGN `.laz` from a `.npz`?** No, and no amount of
tooling changes that. `io_laz.read_laz()` extracts four arrays - `x, y, z,
classification` - and discards intensity, RGB/NIR, return number and number of
returns, GPS time, scan angle, point source ID, user data and the
classification flag bits before a single voxel exists. `_cells_from_points`
then floors the survivors into integer indices. The map is many-to-one by
design.

**Can we recreate a raw `ColumnStore` bit-exactly from a LAZ we generate?** Yes,
and that is what this work implements. The six store arrays are verified
identical, and the re-saved `.npz` reproduces byte-for-byte as well: the
packaged writer is deterministic, and `tests/test_archive_cli.py` pins the
pack/unpack SHA-256 equality.

---

## 1. Why `exact` is "one point per voxel + padding"

### The rule

For each interval `[z_start, z_end)` with class `c` and count `n`:

- emit one point at the centre of every voxel in the interval - `z_end -
  z_start` points, restoring the **occupancy and class** of every cell;
- emit `n - (z_end - z_start)` further points in the interval's **first**
  voxel, restoring the **count**.

### Why it is exact

`voxelize._store_from_cells` sums point counts **per interval**, never per
voxel. So the only things a re-voxelization can observe are (a) the set of
occupied `(ix, iy, iz, cls)` cells and (b) the per-interval totals. The rule
above reproduces both. Where the padding sits inside the interval is
unobservable - which is what makes the cheapest possible placement legal.

The second half of the argument is subtler: the re-run RLE must produce the
*same intervals*, not merely the same cells. It does, because a raw store **is
the canonical RLE of its own cell set** - expanding and re-encoding is
idempotent. This matters most where two classes interleave in one column: the
RLE breaks a run at every class change, so class 2 spanning `iz 0-4` with
class 5 poking through at 0, 1, 4, 5 fragments into `[0,1) [1,2) [2,5)`. That
fragmentation is what the `height_first` order produces, which is the order
every pre-switch store carries and the one this example is written in; the
`class_first` default walks the same cells per class and would write class 2
as a single `[0,5)`. The round trip has to reproduce whichever fragmentation
the store was built with, and does, because the archive tools re-voxelize
under the order recorded in the provenance.
`test_the_interleaved_fixture_really_does_fragment` guards that the fixtures
still exercise it.

### Why the counts are recoverable at all

`count >= z_end - z_start` holds for every interval of a freshly voxelized
store: each occupied voxel contributed at least one point. Checked across
2,195,849 real intervals - 0 violations. When it *doesn't* hold, the store did
not come from `voxelize` (see section 6).

### The precondition, stated precisely

Exactness is a property of **canonical** stores - those that are the RLE of
their own cell set - not of every well-formed `ColumnStore`. Anything
`voxelize` emits qualifies by construction, which is why per-tile shards are
archivable. A hand-assembled interval list need not: during implementation a
fixture describing three intervals turned out to have a true canonical RLE of
*eight*, and failed the round trip correctly. That is why the interleaved-class
test fixtures are built by voxelizing synthetic points (`_from_cells`) rather
than by listing intervals, and why the invalid hand-built store survives as
`test_a_non_canonical_handmade_store_is_not_exact`.

### Alternatives considered and rejected

| Alternative | Why not |
|---|---|
| One point per voxel, count carried in `gps_time` on the interval's first point | ~3.4x fewer points and a smaller file, but it needs a bespoke reader - the whole appeal of exact-LAZ is that `voxelize` reads it back with no special casing, and that any GIS tool can open it meaningfully. A file whose semantics live in a side channel is a `.npz` with extra steps. |
| Distribute the padding evenly across the interval's voxels | Costs a division per interval and buys nothing: the per-voxel distribution is unobservable to `_store_from_cells`. It would *also* be a lie - the original per-voxel counts are not in the store, so an even spread invents structure that was never measured. |
| Store per-voxel counts in the `.npz` so the padding could be exact under re-gridding | A schema change to every existing store, to fix a problem nobody has (section 8). |

Parking the padding in the first voxel is the cheapest correct choice, and it
is a *visible* lie rather than a plausible one - anyone inspecting the cloud
sees a spike at each interval base and knows the vertical distribution is
synthetic.

---

## 2. Why a `mode` enum instead of a new function

`store_to_points` already had a boolean `one_per_voxel`. A third behaviour
makes a boolean pair meaningless, and two of the three modes now differ only
in a trailing step. So: `mode="density" | "one_per_voxel" | "exact"`, with
`one_per_voxel=True` kept as an alias.

The alias is not politeness - `tests/test_regression_audit.py` and any
downstream caller pass that keyword, and silently changing its meaning would
be the worst possible failure mode. Passing both a `mode` and a conflicting
`one_per_voxel=True` raises rather than picking a winner
(`_resolve_mode`).

The constant is `EXPORT_MODES`, not `MODES`, because `pipeline.MODES` (height
modes) already exists and both are re-exported from `voxelizer/__init__.py`.

`mode="exact"` refuses `z_as_centre=False`: interval-base z puts every point
of an interval into one voxel, which is precisely what the mode exists to
avoid. Failing beats silently producing a file that verifies as wrong later.

---

## 3. Why the grid travels in a VLR

A re-voxelization only reproduces the store when it uses the **same origin**.
`voxelize(origin=None)` derives the origin from `min(x)` - and for a
reconstructed cloud that is a voxel *centre*, so the rebuilt store lands half a
cell off. Measured: `x_min 1841000.0 -> 1841000.5`, `z_min 161.97 -> 162.22`.
`test_derived_origin_lands_half_a_cell_off` pins it.

So the grid must travel with the file. Options were a sidecar JSON or a VLR
inside the LAS header; the VLR wins because a sidecar can be separated from
its file by any copy, upload or archive step, and a self-describing file is the
entire point of choosing an interoperable format.

Layout - `user_id="IARBRE"`, `record_id=1`, 52 bytes:

```
8s  magic  b"VOXGRID1"     versioned, so a future layout change is detected
5d  x_min, y_min, z_min, cell_xy, cell_z
I   epsg
```

The magic is checked on read and a foreign or truncated record raises rather
than being reinterpreted (`test_grid_vlr_rejects_a_foreign_payload`). The
user_id/record_id pair is pinned by a test, because changing it silently
orphans every archive already written.

Reading happens through `io_laz.read_vlr_bytes`, not directly in
`reconstruct`: `io_laz` is the module that isolates the laspy dependency ("if
we ever swap to PDAL, only this file changes"), so the *read* belongs there
while the *schema* stays with the code that owns it.

Explicit `origin` / `cell_xy` / `cell_z` arguments override the VLR, and a file
without one demands them (`store_from_laz` raises rather than guessing).

---

## 4. Header choices

**The CRS was a live bug.** `store_to_las` wrote no CRS, and `io_laz.read_laz`
*rejects* files without one - so nothing `reconstruct.py` produced could be fed
back into the pipeline. `test_written_files_carry_a_crs` is the regression.

`DEFAULT_EPSG = 3946` lives in `io_laz.py`, next to the `_ALLOWED_CRS`
validation it feeds, rather than in `reconstruct`. It is a parameter, not a
constant, because the validator also accepts Lambert-93 (2154).

**Why not store the EPSG in the ColumnStore?** `ColumnStore.save` writes a
`meta` array of exactly five floats, and `load` does `cls(*(float(v) for v in
meta))`. A sixth element breaks the constructor for every existing `.npz`, in
both directions. The CRS therefore lives in the VLR and in the manifests, and
the store stays a pure lattice description.

**Point format.** LAS 1.2 / point format 3 carries the reconstruction output
(the IGN input itself is point format 1) and is the most widely readable
option, so it stays the default. But pf0-5 store
classification in **5 bits**, so class 67 raised
`OverflowError: value 67 is greater than allowed (max: 31)` from inside laspy.
Grand Lyon data is 0-22 and `classes_config` notes that IGN's custom 64-67
cannot occur in LAS 1.2 data - but synthetic fixtures and IGN "optimised"
tiles can carry them, so codes above 31 promote the file to LAS 1.4 / pf6.
Both branches are pinned by tests, including the negative one: real data must
*not* silently change format.

**Scales and offsets** stay at `0.001` with `offsets = min(x/y/z)`. This is
better than it looks for exact mode: with the offset at a voxel centre, every
exported coordinate differs from it by an exact multiple of the cell size, so
the millimetre quantization is lossless rather than merely tolerable. The
worst case is ~0.5 mm against a half-cell (0.25 m) margin to the nearest voxel
boundary.

---

## 5. Why verification returns a verdict instead of raising

`verify_exact` / `verify_roundtrip` return `(ok, differences)`. A verifier's
job is to answer a yes/no question; a verifier that explodes on the very input
it exists to reject is not usable in a loop over 2,842 shards.

So `store_to_points(mode="exact")` **raises** on a store it cannot honestly
export (it is an export function - refusing to emit garbage is correct), while
`verify_exact` catches that specific `ValueError` and reports it as a
difference. `test_verify_reports_a_grouped_store_instead_of_raising` pins the
split.

`stores_equal` deliberately ignores `_gi` (the ground index): it is derived,
recomputed downstream, and is not part of what a round trip must preserve.

---

## 6. Grouped stores: why `pack` refuses structurally, not by inspection

`ColumnStore.grouped()` merges same-class intervals **across empty z-gaps**.
The initial assumption was "grouped stores never round-trip". A randomized
test disproved it:

- **Most** merges leave the interval with `count < height` - the gap voxels
  add height without adding points. On a real 40,000-column slice, 771
  intervals. The exact export refuses these outright, with an error naming
  grouping as the likely cause.
- **But** a merge whose summed count still covers the merged height, with no
  foreign class inside the span, round-trips perfectly well - to the *grouped*
  store. Found in the wild by `test_grouped_stores_are_not_reliably_archivable`
  (raw `[5,6)c5 ct8` + `[7,9)c5 ct9` -> merged `[5,9)c5 ct17`, height 4, and it
  verifies).

Nothing on the store distinguishes the two cases. A content check would
therefore pass some grouped input and fail other grouped input, which is the
worst of both worlds - it looks like a safety net while silently blessing
half the wrong inputs.

So `archive_cli.pack` decides **structurally**: it accepts only `shards/`, a
directory `shard_worker` guarantees is raw (it saves straight out of
`voxelize`; grouping happens in memory at merge time), and refuses everything
else with a message that says why. Per-shard verification then runs anyway, as
the second line of defence.

This costs nothing, because `_merge_all_shards` is deterministic:
`merge_many([shard.grouped(gap) for shard in shards])`. `area.npz` is
**regenerated** from the archive rather than archived - provided the grouping
settings were written down, which is what section 7 is for.

---

## 7. Why the manifests grew a provenance block

`group_intervals` and `group_gap` were recorded **nowhere** - not in
`area_manifest.json`, not in `shards/manifest.json`, not in the shard
`run_config.json`. Without the gap, `area.npz` cannot be reproduced from the
shards even in principle, so the archive would have been an archive of
something you could no longer turn back into the deliverable.

`run_utils.make_provenance` is the single schema, shared by both manifests so
they cannot drift:

| field | why it is not derivable from the store |
|---|---|
| `group_intervals`, `group_gap_m` | grouping is not invertible |
| `keep_classes` | a class filter changes point totals with no on-disk trace |
| `epsg` | `ColumnStore` has no CRS field (section 4) |

`group_gap_m` is recorded in **metres, not cells**, because the cell count is
`round(gap / cell_z)` - a consumer changing `cell_z` needs the physical
quantity, not a number that silently means something else on a different
lattice.

Every field is written even when unknown (as `null`) so a consumer can tell
"not applicable" from "written by an older version". The manifests carry
`"schema": ".../2"` for the same reason.

`make_provenance` lives in `run_utils` - a Level 0/1 module - so both
`area_cli` and `sharding` can import it without reopening the dependency cycle
that `area_outputs` was created to break.

### Verified, not assumed

The point of the provenance block is that the *deliverable* survives archiving,
not just the shards. Driven end to end on a real `run_area_sharded` run
(`--group-gap 1.5`, `--keep-classes 2,5,6`, `--merge-shards`):

```
shards/manifest.json: schema=voxelizer.shards/2  group_gap_m=1.5
                      keep_classes=[2,5,6]  epsg=3946  shards_are_raw=True
area_manifest.json:   schema=voxelizer.area/2   group_gap_m=1.5  ...
pack: 2 shards, 0 failed, verified=True
shard sha256 identical after pack/unpack: True
area.npz regenerated from the archive (group_gap_m=1.5): IDENTICAL
```

That last line is the whole chain: archive -> restore -> re-merge with the
*recorded* gap -> byte-identical `area.npz`. Without `group_gap_m` on disk the
final step would have been a guess. The same provenance appears on the
non-sharded `run_area` path, and travels through `stages/params.json` so an
isolated stage child and a `--resume-from-store` rerun write the same manifest.

---

## 8. What the archive deliberately does not preserve

An exact-LAZ is a faithful container for the **store**, at the grid it was
built on. It is not a LiDAR archive. Every point in it sits at a voxel centre,
so:

**Coarsening works.** Re-voxelizing an exact export at any integer-multiple
coarser cell reproduces the geometry the original tile would have given -
measured 0.5 m -> 1.0 m: `_keys`, `_off`, `_zs`, `_ze` and `_cl` all identical
(0 of 811,201 differ), with only 1.15 % of per-interval *counts* drifting
because padding points parked at an interval base land in a different coarse
voxel than the originals did. That residual is a limitation of the `.npz`
itself - it stores per-interval totals, never per-voxel counts - so no
exporter could do better. Voxel centres nest cleanly: a centre sits at least
half a fine cell from every coarse boundary, against ~0.5 mm of quantization
error.

**Refining does not.** 0.5 m -> 0.25 m collapses to 44.5 % of the true occupied
voxels and to exactly the 0.5 m column count, because every point of a voxel
was snapped to one coordinate and lands in one of the eight sub-cells. The
output *looks* like data. Going finer requires the original IGN tiles.

Keeping an exact-LAZ archive instead of the source tiles is therefore a bet
that the grid geometry stays fixed. **0.5 m is a floor, not a lock**: coarser
is always available, finer never is.

---

## 9. Archive tooling choices

**Verification on by default.** Packing is an archival operation; correctness
beats speed. It roughly triples the per-shard cost (2.7 s export vs 7.0 s
rebuild on a median IGN tile) and is the only thing standing between a silent
grid mismatch and a deleted `.npz`.

**`--replace` with `--no-verify` is refused outright.** It is the one
combination that can lose data: deleting the only exact copy on the strength
of an unchecked conversion. Every other flag combination is merely slow or
wasteful.

**Atomic writes** (`_tmp_` prefix + `os.replace`) on both directions, matching
`sharding._atomic_shard_save`. A process killed mid-write must never leave a
truncated `.laz` that a later `unpack` would trust. A shard that fails
verification has its `.laz` deleted, so a failed pack leaves no plausible-
looking wrong file behind.

**Per-shard failures are recorded, not fatal.** One unreadable shard should
not abandon a five-hour pack; failures land in `archive_manifest.json` with
their error text and the CLI exits non-zero.

**`ProcessPoolExecutor` with module-level workers** - shards are independent,
so this is embarrassingly parallel, the same shape as the sharded runner's
per-tile children. The workers are module-level functions because that is what
pickling requires on Windows' spawn start method. `workers <= 1` runs in-process
so a debugger still works.

Spawn also re-imports the **caller's** `__main__`, so a script that calls
`pack(..., workers=N)` at import time re-runs itself in every worker and dies
with an opaque `BrokenProcessPool`. That is standard Python semantics, not
something the library can prevent - but it *can* be explained, so
`_run_parallel` catches it and re-raises naming the missing
`if __name__ == "__main__":` guard. (Found while writing the end-to-end
verification script, which had exactly that bug.) The `python -m
voxelizer.archive_cli` entry point is guarded and unaffected.

**The archive keeps a verbatim copy of `shards/manifest.json`.** `unpack`
restores it alongside the rebuilt shards, because the run's merge and
`--resume-shards` paths iterate it - shards without their manifest are inert.

**No streaming writer.** Expansion holds 25 B/point (3 x float64 + uint8):
0.43 GB for a median tile, the same order as `read_laz` itself. Fine per
shard, which is the only granularity the tooling works at. A merged area store
would be 1.86 GB at 74 M points; if that is ever needed, stream with
`laspy.open(...).append_points()`. Deferred deliberately rather than built
speculatively.

---

## 10. Measured cost

Real IGN tile `18340_51775.laz`, 17,359,133 points, 0.5 m / 0.5 m:

| artefact | size |
|---|---|
| original IGN `.laz` | 81.5 MB |
| shard `.npz` | 11.1 MB |
| exact `.laz` | 11.5 MB (**1.03x**) |

| operation | time |
|---|---|
| expand | 0.5 s (35 Mpts/s, 0.43 GB) |
| write `.laz` | 2.2 s |
| read back + re-voxelize | 7.0 s |
| *(reference)* re-voxelize the original tile | 8.8 s |

Padding duplicates cost ~ **0.02 bytes/point** - LAZ's predictor compresses
identical consecutive points to almost nothing - so archive size tracks
*interval* count far more than point count.

### The size ratio inverts with grid fineness

Measured across three real tiles of the 2,842-tile corpus x three LODs (see section 12), the
`.laz`/`.npz` ratio spans **0.29x to 1.65x**:

| tile | points | 1 m | 0.5 m | 0.25 xy / 0.1 z |
|---|---|---|---|---|
| `18410_51825` (largest of 2,842) | 60,937,237 | 1.65x | 1.17x | **0.65x** |
| `18385_51830` | 16,864 | 0.62x | 0.37x | **0.29x** |
| `18380_51835` (smallest) | 7,676 | 1.14x | 0.82x | **0.50x** |

The mechanism: a `.npz` costs roughly **4 bytes per interval** (3.60-4.00
measured on the largest tile), while the exact-LAZ's per-point cost is
resolution-dependent - 0.13 B/point at 1 m up to 1.1 B/point at 0.25/0.1 on
the same tile, because padding duplicates compress to almost nothing. Point
count is fixed by the source tile; interval count explodes as the grid
refines (the largest tile goes 1.34 M -> 5.67 M -> 25.9 M intervals). So the coarser and denser the grid, the more the `.npz`
wins; the finer and sparser, the more the `.laz` wins. At 0.25 m xy / 0.1 m z
the archive is 1.5-3.4x **smaller** than the store it encodes.

One measurement at one LOD on one tile does not characterise this: the
0.5 m dense-tile case taken alone suggests the archive "saves nothing",
which is true for that case and wrong everywhere else.

Whole Grand Lyon projection at 0.5 m (2,842 tiles, ~51.9 G points): ~34 GB of
archive against ~33 GB of npz shards and ~240 GB of source LAZ; ~17 min to pack
and ~44 min to unpack on 8 cores.

Reproduce with `tests/bench_laz_roundtrip.py <store.npz> --modes --source
<original.laz>`.

---

## 11. When this is worth using

| you want... | use |
|---|---|
| smallest thing that regenerates `area.npz`, coarse grid (>= 1 m) | measure it - at 1 m the ratio is tile-dependent, 0.62-1.65x across the three tiles of section 12 |
| smallest thing that regenerates `area.npz`, fine grid (<= 0.25 m) | **the archive** - 1.5-3.4x smaller than the shards |
| to hand voxel data to QGIS / CloudCompare / PDAL | the archive |
| an archive readable without this repository's code | the archive - `keys/off/zs/ze/cl/ct` means nothing to a stranger |
| coarser grids later (1 m, 2 m) | either; both coarsen faithfully |
| finer grids later (0.25 m from a 0.5 m store) | neither - keep or re-download the IGN tiles |

The archive is primarily an interoperability and longevity move. Whether it
also saves space depends on the LOD and on the tile (sections 10 and 12): at
0.25 m xy / 0.1 m z it was a clear win on all three measured tiles
(0.29-0.65x), at the project's 0.5 m default it ranged 0.37-1.17x, and at 1 m
it ranged 0.62-1.65x - so at the coarse end it can cost or save, and has to be
measured per run.

---

## 12. Real-tile parity matrix

Three tiles from the full 2,842-tile Grand Lyon corpus - the
largest and the two smallest - at three LODs. Every cell compares three
SHA-256 hashes of the saved `.npz`:

1. **baseline** - the `voxelizer` package extracted from git HEAD `2f7e77c`
   (pre-change) with `git archive`, run on its own `PYTHONPATH`;
2. **current** - the working tree, same call, same `origin=None` default;
3. **round trip** - current store -> `mode="exact"` `.laz` -> `store_from_laz`
   (grid recovered from the VLR) -> `.npz`.

`1 == 2` answers "does the new pipeline still produce the old bytes";
`2 == 3` answers "is the `.laz` interchangeable with the `.npz`". Both are
whole-file comparisons, not array comparisons. Each step ran in its own
subprocess so the 61 M-point tile could not carry memory across steps.

| tile | LOD | points | intervals | npz | exact laz | ratio | pipeline | round trip |
|---|---|---|---|---|---|---|---|---|
| `18410_51825` | 1 m | 60,937,237 | 1,335,554 | 4,889,113 B | 8,079,106 B | 1.65x | [x] | [x] |
| `18410_51825` | 0.5 m | 60,937,237 | 5,665,704 | 20,397,059 B | 23,804,938 B | 1.17x | [x] | [x] |
| `18410_51825` | 0.25/0.1 | 60,937,237 | 25,915,001 | 103,772,627 B | 67,286,444 B | 0.65x | [x] | [x] |
| `18380_51835` | 1 m | 7,676 | 648 | 4,327 B | 4,916 B | 1.14x | [x] | [x] |
| `18380_51835` | 0.5 m | 7,676 | 1,917 | 9,990 B | 8,214 B | 0.82x | [x] | [x] |
| `18380_51835` | 0.25/0.1 | 7,676 | 5,000 | 25,876 B | 12,826 B | 0.50x | [x] | [x] |
| `18385_51830` | 1 m | 16,864 | 1,631 | 9,221 B | 5,712 B | 0.62x | [x] | [x] |
| `18385_51830` | 0.5 m | 16,864 | 5,754 | 27,025 B | 10,065 B | 0.37x | [x] | [x] |
| `18385_51830` | 0.25/0.1 | 16,864 | 12,783 | 56,361 B | 16,469 B | 0.29x | [x] | [x] |

**27 hashes collapse to 9 distinct values - one per cell.** `stores_equal`
reported zero array differences everywhere.

Timings on the largest tile (single core): voxelize 32.7 s / 34.4 s / 43.6 s
for the three LODs; exact export 11.7 / 12.0 / 14.9 s; rebuild 19.9 / 24.1 /
40.3 s. The 0.25 m xy / 0.1 m z case puts 61 M points on a 2000 x 2000 column
grid spanning ~2,000 z-cells and produces 25.9 M intervals - the densest index
space the store has been exercised at, and it showed no int32 or memory edge.

### Shard mode: same property, different bytes

The matrix above used `voxelize_laz(origin=None)`. A sharded run does not: it
passes every tile a **shared area-wide origin** so all shards land on one
lattice, streams the tile in chunks, and may clip to the area bbox and filter
classes. Running the real `voxelizer.shard_worker` (the crash-isolation child a
sharded run spawns) on the same three tiles:

| tile | LOD | chunked == whole-file | shard == whole-file/`origin=None` | shard round trip |
|---|---|---|---|---|
| `18410_51825` | 0.5 m | [x] | [ ] (different origin) | [x] identical |
| `18380_51835` | 0.25/0.1 | [x] | [ ] (different origin) | [x] identical |
| `18385_51830` | 1 m | [x] | [ ] (different origin) | [x] identical |

Three things follow:

1. **`voxelize_laz_chunked` == `voxelize_laz`** on real tiles - the two-stage
   factoring claim in `voxelize.py`'s docstring, confirmed array for array
   (`--chunk-size 5000000` vs `--chunk-size 0`). This also covers the streamed
   path, which is the same reader.
2. **A shard is not byte-comparable to a whole-file store of the same tile**,
   and should not be expected to be: a different grid origin means different
   `ix/iy/iz`, hence different `_keys`/`_zs`/`_ze`. Nothing is wrong; they are
   two encodings on two lattices.
3. **Shards round-trip exactly**, including with `--clip` and
   `--keep-classes` applied (verified at all three LODs). That is the property
   the archive depends on, and it does not care about the origin - only that
   the store is a canonical RLE fixed point, which `_store_from_cells`
   guarantees on every path.

The practical corollary: **a shard is tied to its run's lattice.** Two runs
over different bboxes or different tile sets derive different `x0/y0` and a
different area-wide `z0`, so their shards are not interchangeable even for the
same tile. This is why the grid travels in the file's VLR and why
`shards/manifest.json` records the origin - and why `unpack` restores that
manifest verbatim.

Harness: `tests/bench_laz_roundtrip.py`.
