@echo off
rem ============================================================
rem  Serves a 3-D Tiles directory and opens the Cesium viewer page.
rem
rem    Drag & drop a tileset directory (holding tileset.json) onto
rem    this file, or pass a path:
rem      scripts\serve_tiles_cesium.cmd tilesexport\Run1
rem
rem    Extra flags appended after the path override the defaults
rem    (e.g. --port 8765, --no-open). Relative paths resolve from
rem    the package root.
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0.."

if "%~1"=="" (
    echo Usage: drag ^& drop a tileset directory onto this file, or:
    echo   scripts\serve_tiles_cesium.cmd tilesexport\Run1 [flags]
    pause
    exit /b 1
)

if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
) else (
    set PYTHON=python
)

set "DIR=%~1"
set REST=
:collect
shift
if "%~1"=="" goto run
set REST=!REST! %1
goto collect
:run
echo Serving %DIR% with the Cesium page ...
%PYTHON% -m voxelizer serve "%DIR%" --kind tiles --open-viewer cesium!REST!

echo.
pause
