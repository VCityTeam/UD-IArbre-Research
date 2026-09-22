#!/usr/bin/env sh
# Regenerate the Doxygen documentation of the voxelizer package.
# Needs doxygen (>= 1.9.2) and graphviz: apt install doxygen graphviz
set -e
cd "$(dirname "$0")"
command -v doxygen >/dev/null || { echo "doxygen introuvable"; exit 1; }
command -v dot >/dev/null || { echo "graphviz dot introuvable"; exit 1; }
doxygen Doxyfile
echo "Documentation : $(pwd)/doxygen/html/index.html"
[ -f doxygen_warnings.log ] && echo "Avertissements : $(wc -l < doxygen_warnings.log) (doxygen_warnings.log)"
