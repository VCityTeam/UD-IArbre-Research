"""
Shared download infrastructure for Grand Lyon 2023 tile inventories.
@ingroup t0_socle


Provides the low-level building blocks used by both ``download_laz`` and
``download_orthos``:

    validate_bbox()      Ensure start <= end for both axes.
    resolve_inventory()  Locate a bundled inventory JSON by file name (both
                         downloaders derive their DEFAULT_JSON from it).
    load_values()        Read a ``{"values": [...]}`` inventory JSON.
    select_tiles()       Filter tiles whose origin falls inside a bounding box.
    default_workers()    Pick a sensible thread count for parallel downloads.
    download_one()       Stream a single URL to disk with retry + atomic write.

    origin_window()      Tile-origin selection window for a bounding box.
                         Not download-specific: ``preflight``, ``area``,
                         ``area_cli`` and ``sharding`` all use it to decide
                         which tiles a box touches.

Nothing here is CLI-specific - each caller (``download_laz`` or
``download_orthos``) brings its own ``argparse`` and orchestration.

Stdlib only (``urllib``) so it adds no dependencies to the voxelizer image.
"""

from __future__ import annotations
import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any

REQUEST_TIMEOUT_SECONDS = 120
MAX_RETRIES = 3


def resolve_inventory(filename: str) -> Path:
    """Return the path of an inventory JSON shipped alongside the package.

    Two repository layouts are in use and they keep the inventories in
    different places:

    * the development tree keeps them under ``<package parent>/inputs/
      quickhelpers/``;
    * the delivered tree keeps the code in a subdirectory and the
      inventories at the repository root, ``<package grandparent>/
      quickhelpers/``.

    The first existing candidate wins. If neither exists the development
    path is returned unchanged, so ``load_values`` reports the canonical
    location in its "not found" message and ``--json`` remains the escape
    hatch.
    """
    pkg_dir = Path(__file__).resolve().parent           # .../voxelizer
    candidates = (
        pkg_dir.parent / "inputs" / "quickhelpers" / filename,
        pkg_dir.parent.parent / "quickhelpers" / filename,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def default_workers() -> int:
    """Return the default number of download threads.

    Follows the ``ThreadPoolExecutor`` default of Python 3.8-3.12,
    ``min(32, os.cpu_count() + 4)``, a good fit for I/O-bound work where
    threads spend most of their time blocked on the network. (Python 3.13
    moved the stdlib default to ``os.process_cpu_count()``, which differs
    only under CPU-affinity limits.)

    One deliberate divergence: when ``os.cpu_count()`` returns None this
    falls back to 4, where the stdlib falls back to 1 - so the None case
    gives 8 threads here against the stdlib's ``1 + 4 = 5``. A machine that
    cannot report its CPU count is not a single-core machine, and on work
    this thoroughly I/O-bound the extra threads cost nothing.
    """
    return min(32, (os.cpu_count() or 4) + 4)


def validate_bbox(
    xmin_start: int, xmin_end: int, ymin_start: int, ymin_end: int,
) -> None:
    """Raise ``SystemExit`` if the bounding box is malformed."""
    if xmin_start > xmin_end or ymin_start > ymin_end:
        raise SystemExit(
            f"Invalid bounding box: start must be <= end "
            f"(got x [{xmin_start}, {xmin_end}], y [{ymin_start}, {ymin_end}])."
        )


def load_values(json_file: Path) -> list[dict[str, Any]]:
    """Return the ``values`` list from a Grand Lyon inventory JSON."""
    if not json_file.is_file():
        raise SystemExit(f"Inventory JSON not found: {json_file}")
    with json_file.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    values = data.get("values")
    if not isinstance(values, list):
        raise SystemExit(f"{json_file} must contain a 'values' list.")
    return values


def select_tiles(
    tiles: list[dict[str, Any]],
    xmin_start: int,
    xmin_end: int,
    ymin_start: int,
    ymin_end: int,
) -> list[dict[str, Any]]:
    """Keep tiles whose (x_min, y_min) origin lies inside the bbox
    (bounds INCLUSIVE)."""
    selected = [
        t for t in tiles
        if xmin_start <= int(t["x_min"]) <= xmin_end
        and ymin_start <= int(t["y_min"]) <= ymin_end
    ]
    selected.sort(key=lambda t: (int(t["x_min"]), int(t["y_min"])))
    return selected


def origin_window(bbox, tile_pitch: int) -> tuple[int, int, int, int]:
    """Exact origin-selection window for tiles overlapping *bbox*.

    A tile [o, o + pitch) x [p, p + pitch) overlaps the query
    [xs, xe) x [ys, ye) iff  o > xs - pitch  and  o < xe  (same for y).
    With integer tile origins and select_tiles()'s INCLUSIVE
    bounds, that is the window
    ``[floor(xs) - pitch + 1, ceil(xe) - 1]``.

    The previous convention expanded the HIGH side by +pitch as well,
    which admitted a full ring of tiles that could not contribute a
    single clipped point: 25 selected vs 4 useful on a 1 km^2 query at
    the default 500 m pitch (84% wasted downloads/decodes in stream
    mode, and inflated pre-flight tile counts).
    """
    import math as _math
    xs, ys, xe, ye = bbox
    return (int(_math.floor(xs)) - int(tile_pitch) + 1,
            int(_math.ceil(xe)) - 1,
            int(_math.floor(ys)) - int(tile_pitch) + 1,
            int(_math.ceil(ye)) - 1)


def download_one(url: str, dest: Path) -> tuple[str, str, str | None]:
    """Stream *url* to *dest* atomically. Returns ``(name, status, error)``."""
    name = dest.name
    if dest.exists():
        return (name, "skip", None)
    tmp = dest.with_suffix(dest.suffix + ".part")
    last_err: str | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as resp, \
                    tmp.open("wb") as out:
                declared = resp.headers.get("Content-Length")
                received = 0
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    out.write(chunk)
                    received += len(chunk)
            # A connection cut mid-body reads as a clean EOF, so without this
            # a short file would be renamed into place and then SKIPPED by
            # every later run (dest.exists() above), pinning the truncation
            # permanently. Comparing against the declared length turns that
            # into an ordinary retry.
            if declared is not None:
                try:
                    expected = int(declared)
                except ValueError:
                    expected = None
                if expected is not None and received != expected:
                    tmp.unlink(missing_ok=True)
                    raise OSError(
                        f"short download: got {received} of {expected} bytes")
            os.replace(tmp, dest)
            return (name, "ok", None)
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
            tmp.unlink(missing_ok=True)
            if attempt < MAX_RETRIES:
                time.sleep(2 * attempt)
    return (name, "FAILED", last_err)
