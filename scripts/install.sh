#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
source "$ROOT/scripts/common.sh"
ENV_NAME="${NVR_CONDA_ENV:-vision-nvr}"
BACKEND="${INSTALL_BACKEND:-auto}" # auto | venv | conda
PYTHON_BIN="${PYTHON_BIN:-}"
CHANNEL="${CONDA_CHANNEL:-https://conda.anaconda.org/conda-forge}"
if [ -z "$PYTHON_BIN" ]; then
  for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; assert (3,10) <= sys.version_info[:2] <= (3,13)' 2>/dev/null; then
      PYTHON_BIN="$(command -v "$candidate")"; break
    fi
  done
fi
if [ "$BACKEND" = auto ]; then
  if [ -x "$ROOT/.venv/bin/python" ]; then BACKEND=venv
  elif command -v conda >/dev/null 2>&1 && conda run -n "$ENV_NAME" python -c 'import sys; assert sys.version_info[:2] >= (3,10)' >/dev/null 2>&1; then BACKEND=conda
  elif [ -n "$PYTHON_BIN" ]; then BACKEND=venv
  elif command -v conda >/dev/null 2>&1; then BACKEND=conda
  else echo '需要 Python 3.10–3.13 + venv，或 Conda/Miniforge（Ubuntu 20.04 默认 Python 3.8 不满足）。' >&2; exit 1; fi
fi
case "$BACKEND" in
  venv)
    if [ ! -x "$ROOT/.venv/bin/python" ]; then
      [ -n "$PYTHON_BIN" ] || { echo '没有可用的 Python 3.10–3.13；请设置 PYTHON_BIN=/path/to/python3.11' >&2; exit 1; }
      "$PYTHON_BIN" -m venv "$ROOT/.venv" || { echo 'venv 创建失败，检查 python3-venv 是否已安装' >&2; exit 1; }
    fi
    EXEC="$ROOT/.venv/bin/python"
    ;;
  conda)
    command -v conda >/dev/null || { echo '未安装 Conda/Miniforge' >&2; exit 1; }
    if ! conda run -n "$ENV_NAME" python -c 'import sys; assert sys.version_info[:2] >= (3,10)' >/dev/null 2>&1; then
      conda create -n "$ENV_NAME" --override-channels -c "$CHANNEL" python=3.11 pip ffmpeg -y
    fi
    EXEC="$(conda env list | awk -v n="$ENV_NAME" '$1==n {print $NF;exit}')/bin/python"
    if ! "$EXEC" -m pip --version >/dev/null 2>&1; then
      conda install -n "$ENV_NAME" --override-channels -c "$CHANNEL" pip -y
    fi
    ;;
  *) echo 'INSTALL_BACKEND 应为 auto、venv 或 conda' >&2; exit 2;;
esac
"$EXEC" -m pip --version >/dev/null || { echo 'Python 环境缺少 pip' >&2; exit 1; }
echo "环境: $BACKEND / $EXEC"
"$EXEC" -m pip install --index-url "${PIP_INDEX_URL:-https://pypi.org/simple}" -r requirements.txt
"$EXEC" -m pip check
"$EXEC" -c 'import cv2,fastapi,numpy; print("OpenCV:",cv2.__version__,"FastAPI:",fastapi.__version__)'
if ! command -v ffmpeg >/dev/null 2>&1 && [ ! -x "$(dirname "$EXEC")/ffmpeg" ]; then
  echo 'FFmpeg 缺失：Ubuntu 用 sudo apt install ffmpeg，或 Conda 用 conda install -n vision-nvr -c conda-forge ffmpeg' >&2
  exit 1
fi
if ! PATH="$(dirname "$EXEC"):$PATH" ffmpeg -hide_banner -encoders 2>/dev/null | grep -q libx264; then
  echo '缺少 libx264 编码器，事件录像无法生成，请安装完整的 FFmpeg。' >&2; exit 1
fi
echo '安装完成。cp .env.example .env 后可自定义配置，再执行 bash scripts/run.sh'
