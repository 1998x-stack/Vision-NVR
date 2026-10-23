#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if ! command -v conda >/dev/null 2>&1; then
  echo '没有检测到 Conda。请先安装 Miniconda，或手动用 Python 3.11 venv 安装 requirements.txt。'
  exit 1
fi
ENV="${NVR_ENV:-vision-nvr}"
if ! conda env list | awk '{print $1}' | grep -Fxq "$ENV"; then
  echo "创建独立 Conda 环境：$ENV；绕过已有清华镜像配置"
  conda create -n "$ENV" --override-channels -c https://conda.anaconda.org/conda-forge python=3.11 pip ffmpeg -y
fi
PREFIX="$(conda env list | awk -v n="$ENV" '$1==n{print $NF;exit}')"
if [ ! -x "$PREFIX/bin/python" ]; then
  echo "Conda 环境缺少 Python: $PREFIX"; exit 1
fi
if ! "$PREFIX/bin/python" -m pip --version >/dev/null 2>&1; then
  conda install -n "$ENV" --override-channels -c https://conda.anaconda.org/conda-forge pip -y
fi
"$PREFIX/bin/python" -m pip install --no-input -r "$ROOT/requirements.txt" -i https://pypi.org/simple
export PATH="$PREFIX/bin:$PATH"
"$PREFIX/bin/python" - <<'PY'
import cv2,fastapi,numpy,uvicorn
print('OpenCV:',cv2.__version__,'FastAPI:',fastapi.__version__,'NumPy:',numpy.__version__)
PY
ffmpeg -hide_banner -encoders 2>/dev/null | grep -q libx264 || {
  echo '警告：FFmpeg 没有找到 libx264 编码器，请安装带 libx264 的 FFmpeg。'
}
echo "安装完成。运行：bash $ROOT/scripts/run.sh"
