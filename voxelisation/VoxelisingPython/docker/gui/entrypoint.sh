#!/bin/bash
# ===================================================================
#  Entrypoint for the voxelisation GUI service.
#
#  Starts Xvfb (virtual display), fluxbox (window manager), x11vnc,
#  noVNC (web-based VNC client), and then launches the Tkinter GUI.
#
#  Usage:
#    docker compose up voxelisation-gui
#    Open http://localhost:6080 in your browser
# ===================================================================
set -e

# Kill BLAS threading to prevent access violations on Windows with OpenBLAS
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

echo "=== Starting voxelisation GUI environment ==="

# Start Xvfb (virtual X11 display on :99)
echo "[1/4] Starting Xvfb on display :99 ..."
Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!
sleep 1

# Verify Xvfb started
if ! kill -0 $XVFB_PID 2>/dev/null; then
    echo "ERROR: Xvfb failed to start"
    exit 1
fi

# Start fluxbox window manager
echo "[2/4] Starting fluxbox window manager ..."
DISPLAY=:99 fluxbox &
sleep 1

# Start x11vnc (VNC server on port 5900, no password for local use)
echo "[3/4] Starting x11vnc on port 5900 ..."
x11vnc -display :99 -forever -nopw -shared -rfbport 5900 -bg -o /tmp/x11vnc.log
sleep 1

# Start noVNC (web-based VNC client on port 6080)
echo "[4/4] Starting noVNC on port 6080 ..."
# Find the noVNC utils directory (location varies by distro)
NOVNC_DIR=""
for dir in /usr/share/novnc /usr/share/novnc/utils /opt/noVNC; do
    if [ -d "$dir" ]; then
        NOVNC_DIR="$dir"
        break
    fi
done

if [ -z "$NOVNC_DIR" ]; then
    echo "ERROR: noVNC not found. Install novnc package."
    exit 1
fi

# Start websockify to bridge VNC to WebSocket
websockify --web="$NOVNC_DIR" 6080 localhost:5900 &
WEBSOCKIFY_PID=$!
sleep 2

echo ""
echo "============================================="
echo "  Voxelizer GUI is starting!"
echo "  "
echo "  Open http://localhost:6080 in your browser"
echo "  to access the Tkinter GUI."
echo "============================================="
echo ""

# Launch the Tkinter GUI
cd /workspace
DISPLAY=:99 python -m voxelizer.gui_area &
GUI_PID=$!

# Wait for either the GUI or websockify to exit
wait -n $GUI_PID $WEBSOCKIFY_PID
EXIT_CODE=$?

echo "Process exited with code $EXIT_CODE"
exit $EXIT_CODE
