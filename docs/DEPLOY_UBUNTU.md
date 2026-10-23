# Vision NVR | 通用 Ubuntu 部署与配置指南

版本：便携化改进版，适用 Ubuntu 20.04 / 22.04 / 24.04 的常规 x86_64 主机；更多版本需单独验证。**服务器没有身份认证，默认只能本机访问。** 摄像头画面涉及个人隐私，仅在知情、合法且授权的区域使用。

## 0. 最小条件

- 摄像头：USB/UVC 等 Linux `/dev/video*` 设备；无摄像头可先用 `NVR_DEMO=1` 测试。
- Python：3.10–3.13；Ubuntu 20.04 系统默认 3.8 不满足，请安装 Miniforge/Conda 的 Python 3.11 或另备 3.11。
- FFmpeg：必须可以使用 `libx264` 编码器。`ffmpeg -hide_banner -encoders | grep libx264`。
- 依赖安装：互联网仅在首次安装时需要，正常运行不调用云端 API。
- 录像盘：建议至少 10 GB 可用空间，独立录像磁盘更可靠；录像不可视为永久备份。
- 系统：运行 systemd 的桌面 Ubuntu 最方便，服务器版可前台运行；后台 `systemctl --user` 通常在用户登录时生效。

## 1. 首次部署（新 Ubuntu）

```bash
sudo apt update
sudo apt install -y git ffmpeg v4l-utils curl
# 若 Python3 >= 3.10 且未安装 venv：
sudo apt install -y python3-venv
mkdir -p ~/apps && cd ~/apps
git clone https://github.com/1998x-stack/Vision-NVR.git
cd Vision-NVR
cp .env.example .env
# 修改 .env 前先看后文参数
nano .env
bash scripts/install.sh
bash scripts/check.sh
NVR_DEMO=1 bash scripts/run.sh
```

打开本机 `http://127.0.0.1:7866`。第一次演示时设置 `NVR_DEMO=1`，可以暂时覆盖 `.env` 的默认值。演示成功后 `Ctrl+C`，然后 `bash scripts/run.sh` 启动真实摄像头。请确认浏览器摄像头没有占用 `/dev/video0`。

**Ubuntu 20.04 / 镜像 403 / 系统 Python 3.8**：已有 Conda 或 Miniforge 时，执行 `INSTALL_BACKEND=conda bash scripts/install.sh`。脚本使用独立 Conda 环境 `vision-nvr`，官方 conda-forge 下载 FFmpeg，不直接修改系统 apt 源。Ubuntu 22/24 通常可直接使用 `.venv`。如果 `apt` 源 403，先处理源配置或者使用 Conda，不要继续安装不完整包。

## 2. 配置文件 `.env`（在仓库根目录）

**必须使用受信任的配置文件**：`.env` 由 Bash 读取，可执行 shell 展开；不要运行陌生人提供的配置。`.env` 已被 gitignore 排除，升级代码不会覆盖自己的配置。命令行传入的环境变量优先于文件配置。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `NVR_CAMERA` | `/dev/video0` | 摄像头路径，可用 `v4l2-ctl --list-devices` 确认 |
| `NVR_PORT` | `7866` | 本机端口，1024–65535 |
| `NVR_DATA_DIR` | `$HOME/.local/share/vision-nvr` | 数据库、录制文件、缩略图目录 |
| `NVR_REQUIRE_MOUNT` | 空 | 如果录像在独立盘，写该盘挂载点；未挂载则启动失败，避免误写系统盘 |
| `NVR_DEMO` | `0` | `1` 使用模拟摄像头，不是实际录像 |
| `NVR_FPS` | `8` | 1–30，旧 CPU 建议 5–10 |
| `NVR_WIDTH` | `640` | 160–1920，必须为偶数 |
| `NVR_HEIGHT` | `360` | 120–1080，必须为偶数 |
| `NVR_PYTHON` | 自动发现 | 指定完整 Python 路径，优先级高于 `.venv` 和 Conda |
| `NVR_CONDA_ENV` | `vision-nvr` | 使用的 Conda 环境名 |

不同电脑例子：

```bash
# SSD 存储，无专用录像盘
NVR_DATA_DIR=$HOME/VisionNVR

# 外接盘 /mnt/camera-disk 必须成功挂载才开始运行
NVR_DATA_DIR=/mnt/camera-disk/NVR
NVR_REQUIRE_MOUNT=/mnt/camera-disk
NVR_CAMERA=/dev/video2
NVR_FPS=6
NVR_WIDTH=640
NVR_HEIGHT=480
NVR_PORT=7866
```

把这些行写入 `.env`，然后执行 `findmnt /mnt/camera-disk` 验证。挂载点不是目录存在就算挂载，必须真正连接设备。应用每次新建事件录像前还会检查指定挂载点；录像途中磁盘拔出仍可能造成该片段损坏，不能替代硬件冗余与备份。

## 3. 网页功能、录像保留

Web UI 支持摄像头预览、MOG2 运动触发、H.264 事件录像、3 秒预录与尾录、按日浏览、缩略图、下载、保护、手动删除。页面设置中的 `retention_days` 默认 30 天，`max_storage_gb` 默认 20 GB，`min_free_gb` 默认 2 GB。被标记为受保护的录像不会自动删除，但可能占满配额，使新录像停止。默认启动时**录像关闭**，需主动点击开启。

## 4. systemd 自动启动（用户服务）

```bash
# 前台 Ctrl+C 后执行
bash scripts/install-user-service.sh
systemctl --user status vision-nvr.service
journalctl --user -u vision-nvr.service -n 100 --no-pager
# 停止、重启、取消自动启动
systemctl --user stop vision-nvr.service
systemctl --user restart vision-nvr.service
systemctl --user disable --now vision-nvr.service
```

用户服务会在安装时记录检测到的 Python 完整路径，即使 systemd 不加载交互式 Conda 的 PATH 也能启动；如果移动/删除 Python 环境，重新运行 `bash scripts/install-user-service.sh`。

默认行为：用户登录 Ubuntu 后启动网页后端，仍需手动开启录制。不要将用户服务误认为无人登录也能自动录像的 24 小时 NVR。对于长时间运行，建议先测试摄像头断线恢复、硬盘意外拔出、系统休眠和磁盘配额。若真的需要无人登录运行，应由管理员制定独立系统服务、录制同意及权限策略，而不是直接无认证地开放网络端口。

## 5. 局域网/远程访问

只绑定 `127.0.0.1`，Web 页面无用户名/密码，不能直接用 `--host 0.0.0.0` 暴露。若从另一台自己的电脑查看，可用 SSH 隧道：

```bash
ssh -L 7866:127.0.0.1:7866 youruser@ubuntu-host
```

然后在 SSH 客户端所在电脑访问 `http://127.0.0.1:7866`。请确保 SSH 本身受到密码/密钥及防火墙保护。录制区人员应知情；不要用于隐蔽摄录。

## 6. 升级、迁移、备份、卸载

```bash
systemctl --user stop vision-nvr.service
cd ~/apps/Vision-NVR
git status --short
# 如本地修改过代码，先备份或提交本地变更，再 git pull
git pull --ff-only
bash scripts/install.sh
bash scripts/check.sh
systemctl --user start vision-nvr.service
```

迁移新电脑：拷贝 `.env`（不提交 Git）、数据目录（数据库及录制文件一起）、并在新主机调整摄像头路径与磁盘挂载点，然后 `bash scripts/install.sh`。备份时先停止服务，避免 SQLite/WAL 文件和视频正在写入。卸载用户服务：`systemctl --user disable --now vision-nvr.service`，删除 `~/.config/systemd/user/vision-nvr.service` 后执行 `systemctl --user daemon-reload`。**删除代码不等于删除录像，录像应由用户单独确认清理**。

## 7. 验证及故障诊断

```bash
bash scripts/check.sh
python3 --version
ls -l /dev/video*
ffmpeg -encoders 2>/dev/null | grep libx264
findmnt /mnt/camera-disk       # 若使用独立录像盘
curl -fsS http://127.0.0.1:7866/healthz
# 运行测试时请使用应用环境的 python：
.venv/bin/python -m unittest discover -s tests -v
# 如使用 Conda：conda run -n vision-nvr python -m unittest discover -s tests -v
```

故障：没有视频设备→检查 `/dev/video*`、权限及其他占用程序；没有 `libx264`→装带该编码器的 FFmpeg；录像目录没有写权限→检查所有者和文件系统挂载；录像中断→查看 systemd 日志并确保磁盘空间；自启失败→确认 `scripts/run.sh` 可以独立前台执行。不应对 `/dev/video*` 使用 `chmod 777`。

## 8. 已知边界

- 本版本不是多摄像头、集中授权、RTSP、HLS 或硬件编解码的商业 NVR。
- H.264 目前固定采集 FPS，USB 摄像头抖动时录像媒体时间与真实墙钟时间可能略偏离；不适合司法取证级精确时间。
- 摄像头在低 FPS 下运行，事件触发可能漏掉很短的活动，运动检测会对光照变化产生误报。
- 不自动辨别人脸或身份。视频数据在本机保存，用户自行选择备份和保留策略。

## 9. GitHub 持续集成（CI）

新增 `.github/workflows/ci.yml`，针对 Ubuntu 22.04 与 24.04 执行无真实设备/模型下载的自动化回归和 Shell 语法检查。**CI 不代表所有摄像头、麦克风、驱动和 CPU 型号均已通过实机测试**。
