"""Loopback-only Vision NVR API; recording requires explicit operator consent."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, date
import os
from pathlib import Path
import re
import time

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from .config import NVRConfig, config
from .engine import CameraWorker
from .store import Store

PROJECT = Path(__file__).resolve().parent


def require_local_action(request: Request):
    # Anti-CSRF, not authentication. Host validation also protects read endpoints.
    if request.headers.get("x-local-ui") != "1":
        raise HTTPException(403, "请使用本机网页控制台")
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != f"{request.url.scheme}://{request.headers.get('host')}":
        raise HTTPException(403, "跨站请求被拒绝")


class SettingsUpdate(BaseModel):
    record_enabled: bool | None = None
    sensitivity: float | None = None
    min_motion_frames: int | None = None
    post_roll_seconds: float | None = None
    retention_days: int | None = None
    max_storage_gb: float | None = None
    min_free_gb: float | None = None
    show_boxes: bool | None = None
    zones: dict | None = None


class Protected(BaseModel):
    protected: bool


def create_app(cfg: NVRConfig | None = None) -> FastAPI:
    """Construct the web app without opening a camera or writing to disk."""
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime = cfg if cfg is not None else config()
        data = Store(runtime.data_dir)
        # Only at startup: no active recording is running during reconciliation.
        recovered = data.reconcile()
        camera = CameraWorker(
            data, camera=runtime.camera, demo=runtime.demo, fps=runtime.fps,
            width=runtime.width, height=runtime.height,
            require_mount=runtime.require_mount)
        app.state.store = data
        app.state.worker = camera
        app.state.recovery = recovered
        camera.start()
        try:
            yield
        finally:
            camera.stop()

    app = FastAPI(
        title="Vision NVR", description="本地事件录像 / 不含人脸识别",
        lifespan=lifespan, docs_url=None, redoc_url=None)
    # DNS rebinding: a browser must not be able to read or control 127.0.0.1
    # through a Host header belonging to an attacker-controlled domain.
    hosts = [h.strip() for h in os.getenv(
        "NVR_ALLOWED_HOSTS", "127.0.0.1,localhost,[::1]").split(",") if h.strip()]
    if not hosts or "*" in hosts or any("/" in h or "://" in h for h in hosts):
        raise ValueError("NVR_ALLOWED_HOSTS must list explicit hostnames (no wildcard or URL)")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; media-src 'self' blob:; "
            "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        return response

    @app.get("/", response_class=HTMLResponse)
    def home():
        return (PROJECT / "static" / "index.html").read_text("utf-8")

    @app.get("/static/{filename}")
    def static_asset(filename: str):
        # Explicit allowlist: no user-controlled archive or arbitrary file serving.
        types = {"app.js": "text/javascript", "app.css": "text/css"}
        if filename not in types:
            raise HTTPException(404)
        return FileResponse(PROJECT / "static" / filename,
                            media_type=types[filename],
                            headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/status")
    def status(request: Request):
        data = request.app.state.store
        return {
            "camera": request.app.state.worker.status(),
            "settings": data.settings(),
            "storage": data.storage(),
            "events": data.count_events(),
            "recovery": request.app.state.recovery,
            "server_time": datetime.now().astimezone().isoformat(),
            "today": date.today().isoformat(),
        }

    @app.get("/api/live.mjpg")
    def live(request: Request):
        return StreamingResponse(
            request.app.state.worker.frames(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store"})

    @app.get("/api/snapshot")
    def snapshot(request: Request):
        jpg = request.app.state.worker.snapshot()
        if jpg is None:
            raise HTTPException(503, "摄像头尚未提供画面")
        return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/days")
    def days(request: Request):
        return request.app.state.store.days()

    @app.get("/api/events")
    def events(request: Request, day: str | None = None, limit: int = 250):
        if day:
            try:
                date.fromisoformat(day)
            except ValueError:
                raise HTTPException(400, "日期格式必须 YYYY-MM-DD")
        return request.app.state.store.events(day, max(1, min(limit, 500)))

    @app.get("/api/events/{event_id}/{asset}")
    def event_asset(event_id: str, asset: str, request: Request):
        if not re.fullmatch(r"[0-9a-f]{18}", event_id):
            raise HTTPException(404)
        row = request.app.state.store.get_event(event_id)
        if not row or not row["done"] or asset not in ("video", "thumb", "download"):
            raise HTTPException(404)
        file = request.app.state.store.safe_path(
            row["thumb_path"] if asset == "thumb" else row["video_path"])
        if not file.is_file():
            raise HTTPException(404)
        disposition = "attachment" if asset == "download" else "inline"
        return FileResponse(
            file, media_type="image/jpeg" if asset == "thumb" else "video/mp4",
            filename=file.name, content_disposition_type=disposition,
            headers={"Cache-Control": (
                "private, max-age=31536000, immutable" if asset == "thumb"
                else "private, no-store")})

    @app.post("/api/settings")
    def settings_change(payload: SettingsUpdate, request: Request):
        require_local_action(request)
        params = payload.model_dump(exclude_none=True)
        try:
            result = request.app.state.store.update_settings(params)
            if "zones" in params:
                request.app.state.worker.invalidate_preview()
            return result
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/events/{event_id}/protect")
    def protect(event_id: str, payload: Protected, request: Request):
        require_local_action(request)
        if not request.app.state.store.toggle_protect(event_id, payload.protected):
            raise HTTPException(404)
        return {"ok": True}

    @app.delete("/api/events/{event_id}")
    def delete(event_id: str, request: Request):
        require_local_action(request)
        if not request.app.state.store.delete(event_id):
            raise HTTPException(409, "不存在或已锁定，先取消保护")
        return {"ok": True}

    @app.post("/api/cleanup")
    def cleanup(request: Request):
        require_local_action(request)
        return {"deleted": request.app.state.store.cleanup()}

    @app.get("/healthz")
    def health(request: Request):
        state = request.app.state.worker.status()
        fresh = state["last_frame"] > 0 and (
            time.time() - state["last_frame"] < max(5, 4 / state["fps_target"]))
        ready = state["worker_alive"] and state["online"] and fresh
        if not ready:
            return Response(
                content='{"ok":false,"camera_connected":false}',
                media_type="application/json", status_code=503)
        return {"ok": True, "camera_connected": True}

    return app


app = create_app()
