@echo off
rem ============================================================
rem  Serves a finished output directory and opens it in a browser.
rem
rem    Drag & drop a run directory onto this file, or pass a path:
rem      scripts\serve_run.cmd outputs\Run3\area_output
rem
rem    The directory decides the server: a run holding *_stream.html
rem    gets the streaming viewer, a directory holding tileset.json
rem    gets the 3-D Tiles server (Cesium page by default).
rem    Extra flags pass through: --port, --bind, --no-open, --kind,
rem    --open-viewer, --open-page. Relative paths resolve from the
rem    package root.
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0.."

if "%~1"=="" (
    echo Usage: drag ^& drop a run directory onto this file, or:
    echo   scripts\serve_run.cmd outputs\Run3\area_output [flags]
    echo Relative paths resolve from the package root.
    pause
    exit /b 1
)

rem -- Pick the right Python interpreter ------------------------
if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
) else (
    set PYTHON=python
)

echo Serving %~1 ...
%PYTHON% -m voxelizer serve %*

echo.
pause
