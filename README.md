# Vision NVR 3.0 · 本地智能摄像头事件录像系统

> **3.0 采集解耦与隐私区域：** 采集线程 + 最新帧 Mailbox + 分析线程 + 有界异步 FFmpeg 编码 + 独立清理线程；支持可视化多边形包含/排除检测区域与在录像前永久黑化的隐私区域。新增捕获 FPS、P95、丢帧/补帧、编码队列指标；将 CSS/JS 独立成静态文件并加上限制性 CSP。提供双 Ubuntu CI、完整模拟摄像头→MP4→SQLite→HTTP Range 集成测试。详见 [v3.0 架构与部署](docs/V3_ARCHITECTURE.md)。**硬件 VAAPI、多摄像头、人员 AI 分类与真实硬件 24h 压力验证尚未完成。**

> **2.0 稳定性与隐私修复：** 新增 Host 白名单防 DNS Rebinding、采集线程监督恢复、独立定期清理、批量配额回收、未完成录像启动恢复、光照变化抑制、私有存储权限、MP4 分片、自动化安全回归测试。详见 [v2.0 升级、兼容性与验证指南](docs/V2_MIGRATION.md)。录像仍须每次启动后手动开启，仍默认仅供本机访问。**目前还未完成采集/FFmpeg 完整异步解耦、视频 ROI 与隐私遮罩，也未进行目标物理摄像头压力测试。**

> **跨 Ubuntu 便携部署版本**：新增 `.env.example`、`scripts/common.sh`、venv/Conda 双模式以及 `docs/DEPLOY_UBUNTU.md`。**完整、最新的安装方法以 [Ubuntu 部署与配置指南](docs/DEPLOY_UBUNTU.md) 为准。** 旧版 README 中固定 Miniconda 路径和 `/mnt/sda4` 的操作示例仅适用于原始机器，不是所有 Ubuntu 的默认要求。


> 专为 Ubuntu 20.04 + 老款 Intel i7 + USB 笔记本摄像头设计。无 API Key、无云服务，所有录像都保存在自己的机器。**仅在有权使用摄像头、被拍摄者知情的环境中开启录制。** 默认仅允许本机访问，用户每次启动服务都必须手动点击“开启智能录像”。

## 一、功能

- **实时监控：** 页面显示本机 USB 摄像头画面，含实时本地时间、运动区域框、在线与录制状态，以及下方最近 3 分钟按秒着色的运动条。MJPEG 预览约 2.5 FPS，后台采集与事件录像 8 FPS。
- **智能事件录像：** OpenCV MOG2 前景分割 + 影子过滤 + 形态学去噪 + 面积阈值 + 多帧确认 + 光线突变抑制。发生运动时记录，保留约 3 秒预录及静止后 3 秒（可调）。录像期间按采集率记录画面，而不是只保存变化图像；H.264 自己通过帧间压缩减少文件大小。
- **彩色时间轴：** 实时画面下方保留最近 3 分钟的内存活动强度条；今日时间轴记录已保存的事件。静止区域灰色，轻微运动绿色、一般活动黄色、明显活动红色。点击今日时间轴的彩色事件片段播放视频。**颜色不表示经过人工智能确认“有人”。**
- **网盘式资料库：** 日期分组、缩略图、MP4 回放、下载、保护（收藏）、手动删除。按日期存储：`recordings/YYYY-MM-DD/HH-MM-SS_event.mp4`。
- **空间与保留：** 默认 30 天自动清理，每 2 分钟巡检；可设置 1–365 天、最大归档容量和磁盘最低剩余空间。已保护录像不自动删除。达到最低剩余空间不再新建录像。
- **适配旧设备：** 默认 640×360、8 FPS、单摄像头、CPU-only；不依赖 NVIDIA CUDA，不部署大模型。
- **无音频：** v1 默认只录视频，不处理任何麦克风；无人物识别、无推送、无远程用户管理。

## 二、安装（你的 Ubuntu 20.04）

你之前已经遇到过清华 APT / Conda 镜像 **HTTP 403**，所以我们不会依赖它们，改用独立 Conda 环境。

```bash
cd ~/vision-nvr
bash scripts/install.sh
bash scripts/check.sh
```

`install.sh` 使用 `--override-channels -c https://conda.anaconda.org/conda-forge` 创建 `vision-nvr` 环境，安装 `python=3.11 pip ffmpeg`，然后使用 PyPI 安装 `fastapi uvicorn opencv-python-headless numpy`。**不会安装 CUDA。** 若公司或家庭网络对 conda-forge 也有限制，请更换可用下载源，别修改现有 `whisper-cpu` 环境。

### 先在演示模式测试 Web 和运动检测

```bash
cd ~/vision-nvr
NVR_DEMO=1 bash scripts/run.sh
```

浏览器打开 **http://127.0.0.1:7866**。演示画面标注了 `DEMO CAMERA`，不是实际摄像头，模拟画面每隔一段时间有圆形物体移动。打开智能录像，可以演示自动事件录像和日期归档。按 `Ctrl+C` 停止。演示录像也会写到本机归档目录。请不要用模拟录像验证真实摄像头画质。

### 使用真正的笔记本摄像头

```bash
# Linux 设备编号可能不是 /dev/video0
ls -l /dev/video*
# 可选安装 v4l-utils 后查看准确设备
v4l2-ctl --list-devices

cd ~/vision-nvr
NVR_CAMERA=/dev/video0 bash scripts/run.sh
```

如果 OpenCV 无法打开摄像头：检查是否被浏览器或其他软件占用，且当前用户是否有摄像头访问权限（`id -nG`，`ls -l /dev/video0`）。若设备属于 `video` 组而当前用户不在组内，可请管理员评估添加相应权限并重新登录；不要给 `/dev/video*` 使用 `chmod 777`。

### 推荐录像存 HDD，避免占用系统 SSD

你之前的机器分区为 SSD 根目录 `/dev/sdb2`，HDD 大数据目录 `/mnt/sda4`。如果 `/mnt/sda4` 挂载正常，且你的账号对目标路径具有写权限，可以用：

```bash
mkdir -p /mnt/sda4/VisionNVR
NVR_DATA_DIR=/mnt/sda4/VisionNVR NVR_CAMERA=/dev/video0 bash scripts/run.sh
```

**第一次操作务必确认目录实际位于已挂载 HDD 上**：`findmnt /mnt/sda4`。如果未挂载，绝对不要以为在那个路径写入就是写入 HDD。

### 登录 Ubuntu 后自动运行网页服务（可选）

先停止前台程序，再执行：

```bash
cd ~/vision-nvr
NVR_DATA_DIR=/mnt/sda4/VisionNVR bash scripts/install-user-service.sh
systemctl --user status vision-nvr.service
journalctl --user -u vision-nvr.service -f
```

这是**用户服务**，默认登录 Ubuntu 后启动（不是无人登录也 24 小时运行的系统服务）。出于隐私保护，每次服务进程启动都需手动点击网页开启录像；开机自动打开摄像头仅显示本机预览，不会未经确认写入录像。要停止：`systemctl --user stop vision-nvr.service`；要取消自启动：`systemctl --user disable --now vision-nvr.service`。

## 三、使用说明

1. 打开 **实时监控**，确认摄像头连接。看到画面后，等待约 3 秒建立背景模型。
2. 点击 **开启智能录像**，确认被拍摄者知情。发生明显运动时状态变为“活动中”；较短的片段可能仍在编码，请在静止后等待约 3 秒生成 MP4。
3. “今日活动时间轴”中的绿色、黄色或红色短条可以点击回看；下方“最新录像”可下载。
4. 打开 **录像资料库**，按日浏览、标记保护、下载和删除。
5. 打开 **设置**，调整灵敏度、连续运动确认帧数、录制后缓冲、保存天数（默认 30 天）、录像容量上限（默认 20GB）、磁盘保留空间（默认 2GB）、是否框选活动区域。保护片段不会被自动清理。

## 四、算法原理与为什么不是“只保存变化的像素”

1. **摄像头采样：** 固定机位约 8 FPS；在低分辨率帧上执行运动检测，以减少 CPU 开销。
2. **背景建模：** MOG2 为每个像素建立动态背景概率模型，标识新出现的前景。利用前景二值化去掉阴影，再做开运算去噪、膨胀连通、小区域过滤。
3. **误报抑制：** 设最低有效变化面积，例如 0.70%；要求连续三个采样帧均满足；对突然大面积亮度变化进行初步屏蔽（无法完全消除误报）。
4. **事件缓冲：** 内存环形队列保留触发前 3 秒，事件结束后额外录制 3 秒。静止时不产生录像文件。若场景持续变化超过 180 秒则拆分录像片段。
5. **H.264 编码：** 使用 FFmpeg 的 `libx264 -preset veryfast -crf 29 -g 16`，H.264 对帧内与跨帧冗余编码。视频不能仅保存单帧差异图片而没有标准参考帧、时间戳和码流结构，否则回放体验、兼容性都会较差。
6. **事件管理：** SQLite 保存开始时间、时长、运动强度峰值、MP4 文件路径、缩略图、是否保护，按日期索引。页面活动时间轴在未录制部分保持灰色。**静止时没有可回溯视频，因此这不等于 24 小时无遗漏录像。**

## 五、资源与存储

- 推荐先使用 640×360 / 8 FPS；若 2013 年 CPU 编码耗时过长，降低采集 FPS，或使用更低 CRF 质量和更快预设；`veryfast + CRF29` 是偏向低 CPU 的折中。
- 录像只有发生运动时才写 MP4；对有很多运动的室外场景，存储仍然可能很大。默认录像空间上限 20 GB，并保留至少 2 GB 磁盘空间；如果“受保护”录像占满配额，新录像会暂停，而不是删除受保护资料。
- 强烈建议 HDD 的 `/mnt/sda4/VisionNVR` 作为录像盘，不在 SSD 根分区长时间高频写入。
- 一段事件视频的**真实发生时间**、**MP4 播放时长**可能存在小幅差异：USB 摄像头无法保证严格恒定帧率，当前 H.264 使用固定 8 FPS 时间基。此 MVP 版本不做专业 PTS 重采样，不适合要求法证级逐帧绝对时间的场景。

## 六、安全与使用边界

- **默认监听 `127.0.0.1:7866`，没有登录系统，不得直接用 `--host 0.0.0.0` 开放到局域网或公网。** 如果想从自己另一台设备远程访问，可使用 SSH 隧道，例如 `ssh -L 7866:127.0.0.1:7866 x@你的Ubuntu局域网IP`，然后访问本地 `http://127.0.0.1:7866`；需要已经具备合法 SSH 访问。
- Web 控制接口需要本机网页发出的自定义请求头，但**这不是身份认证**。请勿把程序当成可对公网暴露的正式多用户 NVR。
- 录像目录可能包含其他人的影像，应设置受限文件权限、合理保留期限，避免分享未获同意的片段。录制前通知被拍摄者，不要用于隐蔽监视或侵犯隐私。
- 没有面部识别、人员身份识别，也不做人员跟踪和行为推断；颜色只是画面运动强度。

## 七、与成熟 NVR 的差异 / Roadmap

| 功能 | Vision NVR 1.0 | 成熟 NVR 示例（Frigate / Motion） |
|---|---|---|
| 实时预览 | USB 摄像头、单路本机 MJPEG | 多摄像头 + RTSP 等 |
| 检测 | MOG2 运动前景 | 运动 + AI 对象检测、区域掩码 |
| 录像 | 仅运动事件；H.264 MP4 | 连续/运动/告警多级留存 |
| 时间轴 | 按天事件片段与不同强度颜色 | 连续时间轴、告警分级、检索 |
| 资料库 | 按日期、视频回放/下载/保护 | 多路、多硬盘、导出、快照 |
| 存储 | 保留天数与额度限制 | 分层策略、保留优先级与紧急清理 |
| 安全 | 只监听本机，无账号 | 用户鉴权、网络接入管理 |
| 后续能力 | ROI 区域与轻量目标检测待扩展 | Person/Car/Pet 分类、多区告警 |

参考：
- [OpenCV MOG2 背景减除](https://docs.opencv.org/4.11.0/d1/dc5/tutorial_background_subtraction.html)
- [Frigate Motion Detection](https://docs.frigate.video/configuration/motion_detection/)
- [Frigate Recording and Retention](https://docs.frigate.video/configuration/record/)
- [Motion Project](https://motion-project.github.io/motionv5_config.html)

## 八、API 说明（仅本机）

- `GET /api/status`：摄像头连接/运动强度/录制状态/剩余空间/设置
- `GET /api/live.mjpg`：实时 MJPEG 画面
- `GET /api/days`：过去 90 个有录像的日期
- `GET /api/events?day=YYYY-MM-DD`：按日期筛选的事件录像
- `GET /api/events/{id}/video`：H.264 MP4 回放
- `GET /api/events/{id}/download`：下载 MP4
- `GET /api/events/{id}/thumb`：缩略图
- `POST /api/settings`：录制开关、灵敏度、保留天数等设置
- `POST /api/events/{id}/protect`：保护/取消保护
- `DELETE /api/events/{id}`：手动删除未保护录像
- `POST /api/cleanup`：按到期时间与空间配额清理

## 九、故障排查

```bash
bash scripts/check.sh
journalctl --user -u vision-nvr.service --no-pager -n 100
curl -fsS http://127.0.0.1:7866/healthz
# 本机浏览器如果连接不上，检查端口是否被其他软件占用
ss -ltnp | grep ':7866'
# 检查录像是否真的写入 HDD
findmnt /mnt/sda4
```

**如果 ffmpeg 缺少 `libx264` 编码器或摄像头没有被系统识别，网站仍可启动，但无法成功保存录像。** 检查命令会指出原因，不应通过修改视频设备权限为 777 来绕过。

## 十、测试与验证范围

项目内置单元测试，可在安装完依赖后执行：

```bash
cd ~/vision-nvr
~/miniconda3/envs/vision-nvr/bin/python -m unittest discover -s tests -v
```

开发阶段已在模拟摄像头模式下完成 MOG2 检测、H.264 MP4 生成（通过 ffprobe 验证）、录像归档和接口访问测试。**没有在你的 ASUS 实体摄像头上运行**；需你本人测试实际设备、光照、权限与长期留存。网页可通过浏览器打开，不代表适用于公网部署或多人账户管理。
