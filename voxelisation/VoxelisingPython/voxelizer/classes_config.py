"""
LiDAR point classification codes and display colors used by the IA.rbre
voxelizer.
@ingroup t0_socle


Two groups of codes:

  1) ASPRS LAS 1.4 standard codes (0-22). These are the cross-vendor
     convention every LiDAR file ought to use. Any file this pipeline
     loads - IGN HD or otherwise - is interpreted against this numbering.

  2) IGN-custom codes (64-67). IGN uses the user-definable range to add a
     few classes it cares about, per "Descriptif de contenu" v1.0 (Sept 2023).
     Reference: https://geoservices.ign.fr/lidarhd -> DC_LiDAR_HD_1-0_PTS.pdf

     IMPORTANT - only reachable in LAS 1.4 extended point formats (6-10).
     The legacy classification byte used by point formats 0-5 (all of LAS
     1.0-1.3, and any LAS 1.4 file still using those formats) packs the
     class into 5 bits, capping it at 0-31. The Grand Lyon 2023 delivery
     this project reads is LAS 1.2 / point format 1, so codes 64-67 can
     never actually occur in our data - writing e.g. class 67 into a
     point-format-1 record raises `OverflowError` (max 31), and the full
     corpus scan - every point of all 2,842 files, 51.9 billion points
     (the Genstats corpus record, LAZ_STATS.md) - finds only classes 1-9,
     never 64-67.
     They're kept in the tables below for forward compatibility in case a
     future delivery switches to a LAS 1.4 extended format, not because
     they appear today.

What's deliberately NOT listed:
 - Code 12 is reserved by ASPRS (legacy "overlap points"; superseded by
    the overlap bit-flag in LAS 1.4). If a tile contains class 12 it's
    almost certainly flight-strip overlap -> drop it via `keep_classes`
    rather than naming it here.
 - Codes 23-63 are reserved for future ASPRS use. Leaving them out means
    "class_23" will appear in our output if encountered, signalling clearly
    that something unexpected is in the file.
 - Codes 68-255 are vendor-specific. Anything from a non-IGN producer in
    this range will need to be added per data source.

A note on code 8: in LAS 1.0-1.3 this was "Model Key-Point" (sparse DTM
seeds); in LAS 1.4 it became reserved. *In practice*, the Grand Lyon 2023
corpus this project reads (TerraScan-generated, per the LAZ headers) uses
code 8 as a de-facto upper-canopy / very-high-vegetation tier starting
around 15 m above local ground (returns observed clustering 15-25 m above
ground across tiles; ProblemSolving.md 1.3),
sitting on top of class 5 (high_vegetation): 6.4% of all points, present
in 2,691 of 2,842 tiles (LAZ_STATS.md).
We name it `high_vegetation_upper` so it renders in colour (not gray)
and is counted under its own name in stats, legends and diagnostics.
Naming does not change the RLE: grouping merges same-code runs only, so
a canopy column alternating 8 with 5 fragments either way.

Notes specific to IGN's classification:
 - Vegetation is split by height above ground: low (<50 cm), medium
    (50 cm-1.5 m), high (>1.5 m). Grass shorter than 20 cm is classed
    as ground, not vegetation.
 - "Sursol perenne" (64) covers things sticking up that aren't buildings,
    trees, or bridges: power-line cables and pylons, wind turbines,
    antennas, cable-car wires, parts of bridges above the deck.
 - "Points virtuels" (66) only appear in the "optimized" tiles. These are
    synthetic points inserted under bridges so DTM tools can model the ground
    underneath.
 - "Divers - batis" (67) is IGN's "looks like a building but couldn't
    be confirmed by BD TOPO(r) / AI" bucket: hedges, big rocks, caravans,
    unusual building shapes, recent constructions.
"""

from __future__ import annotations

CLASS_NAMES: dict[int, str] = {
    # -- ASPRS LAS 1.4 standard ------------------------------------------------
    0:  "created_never_classified",   # raw capture default; shouldn't survive QA
    1:  "unclassified",               # processed but not assigned (vehicles, etc.)
    2:  "ground",                     # natural or artificial bare-earth surface
    3:  "low_vegetation",             # grass, crops; IGN cutoff < 50 cm
    4:  "medium_vegetation",          # shrubs, bushes; IGN 50 cm - 1.5 m
    5:  "high_vegetation",            # trees, canopy; IGN > 1.5 m
    6:  "building",                   # roofs, facades, chimneys, balconies
    7:  "low_point_noise",            # low outliers / clutter / errors
    # 8 is officially reserved in LAS 1.4, but IGN/TerraScan-processed
    # tiles routinely use it for upper-canopy returns (see module docstring).
    8:  "high_vegetation_upper",      # upper-canopy tier, > ~15 m above ground
    9:  "water",                      # rivers, lakes, sea, ponds
    10: "rail",                       # railway tracks
    11: "road_surface",               # paved roads
    # 12 reserved (legacy "overlap points") left undefined on purpose
    13: "wire_guard",                 # power-line shield wires
    14: "wire_conductor",             # power-line conductors (phase)
    15: "transmission_tower",         # pylons, power poles
    16: "wire_structure_connector",   # insulators, line connectors
    17: "bridge_deck",                # deck only; pillars / parapets -> unclassified
    18: "high_noise",                 # high outliers, atmospheric noise, birds
    19: "overhead_structure",         # conveyors, traffic lights, mining gear
    20: "ignored_ground",             # ground near breaklines (USGS 3DEP)
    21: "snow",                       # snow-covered surfaces
    22: "temporal_exclusion",         # features excluded due to temporal change

    # -- IGN LiDAR HD custom codes (user-definable range) -----------------------
    # Dead for the current LAS 1.2/point-format-1 dataset - structurally
    # unreachable (5-bit classification byte caps at 31). See module
    # docstring. Kept for forward compatibility with a future LAS 1.4 delivery.
    64: "perennial_above_ground",     # IGN: power lines, antennas, wind turbines
    65: "artefact",                   # IGN: unexplained / spurious points
    66: "virtual_point",              # IGN: synthetic points under bridges
    67: "misc_building_like",         # IGN: building-like, not BD TOPO confirmed
}

# Named constants referenced by the rest of the codebase.
# Use these instead of hardcoding class codes. The table is COMPLETE:
# every named code in CLASS_NAMES above - ASPRS 0-11 and 13-22 (12 is
# reserved and unnamed, so there is no constant for it) plus IGN 64-67 -
# has a constant, so any module can reason about any class without magic
# numbers, and "represent every class" consumers iterate ALL_CLASS_CODES
# below. They do not all iterate ALL of it:
# :meth:`ColumnStore.class_presence` takes the whole 26 codes by default,
# while columns_by_class.png filters to codes <= 31 and so draws 22 of
# them - the IGN range cannot occur under point format 1's 5-bit
# classification byte, and a panel for it would always be empty.
CREATED_NEVER_CLASSIFIED = 0
UNCLASSIFIED = 1
GROUND = 2
LOW_VEGETATION = 3
MEDIUM_VEGETATION = 4
HIGH_VEGETATION = 5
BUILDING = 6
LOW_POINT_NOISE = 7
HIGH_VEGETATION_UPPER = 8
WATER = 9
RAIL = 10
ROAD_SURFACE = 11
WIRE_GUARD = 13
WIRE_CONDUCTOR = 14
TRANSMISSION_TOWER = 15
WIRE_STRUCTURE_CONNECTOR = 16
BRIDGE_DECK = 17
HIGH_NOISE = 18
OVERHEAD_STRUCTURE = 19
IGNORED_GROUND = 20
SNOW = 21
TEMPORAL_EXCLUSION = 22
PERENNIAL_ABOVE_GROUND = 64
ARTEFACT = 65
VIRTUAL_POINT = 66
MISC_BUILDING_LIKE = 67

VEGETATION_CLASSES = frozenset({LOW_VEGETATION, MEDIUM_VEGETATION, HIGH_VEGETATION, HIGH_VEGETATION_UPPER})

# Every named code, ascending (0-11, 13-22, 64-67; 12 is deliberately
# unnamed) - the canonical iteration
# order for consumers that must cover the whole classification table.
ALL_CLASS_CODES: tuple[int, ...] = tuple(sorted(CLASS_NAMES))

# Display colors for each class. RGB tuples in 0-255. Picked to match common
# GIS conventions (brown ground, greens for vegetation, red for buildings,
# blue for water) and to try and stay distinguishable on both light and dark
# backgrounds. Anything not in this dict falls through to a default gray
# in the visualization code, which is what we want for unknown classes.
CLASS_COLORS: dict[int, tuple[int, int, int]] = {
    # -- ASPRS LAS 1.4 standard -------------------------------------------------
    0:  (255,   0, 255),   # never-classified - loud magenta (shouldn't be there)
    1:  (180, 180, 180),   # unclassified - neutral gray
    2:  (160, 110,  60),   # ground - brown
    3:  (170, 220, 130),   # low vegetation - pale green
    4:  ( 90, 180,  90),   # medium vegetation - mid green
    5:  ( 40, 120,  40),   # high vegetation - dark green
    6:  (200,  70,  70),   # building - red
    7:  (120,  60, 120),   # low-point noise - dark purple (rare, but flagged)
    8:  ( 15,  70,  30),   # high vegetation (upper) - very dark forest green
    9:  ( 60, 120, 220),   # water - blue
    10: ( 90,  90,  90),   # rail - dark gray
    11: (130, 130, 130),   # road surface - mid gray (lighter than rail)
    13: (255, 220, 100),   # wire (shield) - pale yellow
    14: (255, 200,  50),   # wire (conductor) - golden yellow
    15: (200, 130,  30),   # transmission tower - burnt orange
    16: (220, 180,  90),   # wire-structure connector - sand
    17: (160,  90, 200),   # bridge deck - purple
    18: (255,  80, 200),   # high noise - hot pink (also loud on purpose)
    19: (140, 100,  60),   # overhead structure - dark brown
    20: (110,  80,  50),   # ignored ground - darker brown than 2
    21: (240, 240, 250),   # snow - almost-white
    22: ( 80,  80,  80),   # temporal exclusion - very dark gray

    # -- IGN LiDAR HD custom codes (dead for our LAS 1.2 data; see CLASS_NAMES) --
    64: (230, 180,  60),   # perennial above-ground - amber
    65: (255,   0, 255),   # artefact - loud magenta
    66: (200, 200, 255),   # virtual point - pale blue
    67: (220, 150, 100),   # misc building-like - terracotta
}
