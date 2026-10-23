# Vision NVR 2.0 — reliability/security migration

Base revision: `main` at `c7fc221`. Implementation branch: `v2.0`.

## What changed

- **Trusted Host guard** on ALL routes, including live video and archive downloads. Only loopback hostnames are accepted by default. Remote access remains SSH port-forwarding to `localhost`, not a public bind.
- **Worker watchdog** restarts the capture loop after an uncaught exception. `/healthz` returns HTTP 503 if its frame is stale, the camera is offline, or the worker is dead. An unplugged camera gives a not-ready status; it should not cause destructive reconfiguration.
- **Retention janitor** runs every 120 seconds in an independent daemon thread. Cleanup chooses expired/quota victims in a single indexed SQL pass and deletes paths outside the DB lock. It never automatically deletes protected events.
- **Startup reconciliation** probes incomplete (`done=0`) clips with `ffprobe`. Recoverable clips are indexed, corrupt clips are removed. Orphan files without DB rows are **counted but preserved** for manual examination.
- **Privacy permissions**: process umask 077; private data root (0700), SQLite/JSON (0600), recorder/thumbnail directory inheritance under the private root. Existing data directories are restricted on startup; explicit user consent remains required for every new process start.
- **Lighting-settling**: a large foreground fraction accompanied by a global brightness-baseline shift starts a 20-sample suppressed-learning window. This reduces false clips after light switches but is a heuristic, NOT object classification.
- **FFmpeg** limits x264 threads to 2 and writes fragmented MP4 with approximately 2-second keyframe spacing, improving the chance of surviving an interruption. Short/incomplete trailing fragments may still be lost.
- **Aspect ratio** of 4:3 webcams is preserved by letterboxing into the configured recording dimensions. A best-effort V4L2 buffer-size hint and FFmpeg network source backend selection are included.
- **HTTP Range**: explicitly require a recent Starlette implementation to support seeking in archived clips.
- **API lifecycle**: `create_app()` defers disk and camera initialization to ASGI lifespan, making TestClient isolation possible. `/api/status` includes capture supervision and recovery/cleanup diagnostics.

## Upgrade

```bash
git fetch origin
git checkout v2.0
git pull --ff-only origin v2.0

# Use the venv/Conda setup from docs/DEPLOY_UBUNTU.md
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v

# Test without a physical camera first
NVR_DEMO=1 bash scripts/run.sh
# Open http://127.0.0.1:7866
```

Back up the data directory before upgrading. On restart, recording is **off**, even if it was on before the upgrade. To use an external mounted disk, set both `NVR_DATA_DIR` and `NVR_REQUIRE_MOUNT`. Do not expose the service directly on a public/LAN interface; a Host allowlist is not user authentication.

To set explicit alternate Host headers (e.g. an intentionally trusted reverse proxy), use:

```bash
NVR_ALLOWED_HOSTS=127.0.0.1,localhost bash scripts/run.sh
```

Do not add untrusted domain names or use `*`. Do not treat the `X-Local-UI` header as authentication. When using an SSH `-L` tunnel, the browser normally uses `localhost` and no allowlist change is necessary.

## Smoke test

```bash
curl -i -H 'Host: evil.example' http://127.0.0.1:7866/api/status
# Expected: HTTP 400

curl -i http://127.0.0.1:7866/healthz
# Expected: 200 only when the capture thread is alive and fresh frames arrive;
# otherwise 503.

curl -s http://127.0.0.1:7866/api/status | python -m json.tool
# Inspect camera.loop_restarts, camera.lighting_changes_ignored,
# camera.last_cleanup_seconds and recovery.
```

Simulated tests do **not** establish real-camera timing, USB buffer freshness, VAAPI support or SSD/HDD latency. Validate those on the actual computer. Enabling recording is a manual act; operate cameras only in authorized, disclosed contexts.

## Important remaining work

The FFmpeg raw-frame `stdin.write()` still runs on the capture thread; slow encoding can still stall camera reads. A later release should introduce a bounded latest-frame capture slot and encoder queue with measured drop counters. The UI is still a monolithic HTML file using frequent polling; multi-camera/RTSP end-to-end integration, polygonal detection zones, privacy masking and actual-camera motion regression datasets are NOT part of this release. Changes to lighting heuristics should be calibrated against false negatives (e.g. very large near-camera motion), not just false positives.

## Operators: storage recovery

If status reports `recovery.unindexed > 0`, examine the `recordings/` directory and reconcile files manually. The application intentionally does not delete unknown footage. During cleanup the DB row is removed before slow disk unlinks; a crash at that point can create unindexed files. Keep an independent backup of important protected clips.
