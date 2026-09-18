"""
Click-to-launch ``.cmd`` files written beside the pipeline's visual outputs.
@ingroup t0_socle


A run leaves a directory of artifacts that are useless on their own: an
``area_stream.html`` that kept a ``.bin`` sidecar cannot fetch it over
``file://`` (a small export whose payload was inlined into the page is
self-contained and does open that way), and a ``tileset.json`` is not a
document a browser renders at all.  Both need a local HTTP origin.  The knowledge of which server, with which
arguments, for which directory is exactly what these generated files carry, so
that opening a result is one double-click rather than a remembered command
line:

  * ``view_stream.cmd``  - beside ``*_stream.html``: starts
    voxelizer.serve_voxel_html on that directory and opens the page.
  * ``view_cesium.cmd`` / ``view_itowns.cmd`` - beside ``tileset.json``: start
    voxelizer.serve_tiles and open the matching bundled viewer.
  * ``launch_unreal.cmd`` - the second click, for the Unreal path.

For the three SERVER launchers - ``view_stream.cmd``, ``view_cesium.cmd`` and
``view_itowns.cmd`` - the console window the ``.cmd`` opens IS the server's
lifetime; closing it stops the server, and each of those files says so in its
own header.  ``launch_unreal.cmd`` starts no server, so it carries no such
line: it hands the project to the editor and exits.

Portability
-----------
A generated file that hard-codes an absolute interpreter path works on exactly
one machine, and these outputs are copied between machines, archived, and
shipped to other people.  So no absolute path is ever written.  What is baked in at generation time is the RELATIVE path from
the output directory up to the repository root - ``..\\..`` and so on - which
survives any relocation that moves the checkout as a whole.  ``%~dp0`` (the
directory of the running ``.cmd``) supplies the absolute part at run time.

The launcher then tries, in order:

  1. the repository's own virtualenv, ``<repo>\\.venv\\Scripts\\python.exe``;
  2. ``python`` from ``PATH``, with ``PYTHONPATH`` pointed at the repository so
     ``-m voxelizer.*`` resolves regardless of the working directory;
  3. failing both, a message naming the two places it looked and saying to
     install Python or create the virtualenv (the ``:nopython`` branch).

There is a fourth outcome, and it is a different failure: an interpreter was
found but ``import voxelizer`` fails under it.  That is the one case relative
paths cannot survive - an output directory copied out of the tree, or onto
another drive - so the ``:norepo`` branch explains that the folder has to sit
inside the checkout and where to put it back.  Both branches print a sentence
rather than failing with a Python traceback.

Line endings are CRLF, written explicitly.  ``cmd.exe`` tolerates LF in most
constructs but not all of them, and these files are generated at run time into
output directories that no checkout covers, so nothing outside this module can
correct them afterwards.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# The repository root: this file is <repo>/voxelizer/launchers.py.
REPO_ROOT = Path(__file__).resolve().parent.parent

# The Unreal project, relative to whichever directory actually holds
# ``tilesexport``. That is not always the repository root: in this tree
# tilesexport/ is a CHILD of REPO_ROOT, while in the delivery layout the
# package sits one level down and tilesexport is its SIBLING. Resolving the
# path against REPO_ROOT alone therefore names nothing in the second layout,
# and every generated launcher went straight to its :noproject branch. Probed
# at generation time instead of assumed, so one module serves both trees.
UPROJECT_REL = r"tilesexport\UEViz\IarbreVoxels\IarbreVoxels.uproject"


def uproject_base() -> Path:
    """The directory ``UPROJECT_REL`` should be resolved against.

    ``REPO_ROOT`` when it holds ``tilesexport/``, its parent when the parent
    does. Falls back to ``REPO_ROOT`` when neither does, so the generated file
    still names one definite path and its ``:noproject`` branch explains the
    miss rather than the launcher failing silently.
    """
    for base in (REPO_ROOT, REPO_ROOT.parent):
        if (base / "tilesexport").is_dir():
            return base
    return REPO_ROOT


def uproject_from_repo() -> str:
    """``UPROJECT_REL``, prefixed so it resolves from the repository root.

    The generated ``.cmd`` builds every path from ``%REPO%``, so a project
    living beside the repository is reached with a leading ``..``.
    """
    rel_base = os.path.relpath(uproject_base(), REPO_ROOT)
    if rel_base == os.curdir:
        return UPROJECT_REL
    return rel_base.replace("/", "\\") + "\\" + UPROJECT_REL


def _crlf(lines: list[str]) -> str:
    """Join *lines* with explicit CRLF, ending with one."""
    return "\r\n".join(lines) + "\r\n"


def _write(path: Path, lines: list[str]) -> Path:
    """Write *lines* to *path* as ASCII with explicit CRLF line ends, log it
    and return the path."""
    # newline="" keeps Python's translation layer out of it: the \r\n in the
    # text is what lands on disk, on any platform this is generated from.
    path.write_text(_crlf(lines), encoding="ascii", newline="")
    logger.info("wrote %s", path)
    return path


def _repo_rel(out_dir: Path) -> str | None:
    """Relative path from *out_dir* up to the repository root.

    ``None`` when no relative path exists (a different drive), which is the
    case the generated file's own error branch is written for.
    """
    try:
        rel = os.path.relpath(REPO_ROOT, Path(out_dir).resolve())
    except ValueError:
        return None
    return rel


def _preamble(title: str, out_dir: Path, extra: list[str]) -> list[str]:
    """The shared header: comments, then the python-interpreter search."""
    rel = _repo_rel(out_dir)
    lines = [
        "@echo off",
        "rem " + "=" * 66,
        f"rem  {title}",
        "rem",
        "rem  Double-click this file. The console window it opens IS the",
        "rem  server: close this window to stop the viewer.",
        "rem",
    ]
    lines += [("rem  " + e).rstrip() for e in extra]
    lines += [
        "rem",
        "rem  This file contains no absolute path. It finds the repository",
        "rem  through a path relative to its own location, so it keeps working",
        "rem  if the checkout moves - but NOT if this output directory is",
        "rem  copied out of the checkout. See the message at the bottom.",
        "rem",
        "rem  VOXELIZER_NO_BROWSER=1 in the environment starts the server",
        "rem  without opening a browser (for scripted use).",
        "rem " + "=" * 66,
        "setlocal",
        "set \"PYTHONDONTWRITEBYTECODE=1\"",
        "set \"OUTDIR=%~dp0\"",
        "rem %~dp0 ends with a backslash, and a backslash immediately before a",
        "rem closing quote escapes THAT QUOTE when the argument is handed to a",
        "rem program: the served directory would arrive with the rest of the",
        "rem command line glued onto it. Drop it.",
        "if \"%OUTDIR:~-1%\"==\"\\\" set \"OUTDIR=%OUTDIR:~0,-1%\"",
    ]
    if rel is None:
        # No relative path exists (another drive). Point REPO at this very
        # directory: neither candidate interpreter will import voxelizer from
        # it, so the run lands in the explanatory branch instead of guessing.
        lines.append("set \"REPO=%~dp0\"")
    else:
        lines.append(f"set \"REPO=%~dp0{rel}\"")
    lines += [
        "if \"%REPO:~-1%\"==\"\\\" set \"REPO=%REPO:~0,-1%\"",
        "",
        "rem -- 1. the repository's own virtualenv --------------------------",
        "set \"PY=\"",
        "if exist \"%REPO%\\.venv\\Scripts\\python.exe\" "
        "set \"PY=%REPO%\\.venv\\Scripts\\python.exe\"",
        "",
        "rem -- 2. otherwise python from PATH ------------------------------",
        "if not defined PY (",
        "    where python >nul 2>&1",
        "    if not errorlevel 1 set \"PY=python\"",
        ")",
        "if not defined PY goto :nopython",
        "",
        "rem The package is imported from the repository root, whatever the",
        "rem working directory is when this file is double-clicked.",
        "set \"PYTHONPATH=%REPO%\"",
        "\"%PY%\" -c \"import voxelizer\" >nul 2>&1",
        "if errorlevel 1 goto :norepo",
        "",
    ]
    return lines


def _epilogue(what: str) -> list[str]:
    """The shared tail of a server launcher: the "Viewer stopped" pause after
    the server returns, then the ``:nopython`` and ``:norepo`` error branches
    the preamble jumps to; *what* names the viewer in the ``:norepo`` text."""
    return [
        "",
        "echo.",
        "echo Viewer stopped.",
        "pause",
        "exit /b 0",
        "",
        ":nopython",
        "echo.",
        "echo No Python interpreter found.",
        "echo   Looked for: \"%REPO%\\.venv\\Scripts\\python.exe\"",
        "echo   and for a \"python\" on PATH. Neither exists.",
        "echo Install Python, or create the repository virtualenv.",
        "pause",
        "exit /b 1",
        "",
        ":norepo",
        "echo.",
        "echo The voxelizer package was not found at:",
        "echo   \"%REPO%\"",
        f"echo This launcher opens {what} by running the voxelizer package,",
        "echo which it locates through a path relative to its own folder.",
        "echo That only works while this folder sits inside the repository",
        "echo checkout, where the run wrote it. Move the folder back (or copy",
        "echo the whole checkout), then double-click this file again.",
        "pause",
        "exit /b 1",
    ]


# ---------------------------------------------------------------------------
# streaming viewer
# ---------------------------------------------------------------------------
def write_stream_launcher(out_dir: str | Path,
                          html_name: str = "area_stream.html") -> Path:
    """Write ``view_stream.cmd`` beside *html_name* in *out_dir*.

    One double-click serves this directory over HTTP and opens the streaming
    page.  Returns the path written.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = _preamble(
        f"Streaming voxel viewer - {html_name}",
        out_dir,
        [
            f"Serves THIS folder over HTTP and opens {html_name}.",
            "A page that kept a .bin sidecar cannot be opened from the file",
            "system directly: it fetches byte ranges of that sidecar, which",
            "needs an origin. A small export whose payload was inlined into",
            "the page is self-contained and does open over file:// - this",
            "serves it either way, so you need not know which it is.",
            "",
            "Port 0 means 'ask the operating system for a free port', so",
            "several runs can be viewed side by side; the console prints",
            "the url it got.",
        ])
    lines += [
        "echo Starting the streaming voxel viewer ...",
        f"\"%PY%\" -m voxelizer.serve_voxel_html \"%OUTDIR%\" --port 0 "
        f"--open --open-page {html_name}",
    ]
    lines += _epilogue("the streaming viewer")
    return _write(out_dir / "view_stream.cmd", lines)


# ---------------------------------------------------------------------------
# 3-D Tiles viewers + Unreal
# ---------------------------------------------------------------------------
def _write_tileset_viewer(out_dir: Path, engine: str) -> Path:
    """Write ``view_<engine>.cmd`` in *out_dir* for *engine* ``"cesium"`` or
    ``"itowns"``: it runs ``voxelizer.serve_tiles`` on that folder with
    ``--open-viewer <engine>``. Returns the path written."""
    label = {"cesium": "CesiumJS", "itowns": "iTowns"}[engine]
    lines = _preamble(
        f"3-D Tiles viewer - {label}",
        out_dir,
        [
            "Serves THIS folder (tileset.json + tile_*.glb) and opens the",
            f"bundled {label} page against it.",
            "",
            f"{label} itself is fetched from a CDN, so the page needs network",
            "access on first load; the tiles are local.",
            "",
            "The port is chosen by the operating system, so the Cesium and",
            "iTowns launchers can both run at once for a comparison.",
        ])
    lines += [
        f"echo Starting the {label} 3-D Tiles viewer ...",
        f"\"%PY%\" -m voxelizer.serve_tiles \"%OUTDIR%\" "
        f"--open-viewer {engine}",
    ]
    lines += _epilogue(f"the {label} viewer")
    return _write(out_dir / f"view_{engine}.cmd", lines)


def write_unreal_launcher(out_dir: str | Path) -> Path:
    """Write ``launch_unreal.cmd`` in *out_dir*.

    The second click of the Unreal path: this opens the repository's Unreal
    project in the editor.  It is deliberately machine-dependent - Unreal
    Engine is a multi-gigabyte install this repository neither ships nor can
    locate - so the engine comes from ``UE_ENGINE_PATH`` in the environment,
    and the file says so when it is not set.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rel = _repo_rel(out_dir)
    lines = [
        "@echo off",
        "rem " + "=" * 66,
        "rem  Unreal Engine - open the IArbre voxel project",
        "rem",
        "rem  This is the SECOND click of the Unreal path: it opens the",
        "rem  editor. The tiles are served by view_cesium.cmd (first click)",
        "rem  or by any HTTP server on this folder; inside the editor the",
        "rem  Cesium3DTileset actor points at that url.",
        "rem",
        "rem  MACHINE-DEPENDENT, unavoidably: Unreal Engine is a separate",
        "rem  multi-gigabyte install that this repository does not ship and",
        "rem  cannot guess the location of. Set UE_ENGINE_PATH once:",
        "rem",
        "rem    setx UE_ENGINE_PATH \"C:\\Program Files\\Epic Games\\UE_5.8\"",
        "rem",
        "rem  Point it at the engine ROOT (the folder holding",
        "rem  Engine\\Binaries\\Win64\\UnrealEditor.exe), or directly at an",
        "rem  UnrealEditor.exe - both are accepted. The project asks for",
        "rem  engine association 5.8.",
        "rem",
        "rem  Everything else here is relative: the project is found through",
        "rem  a path relative to this file, so no machine path is written.",
        "rem " + "=" * 66,
        "setlocal",
    ]
    if rel is None:
        lines.append("set \"REPO=%~dp0\"")
    else:
        lines.append(f"set \"REPO=%~dp0{rel}\"")
    lines += [
        # A trailing backslash would escape the closing quote of any argument
        # built from this variable; see write_stream_launcher's preamble.
        "if \"%REPO:~-1%\"==\"\\\" set \"REPO=%REPO:~0,-1%\"",
        f"set \"UPROJECT=%REPO%\\{uproject_from_repo()}\"",
        "",
        "if not defined UE_ENGINE_PATH goto :noengine",
        "",
        "set \"EDITOR=%UE_ENGINE_PATH%\"",
        "if /i not \"%EDITOR:~-4%\"==\".exe\" "
        "set \"EDITOR=%UE_ENGINE_PATH%\\Engine\\Binaries\\Win64\\UnrealEditor.exe\"",
        "if not exist \"%EDITOR%\" goto :badengine",
        "if not exist \"%UPROJECT%\" goto :noproject",
        "",
        "echo Opening %UPROJECT%",
        "echo   with %EDITOR%",
        "echo First launch compiles shaders - expect several minutes.",
        "start \"\" \"%EDITOR%\" \"%UPROJECT%\"",
        "exit /b 0",
        "",
        ":noengine",
        "echo.",
        "echo UE_ENGINE_PATH is not set, so the Unreal editor cannot be found.",
        "echo Set it to your Unreal Engine 5.8 installation root, e.g.",
        "echo.",
        "echo   setx UE_ENGINE_PATH \"C:\\Program Files\\Epic Games\\UE_5.8\"",
        "echo.",
        "echo Then open a NEW console (setx only affects new ones) and",
        "echo double-click this file again.",
        "pause",
        "exit /b 1",
        "",
        ":badengine",
        "echo.",
        "echo UE_ENGINE_PATH is set to:",
        "echo   \"%UE_ENGINE_PATH%\"",
        "echo but no editor executable was found at:",
        "echo   \"%EDITOR%\"",
        "echo Point UE_ENGINE_PATH at the engine root (the folder holding",
        "echo Engine\\Binaries\\Win64\\UnrealEditor.exe) or at the .exe itself.",
        "pause",
        "exit /b 1",
        "",
        ":noproject",
        "echo.",
        "echo The Unreal project was not found at:",
        "echo   \"%UPROJECT%\"",
        "echo This launcher reaches it through a path relative to its own",
        "echo folder, fixed when the export ran, so it holds only while this",
        "echo folder stays inside the checkout that also holds tilesexport.",
        "echo If this output was copied out of that tree, or shipped without",
        "echo tilesexport, open the .uproject from the Unreal editor instead.",
        "pause",
        "exit /b 1",
    ]
    return _write(out_dir / "launch_unreal.cmd", lines)


def write_tileset_launchers(out_dir: str | Path) -> list[Path]:
    """Write ``view_cesium.cmd``, ``view_itowns.cmd`` and
    ``launch_unreal.cmd`` beside a ``tileset.json``.  Returns the paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = [_write_tileset_viewer(out_dir, "cesium"),
               _write_tileset_viewer(out_dir, "itowns")]
    written.append(write_unreal_launcher(out_dir))
    return written
