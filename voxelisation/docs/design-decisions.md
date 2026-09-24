# Design decisions - the why behind every choice

The code and the results matter less than the reasoning that produced them.
This file is that reasoning: for each debatable choice, what was picked, what
was rejected, why, and what the choice COSTS us (every real decision has a
price - a decision with no downside was not a decision). Claims trace to the
code, a test, or a recorded measurement; where a number was once wrong and
corrected, that is said, because the correction is part of the reasoning.

This file is one of three companion documents behind the internship report -
with `problem-solving.md` (every problem hit and how it was solved)
and `bibliography-and-references.md` (the source of record for every
citation). Where a choice here was forced by a failure, the failure's full
story is in the problem log; where it leans on the literature, the entry is
in the bibliography. The report argues the same choices at chapter level and
points back here for the detail.

Three constraints were FIXED at the first briefing and are not ours to defend
or attack, only to work within: no octrees, columns independent of their
neighbours, and a run-compressed vertical encoding. Where a choice follows
from one of these it is marked (constraint); everything else was open.

---

## 1. The macro choice: a run-length column store

### The alternatives, and why not each

| Representation | Why not |
|---|---|
| Dense 3-D array | A 3 x 3 km area at 0.5 m cubed is ~10^10 voxels, almost all empty air; storing air is the one thing a LiDAR representation must not do. |
| Octree | Excluded by constraint - but the constraint is defensible: an octree couples every node to its parent, so it cannot be built one column at a time, sharded across tiles, or merged by concatenation, all of which this project needs at metropolis scale. |
| Point cloud kept as-is | Answers no volumetric question ("what is above this point", "is this gap air or interior") without re-deriving structure on every query; the whole task is to impose that structure once. |
| Per-voxel sparse map (hash of occupied voxels) | Loses the vertical ADJACENCY that makes "walk up this column" and run-length compression cheap; a column is the natural unit because the questions are vertical. |
| **Run-length column store (chosen)** | Each (x, y) holds an ordered list of `(z_start, z_end, class, count)` intervals. Vertical questions are answered against the compressed form directly, columns are independent so the whole thing shards and merges trivially, and run-length turns a tall uniform wall or canopy into one record. |

### Why run-length at all, and what it costs

Urban vertical structure is piecewise-uniform: a wall is one class for many
metres, ground is one class, a canopy layer is one class. Run-length stores
the RUN, not the voxels, so a 20 m building wall is one interval, not 40. The
measured payoff is order-of-magnitude compression from the run structure
before any lossy encoding.

The cost is that fragmentation is the enemy: anything that breaks a run -
wall returns splitting a column, a misclassified voxel mid-wall, alternating
canopy classes - multiplies the interval count. This is why a wall-heavy tile
costs roughly twice the intervals per column of a mixed area at the same cell
size, and why the encoder family (Section 8) exists to rejoin runs that
fragmentation split.

### Why columns are INDEPENDENT (constraint), and what it costs

Independence means no column stores anything about its neighbours. The
benefit is total: the store is embarrassingly parallel to build, shards along
tile boundaries with no seams, and merges by concatenation. The cost is
equally real and we pay it openly: any question that is horizontal by nature -
the nadir neighbourhood check, morphological denoising, the ground-hole flood
fill - must RE-FIND its neighbours by binary search at query time, because
there is no adjacency stored to follow. The report states this cost as a
standing limitation rather than hiding it.

### Why intervals carry exactly four fields

`(z_start, z_end, class, count)`. The first three are the minimum that
describes "this run of this class occupies this vertical span". The fourth,
`count`, is the debatable one:

- **The case against storing count**: most of what the project reports -
  interval totals, payload sizes, top-class plan views, Jaccard overlaps - is
  computed WITHOUT counts. The encoders' one genuinely lossy behaviour is in
  the counts (a merge must pick one class's count for an overlapped voxel).
  So counts carry the project's only irreducible information loss for data
  many outputs never read.
- **The case for, which won**: counts are what make the store INVERTIBLE - the
  inverse voxelizer reconstructs `count` points per interval, closing the
  round trip to LAS/LAZ that validates the whole build - and they are what
  `denoise.min_points_filter` thresholds on to drop noise runs. Without counts
  the store could not audit itself and could not filter. The loss is
  acknowledged and measured (6.332 percent residual on a real store, a
  property of flattening overlaps, not a bug) and not avoided by dropping
  the field.

### Why sorted-by-key canonical form, not a hash map

Columns are stored sorted by their packed key so "find this column" is one
`np.searchsorted` and "give me all columns in order" is free. A hash map would
make single lookups O(1) but destroy the ordered bulk iteration that every
vectorised pass (build, encode, export, merge) depends on, and it would carry
Python-object overhead - the very thing the flat layout exists to kill.

---

## 2. Integer widths, dtype by dtype

The store is six flat arrays plus a seventh cache. Every width is a decision
between silent failure (too narrow) and wasted memory on the store's largest
arrays (too wide).

| Array | dtype | Why this width and not another |
|---|---|---|
| `_keys` | uint64 | One packed key per column: `(ix + 2^31) << 32 \| (iy + 2^31)`. Packed instead of two int32 arrays so "find a column" is one searchsorted over one array and the canonical order is one sort. The `2^31` offset maps SIGNED grid indices (see Section 4) into unsigned range while PRESERVING ORDER - a raw cast would sort -1 after +1 and corrupt every search. |
| `_off` | int64 | Per-column offsets into the interval arrays. int32 caps the store at 2^31 intervals; the whole-metropolis 0.5 m run holds 5.42 billion - past that cap by 2.5x. The ONE place 64 bits is a hard requirement, not caution. `merge_many` still guards each batch against the int32 limit because its scratch gather index is int32. |
| `_zs`, `_ze` | int32 | Vertical voxel indices. int16 fails SILENTLY: at `cell_z = 0.01` a real altitude quantises past 32,767 (a hypothetical finer-than-delivered resolution - nothing in the format forbids it, which is the point) and wraps to another VALID-looking index rather than crashing. int64 buys nothing - the worst plausible index is a few hundred thousand, ~10,000x below the int32 ceiling. |
| `_cl` | uint8 | ASPRS class codes are 0-255 by the LAS point-record specification; one byte is transcription, not choice. |
| `_ct` | int32 | Point count per interval. uint16 fails: a dense merged ground interval exceeds 65,535 points. int64 doubles the store's largest array for values that never approach 2^31 (the densest whole TILE is 60.9 M points). Enforced the hard way: an early path leaked int64 counts into saved stores, costing 17 B/interval on disk instead of 13; the constructor now casts and `load()` normalises old files. Not float, because counts are exact - the sole place fractional counts exist is the encoder conservation fix, which spreads a run's count over its voxels in float64 so the rebuild SUMS back to the exact integer. |
| `meta` | float64 x5 | Origin (x, y, z) and cell sizes. EPSG:3946 coordinates are ~10^6 metres with millimetre precision wanted; float32's ~7 significant digits sits exactly at that failure point, float64 is two orders clear. |

Per-column: 8 + 8 = 16 B. Per-interval: 4 + 4 + 1 + 4 = 13 B. Verified on a
fresh build-and-save of a real tile (`code_verification/exp04`).

### Why `z_end` is EXCLUSIVE

Half-open `[z_start, z_end)`: length is `ze - zs` with no +1, "touching" is
`ze == zs_next` with no off-by-one, and it matches Python slicing so the code
reads the way the language does. The price is that every docstring must SAY
"exclusive" - two audits found comments that had silently read it as inclusive
("highest occupied voxel index" for what is one past it). That price is why
the convention is now restated at each use, not assumed.

---

## 3. The 6 -> 7 array question

The store shipped with six arrays because six describe WHAT WAS MEASURED. The
seventh (`_gi`, one int32 per column: the ground index) was added because the
decoder asks a question the six cannot answer per voxel at acceptable cost -
"where is the terrain in this column?", the anchor that separates subsurface
from air from interior.

- **Why a seventh array, not a per-query scan**: classification runs per VOXEL
  in the ray walkers; re-finding the highest ground run at every voxel turns an
  O(1) lookup into a per-voxel loop on the hottest path.
- **Why lazy + cached, not built with the store**: only decoder-facing
  consumers (rays, transmittance, diagnostics) need it; build, encode, maps and
  export never touch it. Eager construction would tax every run for the benefit
  of some.
- **Why derived, not truth**: it is recomputable from the six real arrays, so
  it is a CACHE, persisted only as an optional entry - deleting it loses
  nothing.
- **Why the HIGHEST ground interval as the anchor, not the lowest or mean**:
  where a column holds several class-2 runs, the lower ones are bridge
  undersides, pits, or a steep surface split across voxels; the real terrain is
  the top one. Lowest would anchor to an artefact; a mean would sit in mid-air
  between two real surfaces. (The rarity of multi-ground columns is stated as
  expected-not-measured, because no artefact counts it.)
- **Why int16 first, int32 now**: int16 was "obviously enough" and was not - a
  fine-cell ground index overflows it and WRAPS to a valid-looking index rather
  than crashing. The audit that caught it widened the array; the lesson (silent
  wrap, not range, is the failure mode) is the same one that fixes `_zs`/`_ze`.
- **Why `NODATA = -32768`**: the int16 sentinel kept verbatim after widening -
  persisted stores already carry it and any impossible index serves; changing
  sentinels breaks old files for nothing.
- **Why `SUBSURFACE_BAND = 2`**: subsurface starts two voxels below the ground
  top, not immediately, because the exclusive end plus real terrain slope makes
  the voxel just under the ground run frequently legitimate measurement; two
  cells absorb quantisation and steep-surface splitting. Compared against the
  exclusive index, it leaves exactly one non-subsurface gap voxel below the
  terrain top - stated in `ground_index.py` so nobody "fixes" it to zero.

---

## 4. Origin and coordinates

- **Why a shared grid origin across tiles, not per-tile origins**: a shared
  origin is what makes area processing equal to voxelising the clipped union -
  two adjacent tiles land on the SAME lattice, so their columns merge by key
  with no resampling. Per-tile origins would force a reprojection at every seam.
  The cost is the next two points.
- **Why signed indices (negatives allowed), not shift-everything-positive**:
  with a shared origin, any tile west or south of it produces negative indices.
  We keep them signed (int32, and the `2^31` key offset) instead of choosing an
  origin guaranteed below-left of the whole metropolis, because that origin
  would push every index into the millions and waste range for a cosmetic "no
  negatives". Correctness never depends on sign - a later, lower tile simply
  voxelises to negative z, which the signed types handle.
- **Why origin = grid-aligned bbox corner + GLOBAL header z-minimum**: x/y come
  from the requested box snapped to the lattice; z comes from the lowest header
  z across ALL selected tiles so that no tile ever produces a negative z at
  build time and the datum is stable across an area. z is a REFERENCE, not a
  floor - correctness does not depend on which tile is first (verified) - but
  taking the global minimum keeps indices small and non-negative in the common
  case. Header z-floors of adjacent tiles differ by ~4 m median, up to ~150 m
  across the hilly corpus, which is why the datum is taken once,
  globally, and not per tile.

---

## 5. Resolution: the defaults question

This is the choice most worth interrogating, because three different cell
sizes appear in the project and they are three different KINDS of thing:

| Setting | Value | What it is |
|---|---|---|
| Code default | 0.5 m cubed | What `voxelize()`, the CLIs and the GUI use when the user chooses nothing. UNIFORM - there is no CLI-vs-elsewhere split. |
| Analysis recommendation | 1.0 m xy / 0.5 m z | The size-vs-fidelity sweet spot the encoder sweep identified for analysis PRODUCTS; a recommendation in the report, not a code default. |
| Fine probe | 0.25 m xy / 0.1 m z | The finest sweep setting, for neighbourhood-scale detail and the stress cases. |

- **Why one flat default and not resolution TIERED BY AREA SIZE** (the obvious
  alternative - coarse for big areas, fine for small): resolution is a SEMANTIC
  choice, not a capacity knob. Auto-tiering by bounding-box size would mean the
  same neighbourhood produces a DIFFERENT, non-comparable store depending on how
  large a box the user happened to draw, and two studies of the same place at
  different extents could no longer be compared. Capacity is handled ORTHOGONALLY
  instead: the pre-flight estimator predicts tiles, RAM and store size from
  headers alone and offers continue / downsample / shard, so a user voxelising
  the whole metropolis at fine cells is warned and sharded, not silently
  coarsened. The cost of this choice is worse out-of-box behaviour - a user who
  ignores the pre-flight warning and forces a huge fine run will pay in RAM - and
  that is the trade we accept for comparable stores.
- **Why the code default is the FINE 0.5 cubed, not the recommended 1.0/0.5**:
  a user who does not choose should get the safer artefact, and finer is safer -
  it discards less. The report then RECOMMENDS 1.0/0.5 for analysis because the
  sweep showed the coarser grid 3.3x smaller with every footprint and crown
  still surviving; the two are not in conflict, they answer different questions
  ("what if the user says nothing" vs "what should an analyst choose").
- **Why cubic defaults but an ANISOTROPIC fine probe (0.25 xy / 0.1 z)**: cubic
  cells make the storage-cost sweep clean (interval counts compare like for
  like). But vertical structure is what a volumetric model is FOR - separating
  understorey from canopy, floor from floor - so the fine probe spends its
  detail where it pays, finer in z (0.1) than xy (0.25); horizontal detail below
  0.25 m rarely changes a plantability answer.
- **Why non-power-of-two cells (0.1 m) are allowed despite a known artefact**:
  0.1 m is not dyadic, and the viewer's float32 codec reconstructs its z centres
  1 ULP (~10^-6 m) off the fill loop for non-dyadic cells. We allow it anyway: 10
  cm is the natural vertical unit, and a sub-micron rendering difference is
  physically meaningless, whereas forcing dyadic-only cells would trade a real
  physical choice for an invisible numerical tidiness.

---

## 6. The three-state decoder

The decoder returns the occupying CLASS CODE (0-255) or one of three negative
sentinels: `MEASURED_AIR = -1`, `OPAQUE_INTERIOR = -2`, `SUBSURFACE = -3`.

- **Why sentinels in the same integer channel, not an enum or a second return
  value**: every consumer already switches on the class code, so one comparison
  and numpy-friendly `state >= 0` ("occupied") beats tuple unpacking on the
  hottest path.
- **Why three states, not two**: "empty" conflates two opposite physical facts -
  air a beam demonstrably crossed, and volume no beam ever entered. A shadow ray
  must pass the first and stop at the second; collapse them and simulated light
  shines through buildings. Subsurface is the third because below-terrain volume
  is neither, and the plantability question needs it distinct.
- **Why the ground-less-column split (the correction this project made)**: a
  column with returns but no ground return - roof-only or canopy-only, a third to
  half of urban columns, measured on the real stores - is opaque BELOW its
  highest return, but ABOVE that return the beam that produced the return
  demonstrably passed. The decoder first called the whole column opaque; the
  real-data ray sweep caught the two walkers disagreeing above roofs, and the
  corrected rule is the one the ceiling optimisation's exactness proof requires
  (`code_verification/exp05`, `exp09`).
- **Why 4-neighbour nadir check, not 8**: the diagonals were weighed and rejected
  - two more column lookups per gap voxel on the hottest path for almost no
  change in which gaps get demoted.
- **Why a ground-less NEIGHBOUR is "no evidence", not a vote**: a column never
  penetrated cannot testify whether the beam here was blocked; counting it would
  bias the check toward measured columns over ignorant ones.
- **Why cross-class overlaps are KEPT, not resolved at build**: about 45 percent
  of adjacent interval pairs on a real store overlap in z with different classes
  (a wall return and a canopy return at the same height). The store keeps both
  because they are both real measurements; resolving to one class per voxel at
  build would bake in a choice the analyst might want to make differently, and it
  is what the optional `resolve` encoder does WHEN asked. Keeping
  overlaps is why `z_end` is not monotone within a column and why several
  routines must vector-check the ends, not probe one index - a real cost,
  paid to avoid a premature commitment.

---

## 7. Scale and processing

- **Why chunked reading is available alongside whole-file**: `laspy.read` loads a
  whole tile; a streamed `chunk_iterator` bounds the RAW-POINT buffer to one
  chunk. The two produce byte-identical stores (proven, `exp02`), so the pipeline
  picks the strategy on MEMORY grounds alone. Note the honest limit: only the
  raw-point buffer is bounded - the merged store still grows with the area, which
  is why sharding exists.
- **Why merged AND sharded area modes both exist**: merged keeps one growing store
  (simplest, fine while it fits RAM); sharded processes tile-by-tile to disk and
  merges in bounded batches (survives the metropolis). They are interchangeable in
  MEANING (area = clipped union, proven), so the choice is purely capacity.
- **Why per-block PROCESS isolation, not threads or in-process try/except**: the
  crashes that forced this were NATIVE faults (0xC0000005 access violations inside
  numpy/matplotlib C code) that kill the interpreter before any Python `except`
  can run. Only a separate process contains them, and only a subprocess boundary
  turns "the run dies at hour four" into "one block dies and the run resumes". The
  cost is subprocess launch and serialisation overhead per stage, accepted because
  a five-hour metropolis run cannot restart from zero.
- **Why pre-flight ESTIMATE, not run-and-OOM**: the estimator reads only headers
  and the tile inventory, so it predicts tile count, km^2, store size and peak RAM
  BEFORE a single point is read, and offers continue / downsample / shard. It was
  born from the 25.8 GB streamed-area incident where a box silently selected the
  entire 2,842-tile inventory. The cost is that the estimate is a model, not a measurement, and is
  scoped as such; the alternative (find out by exhausting RAM) is worse.

---

## 8. The encoder family

Five encoders (raw baseline, then v1 class smooth, v2 gap fill, v3 minor-class
drop, v4 majority) sit between the built store and analysis.

- **Why offer LOSSY encoders at all, in a project that prizes fidelity**: because
  fragmentation is the store's cost (Section 1) and some of it is noise, not
  signal - a single misclassified voxel splitting a wall into three intervals is
  pure overhead. The encoders rejoin runs, and each one's semantic cost is
  MEASURED per class (top-class disagreement, per-class Jaccard) and not
  assumed, so the analyst chooses with numbers in hand.
- **Why keep the REJECTED majority variant in the codebase**: it was the
  supervision-meeting idea, implemented to be measured, and measurement showed its
  plan-view vegetation footprint parting company with the raw one over 27 percent of
  their union at the 1.0 m grid (J_veg 0.731) and 18 percent at 0.5 m (J_veg 0.820).
  One minus a Jaccard index is a symmetric difference, not a destroyed fraction, and
  it covers the whole vegetation footprint, while the
  rule rewrites only the band above 5 m; what it establishes is that raw and encoded canopies disagree over a
  quarter of their union at the coarse grid, on the canopy the project exists to
  protect. It
  stays IN the tree, excluded from analysis but available for lightweight
  visualisation, because deleting it would erase the evidence for WHY it was
  rejected - a negative result is a result.
- **Why gap-fill's tolerance is expressed in metres (default 1 m), not voxels**:
  the physical question is "are these two runs the same object separated by a
  sub-metre gap"; a metres tolerance means the same physical decision at every
  resolution, whereas a voxel count would merge differently at 0.5 m than at 0.1 m
  for no physical reason.
- **Why measure semantic cost per CLASS, not as one number**: a single "loss"
  figure would hide that an encoder can be free for buildings and destructive for
  canopy. Per-class Jaccard is what exposed the majority variant's canopy damage;
  one number would have passed it.

---

## 9. Ray queries and transmittance

- **Why a per-voxel 3-D DDA reference walker, and why keep it when a faster one
  exists**: the reference steps one voxel at a time on all three axes - simple,
  obviously correct, and slow. The ceiling-bounded walker is the one used in
  anger, and it is validated by a DIFFERENTIAL test that asserts identical output
  ray-for-ray. Keeping the slow reference is the whole point: the fast one is only
  trustworthy because an independent, simpler implementation agrees with it. This
  caught the ground-less-column decoder bug (Section 6).
- **Why the ceiling bound is EXACT, not an approximation**: above the highest
  occupied voxel of a column and its neighbours, every gap is provably measured
  air (a theorem about the decoder, not a tolerance), so an upward ray may stop
  there with the SAME answer the full walk gives. An approximate early-out would
  trade correctness for speed; this trades nothing, which is why it can be the
  default, not an option.
- **Why Beer-Lambert transmittance, not radiative transfer or geometric-optical
  models**: richer canopy models (SAIL, Li-Strahler) need leaf-angle distributions
  and explicit crown geometry that LiDAR does not supply and the column store does
  not retain. Beer-Lambert needs only a per-class extinction coefficient
  and a path length, both of which the store HAS. Using a model whose inputs we
  cannot supply would be precision theatre.
- **Why accumulate PATH LENGTH, not voxel count**: counting voxels makes the
  answer depend on the grid - an oblique ray crosses more voxels than a vertical
  one through the same canopy thickness, and halving cell size doubles the count
  without changing the physics. Normalising the direction to unit length makes the
  parametric distance metres, so a 45-degree ray correctly accumulates sqrt(2)
  metres per metre of height. This is pinned by a resolution-invariance test that
  a voxel-counting implementation fails.
- **Why extinction coefficients are DERIVED from the corpus, not taken from
  literature**: a literature constant would make every downstream sun-hours figure
  an assumption wearing a decimal point. The corpus already measures canopy
  penetration (ground-return fraction against canopy thickness gives `k = -ln(p)/H`),
  so the coefficient is fitted from the data it will be applied to. The cost, stated
  openly: an estimator that counts RETURNS rather than PULSES counts a penetrating
  pulse more than once, which deflates the transmittance it reads and pushes the
  fitted k up, so the return-count values are an UPPER bound. The published
  coefficients are the per-pulse refit that replaced them
  (the archived campaign of 2026-08-08 (outside this delivery), run E10_per_pulse_refit_scopeA), and the fit stays
  resolution-dependent either way, so k is quoted per resolution.

---

## 10. Solar geometry

- **Why NOAA/Meeus, not NREL SPA / PSA / Michalsky**: the report's Section 4.4.4
  table is the full argument; in short, SPA's +/-0.0003 degree accuracy moves a
  shadow 0.5 mm at 100 m against a grid quantising at 100-1000 mm - precision far
  below the model's own discretisation, for ten times the code and a large periodic
  table. PSA and Michalsky match NOAA's accuracy but carry FITTED validity windows
  (1999-2015, 1950-2050), the wrong property for something meant to be re-run on
  future reflights.
- **Why grid convergence is computed NUMERICALLY, not by the closed form**: the
  closed forms invite sign and hemisphere errors that fail silently; a
  finite-difference step along grid north, its true bearing read off a geodesic
  engine, is checkable against a map. The convergence is quoted as
  position-dependent (1.28-1.41 degrees across the studied areas), not a fixed
  constant, because it genuinely varies with position.

---

## 11. Export, viewers, language

- **Why THREE viewer paths (self-contained HTML, PLY, streaming)**: they serve
  three audiences - a colleague who wants a file that opens in any browser with no
  install, a researcher who wants the geometry in CloudCompare/Blender, and a
  metropolis-scale store that no browser loads as one scene. One viewer cannot be
  all three; the shared geometry-collection pass means a fix benefits all.
- **Why 3D Tiles with GPU instancing, not triangulated geometry**: one shared
  unit-box mesh per class with per-instance translation/scale ships ~24 bytes of
  instance data per box instead of ~700 bytes of triangulated vertices - an
  order-of-magnitude cut in file size and export time. (The exact multiple was
  once quoted as "~10x" without measurement; it is now stated as an order of
  magnitude, since the triangulating exporter it would be compared against predates
  the repository.)
- **Why a parent's bounding volume is the UNION of its children's, not its slot in
  the tile grid**: the two are not the same box, and the LOD exporter originally used
  whichever was cheaper at each level - the nominal tile square for leaves, the tight
  extent of the coarsened records for interior nodes - which let a sparse leaf sit
  outside its own parent. 3D Tiles forbids that because a client may cull an entire
  subtree on the parent's box alone, so each node now unions its own extent with its
  children's on the way back up. The cost is real and small: containment forces
  slightly larger interior boxes (Run1's root y half-extent went 1256 -> 1300 m), i.e.
  a marginally more conservative frustum test in exchange for culling that cannot
  discard a visible descendant. Full story in `problem-solving.md` 5.7.
- **Why the LOD pyramid is built by a Morton-ordered cascade, not level buffers**:
  the build walks the leaves in Morton (Z-)order, whose defining property is that
  the descendants of EVERY quadtree node form one contiguous run at EVERY level.
  The moment the walk leaves a node's run that node is provably complete -
  concatenate its children, write its GLB, coarsen it 2x into its parent, free it -
  so peak memory is one open node per level, proportional to tree DEPTH and not to
  area. 50 m and 25 m leaves therefore cost the same peak as the default 100 m
  (measured: 6.14 / 6.09 GB at 100 / 50 m; a 25 m export, 11,780 leaves over 8
  levels, completes with its peak dominated by payload construction). Grouping the
  leaves by their immediate parent - the obvious simpler order - gives contiguity at
  exactly ONE level, and every level above it must then buffer the whole area's
  coarsened records, O(area) at fine tiles. Node content must not depend on the
  walk, so every part is tagged with its child's identity and sorted at flush; that
  is what makes the streamed construction byte-identical to a buffered one rather
  than merely equivalent. The cost is legibility: the walk is no longer the
  payload's band order, and reasoning about flush timing requires the Morton
  property - which is why the property is pinned by a test
  (`tests/test_tileset_streaming.py`) and not trusted from a comment. Rejected:
  buffering whole levels (the O(area) peak this exists to remove); streaming only
  the first interior level, as the code did before (halves the constant, keeps the
  scaling); a two-pass design that sizes every node before writing (a full extra
  read of the payload for information the Z-order walk yields for free). Full story
  in `problem-solving.md` 5.8.
- **Why `geometricError` is 8 x voxel size rather than the voxel size itself**: the
  ladder (leaves 0, then 4/8/16/32/64 for the 0.5/1/2/4/8 m levels) is `error_factor`
  = 8 times each level's voxel size. A node refines when `geometricError / metres-per-pixel`
  exceeds the client's threshold, and the thresholds in the wild are 16 (Cesium,
  Unreal) or 2 (the iTowns example). At `error_factor = 1.0` the root's error at
  street range works out to ~5 px against a default threshold of 16, so a
  standard client would leave even the ROOT unrefined - a blocky city that never
  sharpens. The factor keeps the ladder inside the range real clients react to, and
  makes the client threshold the tuning knob rather than the export (the measured
  sweep is recorded in the private workspace's delivery kit). Cost: the published
  numbers are not literal metres of error, so anyone reading `geometricError` that
  way must divide by 8.
- **Why Python, not a compiled hot loop**: the hot paths are VECTORISED numpy -
  the build is two sorts and a cumulative sum, the encoders are column-batched
  array ops - so the interpreter is never in the inner loop; the per-tile cost is
  dominated by decompressing the LAZ. A compiled rewrite would speed code that is
  not the bottleneck, at the cost of the readability and reproducibility a
  Master's deliverable needs.
- **Why a minimal dependency footprint (implement NOAA by hand rather than add
  pvlib)**: the report's Section 2.1 design constraint rules out adding libraries
  casually; no solar library is installed, and the NOAA algorithm is small enough
  to implement, read and verify against spherical astronomy without one. Each avoided
  dependency is one fewer thing to pin, audit and reproduce.
- **Why laszip is PINNED as the LAZ backend**: `lazrs_parallel` corrupted memory on
  large IGN tiles; single-thread `lazrs` failed nondeterministically; laszip
  survived 25+ stress reads clean. lazrs is retained only as a second-opinion
  decoder for corruption triage. The once-quoted "deterministic panic on tile
  18340_51775" was later traced to a bit-corrupted LOCAL copy - the published tile
  was fine - which is why the pin's justification names the memory corruption, not
  that tile.

---

## 12. Method choices

- **Why differential and equivalence testing carry more weight than unit tests**:
  three properties - chunked equals whole-file, area equals clipped union, fast
  walker equals reference - LICENSE the architecture's flexibility. They are why
  the pipeline may pick a reading strategy, an area mode, or the fast walker freely:
  each is proven to mean the same thing as its simpler sibling. A unit test checks
  a case; an equivalence property checks a substitution.
- **Why every sampled experiment hardcodes its RNG seed**: a "random" result
  without a seed is an unrepeatable claim. Every hold-out split, probe sample and
  sun-hours subset fixes one literal seed so the run reproduces bit for bit and any
  quoted figure can be re-derived.
