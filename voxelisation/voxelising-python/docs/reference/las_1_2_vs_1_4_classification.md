# LAS 1.2 vs LAS 1.4, and why `classes_config.py` stays "1.4-shaped"

## Why this doc exists

`voxelizer/classes_config.py` defines the full IGN classification scheme:
the standard ASPRS codes (0-22) plus IGN's four custom codes (64-67:
`perennial_above_ground`, `artefact`, `virtual_point`, `misc_building_like`).
Those custom codes can only physically exist in **LAS 1.4 extended point
formats (6-10)**. The Grand Lyon 2023 tiles this project actually reads are
**LAS 1.2, point format 1**, verified across all 2,842 files of the corpus -
so codes 64-67 can never occur in our data; the classification byte in
point format 1 is only 5 bits wide (max value 31).

This raised a natural question: should the code table be trimmed down to
just what LAS 1.2 can hold, or is it fine (or even better) to keep the
full LAS-1.4-shaped table and simply let the unused part sit empty? This
doc lays out the comparison and the reasoning.

## LAS 1.2 vs LAS 1.4: what actually differs

| Aspect | LAS 1.2, point format 1 (what we have) | LAS 1.4, extended point formats 6-10 |
|---|---|---|
| Classification storage | 1 byte, but only 5 bits for the class value (0-31) - the other 3 bits are the synthetic/key-point/withheld flags | 1 full byte for class (0-255), flags moved to a separate byte, plus a real overlap bit |
| Returns per pulse | up to 5 | up to 15 |
| Scan angle precision | 8-bit (coarse) | 16-bit (fine) |
| Point count fields | 32-bit (caps a single file around 4.29 billion points) | 64-bit |
| Extra channels | none native | waveform packets, near-infrared (formats 8/10) |
| Record size | smaller (28 B for format 1) | larger (30 B+ for format 6) |
| Header/VLR size | 227-byte header, simpler | 375-byte header, adds Extended VLRs |
| Tool support | universal - every LAS/LAZ reader, old and new | newer; some older/legacy tools and viewers don't fully support it |

The practical upshot: LAS 1.2 is cheaper and near-universally compatible,
but its classification field physically cannot hold values above 31. LAS
1.4's extended formats remove that ceiling (and add several other
capabilities we don't currently need) at the cost of bigger files and
narrower tool support. *Why* IGN/Grand Lyon actually chose 1.2 for this
delivery isn't documented anywhere in this repo - our best guess is
compatibility and file size, but that's inference, not a confirmed fact.

## Should `classes_config.py` be trimmed to match?

**No - keeping the full table is fine, and arguably the better choice.**
Here's the reasoning, in plain terms:

**1. It costs almost nothing to keep the extra entries.** A Python dict
with 4 extra key-value pairs, or a colour lookup table with a few unused
rows, is not a performance or memory concern. In fact, `visualization.py`'s
`_class_lut()` already allocates a 256-row colour table (`np.full((256, 3),
128, ...)`) regardless - i.e. the codebase already budgets for the entire
possible byte range, not just the 32 values LAS 1.2 can produce. Adding
codes 64-67 on top of that is free.

**2. The real data prunes itself automatically - no special-case code
needed.** Because point format 1 structurally cannot contain values above
31, `np.unique(classification)` on any real tile will simply never return
64-67. Nothing has to actively "cut" or "remove" anything: the data *is*
already the LAS-1.2 subset of the full table, by construction. Trying to
manually strip the table down to match would just be duplicating a
guarantee the file format already enforces for free.

**3. Consumers here are already written to show only what's present, not
what's merely possible.** This is the part worth being precise about,
because it's the difference between "acting as if we have 1.4 data" being
safe versus being a bug magnet. Two real examples from this codebase:

   - `column_diagnostics.py`'s `write_class_representatives_figure()`
     calls `store.class_presence(ALL_CLASS_CODES)` and only adds a class
     to the output figure `if mask.any()` - i.e. only if that code is
     actually found in the data. A code with zero occurrences (64-67, on
     our LAS 1.2 tiles) simply never appears in `columns_by_class.png`.
     No dead legend entries, no wasted rendering.
   - `visualization.py`'s `_class_lut()` builds its colour table from
     `CLASS_COLORS.items()` but is only ever indexed by classes that
     actually occur in a column store, via `CLASS_COLORS.get(cls, ...)`.
     An unused code just never gets looked up.

   In other words, "present in the table" and "present in the data" are
   already kept as two separate questions everywhere that matters. That
   discipline is exactly what makes it safe to define the full domain
   once and let the data decide what's actually used.

**4. It's forward-compatible for free.** If a future Grand Lyon delivery
(or a different region's IGN LiDAR HD product) ever arrives as true LAS
1.4 extended format and actually populates codes 64-67, this codebase
already knows their names and colours - nothing needs to change. If the
table had been trimmed to "only 0-31" instead, someone would have to
notice the gap, go back to IGN's spec document, and re-add the codes
later - redoing work that's already done and sitting idle today.

**5. It matches the upstream source of truth.** The table was written
from IGN's own "Descriptif de contenu v1.0" classification spec, which
describes the full national LiDAR HD vocabulary independent of which
container format a given regional delivery happens to use. Keeping the
table complete keeps it aligned with that spec, rather than with one
delivery's incidental packaging choice.

## The one discipline this depends on

None of the above is a free lunch if a future change breaks the pattern.
The risk to actually watch for: **never let "listed in `CLASS_NAMES`" be
mistaken for "present in this tile."** Concretely:
- Any new report, legend, or export should filter by `class_presence()` /
  `np.unique(classification)` (what's actually in the data), not by
  blindly looping over `ALL_CLASS_CODES` and assuming every code has
  something to show.
- Any code that writes classification values back out to a file (e.g.
  `reconstruct.py`) cannot emit codes 64-67 into a LAS 1.2 /
  point-format-1 output: the classification field is only five bits wide
  there, and `laspy` raises `OverflowError` rather than truncating.
  `reconstruct.store_to_las` handles this by checking `cls.max()` against
  `_PF3_MAX_CLASS = 31` and promoting the output to **LAS 1.4 / point
  format 6** (a full classification byte) when any code exceeds it. Real
  Grand Lyon data (codes 0-22) never trips that branch and stays on the
  **LAS 1.2** container - written as **point format 3**, not the input's
  point format 1: the reconstruction always writes pf3, which is pf1 plus
  RGB and shares the same 5-bit classification sub-field, so the container
  version is preserved and the record layout is not. Both branches are
  pinned by `tests/test_reconstruct_roundtrip.py`
  (`test_class_codes_above_31_promote_to_point_format_6`, and
  `test_low_class_codes_stay_on_las_1_2`, which asserts exactly
  `point_format.id == 3` and `version == "1.2"`).
- The module docstring in `classes_config.py` now says explicitly that
  64-67 are structurally dead for the current dataset, so nobody has to
  rediscover this by tracing an `OverflowError` back to its source.

## tl;dr

Keeping `classes_config.py` shaped like LAS 1.4's full classification
space, while the actual data is LAS 1.2, is not a mismatch to fix - it's
the correct design as long as everything downstream reads "what's present
in the data," not "what's listed in the table." That's already how this
codebase is built (`class_presence`, presence-gated figures, a 256-row
colour LUT), so the four unused IGN codes just sit there at zero cost
until - if ever - a future delivery actually needs them.
