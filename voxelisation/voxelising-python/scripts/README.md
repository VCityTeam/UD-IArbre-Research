# scripts/ - one-purpose launchers

Click-to-run entry points for the parts of the toolkit that the GUI does
not cover, plus Linux/macOS twins of the Windows launchers at the package
root. Every script changes to the package root before running, so relative
paths in arguments resolve from there, and each one picks the `.venv`
interpreter when it exists.

## Servers

| Script | Purpose |
|---|---|
| `serve_run.cmd` / `serve_run.sh` | Serve any finished output directory; the directory decides the server (streaming viewer for a `*_stream.html` run, 3-D Tiles server for a `tileset.json` directory). Drag & drop a folder onto the `.cmd`. |

It wraps `python -m voxelizer serve` and passes extra flags through:
`--port`, `--bind`, `--no-open`, `--open-page`, `--open-viewer`. Flags you
append after the directory override the wrapper's own defaults. For a 3-D
Tiles directory with a specific viewer, use
`python -m voxelizer serve DIR --kind tiles --open-viewer cesium|itowns`
(or double-click the `view_cesium.cmd` / `view_itowns.cmd` a tileset export
writes beside itself).

Two more server entry points ship outside this folder:

* `run_viewer.bat` / `run_viewer.sh` (package root) serve the most recent
  streaming run without any argument, through
  `python -m voxelizer.serve_voxel_html --open`.
* every run that writes a viewer also writes double-clickable launchers
  beside it: `view_stream.cmd` next to a `*_stream.html`, and
  `view_cesium.cmd` / `view_itowns.cmd` next to a `tileset.json`; each
  serves its own folder on a free port.
* `python -m voxelizer serve` replaces the historical `serve_run.py`
  helper of the export kit: it reads the server kind off the output
  directory and keeps each server's own defaults (an OS-chosen free port
  unless `--port` says otherwise).

## GUI twin

| Script | Purpose |
|---|---|
| `run_area.sh` | Linux/macOS twin of the root `run_area.bat`: opens the Tkinter GUI (single file or area by coordinates; the Export & Serve tab covers 3-D Tiles export and serving from a finished run). |

The docker twins (`dockerdownload.sh`, `dockerruncpu.sh`) live in `../docker/`
with the `Dockerfile`, `docker-compose.yml` and the entrypoint.

## Serving from docker

The servers bind to localhost inside the container, so publish a port and
bind to all interfaces, then open the page on the host yourself:

    docker compose -f docker/docker-compose.yml run --rm -p 8000:8000 voxelisation \
        python -m voxelizer serve outputs/Run1/area_output \
        --port 8000 --bind 0.0.0.0 --no-open

Then open `http://localhost:8000/` on the host. The same pattern works for
a tiles directory (`--kind tiles`); the viewer pages are
`/viewer.html` (Cesium) and `/viewer-itowns.html` (iTowns).

## Other launchers (unchanged, for reference)

| File | Purpose |
|---|---|
| `run_area.bat` | The GUI (Windows). |
| `run_viewer.bat` / `run_viewer.sh` | Streaming viewer, latest run auto-detected. |
| `docker/dockerdownload.cmd` (`+ .sh`) | LAZ download example through docker compose. |
| `docker/dockerruncpu.cmd` (`+ .sh`) | Single-tile processing example through docker compose. |
| `docker/docker-compose.yml` | The compose service (`voxelisation`) all docker examples use. |
