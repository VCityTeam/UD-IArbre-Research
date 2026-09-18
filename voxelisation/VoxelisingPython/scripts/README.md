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
| `serve_tiles_cesium.cmd` / `.sh` | Serve a 3-D Tiles directory and open the bundled Cesium page. |
| `serve_tiles_itowns.cmd` / `.sh` | Serve a 3-D Tiles directory and open the bundled iTowns page. |

All three wrap `python -m voxelizer serve` and pass extra flags through:
`--port`, `--bind`, `--no-open`, `--open-page`. Flags you append after the
directory override the wrapper's own defaults.

Two more server entry points ship outside this folder:

* `run_viewer.bat` / `run_viewer.sh` (package root) serve the most recent
  streaming run without any argument, through
  `python -m voxelizer.serve_voxel_html --open`.
* every run that writes a viewer also writes double-clickable launchers
  beside it: `view_stream.cmd` next to a `*_stream.html`, and
  `view_cesium.cmd` / `view_itowns.cmd` / `launch_unreal.cmd` next to a
  `tileset.json`; each serves its own folder on a free port.
* `tilesexport/AssistingRuns/serve_run.py` is the historical name for the
  tiles server used throughout the export kit's docs; it forwards to
  `voxelizer.serve_tiles` with the port pinned to 8765.

## GUI and docker twins

| Script | Purpose |
|---|---|
| `run_area.sh` | Linux/macOS twin of the root `run_area.bat`: opens the Tkinter GUI (single file or area by coordinates; the Export & Serve tab covers 3-D Tiles export and serving from a finished run). |
| `dockerdownload.sh` | Twin of the root `dockerdownload.cmd`: fetch Grand Lyon LAZ tiles by bounding box through docker compose. |
| `dockerruncpu.sh` | Twin of the root `dockerruncpu.cmd`: process one tile through docker compose; a sharded area example sits in the comments. |

## Serving from docker

The servers bind to localhost inside the container, so publish a port and
bind to all interfaces, then open the page on the host yourself:

    docker compose run --rm -p 8000:8000 voxelisation \
        python -m voxelizer serve outputs/Run1/area_output \
        --port 8000 --bind 0.0.0.0 --no-open

Then open `http://localhost:8000/` on the host. The same pattern works for
a tiles directory (`--kind tiles`); the viewer pages are
`/viewer.html` (Cesium) and `/viewer-itowns.html` (iTowns).

## Root launchers (unchanged, for reference)

| File | Purpose |
|---|---|
| `run_area.bat` | The GUI (Windows). |
| `run_viewer.bat` / `run_viewer.sh` | Streaming viewer, latest run auto-detected. |
| `dockerdownload.cmd` | LAZ download example through docker compose. |
| `dockerruncpu.cmd` | Single-tile processing example through docker compose. |
| `docker-compose.yml` | The compose service (`voxelisation`) all docker examples use. |
