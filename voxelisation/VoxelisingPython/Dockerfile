# Python 3.13 is REQUIRED, not incidental: on Python 3.11.9 this stack
# corrupts the interpreter heap under numpy-heavy work. Roughly a quarter
# of processes running a bare `np.fromiter` loop failed with impossible
# errors (a tuple decaying to an int, a list of ints reported as
# "inhomogeneous", access violations during garbage collection). It is
# allocator-layout dependent (PYTHONMALLOC=debug hides it) and reproduces
# with no project code involved; 3.13 and 3.14 are clean on identical
# hardware with the same numpy build. Do not pin this image back to 3.11.
FROM python:3.13-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLBACKEND=Agg \
    DISPLAY=:99 \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1

# Default bind for both HTTP servers (serve_voxel_html, serve_tiles). A
# container's ports are published deliberately, and a server on 127.0.0.1
# inside it is reachable by nothing; desktops keep the localhost default
# because this variable is set only here, in the image.
ENV VOXELIZER_BIND=0.0.0.0

WORKDIR /workspace

# --- System packages for GUI (X11, VNC, noVNC, window manager) ---
# These are only needed for the voxelisation-gui service, but installing
# them here keeps a single image for both CLI and GUI services.
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    x11vnc \
    fluxbox \
    novnc \
    websockify \
    procps \
    # X11 libraries needed by tkinterdnd2 (drag-and-drop, optional)
    libxcursor1 \
    libxrender1 \
    libxft2 \
    libxtst6 \
    libxi6 \
    python3-tk \
    && rm -rf /var/lib/apt/lists/*

# --- Python dependencies ---
# numpy, matplotlib (Agg backend) and laspy (+ laszip, lazrs) all ship
# self-contained manylinux wheels, so no apt build toolchain or system GUI
# libraries are needed. MPLBACKEND=Agg keeps matplotlib headless inside the
# container. Laszip stays the pinned default backend everywhere (see
# voxelizer/io_laz.py); lazrs ships only for --retry-lazrs's single-thread
# second-opinion retry on a crashed tile.
COPY requirements.txt /tmp/requirements.txt

RUN python -m pip install --upgrade pip setuptools wheel && \
    python -m pip install -r /tmp/requirements.txt

# --- Application code ---
COPY voxelizer /workspace/voxelizer
COPY inputs /workspace/inputs

# --- GUI entrypoint (used by voxelisation-gui service) ---
COPY docker/gui/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Default: show CLI help (CLI service uses this)
CMD ["python", "-m", "voxelizer", "--help"]
