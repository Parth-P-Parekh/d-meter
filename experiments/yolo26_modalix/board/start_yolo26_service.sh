#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ " ${*:-} " == *" --port 5001 "* || " ${*:-} " == *" --port 5003 "* ]]; then
  echo "Ports 5001 and 5003 are protected." >&2
  exit 2
fi
export YOLO26_PACKAGE_ROOT="${YOLO26_PACKAGE_ROOT:-/data/simaai/applications/yolo26n_coco_simaaisrc}"
export LD_LIBRARY_PATH="$YOLO26_PACKAGE_ROOT/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export GST_PLUGIN_PATH="$YOLO26_PACKAGE_ROOT/lib${GST_PLUGIN_PATH:+:$GST_PLUGIN_PATH}"
exec /usr/bin/python3 "$SCRIPT_DIR/board_service.py" "$@"

