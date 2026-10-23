#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${HOME}/miniconda3/envs/vision-nvr"
if [ ! -x "$PREFIX/bin/python" ]; then
  echo '先执行 scripts/install.sh，并确认 Conda 环境名是 vision-nvr'; exit 1
fi
DATA="${NVR_DATA_DIR:-$HOME/VisionNVR}"
DEVICE="${NVR_CAMERA:-/dev/video0}"
mkdir -p "$HOME/.config/systemd/user"
UNIT="$HOME/.config/systemd/user/vision-nvr.service"
cat > "$UNIT" <<EOF
[Unit]
Description=Vision NVR - local motion-triggered camera recorder
After=default.target

[Service]
Type=simple
WorkingDirectory=$ROOT
Environment=PATH=$PREFIX/bin:/usr/local/bin:/usr/bin:/bin
Environment=NVR_DATA_DIR=$DATA
Environment=NVR_CAMERA=$DEVICE
ExecStart=$PREFIX/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 7866 --workers 1
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now vision-nvr.service
systemctl --user --no-pager status vision-nvr.service || true
echo "用户登录时启动。查看日志：journalctl --user -u vision-nvr.service -f"
echo '提示：录制每次服务重启都默认关闭，需要在网页点击开启，避免意外录制。'
