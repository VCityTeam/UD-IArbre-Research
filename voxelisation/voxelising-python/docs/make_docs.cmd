@echo off
rem Regenerate the Doxygen documentation of the voxelizer package.
rem Needs Doxygen (winget install DimitriVanHeesch.Doxygen) and Graphviz (winget install Graphviz.Graphviz).
setlocal
cd /d "%~dp0"
set "PATH=%PATH%;C:\Program Files\doxygen\bin;C:\Program Files\Graphviz\bin"
where doxygen >nul 2>nul || (echo doxygen introuvable & exit /b 1)
where dot >nul 2>nul || (echo Graphviz dot introuvable & exit /b 1)
doxygen Doxyfile
if errorlevel 1 exit /b 1
echo Documentation : %~dp0doxygen\html\index.html
if exist doxygen_warnings.log for /f %%n in ('find /c /v "" ^< doxygen_warnings.log') do echo Avertissements : %%n (doxygen_warnings.log)
endlocal
