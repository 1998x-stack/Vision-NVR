#!/usr/bin/env bash
set +e
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
PYTHON="$(resolve_python)" || exit 1
export PATH="$(dirname "$PYTHON"):$PATH"
echo '===== OS / Python ====='
uname -a
"$PYTHON" --version
"$PYTHON" -c 'import cv2,fastapi; print(cv2.__version__, fastapi.__version__)'
echo '===== Camera ====='
ls -l /dev/video* 2>/dev/null
command -v v4l2-ctl >/dev/null && v4l2-ctl --list-devices
echo '===== FFmpeg H.264 ====='
ffmpeg -hide_banner -encoders 2>/dev/null | grep libx264 | head -1
echo '===== Recording root ====='
"$PYTHON" - <<'PY'
from app.config import config
from pathlib import Path
import os
c=config()
print('root=',c.data_dir,'camera=',c.camera,'fps=',c.fps,'size=',(c.width,c.height))
if c.require_mount: print('required mount=',c.require_mount,'mounted=',os.path.ismount(c.require_mount))
PY
