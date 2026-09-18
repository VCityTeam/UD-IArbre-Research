#!/usr/bin/env bash
# ============================================================
#  Serves a 3-D Tiles directory and opens the iTowns viewer page.
#
#    ./scripts/serve_tiles_itowns.sh tilesexport/Run1 [flags]
#
#  Extra flags appended after the path override the defaults
#  (e.g. --port 8765, --no-open). Relative paths resolve from
#  the package root.
# ============================================================
set -e
cd "$(dirname "$0")/.."

if [ $# -eq 0 ]; then
    echo "Usage: ./scripts/serve_tiles_itowns.sh tilesexport/Run1 [flags]"
    exit 1
fi

if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
else
    PYTHON="python"
fi

dir="$1"
shift
echo "Serving $dir with the iTowns page ..."
exec "$PYTHON" -m voxelizer serve "$dir" --kind tiles --open-viewer itowns "$@"
