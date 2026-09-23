#!/usr/bin/env bash
# ============================================================
#  Launches the voxelizer GUI (Tkinter): the Linux/macOS twin of
#  run_area.bat in this same directory.
#
#    * Single file (drag & drop / browse) -> whole-file
#      laspy.read path, launched as a `python -m voxelizer
#      single` child process.
#
#    * Area by coordinates -> enter an RGF93/CC46 (EPSG:3946)
#      bounding box and a tile folder; every intersecting tile is
#      streamed onto one shared grid, clipped to the exact
#      rectangle. A pre-flight cost dialog always runs first
#      (RAM budget: continue / shard).
#
#  Outputs go to the directory configured in the GUI (default:
#  outputs/Run<next>/area_output).
# ============================================================
set -e
cd "$(dirname "$0")/.."

if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif command -v python >/dev/null 2>&1; then
    PYTHON="python"
else
    PYTHON="python3"
fi

echo "Opening area voxelizer GUI..."
exec "$PYTHON" -m voxelizer.gui_area "$@"
