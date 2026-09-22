"""
Shared CLI flag clusters.
@ingroup t0_socle


__main__ and area_cli declare their overlapping cell / viz3d-geometry flags
through these helpers; viz3d_cli shares the cell cluster
(``add_cell_args``) and the ``positive_int`` validator and declares only
its viz3d-geometry flags itself. The shared declarations exist so the
copies cannot drift apart. Defaults are PARAMETERS each caller may
override.

The two HTTP servers (serve_voxel_html, serve_tiles) share their ``--bind``
default through default_bind() for the same reason.
"""
import argparse
import os
from pathlib import Path

# Loopback: what is being served is a directory of the machine's own files,
# and the viewer runs on the machine that ran the pipeline.
LOCALHOST = "127.0.0.1"

# Environment override for that default. A container publishes its ports
# deliberately and must listen on every interface for the published port to
# reach anything; a desktop must not put a directory of its files on the LAN
# merely because a launcher was double-clicked. One `ENV VOXELIZER_BIND` in
# the image therefore moves the whole container, launchers included, without
# a single command line changing - and without weakening the desktop default.
BIND_ENV_VAR = "VOXELIZER_BIND"


def default_bind(env=None) -> str:
    """The ``--bind`` default: ``$VOXELIZER_BIND`` if set, else localhost.

    An explicit ``--bind`` on the command line always wins - this only
    supplies argparse's default, so precedence is flag > environment >
    localhost. An empty or whitespace-only variable is treated as unset
    rather than as a request to bind the empty string (which means "all
    interfaces" to the socket layer, i.e. the opposite of the default).
    """
    raw = (env if env is not None else os.environ).get(BIND_ENV_VAR, "")
    return raw.strip() or LOCALHOST


def positive_int(s: str) -> int:
    """argparse type shared by --max-boxes and --merge-band-intervals:
    reject 0 and negatives.

    The reason it exists is --max-boxes. `estimate_thin_stride` treats 0 as
    falsy - i.e. "no budget" - so ``--max-boxes 0`` silently rendered
    EVERYTHING, and a negative value crashed later with an opaque
    ``math domain error``. ``--merge-band-intervals`` (area_cli) takes the
    same validator because a band of 0 or fewer intervals is equally
    meaningless; only the advice below is --max-boxes-specific, and the
    message says which flag it applies to.
    """
    v = int(s)
    if v <= 0:
        raise argparse.ArgumentTypeError(
            f"must be a positive integer (got {s}); --max-boxes has no "
            f"'unlimited' spelling - for an uncapped full-detail export use "
            f"--viz3d-stream / viz3d_cli stream")
    return v


DEFAULT_CELL_XY = 0.5
DEFAULT_CELL_Z = 0.5


def add_cell_args(p, *, cell_xy=DEFAULT_CELL_XY, cell_z=DEFAULT_CELL_Z):
    """Declare ``--cell-xy`` and ``--cell-z`` (metres, float) on parser *p*.

    The keyword defaults let a caller override the project-wide 0.5 m values
    without redeclaring the flags.
    """
    # 0.5 m / 0.5 m is the project-wide default cell size, kept identical
    # across every entry point on purpose so the CLI, GUI and library never
    # disagree about what an unqualified run means. Every entry point takes
    # it from the two constants above rather than repeating the number, and
    # tests/test_cli_contracts.py pins them against each parser.
    p.add_argument("--cell-xy", type=float, default=cell_xy,
                   help=f"Horizontal cell size in metres (default {cell_xy}).")
    p.add_argument("--cell-z", type=float, default=cell_z,
                   help=f"Vertical cell size in metres (default {cell_z}).")


def add_viz3d_geom_args(p, *, max_boxes=5_000_000, roi_size=200.0):
    """Declare the viz3d geometry flags on parser *p*.

    Adds ``--max-boxes`` (validated by positive_int()), ``--roi-size``,
    ``--roi-cx`` / ``--roi-cy`` (None means the area centre) and the
    ``--no-full`` / ``--no-roi`` switches; *max_boxes* and *roi_size* are the
    caller's defaults.
    """
    p.add_argument("--max-boxes", type=positive_int, default=max_boxes,
                   help="Box budget for the full 3-D view (auto-thinned "
                        "by striding above it). Must be positive; for an "
                        "uncapped export use the streaming viewer.")
    p.add_argument("--roi-size", type=float, default=roi_size,
                   help="Side length (m) of the region-of-interest view.")
    p.add_argument("--roi-cx", type=float, default=None,
                   help="ROI centre x (default: area centre).")
    p.add_argument("--roi-cy", type=float, default=None,
                   help="ROI centre y (default: area centre).")
    p.add_argument("--no-full", action="store_true",
                   help="Skip the full-area 3-D view.")
    p.add_argument("--no-roi", action="store_true",
                   help="Skip the ROI 3-D view.")
