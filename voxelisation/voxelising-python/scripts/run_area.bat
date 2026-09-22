@echo off
rem ============================================================
rem  Launches the voxelizer GUI (Tkinter) with two modes:
rem
rem    * Single file (drag & drop / browse) -> whole-file
rem      laspy.read path, launched as a `python -m voxelizer
rem      single` child process.
rem
rem    * Area by coordinates -> enter an RGF93/CC46 (EPSG:3946) bounding box
rem      and a tile folder; every intersecting tile is streamed
rem      onto one shared grid, clipped to the exact rectangle.
rem      A pre-flight cost dialog always runs first (RAM budget:
rem      continue / shard).
rem
rem  Outputs go to the directory configured in the GUI (default:
rem  outputs\Run<next>\area_output).
rem
rem  NOTE: the dialog never runs heavy work in-process - every
rem  job is a SUBPROCESS, so the window stays responsive and
rem  Stop terminates the job cleanly (optionally wrapped by
rem  voxel_runner_diagnos for live CPU/RAM diagnostics).
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0.."

rem -- Pick the right Python interpreter ------------------------
if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
) else (
    set PYTHON=python
)

echo Opening area voxelizer GUI...
%PYTHON% -m voxelizer.gui_area

echo.
echo Done.
pause
