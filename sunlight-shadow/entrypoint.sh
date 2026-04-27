#!/bin/sh
set -eu

INPUT_DIR="${INPUTS_FOLDER:-/inputs}"
OUTPUT_DIR="${OUTPUTS_FOLDER:-/outputs}"
INPUT_PATH="${INPUT_PATH:-${INPUT_DIR}}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
FORMAT="${FORMAT:-CSV}"
PYSUNLIGHT_DIR="/workspace/sunlight-shadow/pySunlight"

mkdir -p "${OUTPUT_DIR}"

cd "${PYSUNLIGHT_DIR}"

CMD="python3 src/main.py -i ${INPUT_PATH} -o ${OUTPUT_DIR} -log ${LOG_LEVEL} -f ${FORMAT}"

if [ -n "${TIMESTAMP:-}" ]; then
  CMD="${CMD} --single-timestamp ${TIMESTAMP}"
elif [ -n "${START_DATE:-}" ] && [ -n "${END_DATE:-}" ]; then
  CMD="${CMD} -s ${START_DATE} -e ${END_DATE}"
else
  echo "Provide TIMESTAMP or both START_DATE and END_DATE." >&2
  exit 1
fi

if [ -n "${OPTIONAL_ARGS:-}" ]; then
  CMD="${CMD} ${OPTIONAL_ARGS}"
fi

exec sh -c "${CMD}"
