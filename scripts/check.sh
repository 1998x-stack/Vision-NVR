#!/usr/bin/env bash
set +e
echo '===== 操作系统 ====='
lsb_release -d 2>/dev/null; uname -r
echo '===== 摄像头设备 ====='
ls -l /dev/video* 2>/dev/null
command -v v4l2-ctl >/dev/null && v4l2-ctl --list-devices
echo '===== 摄像头访问权限 ====='
id -nG
echo '===== FFmpeg H.264 ====='
ffmpeg -hide_banner -encoders 2>/dev/null | grep libx264 | head -2
echo '===== 运行环境 ====='
"$HOME/miniconda3/envs/vision-nvr/bin/python" -c 'import cv2,fastapi; print(cv2.__version__,fastapi.__version__)'
echo '===== 服务 ====='
systemctl --user is-active vision-nvr.service
echo '===== 网页连通 ====='
curl --max-time 3 -fsS http://127.0.0.1:7866/healthz; echo
