#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV="${NVR_ENV:-vision-nvr}"
PREFIX="${HOME}/miniconda3/envs/${ENV}"
if [ ! -x "$PREFIX/bin/python" ]; then
  if command -v conda >/dev/null 2>&1; then
    PREFIX="$(conda env list | awk -v n="$ENV" '$1==n{print $NF;exit}')"
  fi
fi
if [ ! -x "$PREFIX/bin/python" ]; then
  echo '未找到 Vision NVR 的 Python 环境；请先运行 bash scripts/install.sh'; exit 1
fi
export PATH="$PREFIX/bin:$PATH"
export NVR_DATA_DIR="${NVR_DATA_DIR:-$HOME/VisionNVR}"
export NVR_CAMERA="${NVR_CAMERA:-/dev/video0}"
export NVR_DEMO="${NVR_DEMO:-0}"
cd "$ROOT"
echo "Vision NVR: http://127.0.0.1:7866"
echo "录像目录: $NVR_DATA_DIR | 摄像头: $NVR_CAMERA | DEMO: $NVR_DEMO"
exec "$PREFIX/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 7866 --workers 1
