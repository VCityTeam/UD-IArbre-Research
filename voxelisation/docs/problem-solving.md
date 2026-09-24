# problem-solving.md - every problem this pipeline hit, and how it was solved

One entry per problem, in pipeline order (data, structure, algorithms,
memory and scale, visualisation and export, engine integration, environment
traps). Each entry answers the same six questions: what was the issue, what
side effects it had, how it was approached, what solution was chosen, what
we gain versus lose with it, and what other solutions exist.

This is the catalogue of what went wrong and how each problem was tackled,
and one of three companion documents behind the internship report - with
`design-decisions.md` (why each design choice was made, with alternatives
and costs) and `bibliography-and-references.md` (every source, with
per-section traceability). The report argues the same material at design
level; this file is the engineering record behind it. The 3-D Tiles serving
and client-integration specifics were recorded in the delivery kit of the
private workspace and are summarised in section 5 below.

---

## 1. Data and geodesy

### 1.1 Two dataset vintages, one set of comments (tile size, CRS)

- **Issue.** The first experiments ran on the 2018 acquisition, delivered
  as 1 km tiles in Lambert-93 (EPSG:2154). The project then moved to the
  Grand Lyon 2023 delivery: 500 x 500 m tiles, hectometre-named
  (18410_51825 = SW corner 1 841 000 / 5 182 500), in RGF93 / CC46
  (EPSG:3946). Comments and notes written for the first dataset outlived
  the switch and read as facts about the second.
- **Side effects.** A wrong assumed tile pitch misplaces tiles during
  area selection; a wrong CRS assumption would quantize coordinates in
  the wrong frame; documents disagreed with each other until the
  alignment pass.
- **Approach.** Treat the LAZ headers as the only authority: verify tile
  extent, naming convention and CRS empirically for every dataset the
  pipeline touches, and never carry assumptions across acquisitions.
- **Solution.** Header-verified constants for the 2023 delivery (tile
  500 m, naming unit 100 m); `io_laz._validate_crs()` accepts exactly
  {3946, 2154}, covering both vintages, and rejects tiles whose header
  CRS is missing or anything else; a documentation alignment pass
  retired the leftover 2018-era statements.
- **Gain vs lose.** Gain: a change of dataset vintage can no longer
  silently invalidate assumptions; documentation matches the data in
  use. Lose: tiles from a new producer or acquisition need their CRS
  added to the allow-list first.
- **Alternatives.** Reproject on the fly with pyproj (rejected: silent
  reprojection hides data problems and costs time on every read).

### 1.2 NaN/inf coordinates poisoned the shared grid origin

- **Issue.** One tile contained non-finite points; `min()` over
  coordinates returned NaN, which became the area's shared origin and
  corrupted every voxel index after it.
- **Side effects.** Downstream failures far from the cause, with no
  obvious link back to the bad input tile.
- **Approach.** Trace the absurd indices back to the origin computation,
  then to the input.
- **Solution.** `np.isfinite` validation before quantization; bad points
  are dropped with a warning before any origin math.
- **Gain vs lose.** Gain: one cheap vectorised check removes a whole
  failure class. Lose: silently dropping points could mask a broken tile;
  the warning count mitigates that.
- **Alternatives.** `np.nanmin` (treats symptoms, keeps bad points);
  hard-fail on any non-finite point (safer but makes one broken point
  kill an area run).

### 1.3 Class 8: an undocumented class in the deliveries

- **Issue.** LAS 1.4 reserves code 8, yet the Grand Lyon tiles use it
  heavily; returns cluster 15 to 25 m above ground, above class 5.
- **Side effects.** Rendered grey as "class_8", it fragmented vegetation
  columns (5/8 alternation breaks RLE runs) and confused every map.
- **Approach.** Inspect where class-8 points actually sit relative to
  class 5 across tiles; recognise the TerraScan upper-canopy convention.
- **Solution.** Named `high_vegetation_upper`, given a proper colour, and
  included in `VEGETATION_CLASSES = {3,4,5,8}` so vegetation logic and
  vegetation-only exports treat it as canopy.
- **Gain vs lose.** Gain: correct semantics and less RLE fragmentation in
  canopy. Lose: a de facto convention is baked in; a future delivery that
  uses code 8 differently would need this revisited.
- **Alternatives.** Drop class 8 (loses a quarter of the canopy);
  remap 8 into 5 at read time (loses the stratification information).

### 1.4 A bit-corrupted local tile copy that passed verification (tile 160)

- **Issue.** tile `18340_51775.laz` (tile 160 of 2,842, in a local working copy outside this delivery)
  decoded its first 14,339,522 points perfectly, then every remaining point
  (3.02 M, 17.4 % of the file) came out with coordinates in the
  tens-of-millions-of-metres range - values sitting at +/-int32-max x the
  0.01 m scale factor, i.e. garbage bytes decoded as coordinates. The file
  had the *exact same size* as the clean original (bit-level damage, not
  truncation), and the existing verification pass (`tests/verify_laz.py`)
  had marked it OK, because its check was "does `chunk_iterator()` run to
  completion", which never compares decoded coordinates to the header bbox.
- **Side effects.** All three whole-Lyon shard builds logged out-of-frame
  column drops at tile 160; the affected shards contained the bad points
  until reprocessed; the mosaic safety net protected only the preview
  PNGs, not the persisted data.
- **Approach.** Treat "which layer is broken?" as the question: decoder,
  library, file, or source. Three independent decoders (Laszip C++,
  single-thread Lazrs, LazrsParallel) were run against the same bytes; all
  three failed consistently (two identically-corrupted outputs, one panic),
  which exonerates the decoders. Then hash-compare against the copy archived outside this delivery
  copy and a fresh re-download from the Grand Lyon source.
- **Solution.** The archived copy and the fresh download are byte-identical to
  each other and different from the local mirror copy: a bad local copy,
  introduced by whatever populated that working copy. Replaced the file with the
  verified-clean copy, deleted the tainted shards, and let
  `--resume-shards` reprocess only that tile in each run. Full record with
  hashes: `tests/T160/REPORT.md`.
- **Gain vs lose.** Gain: the whole-Lyon stores are clean, and the
  investigation produced a reusable playbook (multi-decoder comparison +
  hash triangulation against independent copies). Lose: `verify_laz.py`'s
  blind spot remains - "decodes without raising" is not "decodes to sane
  coordinates" - so a bbox-sanity check belongs in any future verification
  pass.
- **Alternatives.** Truncate to the clean 14.3 M-point prefix (loses 17 %
  of a tile); skip the tile (loses 100 %); both rejected once a clean copy
  was proven to exist.

## 2. The core structure

### 2.1 A dense 3-D grid cannot hold a city

- **Issue.** The naive `grid[ix][iy][iz]` for a 500 m tile at 1 m x 0.5 m
  is on the order of fifty million cells, almost all air; merged
  areas multiply that by the tile count.
- **Side effects.** None in practice, because it was never built; it was
  the rejected baseline that shaped everything else.
- **Approach.** Evaluate the alternatives systematically (octree, OpenVDB,
  brick map, SDF, implicit) under the supervisors' constraints
  (no octrees, neighbour-agnostic, run-compressed vertical encoding).
- **Solution.** The RLE column store: sparse columns, each an ordered list
  of (z_start, z_end, class, count) intervals.
- **Gain vs lose.** Gain: 10 to 40x fewer elements than points, vertical
  queries are native, columns merge by concatenation. Lose: horizontal
  adjacency must be recomputed when needed; true 3-D neighbourhood
  operations are more work than in a dense grid.
- **Alternatives.** Documented in the report's state of the art; OpenVDB
  remains the serious contender if per-voxel (rather than per-run) values
  ever become the dominant need.

### 2.2 The dict-of-Columns store collapsed at scale

- **Issue.** The first store was `dict[(ix,iy) -> Column]` with per-column
  Python objects: ~874 bytes of overhead per occupied column.
- **Side effects.** ~3 GB of pure overhead per fine-resolution tile;
  consistent out-of-memory failures on areas; every per-column loop was
  a Python loop.
- **Approach.** Profile, identify object overhead as the cost, redesign
  storage rather than optimise around it.
- **Solution.** Struct of arrays: six flat numpy vectors (packed uint64
  keys, int64 offsets, int32 z-bounds, uint8 classes, int32 counts);
  16 bytes per column plus 13 per interval - a measured 16x less, not the
  30 to 50x this entry once claimed (a list of `Interval`
  dataclasses costs 209 B each by `tracemalloc`, against 13 B for the numpy
  arrays; an earlier `sys.getsizeof` estimate of ~450 B double-counted the
  shared-key instance dict that CPython 3.3 and later amortise across
  instances).
- **Gain vs lose.** Gain: the memory that makes areas possible; every
  algorithm becomes vectorised; persistence is trivial (one file per
  array, memory-mappable). Lose: less idiomatic code; inserting into the
  middle of flat arrays is awkward (the pipeline appends and rebuilds
  instead).
- **Alternatives.** Keep dicts and shard harder (still pays object
  overhead); a C++ core (rejected: build-chain cost, reproducibility).
- **Later refinement.** An early build path let int64 point counts leak
  into saved stores, silently costing 17 bytes per interval on disk
  instead of the designed 13. The constructor now casts to int32 and
  `load()` normalises legacy files; a fresh build-and-save round trip is
  verified at exactly 16 + 13 bytes (`code_verification/exp04`).

### 2.3 Mixed-class voxels: who wins the voxel?

- **Issue.** One voxel can receive returns of several classes (wall +
  vegetation). Voting at build time destroys information.
- **Side effects.** If voted eagerly: unrecoverable loss; if never
  resolved: overlapping intervals that double-count volume in naive
  consumers.
- **Approach.** Separate measurement from interpretation.
- **Solution.** The build is conservative (both classes survive as
  overlapping cells); an explicit `resolve()` pass collapses overlaps by
  near-tie-aware majority vote with proportional count attribution, only
  when a single-label store is wanted.
- **Gain vs lose.** Gain: the raw store stays a lossless record; every
  simplification is a measured choice. Lose: consumers must know whether
  they hold a raw or resolved store; slightly larger raw stores.
- **Alternatives.** Always-vote (simpler, lossy); store class histograms
  per voxel (heavier, closer to Aljumaily's feature approach).

### 2.4 Wall returns shred columns into confetti

- **Issue.** LiDAR returns on vertical surfaces (facades, trunks) create
  many short same-column runs. At the same cell size a wall-heavy tile
  costs roughly TWICE the intervals per column of a mixed area
  (`design-decisions.md` section 1) - that 2x is the defensible figure.
  The often-quoted 13.7 intervals per column of tile 18410_51825 is not
  the other half of that comparison: it is a single dense tile at 1.0 m /
  0.5 m divided by the FULL 500 x 500 grid, whereas the sweep's 2.3-2.8
  area averages are per occupied column over 3 x 3 km, so the two are not
  like for like and their ratio is not the fragmentation cost.
- **Side effects.** Larger stores, more 3-D boxes than the eye needs,
  misleading "complexity" in diagnostics.
- **Approach.** Quantify first (per-column diagnostics, interval
  histograms), then offer consolidation as explicit options.
- **Solution.** Two levels: the measured encoder family (v1 class smooth,
  v2 gap fill, v3 minor drop, v4 majority, each benchmarked with
  Jaccard / top-class-disagreement in `Experiments/`) and the production
  `--group-intervals` (default on) that merges same-class runs before
  outputs; the raw store is preserved alongside.
- **Gain vs lose.** Gain: in the 3 x 3 km production sweep (the eight
  archived runs of report Table 5), `--group-intervals` removed 13 percent
  of the intervals at 1.0 m cubed and 41 percent at 0.25 m / 0.1 m;
  honest measurement of what each simplification costs
  (v4 was rejected for analysis after showing a 27 percent elevated
  vegetation loss). Lose: grouped stores bridge real vertical gaps, so
  gap-faithful analyses must use the raw store.
- **Alternatives.** Fix the classification upstream (out of scope);
  smooth at render time only (leaves the storage cost).

## 3. Algorithms and semantics

### 3.1 Three kinds of "empty" (air, building interior, underground)

- **Issue.** A gap voxel can be measured air (a beam crossed it), opaque
  interior (under a roof, never measured) or subsurface (below ground);
  conflating them breaks shadow and plantability physics.
- **Side effects.** Treating interiors as air lets light shine through
  buildings; treating air as unknown makes shadow maps useless.
- **Approach.** Anchor emptiness on the one thing a penetrated column
  proves: a ground return means a beam traversed the column.
- **Solution.** Per-column ground index (top of the highest class-2
  interval; NODATA when absent) plus the three-way decoder
  (MEASURED_AIR / OPAQUE_INTERIOR / SUBSURFACE); a NODATA column is
  opaque interior below its highest return (unknown is not air) and
  measured air above it - the beam that produced the return came down
  through that region (see 3.5 for how the original all-opaque rule was
  caught and corrected); optional flood-fill interpolation restores a
  terrain estimate under roofs for subsurface bookkeeping.
- **Gain vs lose.** Gain: physically meaningful emptiness at ~2 bytes per
  column. Lose: heuristic ground anchoring inherits classification
  errors (bridge undersides mislabelled as ground are handled by the
  highest-interval rule, not solved).
- **Alternatives.** Full ray casting from scanner trajectories (exact,
  but trajectories are not in the delivery); treat all gaps as air
  (simple, wrong at every building).

### 3.2 The nadir-beam assumption leaks light beside towers

- **Issue.** The decoder assumes beams travel vertically, but the scanner
  fires up to **30 degrees** off nadir, an envelope read off the
  scan-angle field of all 2,842 tiles, not off a datasheet; air
  traversed by an oblique beam is attributed to the ground column, up to
  **~0.58 m** of lateral drift per metre of height (tan 30 deg = 0.5774).
- **Side effects.** Phantom "measured air" shafts beside tall buildings;
  light leaks into shadow zones; sky-view factors biased along facades.
- **Approach.** Quantify the drift geometrically, then mitigate where it
  shows. The original bound rested on an argument that flat-ground
  returns come overwhelmingly from near-nadir beams, supported by a
  per-tile mean scan angle of -0.1 deg median and 0.0 deg mean. That
  support was illusory: the scan pattern is symmetric, so signed angles
  cancel to about zero however oblique the individual beams are, and the
  signed mean can say nothing about obliquity. A per-point pass over four
  adjacent tiles (52,705,942 points, 211,221 building columns) measured
  the quantity that matters, the absolute angle, and does not support the
  argument. Ground returns one to six cells from a building average
  **12.87 deg** off nadir against 13.17 deg beyond forty cells, a
  difference of the expected sign and no practical size, and **74 % of
  the ground returns immediately beside a building arrive more than
  5 degrees off nadir**, so the plus-or-minus-5-degree figure the design
  notes carried is not supported either.
- **Solution.** A neighbourhood check in the decoder: a gap in a
  penetrated column is air unless every penetrated orthogonal neighbour
  is building at that height (a neighbour with no data cannot testify). Runs at query time; the store stays
  neighbour-agnostic. The mitigation is unaffected by the measurement
  above, because it never depended on beams being near-nadir; what the
  measurement changes is the size of the residual it leaves.
- **Gain vs lose.** Gain: removes the visible artefact without touching
  the build. Lose: a residual uncertainty beside tall buildings remains
  and is stated as a limitation, not solved. The same pass sizes
  it as a distribution, not a column count: the suspect band is
  the local building height times the tangent of the incidence angle,
  1.00 m at the median, 2.46 m at p90 and 3.67 m at p99, roughly 0.45
  times the building height. The three-to-four-column rule of thumb the
  design notes used describes the low buildings of that square kilometre
  (median height 4.50 m, maximum 14.00 m) and does not carry to tall
  ones.
- **Alternatives.** Per-beam ray replay from trajectory files (exact,
  unavailable); dilating buildings by one column (blunt, damages open
  streets).

### 3.3 Vegetation "inside" buildings

- **Issue.** Facade returns and misclassification put vegetation runs
  vertically sandwiched and horizontally enclosed by building.
- **Side effects.** Trees apparently growing through roofs; vegetation
  statistics inflated inside building envelopes.
- **Approach.** Batch reclassification with explicit escape rules rather
  than build-time guessing.
- **Solution.** `absorb.py`: absorb short noise runs immediately; absorb
  when >= 75 percent of the PENETRATED 8-neighbours are building at that
  height;
  keep when two or more cardinal directions are open (courtyards and
  roof gardens survive).
- **Gain vs lose.** Gain: cleaner buildings without losing legitimate
  urban greenery. Lose: thresholds are heuristics; exotic architecture
  can still fool them.
- **Alternatives.** Footprint polygons from BD TOPO to mask buildings
  (extra dependency, misses unmapped structures).

### 3.4 Whole-file reading versus memory

- **Issue.** Reading a 60 M point tile at once costs GBs before
  voxelization even starts.
- **Side effects.** Area runs multiplied that peak by concurrency of
  reads and merges.
- **Approach.** Make reading strategy a memory decision, not a
  correctness decision.
- **Solution.** A chunked reader (`read_laz_chunks`) that voxelizes
  incrementally; the chunked and whole-file paths are verified in tests
  to produce identical stores.
- **Gain vs lose.** Gain: bounded read memory, freedom for the area
  pipeline to choose per situation. Lose: chunk concatenation still
  materialises the tile's unique-cell set (a true constant-memory
  streamer is listed as future work).
- **Alternatives.** Streaming accumulator that folds chunks into a
  running per-voxel structure (deferred: current peak is comfortable for
  500 m tiles).

### 3.5 Ground-less columns decoded as opaque sky

- **Issue.** A column with returns but no ground return - roof-only or
  canopy-only, which is a third to half of urban columns on the real
  stores - was classified opaque interior at EVERY height, including the
  open sky above its top return, through which the beam that produced
  that return demonstrably passed.
- **Side effects.** The reference ray walker and the ceiling-bounded
  walker disagreed on real stores (99 of 1,200 sampled rays at 1.0 m)
  while agreeing perfectly on the synthetic test scenes, whose generator
  gave every column a ground interval - a test-fixture blind spot; oblique
  sun rays phantom-hit above roofs and canopy, depressing every sun-hours
  figure (the built-up area's mean was suppressed by nearly four hours)
  and the transmittance probe.
- **Approach.** Re-verify the unit-proven equivalence on REAL data: sweep
  random rays over the production stores comparing the two walkers, then
  probe the first disagreeing voxel decoder-call by decoder-call.
- **Solution.** The decoder's NODATA branch now splits at the column's
  highest return: opaque interior below it, measured air at and above it
  (the same nadir reasoning penetrated columns already used). Unit tests
  with roof-only columns pin the rule; the real-store sweep and a direct
  per-voxel premise check both run clean
  (`code_verification/exp05`, `exp09`), and the sun-hours and
  transmittance experiments were re-run under the corrected decoder, the
  refreshed artefacts becoming the numbers of record.
- **Gain vs lose.** Gain: the walkers provably agree on real data, the
  ceiling optimisation's exactness proof actually holds, and the
  headline sun-hours result strengthened. Lose: every figure measured
  under the old rule had to be re-derived and replaced - the cost of a
  fixture that was too tidy to catch the case.
- **Alternatives.** Keep the conservative all-opaque rule (physically
  wrong above the top return, and it breaks the walker-equivalence
  contract); disable the ceiling shortcut instead (treats the symptom
  and forfeits the speed-up that makes shadow work affordable).

## 4. Memory and scale

### 4.1 Areas larger than RAM

- **Issue.** A merged fine-resolution area store exceeds workstation RAM
  (the 3 x 3 km sweep peaks at 116.5 M columns).
- **Side effects.** OOM kills after long compute; users could not predict
  failure before starting.
- **Approach.** Predict before running; bound instead of hoping.
- **Solution.** Three cooperating mechanisms: pre-flight estimation from
  headers only (predicts store size and peak RSS, proposes coarsen or
  shard); a RAM watchdog that aborts cleanly with partial outputs; and
  sharded mode (one store per tile, streaming statistic folds, bounded
  mosaic rendering, region loader) with peak memory equal to one tile.
- **Gain vs lose.** Gain: any area fits; failures became choices.
  Lose: sharded deliverables need a merge step for whole-area stores;
  more code paths to test (the equivalence tests cover them).
- **Alternatives.** A database-backed store (SQLite like Gorte's engine;
  rejected for dependency and speed); simply buying RAM (does not scale
  to the metropolis).
- **Hardened after the full-Lyon crash.** After that crash, sharded runs
  gained `--resume-shards` (reuse validated existing shards, adopt the
  interrupted run's z floor, guarded by `shards/run_config.json`),
  `--isolate-tiles` (per-tile child process via `shard_worker.py`;
  atomic shard writes; failures recorded in `failed_tiles.json` and the
  run continues) and `--retry-lazrs` (one second-opinion decode after a
  child crash). A 2,842-tile run can now lose at most one tile to a
  native decoder crash and never repeats finished work.

### 4.2 The streaming-viewer crash (the defining incident)

- **Issue.** Exporting the 317 M interval raw store for 3-D viewing
  crashed a 32 GB workstation: the geometry collector materialised
  116.5 M column keys as Python tuples (~13 GB), then sorted them into a
  second copy (~26 GB peak); a second flaw packed the whole 10.1 GB
  binary payload in memory; and even a successful export would have made
  browsers download 10.1 GB per page load.
- **Side effects.** Hours-long runs died at the visualisation stage;
  a misleading "warm cache" retry looked like progress.
- **Approach.** Post-mortem with the diagnostics monitor; fact-check the
  failure line by line; fix the principle violation (bulk data left
  numpy), not the symptom.
- **Solution.** Keys stay in their packed uint64 array end to end
  (0.9 GB for the same store); packing became chunked; the exporter
  writes tile-banded payloads and the viewer treats the instance budget
  as a GPU working set with impostor silhouettes for far tiles. The same
  store now streams end to end with a stage peak below 5 GB.
- **Gain vs lose.** Gain: city-scale interactive viewing; the design
  principle ("no step holds the whole city") now enforced in the last
  place it was violated. Lose: viewer complexity (spatial index, LOD
  logic) and a server requirement for range requests.
- **Alternatives.** Pre-decimating exports only (loses close-up detail);
  3D Tiles for everything (heavier toolchain; eventually done too, see
  5.2).

### 4.3 One crashing stage killed whole runs

- **Issue.** Statistics, maps, diagnostics and 3-D export ran in one
  process; a segfault in any output stage destroyed the run.
- **Side effects.** Multi-hour voxelizations lost to a rendering bug.
- **Approach.** Isolate failure domains along the natural seam: every
  stage consumes only the persisted store.
- **Solution.** Stage isolation: the store is saved as a raw
  memory-mappable directory; each stage runs in its own child process;
  `--resume-from-store` reruns output stages without re-voxelizing.
- **Gain vs lose.** Gain: crash containment, cheap reruns, per-stage
  wall/RSS accounting (recorded per-stage in each run's `stages/` tree;
  the `TestOutputs` runs themselves are not shipped). Lose:
  process startup overhead and a saved-store requirement.
- **Alternatives.** try/except in-process (does not survive native
  crashes); a workflow engine like Argo (right for the platform, heavy
  for a workstation tool).

### 4.4 The shard merge was quadratic in the tile count

- **Issue.** `ColumnStore.merge_many` was an incremental `reduce(merge)`:
  each of n shards re-concatenated and re-sorted the whole growing
  accumulator, O(n x N) overall. Harmless at 36 tiles; at 2,842 whole-Lyon
  shards it turned a minutes-long merge into hours.
- **Side effects.** The merge phase, not voxelization, became the wall-clock
  bottleneck of metropolis runs; the "possible future improvements" note
  had flagged the batch form but it was never built.
- **Approach.** Replace the fold with a single pass over all inputs, and
  demand bit-identity with the old form as the acceptance bar, including
  the tempting-to-break case where the same column appears in several
  stores.
- **Solution.** One-pass batch merge: concatenate every store's flat
  arrays, one argsort over the combined columns, one gather;
  `tests/test_merge_many.py` (12 tests) pins byte-identity against the
  incremental form. Measured at metropolis scale: 2,842 shards load in
  ~20 s and merge in ~22 s into 162.6 M columns / 426.9 M intervals.
- **Gain vs lose.** Gain: whole-Lyon merges are interactive-scale; the
  incremental path remains as the reference implementation in tests.
  Lose: peak memory during the batch concatenation is the sum of inputs
  plus the output (acceptable because the merge phase is opt-in and
  preceded by the pre-flight estimate).
- **Alternatives.** K-way streaming merge on sorted keys (lower peak
  memory, more code); keeping the incremental fold with periodic
  consolidation (still superlinear).

## 5. Visualisation and export

### 5.1 Browsers cannot draw hundreds of millions of boxes

- **Issue.** Even a healthy exporter cannot hand a browser 187 M
  instances; engines have hard caps (CesiumJS allocates a pick ID per
  instance into a JS Map capped at ~16.7 M entries).
- **Side effects.** "Crashes after ~97 tiles", RangeError floods, holes
  in the model (see tilesexport troubleshooting).
- **Approach.** Give every viewer a way to show less until it needs more.
- **Solution.** Three-tier viewing: auto-thinned single-file HTML for
  tiles; the working-set streaming viewer for areas; and the 3D Tiles
  LOD pyramid (downsampled interior nodes, geometricError cascade) for
  standard clients, keeping the resident set to a few coarse tiles plus
  nearby leaves.
- **Gain vs lose.** Gain: every scale has a working viewer; whole-area
  first paint is a ~10 MB root fetch. Lose: three code paths; pyramid
  export time and disk (interior nodes are real geometry).
- **Alternatives.** Greedy meshing to cut instance counts (roadmapped);
  point-based rendering (loses the voxel semantics the project is about).

### 5.2 The tipped city (glTF Y-up vs Z-up)

- **Issue.** 3D Tiles runtimes rotate glTF content +90 degrees about X
  (Y-up convention); our Z-up GLBs rendered tipped over in every client.
- **Side effects.** The city appeared as a tilted sheet; content displaced
  from bounding volumes; initially looked like a georeferencing bug.
- **Approach.** Read the spec corner case instead of compensating
  downstream.
- **Solution.** Every GLB wraps content in a `zup_to_yup` node with a
  baked -90 degree X rotation that the runtime's +90 cancels exactly.
- **Gain vs lose.** Gain: upright rendering in all clients from one fix
  at the source. Lose: single GLBs opened in isolation in a plain glTF
  viewer look rotated (they are not the consumption path).
- **Alternatives.** Rotating in each viewer (n fixes instead of one);
  authoring geometry Y-up throughout (fights every geospatial
  convention upstream).

### 5.3 Georeferencing and the 49.70 m height offset

- **Issue.** Two-part problem: placing a CC46-metre model on the globe at
  all, and the model sitting ~50 m below real terrain when combined with
  Cesium World Terrain.
- **Side effects.** Without the transform, viewers put the model at the
  origin of nowhere; with terrain, the city floats underground.
- **Approach.** Compute the Earth transform from the native CRS; identify
  the residual as geoid vs ellipsoid height (LiDAR altitudes are
  NGF-IGN69 orthometric).
- **Solution.** `root.transform` built via pyproj (East-North-Up frame at
  the origin, capturing Lambert grid convergence); the offset is a precise
  +49.70 m near Lyon computed from the IGN RAF geoid grid via pyproj
  (superseding an earlier ~48 m estimate eyeballed against Cesium World
  Terrain); the exporter now applies this correction automatically (the
  troubleshooting notes of the private workspace's delivery kit record the
  investigation), with a manual per-viewer height nudge
  only needed for `--no-geoid`/offline exports.
- **Gain vs lose.** Gain: the model lands at true Lyon coordinates in
  every client with no manual placement. Lose: `--no-geoid`/offline exports
  still carry the unfixed (documented) offset.
- **Alternatives.** Baking the geoid correction via pyproj + RAF grid
  (done); manual placement per viewer (rejected).

### 5.4 Unreal Editor out-of-memory (34 GiB)

- **Issue.** Loading the tileset in UE with defaults consumed 34 GiB and
  killed the editor.
- **Side effects.** Lost sessions; the impression that UE cannot handle
  the data (it can).
- **Approach.** Bisect the settings that multiply memory.
- **Solution.** Four rules: physics meshes off
  unless needed; Maximum Screen Space Error 48 instead of 16; camera
  above the model when the tileset loads; verify the Georeference
  property is set (else the plugin silently targets its Denver default).
  Stable at 8.6 GiB under these rules.
- **Gain vs lose.** Gain: reliable editor sessions with no data change.
  Lose: no gameplay collision by default; slightly softer distant detail.
- **Alternatives.** Smaller region tilesets for physics work (the E7
  experiment of the private workspace); greedy meshing (roadmap).

### 5.5 Stable payload names made long-lived caching a stale-data trap

- **Issue.** The streaming viewer's `.bin` payload keeps the same name
  across re-exports (`<out>.bin`), while its bytes change. Serving it with
  `Cache-Control: immutable` (the natural choice for a huge write-once
  payload) meant a re-exported run could be invisible to the browser for
  up to a year; serving everything `no-store` (the 6.3-era fix) made every
  revisited tile re-download, defeating the whole point of a tile cache.
- **Side effects.** Either stale visualisations after a re-export or pure
  bandwidth waste on every fly-through; the browser's HTTP cache IS the
  viewer's tile cache, so the policy directly shapes interactivity.
- **Approach.** Make immutability true instead of promised: a URL may only
  be cached forever if the URL itself changes when the bytes do.
- **Solution.** `tiled_exporter` stamps a version tag into the page's
  payload URL (`?v=<size>-<mtime>`), and `serve_voxel_html` promises
  `immutable` only to `?v=`-tagged requests; unversioned payloads fall back
  to revalidation (one conditional request per tile, answered 304 while
  unchanged). The HTML shell stays `no-store`, so a re-export is always
  picked up and carries the new tag.
- **Gain vs lose.** Gain: hard caching and instant re-export visibility at
  the same time. Lose: one query parameter of ceremony, and third-party
  servers that ignore query strings would over-cache (documented in the
  server's header comments).
- **Alternatives.** Content-hashed filenames (classic asset pipeline;
  renames the sidecar every export and breaks simple resume/diff);
  ETag-only (still one revalidation round-trip per tile).

### 5.6 The streaming viewer evicted the tile under the camera

- **Issue.** `tiled_exporter`'s embedded viewer scored tiles for
  residency by their distance to a look-ahead PROBE only - the camera
  position pushed `min(TILE_M*4, fly.speed*1.2)` metres along the view
  direction. But `fly.speed` is the mouse-wheel speed SETTING, not the
  actual velocity: once the user scrolls the speed up, the probe stays
  pinned up to 400 m ahead even with the camera standing still.
- **Side effects.** The tile directly under the camera ranked ~47th in
  the working set and was evicted, which the user experiences as
  "cannot zoom in far enough to see detail" - the one place anybody
  looks first is the one place that never got its full-detail tile.
- **Approach.** Replay the ranking on the real Run8 tile layout instead
  of reasoning about it: score every tile the way the viewer does and
  see where the camera's own tile lands.
- **Solution.** Score by `min(distance-to-camera, distance-to-probe)`.
  The probe still pulls tiles in ahead of the flight direction, but the
  camera's own tile can no longer be demoted by it (rank 47 -> 1 on the
  real Run8 layout). `tests/test_residency_scoring.py` (13 tests) pins
  the rule, with a mutation-verified template pin.
- **Gain vs lose.** Gain: close-up detail appears where the camera
  actually is, at no cost to prefetch. Lose: the scoring lives in the
  HTML template, so an already-exported page keeps the old behaviour
  until it is re-exported - which is why the runs below were
  regenerated and Run6 was deliberately not.
- **Regenerated artefacts.** `outputs/Run7` and
  `outputs/Run8` re-exported byte-identically (`area_stream.bin`
  10,156,878,656 B / 317,402,458 records and 5,995,072,128 B /
  187,346,004 records, 758 tiles each); `outputs/Run9` got its first
  stream export at all (82,212,962 records, 758 tiles, 2,630,814,784 B,
  tile-m 100, 10 M instance budget). All three now carry the
  `min(cam, probe)` scoring, a search generation guard, and the
  cache-busting payload URL of 5.5
  (`area_stream.bin?v=<size>-<mtime>`; the server promises immutable
  caching only to `?v=` URLs, and Run7's old page predated the tag, so
  a browser could serve it a stale 10 GB payload). Run6 stays on the
  old template as the before/after reference.
- **Alternatives.** Track true camera velocity and scale the probe by
  it (more viewer state, and a stationary camera still needs the
  camera-distance term); shorten the probe (gives up the prefetch the
  probe exists for).

### 5.7 Children that escaped their parents' bounding volumes

- **Issue.** `tileset_exporter.convert_to_3d_tiles_lod` mixed two
  conventions: a leaf claimed its full nominal tile square, while an
  interior node used the tight extent of the coarsened records it
  actually held. A sparse leaf - records in one corner of its square -
  therefore poked outside its own parent, which the 3D Tiles spec
  forbids: children must be fully contained, because a client may cull
  a whole subtree on the parent's box alone.
- **Side effects.** Measured on the exported
  `outputs/Run1/tileset.json` (the run tree itself is a 6.1 GB
  local artefact, not part of the code): 556 violating
  parent-child pairs at strict tolerance, 29 of them material (>1 mm),
  worst overhang 90.0 m, every material one at parent depth 4; 79
  descendants also escaped the root box (worst 88.0 m). The real GLB
  geometry never left any parent box (0.00 m escape), so this was a
  culling-correctness and spec-conformance defect, not a
  visible artefact.
- **Approach.** Audit the shipped artefact instead of trusting the
  toolchain: the official `3d-tiles-validator` does NOT check
  bounding-volume containment and reported 0 errors on the broken file
  exactly as it does on the fixed one. A containment audit is the only
  real check.
- **Solution.** Bottom-up union. `_tile_json` now returns `(json, bv)`,
  and every parent unions its own bounding volume with all of its
  children's - the root included - before conversion to the 3D Tiles
  box form. A `_floor_z` helper pre-applies the 0.05 m minimum z
  half-extent BEFORE the union, so a floor-inflated flat child cannot
  escape a parent that was computed without it.
  `tests/test_tileset_bv_containment.py` (4 tests) pins the invariant,
  including a permanent mutation guard that re-execs the exporter with
  the union disabled and asserts the violations reappear; the full suite
  stood at 273 passed and 1 skipped on the day the fix landed, and has
  grown since.
- **Gain vs lose.** Gain: a spec-conformant tree in which culling can
  never discard a visible descendant. Lose: correct containment costs
  marginally larger interior boxes - Run1's root y half-extent grew
  1256 -> 1300 m as its ymin dropped 88 m - which buys a slightly more
  conservative frustum test, not lost detail.
- **Repaired in place.** Only `tileset.json` carries boxes, so the
  shipped artefacts needed no GLB rewrite: a one-off script of the
  private workspace re-applied the same
  union to an already-exported tileset (axis-aligned boxes only behind
  a hard assert, `--audit-only` to measure without writing,
  `tileset.json.bak-prebvfix` backups). Both `outputs/Run1` and its iTowns-adapted copy (a working copy outside this delivery) now audit 0 violations
  and 0 root escapees.
- **Alternatives.** Give leaves the tight extent of their own records
  instead (also containment-correct, but a leaf's box would then move
  whenever its contents change, and the nominal square is what the tile
  grid means); re-export the whole run (hours of GLB writing for a
  defect that lives entirely in one JSON file).

### 5.8 The LOD exporter held whole levels in RAM, and fine tiles killed it

- **Issue.** `convert_to_3d_tiles_lod` streamed its leaves and its first
  interior level with bounded memory, then held EVERY level above 1 in
  RAM at once before writing any of it. The cause is a single line: the
  leaf walk was sorted by `(i // 2, j // 2)` - the level-1 parent - which
  makes level-1 siblings contiguous and nothing else, so the exporter
  cannot know when a level-2 node is complete and must accumulate. At the
  default 100 m tiles the buffer fit and the defect was invisible;
  halving `--tile-m` quadruples the node count at every level, and a 50 m
  export of the 3 x 3 km area died with a native access violation
  (0xC0000005) just after
  logging level 1 - all 2,974 leaves written, no tileset.json, 12 GB of
  orphaned GLBs.
- **Side effects.** Peak memory scaled with area, the one property the
  rest of the pipeline is built to avoid; fine-grained tilesets (which
  the instance-cap work of the delivery kit wants, since smaller leaves
  are what let a 3D Tiles client cull finely) were unreachable; and the
  failure depended on ambient free RAM, so the same command could crash
  or succeed depending on what else was open.
- **Approach.** Rule out the host hardware first (no WHEA or bugcheck
  event in the window; the same code path completed on a 500 m region),
  measure a successful run's resident set mid-flight, then treat the
  sort key as the design flaw instead of tuning buffer sizes.
- **Solution.** Walk the leaves in Morton (Z-)order, whose defining
  property is that the descendants of every quadtree node form one
  contiguous run at EVERY level, and generalise the level-1 flush into a
  cascade: when the node key at any level changes, that node is complete
  - concatenate, write, coarsen into its parent, free. Peak memory
  becomes one open node per level (depth x siblings) instead of the
  area. Because a flush at one level feeds the next, a parent is always
  written after its children, which is the ordering the
  bounding-volume union of 5.7 requires. Node content is kept
  independent of the walk by tagging every part with its child's
  identity and sorting at flush - which is what makes the output
  byte-identical, not merely equivalent.
- **Gain vs lose.** Gain, all measured: the 100 m export reproduces the
  old code's 1,026 files byte for byte (peak 6.69 -> 6.14 GB); the 50 m
  export that had crashed now completes in a 6.09 GB peak - flat against
  the 100 m case - and reproduces the old code's lucky-retry output
  exactly, 4,001 files; a 25 m export (11,780 leaves, 8 levels), which
  the buffered design could not reach at all, completes with the peak
  dominated by the payload build rather than the pyramid. Containment
  checks are clean at both new resolutions and the full suite grew by
  four tests (Morton contiguity, a buffered in-test reference with
  byte-equal GLBs, the non-power-of-four drain, instance conservation).
  Lose: genuinely little - the walk order is no longer the payload's
  band order, and anyone reasoning about flush timing now needs the
  Morton property, which is why it is pinned by a test and not
  described in a comment.
- **Alternatives.** Raise the buffer's headroom (free RAM elsewhere on
  the host - works once, scales nowhere); flush level 2 the way level 1
  was and keep buffering above it (halves the problem, keeps the O(area)
  term); a two-pass design that sizes every node first (an extra full
  read of the payload for information Morton order provides for free).

## 6. Environment traps (cheap to hit, expensive to diagnose)

### 6.1 Stale compiled bytecode

- **Issue.** An error no reading of the source could explain ("'Column'
  object is not iterable") was caused by Python loading outdated
  `__pycache__` bytecode after a refactor.
- **Side effects.** An afternoon of debugging correct code.
- **Solution.** Clear `__pycache__` when tracebacks contradict the
  source; suspicion of the environment is now part of the debugging
  method (the report records it as such).
- **Alternatives.** `PYTHONDONTWRITEBYTECODE` during development.

### 6.2 Shadowed builtins in experiment scripts

- **Issue.** An intermittent "'tuple' object is not callable" in batch
  processing traced to a script-level name shadowing a builtin.
- **Solution.** Module hygiene (no rebinding of builtins), and the
  intermittency itself treated as the diagnostic clue (consistent bugs
  fail consistently).

### 6.3 Browser cache serving fixed bugs

- **Issue.** A repaired GLB kept "failing" because the browser cached the
  broken version.
- **Solution.** `serve_run.py` sends `Cache-Control: no-store` on every
  response; re-exports are always visible.

### 6.4 LAZ backend choice

- **Issue.** The lazrs backend misbehaved on large tiles: LazrsParallel
  (laspy's default when lazrs is installed) intermittently corrupted the
  heap (~1 in 5 runs surfacing as access violations), and single-thread
  Lazrs raised nondeterministic decode errors on identical bytes.
- **Solution.** Pin the C++ LASzip backend (`laszip>=0.3.0` in
  requirements, forced in `io_laz.py`); revisit lazrs only with a
  regression test over the largest tiles.
- **What the pin rests on TODAY.** The memory corruption itself, and
  nothing else: `lazrs_parallel` corrupted the heap on large IGN tiles,
  single-thread `lazrs` failed nondeterministically on identical bytes,
  and laszip survived 25+ stress reads clean
  (`design-decisions.md` section 11). Any external advice to "switch to
  lazrs because laszip is unstable" has it backwards. lazrs stays
  installed for one job only: a second opinion during corruption triage.
- **RETRACTED as evidence: tile `18340_51775.laz`.** This entry used to
  lead with that tile as a deterministic reproducer - LazrsParallel 0.8.1
  panicking on it 5 times out of 5
  (`range end index 18446744073709551615 out of range`) while Laszip and
  single-thread Lazrs read all 17.36 M points with identical checksums.
  Section 1.4 later established that the file being read was a
  bit-corrupted LOCAL copy; the published tile is clean, and all three
  decoders fail on the bad bytes. The panic was therefore evidence about a
  damaged file, not about the backend, and it is withdrawn as support for
  the pin. What survives from it is the triage lesson, not the
  justification: LazrsParallel turns damaged bytes into a loud panic where
  Laszip decodes them silently wrong. (The crash-era analysis lived in
  `EntireLyonOutputs/Run1/crash_analysis.md`, no longer on disk; the
  surviving record is this entry plus `tests/T160/REPORT.md`.)
- **Refined further.** The two backends turn out to fail on
  *different* tiles, which is why both are now installed: Laszip
  hard-crashes on `18375_51880.laz` (tile 613) while single-thread Lazrs
  reads it cleanly, so `--retry-lazrs`'s one-shot second opinion rescues
  it. Laszip remains the pinned default everywhere; `lazrs` became a
  standing dependency used only by the crash-isolated retry path, and
  every unpinned `laspy.read/open` call in the tree was pinned to Laszip
  so that installing lazrs can never silently re-enable LazrsParallel.

### 6.5 Python 3.11.9 corrupted its own heap under numpy load

- **Issue.** A test (`test_grid_codec.py::test_size_win`) failed roughly one
  run in nine with impossible errors: tuples in a freshly-built list
  "becoming" ints mid-iteration, a plain 5-int list raising "inhomogeneous
  shape", access violations inside garbage collection. A minimal repro with
  **no project code** (an `np.fromiter` loop over ~10k tuples plus
  `gc.collect()`) failed in 20-35 % of processes - the band the version
  matrix below actually spans, 4/20 with numpy 2.4.6 and 7/20 with 2.3.5.
- **Side effects.** Any numpy-heavy run under the venv - including
  production voxelizations - carried a small per-process chance of silent
  heap corruption; flaky tests looked like application bugs.
- **Approach.** Stop debugging the application: build a version matrix on
  identical hardware. Python 3.11.9 failed with numpy 2.4.6 (4/20
  processes) and numpy 2.3.5 (7/20); Python 3.13.12 and 3.14.4 were clean
  (0/20 each) with the same numpy build. `PYTHONMALLOC=debug` hid the bug
  (0/15), the classic signature of allocator-layout-dependent corruption.
  Same-hardware cleanliness on 3.13/3.14 pointed away from hardware for
  THIS failure class (the interpreter dependence is decisive for it),
  though the machine was later shown to have real hardware instability
  of its own - see 6.7.
- **Solution.** The corruption follows the 3.11.9 interpreter, not numpy
  and not the code. The venv and the Docker image were migrated to Python
  3.13, the Dockerfile documents that 3.13 is required, not
  incidental, and the full suite plus a smoke run were re-verified.
- **Gain vs lose.** Gain: the failure class is gone, and the version matrix
  is recorded so nobody re-litigates it from a single flaky test. Lose:
  whether the fault is this machine's 3.11.9 install or CPython
  3.11.9-on-Windows generally was left undetermined (3.11 no longer
  receives Windows binary releases either way).
- **Alternatives.** Pin numpy lower (disproved by the matrix);
  `PYTHONMALLOC=debug` in production (hides, not fixes, at a large
  performance cost); per-test process isolation (treats the symptom).

### 6.6 Windows specifics

- **Issue.** Small frictions: stale UE engine-association registry
  (launch the editor by absolute path), path-separator quoting in
  PowerShell command examples, batch wrappers needed for non-technical
  colleagues.
- **Solution.** Documented launch-by-path; `.bat` wrappers (`run_area`,
  `run_viewer`) kept in the repository root.

### 6.7 The development machine itself became a suspect

- **Issue.** The workstation running every experiment turned out to have
  verified hardware instability: recurring CORRECTED processor
  machine-check errors (internal parity errors on one core's thread
  pair) recorded by the OS over weeks, escalating to three kernel
  bugchecks in a single day - two access violations and one page fault
  whose parameters show a kernel address with two flipped bits, a
  classic memory-path corruption signature.
- **Side effects.** Every "nondeterministic native fault" chased in this
  project (the ray-walk access violations of the report's Section 5.4,
  the intermittent test crashes) has a second candidate explanation
  beside the software one; two working sessions were lost mid-run.
- **Approach.** Read the machine's own records (the corrected-error log,
  the bugcheck history) instead of assuming each crash was novel; stop
  treating "random" faults as necessarily software.
- **Solution.** The finding is recorded, not solved: the report's
  5.4 now states the hardware as a plausible contributor, the software
  mitigations stand on their own merits (removing per-voxel call volume
  is sound engineering under either explanation, and per-block process
  isolation contains EITHER failure source), heavy parallel compute is
  paced, and all reported figures come from runs that completed cleanly
  and reproduce.
- **Gain vs lose.** Gain: honest attribution - the report no longer
  implies the fault is fully explained by software; process isolation is
  revealed as the right defence regardless of cause. Lose: the software
  explanation of 5.4 can no longer be asserted as complete, and the
  machine needs vendor attention that is outside this project.
- **Alternatives.** Ignore the hardware evidence and keep a pure software
  narrative (indefensible once the machine-check log is read); halt all
  work pending repair (the mitigations make results trustworthy - every
  number is reproducible and cross-checked).

## 7. Correctness debt and how it was retired

### 7.1 The full-code correctness sweep (the meta-problem)

- **Issue.** After weeks of feature-speed development, nobody could say
  which comments, help texts and defaults still told the truth. A full
  read of every module in the package against its own claims found real
  defects hiding behind plausible documentation: an interval lookup that
  reported occupied voxels as empty under cross-class overlaps (duplicated
  verbatim in the ray tracer), a ground index that silently wrapped in
  int16 at fine cell sizes, every 3D-Tiles box wound inside-out, flags
  that did nothing in shard mode, exit codes that were always zero, and a
  small-store 3D-Tiles export that had never once worked.
- **Side effects.** Wrong results with no errors raised (the worst kind);
  CLI contracts users relied on that the code did not honour;
  documentation that actively misled the next reader.
- **Approach.** Treat the sweep as an artefact, not an event: every finding
  reproduced with a command and its output before it counted as real, each
  fix annotated in place so the original defect stays readable next to the
  correction, and every fixed behaviour pinned by a regression test so it
  cannot silently return. The tests are what survives the sweep; a list of
  findings would not have.
- **Solution.** All correctness and crash classes fixed the same day;
  differential tests against naive reference implementations (24,000-query
  randomized overlap checks, 216k-lookup decoder comparison); two suites
  now guard the contracts and are the standing record of what the sweep
  found: `tests/test_regression_audit.py` (29 unit-level regressions) and
  `tests/test_cli_contracts.py` (18 end-to-end CLI checks in real
  subprocesses). Each test names the behaviour it pins, so the findings can
  be read off the suites and not out of a separate document.
- **Gain vs lose.** Gain: the documentation is again evidence, and the
  regression suites make the findings permanent instead of
  archaeological. Lose: two days of feature time.
- **Alternatives.** Fix-as-found without recording (loses the systematic
  sweep and the regression pinning); external review only (the two
  external static-analysis reports reviewed during development each
  contained false positives that had to be fact-checked claim by claim).

### 7.2 Comments that could not survive an experiment

- **Issue.** After the code sweep of 7.1, a second doubt remained: many
  comments and docstrings made FACTUAL claims (timings, counts, precision
  bounds, cross-references, behaviour of other modules) that had never
  been tested against reality - only read for plausibility.
- **Side effects.** Plausible-but-wrong documentation is worse than none:
  it had already propagated into the report once (the 2.5-D traversal
  description) and it steers every future reader wrong.
- **Approach.** Treat every factual claim in every comment as a
  hypothesis: verify it against the code, a recorded artefact, or a fresh
  experiment on the real data, and change whichever side - comment or
  code - loses. The permanent half of this is the `code_verification/`
  suite: eleven standalone experiments that re-prove the documented
  claims against the actual LAZ corpus and production stores, each
  printing pass/fail per claim and writing its measurements to an
  artefact.
- **Solution.** Thousands of claims checked; a few hundred corrected.
  Representative catches: reconstruction precision was overstated (a
  merged run snaps points to its whole-span centre, not within half a
  cell); the viewer codec's "bit-identical" claim holds only for
  power-of-two cell sizes (1-ULP drift measured otherwise); Windows
  reports native crash codes as large positive returncodes, not negative
  as two modules claimed; a module cited bibliography numbers from a
  retired numbering; dead references pointed at a file no longer on
  disk; and a lower/upper bound flip that crept into the extinction
  script during the sweep itself was caught by the same method one pass
  later.
- **Gain vs lose.** Gain: documentation that can cite its evidence, and a
  rerunnable suite that keeps it honest. Lose: real effort, and the suite
  is one more thing to maintain - accepted because unverifiable comments
  had already cost report accuracy once.
- **Alternatives.** Trust review-by-reading (this is what had
  failed); delete comments instead of verifying them (throws away the
  reasoning this project exists to record).

### 7.3 A hold-out quoted against the wrong fit

- **Issue.** The report quoted the extinction fit's hold-out validation
  as 51.6 percent median relative error, MAE 0.1303, class-5
  interquartile range 0.161-0.324 - but the saved artefact of the 0.5 m
  fit it was attributed to says 59.28 percent, MAE 0.116, IQR
  0.243-0.480.
- **Side effects.** For a while the numbers looked simply unbacked - the
  worst reading - and any defence question on them would have found the
  mismatch.
- **Approach.** Re-run the derivation with the same seed at BOTH
  resolutions and compare every quoted number against both artefacts,
  without assuming the quote was invented.
- **Solution.** The quoted trio reproduces EXACTLY from the 1.0 m refit's
  hold-out: the numbers were real but mis-attributed to the 0.5 m fit.
  Root cause: the derivation script hardcodes one output path, so the
  1.0 m run overwrote nothing and kept no record of its own - its results
  survived only in a transcription that then lost its label. The report
  first took the 0.5 m artefact's numbers for the 0.5 m fit; the
  per-pulse refit then superseded that return-count fit, and the shipped
  report quotes the refit's hold-out - 57.7 percent median relative
  error, MAE 0.120, class-5 IQR 0.233-0.463
  (RESULTS.md of the archived campaign of 2026-08-08 (outside this delivery), run E10_per_pulse_refit_scopeA,
  section 3.2). `code_verification/exp07` re-derives both resolutions
  and pins the mis-attribution as a positive check; one claim in the
  quoted block (a "stratified" MAE comparison) matched neither artefact
  and was removed as genuinely unbacked.
- **Gain vs lose.** Gain: every hold-out number now has a named artefact,
  and the near-miss is converted into a regression check. Lose: nothing -
  except the reminder that a hardcoded output path is a
  record-destroyer.
- **Alternatives.** Quote the recomputed numbers and move on (loses the
  lesson and leaves the overwrite trap unexamined); keep both sets with a
  footnote (confusing where a correct attribution exists).

---

## Reading order for a successor

Start with 2.1 and 2.2 (they explain why the code looks the way it does),
then 3.1, 3.2 and 3.5 (the semantics that make the physics honest, and
the correction that real data forced on them), then 4.2 (the incident
that hardened the memory discipline), then section 5 in order (each
3D Tiles problem builds on the previous one). Sections 6 and 7 are the
humility file: the environment traps (stale bytecode, a heap-corrupting
interpreter, a bit-rotted tile that passed verification, a workstation
with hardware instability of its own) and the sweeps that converted
correctness debt into regression tests and experiments. The design-level
argument for the same material is the internship report; the
choice-by-choice rationale is `design-decisions.md`; the sources are
`bibliography-and-references.md`.
