#!/usr/bin/env bash
# ============================================================
#  Launches the streaming 3-D voxel viewer.
#
#    The browser is always opened: this script passes --open itself,
#    then appends whatever arguments you give it.
#
#    ./scripts/run_viewer.sh                          (latest run)
#    ./scripts/run_viewer.sh outputs/Run3/area_output (specific run)
# ============================================================
set -e
cd "$(dirname "$0")/.."

# Pick the right Python interpreter
if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif command -v python >/dev/null 2>&1; then
    PYTHON="python"
else
    PYTHON="python3"
fi

echo "Starting voxel viewer..."
exec "$PYTHON" -m voxelizer.serve_voxel_html --open "$@"
