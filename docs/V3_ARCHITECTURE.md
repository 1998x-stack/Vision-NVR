# Vision NVR 3.0 — Capture / Analyze / Encode + Polygon Privacy

## Architecture

```text
        camera / V4L2 / RTSP (experimental) / demo source
                    │
                    ▼
       FrameGrabber (dedicated thread)
       one-frame latest-only mailbox; old frames overwritten
                    │ (wall + monotonic timestamps)
                    ▼
       CameraWorker analyzer thread
          ├─ letterbox (no 4:3 stretching)
          ├─ server-side polygon PRIVACY REDACTION (solid black)
          ├─ normalized ROI include/exclude mask
          ├─ MOG2 + exposure settling + multi-frame trigger
          ├─ 3s pre-roll buffer (only masked images)
          ├─ manual consent and storage/mount gate
          └─ event state machine / SQLite event metadata
                    │ no FFmpeg stdin work in this thread
                    ▼
       AsyncEventRecorder (per event background thread)
       bounded Queue(maxsize=24); drop OLDEST during overload
       CFR resampling from capture monotonic time, fill short gaps
                    │
                    ▼
       VideoEncoder (FFmpeg libx264, capped threads)
       bounded nonblocking stdin writes, fMP4 for recovery
                    │
                    ▼
       local filesystem + SQLite; independent cleanup janitor
                    │
                    ▼
       loopback-only FastAPI + CSP web console + MJPEG
```

There are separate **camera**, **analyzer**, **encoder**, and **janitor** threads. The camera mailbox stores only the latest frame; acquisition is not blocked by motion detection or video encoding. When the encoder queue fills, the oldest outstanding frame is discarded, and counters report the loss. This is a best-effort consumer NVR, **not** a forensic recorder; gaps cannot prove inactivity.

## Privacy masks and detection zones

Open the live page at http://127.0.0.1:7866, choose **编辑监测区域** and draw polygons on the camera image:

1. **Blue/include**: when any blue polygon exists, the detector only evaluates pixels inside those shapes.
2. **Yellow/exclude**: ignore motion inside these areas (windows, monitors, fans).
3. **Black/privacy**: permanently turn image pixels black **in each newly encoded frame and event thumbnail**. They are also excluded from motion detection.

Click vertices, double-click to close, then save. Each type supports 0–8 polygons; each polygon has 3–32 vertices. Coordinates are normalized to the actual image area, not browser pixels. Privacy zones are applied on the server BEFORE preroll buffering or compression; they are not just a CSS overlay. Modifying any zone splits the active event, discards old-buffered frames and recalibrates MOG2.

**Limit:** privacy masks are **not retroactive**. Old recordings and exported copies remain unchanged. The previous live image already displayed in a browser cannot be retracted; after saving zones, preview state is invalidated and refreshed with newly masked frames. Protect any private information before first enabling recording.

Zone settings live in `settings.json` and persist, but **record_enabled is always reset to false** on every server start. API updates remain guarded by the local-only Host and anti-CSRF checks; these are NOT remote login/authentication.

## Operational metrics

`GET /api/status` now exposes:

| Key | Meaning |
| --- | --- |
| `camera.capture_fps` / `actual_analysis_fps` | Last ~5s camera/read and analyzer rates |
| `camera.frames_captured` / `frames_overwritten` | Frame-grabber throughput and mailbox overwrites |
| `camera.frames_skipped` | Analyzer-sequence gaps |
| `camera.processing_p95_ms` | Recent analyzer processing cost including ancillary work |
| `camera.encoder_queue_depth` | Pending async media frames across current/draining clips |
| `camera.encoder_frames_dropped` | Oldest frames discarded when async queue overflows |
| `camera.encoder_frames_duplicated` | Approximate CFR gap-fill duplication |
| `camera.encoder_failures` | Recorder exceptions observed after completion |
| `camera.storage_online` | Same filesystem device ID and required mount still available |
| `camera.loop_restarts` | Analyzer supervisor restarts |
| `camera.lighting_changes_ignored` | Exposure jumps causing settling |
| `camera.preview_clients` | Current MJPEG browser consumers |

Recording write failures are reported, not swallowed as valid events. A deliberate queue drop or camera outage still creates a coverage gap; clients must not equate absence of MP4s with absence of motion.

## Install / upgrade

```bash
cd ~/Vision-NVR
git fetch origin
git checkout v3.0
git pull --ff-only origin v3.0
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
NVR_DEMO=1 bash scripts/run.sh
```

On an existing deployment, stop the service and back up the NVR data directory before upgrading. Run the existing installer or use `scripts/install-user-service.sh`; update `NVR_DATA_DIR` and `NVR_REQUIRE_MOUNT` for your actual disk. Default bind remains `127.0.0.1:7866`, without an authentication service. Avoid public/LAN binding without a separate audited auth proxy.

`GET /static/app.js` and `/static/app.css` are allowlisted only; inline browser JS was removed to permit `script-src 'self'` in a Content Security Policy. Basic inline CSS remains, and `style-src 'unsafe-inline'` is therefore still enabled.

## Smoke / CI verification

```bash
# GitHub CI covers Ubuntu 22.04 and 24.04, no real camera
python -m unittest discover -s tests -v
node --check app/static/app.js

# System health must show new frames and capture worker alive
curl -i http://127.0.0.1:7866/healthz
curl -s http://127.0.0.1:7866/api/status | python -m json.tool

# DNS rebinding regression; must be HTTP 400
curl -i -H 'Host: not-local.example' http://127.0.0.1:7866/api/status
```

Test suite covers synthetic motion, background exposure, polygon masking, unsafe coordinates, capture queue pressure, FFmpeg slowdown/failure, encoding/seek, event recovery, consent reset, Host rebinding and end-to-end simulated demo recording.

### Hardware qualification needed

Before claiming 24/7 availability on a specific computer, conduct at least a 24h real-webcam trial: verify V4L2 resolution/FPS negotiation, dropped-frame counters, file integrity after camera hot-unplug, USB disconnect recovery, disk unmount behavior, thermal/CPU pressure, and idle CPU. Network RTSP paths are best-effort and have not received real-camera integration testing. The encoder retains constant-fps approximation rather than original high-resolution PTS for every frame.

### Future stage (not included here)

Hardware VAAPI feature probes and fallback on this exact machine, multi-camera UI, optional ONNX person tagger, dual-background ghost suppression, time-triggered low-bitrate continuous recording, database migrations/permissions for remote user accounts, comprehensive RTSP transport/reconnect and long-run hardware soak/benchmark. Those should remain opt-in and should not compromise the local/consent defaults.
