#!/bin/sh
set -eu

cat > /app/config.js <<EOF
window.SUNLIGHT_DEMO_CONFIG = {
  tilesetUrl: "${DEMO_TILESET_URL:-/data/inputs/tileset.json}",
  csvUrl: "${DEMO_CSV_URL:-/data/output/output.csv}"
};
EOF

exec npx http-server -p 8080 -a 0.0.0.0 -c-1
