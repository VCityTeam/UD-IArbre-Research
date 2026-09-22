#!/usr/bin/env bash
# ============================================================
#  Serves a finished output directory and opens it in a browser.
#
#    ./scripts/serve_run.sh outputs/Run3/area_output [flags]
#
#  The directory decides the server: a run holding *_stream.html
#  gets the streaming viewer, a directory holding tileset.json
#  gets the 3-D Tiles server (Cesium page by default).
#  Extra flags pass through: --port, --bind, --no-open, --kind,
#  --open-viewer, --open-page. Relative paths resolve from the
#  package root.
# ============================================================
set -e
cd "$(dirname "$0")/.."

if [ $# -eq 0 ]; then
    echo "Usage: ./scripts/serve_run.sh outputs/Run3/area_output [flags]"
    echo "Relative paths resolve from the package root."
    exit 1
fi

# Pick the right Python interpreter
if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
else
    PYTHON="python"
fi

echo "Serving $1 ..."
exec "$PYTHON" -m voxelizer serve "$@"
