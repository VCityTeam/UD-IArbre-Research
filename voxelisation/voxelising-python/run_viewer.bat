@echo off
rem ============================================================
rem  Launches the streaming 3-D voxel viewer.
rem
rem    Double-click: auto-detects the latest run under outputs/
rem    Command-line: pass a directory to serve a specific run
rem
rem    The browser is always opened: this script passes --open itself,
rem    then appends whatever arguments you give it.
rem
rem    Examples:
rem      run_viewer.bat                          (latest run)
rem      run_viewer.bat outputs\Run3\area_output (specific run)
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem -- Pick the right Python interpreter ------------------------
if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
) else (
    set PYTHON=python
)

echo Starting voxel viewer...
%PYTHON% -m voxelizer.serve_voxel_html --open %*

echo.
pause
