"""
Serve a 3D Tiles run directory for local visualisation.

    python serve_run.py <run_dir> [--port 8765]

The server itself now lives in the package, as ``voxelizer.serve_tiles`` -
same headers, same ``/viewer.html``, same ``?tileset=`` override, plus an
iTowns page at ``/viewer-itowns.html`` and confinement to the served
directory.  This file stays because every doc in this kit invokes it by name;
it forwards its arguments and nothing else:

    python serve_run.py ..\\Run1
    ->  open http://localhost:8765/viewer.html

The default port is pinned to 8765 here, which is what those docs tell you to
open.  (The package module's own default is 0 - a free port chosen by the OS -
so that several tilesets can be served at once.)  An explicit ``--port`` is
passed straight through.
"""

from __future__ import annotations

import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[2]
# In the delivered layout the package lives under VoxelisingPython/; in the
# original research layout it sat at the tree root. Try both.
REPO_ROOT = (_root / "VoxelisingPython") if (_root / "VoxelisingPython" / "voxelizer").is_dir() else _root


def main() -> int:
    sys.path.insert(0, str(REPO_ROOT))
    from voxelizer.serve_tiles import main as serve_tiles_main

    argv = sys.argv[1:]
    if not any(a == "--port" or a.startswith("--port=") for a in argv):
        argv = argv + ["--port", "8765"]
    return serve_tiles_main(argv)


if __name__ == "__main__":
    sys.exit(main())
