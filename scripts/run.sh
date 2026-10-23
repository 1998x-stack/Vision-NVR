#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
PYTHON="$(resolve_python)"
export PATH="$(dirname "$PYTHON"):$PATH"
export NVR_DATA_DIR="${NVR_DATA_DIR:-$HOME/.local/share/vision-nvr}"
export NVR_CAMERA="${NVR_CAMERA:-/dev/video0}"
export NVR_DEMO="${NVR_DEMO:-0}"
PORT="${NVR_PORT:-7866}"
[[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1024 && PORT <= 65535 )) || { echo 'NVR_PORT 必须是 1024–65535' >&2; exit 2; }
umask 077
cd "$PROJECT_ROOT"
echo "Vision NVR http://127.0.0.1:$PORT | 数据: $NVR_DATA_DIR | 摄像头: $NVR_CAMERA"
exec "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --workers 1
