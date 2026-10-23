"""Private local NVR server. Never serve it directly on a public interface."""
from __future__ import annotations
from contextlib import asynccontextmanager
from datetime import datetime, date
import os
from pathlib import Path
import re

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from .engine import CameraWorker
from .store import Store

from .config import config
PROJECT = Path(__file__).resolve().parent
CONFIG = config()
store = Store(CONFIG.data_dir)
worker = CameraWorker(store, camera=CONFIG.camera, demo=CONFIG.demo, fps=CONFIG.fps,
                      width=CONFIG.width, height=CONFIG.height, require_mount=CONFIG.require_mount)


@asynccontextmanager
async def lifespan(app: FastAPI):
    worker.start()
    yield
    worker.stop()


app = FastAPI(title="Vision NVR", description="本地事件录像 / 不含人脸识别", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def private_frame_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def require_local_action(request: Request):
    # Header makes it non-simple CORS request; no cross-origin CORS policy is enabled.
    # This is anti-CSRF defense, NOT remote authentication.
    if request.headers.get("x-local-ui") != "1":
        raise HTTPException(403, "请使用本机网页控制台")
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != f"{request.url.scheme}://{request.headers.get('host')}":
        raise HTTPException(403, "跨站请求被拒绝")


@app.get("/", response_class=HTMLResponse)
def home():
    return (PROJECT / "static" / "index.html").read_text("utf-8")


@app.get("/api/status")
def status():
    return {"camera": worker.status(), "settings": store.settings(), "storage": store.storage(),
            "events": sum(d["count"] for d in store.days()), "server_time": datetime.now().astimezone().isoformat()}


@app.get("/api/live.mjpg")
def live():
    return StreamingResponse(worker.frames(), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@app.get("/api/snapshot")
def snapshot():
    jpg = worker.snapshot()
    if jpg is None:
        raise HTTPException(503, "摄像头尚未提供画面")
    return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/days")
def days():
    return store.days()


@app.get("/api/events")
def events(day: str | None = None, limit: int = 250):
    if day:
        try:
            date.fromisoformat(day)
        except ValueError:
            raise HTTPException(400, "日期格式必须 YYYY-MM-DD")
    return store.events(day, max(1, min(limit, 500)))


@app.get("/api/events/{event_id}/{asset}")
def event_asset(event_id: str, asset: str):
    if not re.fullmatch(r"[0-9a-f]{18}", event_id):
        raise HTTPException(404)
    row = store.get_event(event_id)
    if not row or not row["done"]:
        raise HTTPException(404)
    if asset not in ("video", "thumb", "download"):
        raise HTTPException(404)
    file = store.safe_path(row["thumb_path"] if asset == "thumb" else row["video_path"])
    if not file.is_file():
        raise HTTPException(404)
    disposition = "attachment" if asset == "download" else "inline"
    return FileResponse(file, media_type="image/jpeg" if asset == "thumb" else "video/mp4",
                        filename=file.name, content_disposition_type=disposition,
                        headers={"Cache-Control": "private, no-store"})


class SettingsUpdate(BaseModel):
    record_enabled: bool | None = None
    sensitivity: float | None = None
    min_motion_frames: int | None = None
    post_roll_seconds: float | None = None
    retention_days: int | None = None
    max_storage_gb: float | None = None
    min_free_gb: float | None = None
    show_boxes: bool | None = None


@app.post("/api/settings")
def settings_change(payload: SettingsUpdate, request: Request):
    require_local_action(request)
    params = payload.model_dump(exclude_none=True)
    try:
        return store.update_settings(params)
    except ValueError as e:
        raise HTTPException(422, str(e))


class Protected(BaseModel):
    protected: bool


@app.post("/api/events/{event_id}/protect")
def protect(event_id: str, payload: Protected, request: Request):
    require_local_action(request)
    if not store.toggle_protect(event_id, payload.protected):
        raise HTTPException(404)
    return {"ok": True}


@app.delete("/api/events/{event_id}")
def delete(event_id: str, request: Request):
    require_local_action(request)
    if not store.delete(event_id):
        raise HTTPException(409, "不存在或已锁定，先取消保护")
    return {"ok": True}


@app.post("/api/cleanup")
def cleanup(request: Request):
    require_local_action(request)
    return {"deleted": store.cleanup()}


@app.get("/healthz")
def health():
    return {"ok": True, "camera_connected": worker.status()["online"]}
