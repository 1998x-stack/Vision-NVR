# Changelog

## 3.0.0 — decoupled recording and server-side privacy zones

- Dedicated `FrameGrabber` grabs the latest camera frame without waiting for detection or compression; status reports captured, overwritten and skipped samples.
- Separate `AsyncEventRecorder` runs FFmpeg stdin on a dedicated thread with a bounded queue; overload drops old samples, adds short CFR compensating frames, and reports queue depth/drops/duplication/failures.
- Nonblocking FFmpeg pipe writes have a timeout to bound stalls; short network/camera outages split events and reset motion history.
- New include/exclude/privacy polygons (up to 8 of each) with coordinate validation, browser drawing and persistent settings. Privacy is redacted in pixel buffers **before** thumbnails/preroll/encoding; zone edits terminate active clips and invalidate stale previews.
- New pipeline metrics (capture and analysis fps, P95 latency, dropped/duplicated frames, background janitor stats, disk state), with a live UI telemetry panel.
- Frontend CSS and JS extracted; unsafe inline event handlers removed. Same-origin CSP and explicit static-asset routing added.
- Broader synthetic tests and demo end-to-end recording/seek regression on Ubuntu 22.04 / 24.04.
- See `docs/V3_ARCHITECTURE.md`. **Not yet** validated on actual UVC/RTSP camera hardware for 24h use.


## 2.0.0 — reliability, privacy and verification

- **Security:** explicit Host allowlist on all routes (DNS rebinding), private directory and file permissions even outside the launcher.
- **Reliability:** independent retention janitor, SQL batch quota cleanup, worker automatic restart and non-successful health responses for stale/offline capture.
- **Recovery:** startup ffprobe reconciliation for incomplete recordings; fragmented MP4 recording improves interrupted-clip recoverability; unindexed files are reported rather than deleted.
- **Detection:** 20-sample accelerated background settling for significant global exposure changes; webcam native aspect preserved by letterboxing.
- **API and UI:** FastAPI app factory, event COUNT query instead of 1.3s GROUP BY, thumbnail caching, range-capable Starlette, remove unused 500-event polling, server-side time/day for UI timeline.
- **Operations/testing:** Linux service resource priority, regression tests for camera supervisor, exposure, crash recovery, host checks and range seeking; 14 tests passing in CI on Ubuntu 22.04 and 24.04 as of the v2 candidate.
- Remaining: FFmpeg input is still synchronous, no multi-camera or polygon privacy masks. See `docs/V2_MIGRATION.md` for upgrade and risk notes.


## 1.1.0 — portable Ubuntu deployment

- 新增 `.env.example` 与通用 `docs/DEPLOY_UBUNTU.md`，适配多台 Ubuntu，不依赖固定用户目录。
- 可选择 Python `.venv`、已有 Conda 环境或 `NVR_PYTHON`，用户级 systemd 服务由统一 `run.sh` 启动。
- 摄像头、端口、画面尺寸、FPS、录像目录全部可配置；拒绝不合理的 H.264 奇数尺寸。
- 新增 `NVR_REQUIRE_MOUNT`：录像盘未挂载时拒绝误写系统盘，录像开启前再次检查。
- FFmpeg 失败日志改为临时文件，防止长错误输出填满 PIPE；异常时回收子进程。
- 摄像头读取中断时自动结束当前事件录像并重连。
- 新增运行环境与挂载点配置测试；保留原有 30 天录像清理和显式开启录像的隐私保护行为。

### 仍待解决

- 无用户登录与角色系统，不能公网部署；保留 127.0.0.1 默认。
- 固定 FPS 编码不能提供法证级帧时间戳精度，后续考虑 VFR/PTS 支持。
- 单机单摄像头；更大型部署应拆分采集、事件处理、媒体索引与告警。

- 新增 Ubuntu 22.04/24.04 CI：模拟场景回归测试和安装脚本语法检查。
