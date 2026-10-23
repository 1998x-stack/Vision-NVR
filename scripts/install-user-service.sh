#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
source "$ROOT/scripts/common.sh"
PYTHON="$(resolve_python)"
command -v systemctl >/dev/null || { echo '需要 systemd user service' >&2; exit 1; }
[ -n "$PYTHON" ] || exit 1
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/vision-nvr.service" <<EOF
[Unit]
Description=Vision NVR (private local camera recorder)
After=default.target

[Service]
Type=simple
WorkingDirectory=$ROOT
# Resolved at install time: systemd need not find conda in its PATH.
Environment="NVR_PYTHON=$PYTHON"
Environment="NVR_DATA_DIR=${NVR_DATA_DIR:-$HOME/.local/share/vision-nvr}"
ExecStart=/usr/bin/bash "$ROOT/scripts/run.sh"
Restart=on-failure
RestartSec=5
UMask=0077
Nice=10
CPUWeight=50
IOSchedulingClass=idle
NoNewPrivileges=true

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable vision-nvr.service
systemctl --user restart vision-nvr.service
systemctl --user --no-pager status vision-nvr.service || true
echo '仅登录后启动；每次仍需在网页主动开启录像。'
