"""
Utilities for auto-incrementing run directories (outputs/Run1/, Run2/, ...).
@ingroup t0_socle


Typical layout::

    outputs/
        Run1/
            area_output/   area-mode files (stats.txt, *.png, columns/, ...)
            single/        single-tile outputs (<tile_stem>/stats.txt, ...)
            diagnosis/     diagnostics (run.log, summary.json, ...)
        Run2/
            area_output/
            single/
            diagnosis/
"""

from __future__ import annotations
import re
from pathlib import Path

_RE_RUN = re.compile(r"^Run(\d+)$")


def next_run_dir(base: Path = Path("outputs")) -> Path:
    """Return the next available ``Run<N>`` directory under *base*,
    creating it (and *base*) if needed.

    ``<base>/Run1/``, ``<base>/Run2/``, ... - the integer part is chosen as
    ``max(existing) + 1``, so old runs are never overwritten.
    """
    base = Path(base).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)

    max_n = 0
    for child in base.iterdir():
        if child.is_dir():
            m = _RE_RUN.match(child.name)
            if m:
                n = int(m.group(1))
                if n > max_n:
                    max_n = n

    # Two concurrent invocations (e.g. GUI-spawned subprocesses) can both
    # scan the same max_n; the loser of the mkdir race takes the next
    # number instead of dying with FileExistsError.
    n = max_n + 1
    while True:
        run_dir = base / f"Run{n}"
        try:
            run_dir.mkdir()
            return run_dir
        except FileExistsError:
            n += 1


def next_run_output_dir(
    base: Path = Path("outputs"),
    subdir: str = "area_output",
) -> Path:
    """Return ``next_run_dir(base) / subdir``, creating the subdirectory."""
    out = next_run_dir(base) / subdir
    out.mkdir(parents=True, exist_ok=True)
    return out


# ---------------------------------------------------------------------------
# Run provenance
# ---------------------------------------------------------------------------
# The store's own metadata (origin, cell sizes) describes the LATTICE, but not
# the settings that decided its CONTENT. Two of those are load-bearing:
#
#   group_gap / group_intervals  - `area.npz` is the GROUPED store by default,
#       and `ColumnStore.grouped()` is not invertible. Without the recorded gap
#       the deliverable cannot be reproduced from the raw per-tile shards, even
#       though those shards contain every bit of the input.
#   keep_classes                 - a class filter silently changes the point
#       totals, so a rebuilt store would differ for reasons nothing on disk
#       explains.
#
# `epsg` is recorded because ColumnStore has no CRS field (its `meta` array is
# five floats and widening it would break every existing .npz), yet
# `reconstruct.store_to_las` must write one - `io_laz.read_laz` rejects files
# without a CRS.
PROVENANCE_SCHEMA = "voxelizer.provenance/1"


def make_provenance(*, group_intervals=None, group_gap=None,
                    keep_classes=None, epsg=None, run_order=None,
                    **extra) -> dict:
    """Build the provenance block embedded in area/shard manifests.

    Every field is optional: callers that genuinely do not know a value pass
    None, and it is recorded as null rather than omitted, so a consumer can
    tell "not applicable" from "written by an older version".
    """
    prov = {
        "provenance_schema": PROVENANCE_SCHEMA,
        "group_intervals": (None if group_intervals is None
                            else bool(group_intervals)),
        # metres, not cells: the cell count depends on cell_z, which a
        # consumer may be changing.
        "group_gap_m": None if group_gap is None else float(group_gap),
        "keep_classes": (None if keep_classes is None
                         else sorted(int(c) for c in keep_classes)),
        "epsg": None if epsg is None else int(epsg),
        # The run-forming cell order that built the store (height_first or
        # class_first): consumers that re-voxelize (archive verify/unpack)
        # must reproduce it, so it travels with the data. None = written
        # before the toggle existed, which means height_first in practice.
        "run_order": None if run_order is None else str(run_order),
    }
    prov.update(extra)
    return prov
