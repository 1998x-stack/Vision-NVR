"""Camera capture, lightweight background subtraction, event-triggered H.264 encoder."""
from __future__ import annotations

from collections import deque
from datetime import datetime
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

import cv2
import numpy as np

log = logging.getLogger(__name__)


class MotionDetector:
    """MOG2 foreground mask + spatial filtering + warmup and light-change rejection.

    This is motion detection, NOT human identification / object recognition.
    """

    def __init__(self, width: int = 320, warmup: int = 22):
        self.width = width
        self.warmup = warmup
        self.samples = 0
        self.subtractor = cv2.createBackgroundSubtractorMOG2(history=240, varThreshold=24, detectShadows=True)
        self.last_mean: float | None = None
        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))

    def detect(self, frame: np.ndarray, threshold: float = 0.65):
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (self.width, max(1, round(h * self.width / w))))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        mean = float(gray.mean())
        mask = self.subtractor.apply(gray)
        self.samples += 1
        mask = cv2.threshold(mask, 250, 255, cv2.THRESH_BINARY)[1]  # suppress gray shadow label
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self.kernel)
        mask = cv2.dilate(mask, self.kernel, iterations=1)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        min_blob = max(110, int(mask.size * 0.002))
        rectangles = []
        significant_area = 0.0
        sx, sy = w / small.shape[1], h / small.shape[0]
        for cont in contours:
            area = cv2.contourArea(cont)
            if area < min_blob:
                continue
            x, y, ww, hh = cv2.boundingRect(cont)
            significant_area += area
            rectangles.append((int(x * sx), int(y * sy), int(ww * sx), int(hh * sy)))
        pct = 100.0 * significant_area / mask.size
        brightness_jump = self.last_mean is not None and abs(mean - self.last_mean) >= 24
        self.last_mean = mean
        # Major auto-exposure / light-switch jumps are ignored for this sampled frame.
        if self.samples <= self.warmup or (brightness_jump and pct > 30):
            return 0.0, [], False
        return round(pct, 3), rectangles[:8], pct >= threshold


def level(score: float) -> str:
    if score >= 8:
        return "high"
    if score >= 2:
        return "medium"
    if score >= 0.5:
        return "low"
    return "idle"


class VideoEncoder:
    def __init__(self, path: Path, width: int, height: int, fps: int):
        binary = shutil.which("ffmpeg")
        if not binary:
            raise RuntimeError("找不到 FFmpeg。请先激活含 ffmpeg 的 Conda 环境。")
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stderr_log = tempfile.TemporaryFile(mode="w+b")
        self.proc = subprocess.Popen([
            binary, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pixel_format", "bgr24", "-video_size", f"{width}x{height}",
            "-framerate", str(fps), "-i", "pipe:0", "-an", "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "29", "-pix_fmt", "yuv420p",
            "-g", str(fps * 2), "-movflags", "+faststart", "-f", "mp4", str(path)
        ], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self._stderr_log)
        self.frame_count = 0
        self.fps = fps

    def append(self, frame: np.ndarray):
        if self.proc.poll() is not None:
            msg = self._error_tail()
            raise RuntimeError(f"FFmpeg 录制中断：{msg}")
        try:
            self.proc.stdin.write(frame.tobytes())
            self.frame_count += 1
        except (BrokenPipeError, OSError) as exc:
            raise RuntimeError("FFmpeg 无法写入视频；检查磁盘空间与编码支持") from exc

    def _error_tail(self):
        self._stderr_log.seek(0, os.SEEK_END)
        end = self._stderr_log.tell()
        self._stderr_log.seek(max(0, end - 4096))
        return self._stderr_log.read().decode("utf-8", errors="replace")[-700:]

    def close(self):
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                try:
                    self.proc.stdin.close()
                except BrokenPipeError:
                    pass
            try:
                rc = self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("FFmpeg 结束录制超时") from exc
            message = self._error_tail()
            if rc != 0:
                raise RuntimeError(f"FFmpeg 编码错误（exit {rc}）：{message}")
            return self.frame_count / self.fps
        finally:
            if self.proc.poll() is None:
                self.proc.kill()
                self.proc.wait()
            self._stderr_log.close()


class CameraWorker:
    def __init__(self, store, *, camera: str = "/dev/video0", demo: bool = False, fps: int = 8,
                 width: int = 640, height: int = 360, require_mount: Path | None = None):
        self.store = store
        self.camera = camera
        self.demo = demo
        self.fps = fps
        self.width, self.height = width, height
        self.require_mount = require_mount
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.running = False
        self.thread: threading.Thread | None = None
        self.jpeg: bytes | None = None
        self.seq = 0
        self.online = False
        self.state = "offline"
        self.error = ""
        self.score = 0.0
        self.activity_level = "idle"
        self.boxes: list = []
        self.rec_active = False
        self.started = time.time()
        self.last_frame = 0.0
        self.last_event_id: str | None = None
        self.frames_processed = 0
        self.detector = MotionDetector()
        # Volatile motion indicators for last 180 seconds, never saved to disk.
        self.activity_seconds: deque[tuple[int, str]] = deque(maxlen=180)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, name="camera-capture", daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        with self.condition:
            self.condition.notify_all()
        if self.thread:
            self.thread.join(timeout=35)

    def status(self):
        with self.lock:
            return {
                "online": self.online, "state": self.state, "error": self.error,
                "score": self.score, "level": self.activity_level, "recording_clip": self.rec_active,
                "last_frame": self.last_frame, "fps_target": self.fps,
                "source": "模拟演示画面" if self.demo else self.camera,
                "last_event_id": self.last_event_id,
                "activity_seconds": list(self.activity_seconds),
            }

    def snapshot(self) -> bytes | None:
        with self.lock:
            return self.jpeg

    def frames(self):
        previous = -1
        while self.running:
            with self.condition:
                self.condition.wait_for(lambda: self.seq != previous or not self.running, timeout=3)
                if not self.running:
                    return
                previous = self.seq
                data = self.jpeg
            if data:
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n")

    def _demo_frame(self, t: float):
        frame = np.empty((self.height, self.width, 3), np.uint8)
        frame[:] = (23, 28, 32)
        cv2.rectangle(frame, (32, 62), (600, 322), (40, 48, 57), -1)
        cv2.rectangle(frame, (52, 88), (210, 260), (55, 67, 78), 2)
        cv2.rectangle(frame, (410, 90), (554, 245), (65, 76, 84), 2)
        cv2.putText(frame, "DEMO CAMERA - NOT REAL VIDEO", (110, 34), cv2.FONT_HERSHEY_SIMPLEX, .62, (190, 190, 190), 1, cv2.LINE_AA)
        phase = t % 18
        if 5 < phase < 12:
            px = int(40 + (phase - 5) / 7 * 500)
            cv2.circle(frame, (px, 191), 29, (160, 180, 220), -1)
        return frame

    def _loop(self):
        cap = None
        encoder = None
        ev = None
        buffer = deque(maxlen=self.fps * 3)
        motion_streak = 0
        last_motion = 0.0
        last_preview = 0.0
        started_at = time.monotonic()
        last_cleanup = 0.0
        next_frame = 0.0
        open_retry = 0.0
        try:
            while self.running:
                now = time.monotonic()
                if now - last_cleanup > 120:
                    try:
                        self.store.cleanup()
                    except Exception:
                        log.exception("cleanup error")
                    last_cleanup = now

                if not self.demo and cap is None:
                    if now < open_retry:
                        time.sleep(.3)
                        continue
                    source = int(self.camera) if self.camera.isdecimal() else self.camera
                    cap = cv2.VideoCapture(source, cv2.CAP_V4L2 if os.name == "posix" else cv2.CAP_ANY)
                    if not cap.isOpened():
                        cap.release()
                        cap = None
                        open_retry = now + 4
                        with self.lock:
                            self.online, self.state = False, "offline"
                            self.error = f"无法打开摄像头 {self.camera}，可运行 v4l2-ctl --list-devices"
                        time.sleep(.3)
                        continue
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                    cap.set(cv2.CAP_PROP_FPS, self.fps)
                    with self.lock:
                        self.error = ""
                    self.detector = MotionDetector()
                    next_frame = 0.0

                if next_frame > now:
                    time.sleep(min(.03, next_frame - now))
                    continue
                next_frame = now + 1.0 / self.fps
                if self.demo:
                    frame = self._demo_frame(now - started_at)
                    ok = True
                else:
                    ok, frame = cap.read()
                if not ok or frame is None:
                    if encoder and ev:
                        self._close_encoder(encoder, ev)
                        encoder, ev = None, None
                    if cap:
                        cap.release()
                        cap = None
                    with self.lock:
                        self.online, self.state, self.error = False, "offline", "摄像头读取失败，正在重连"
                    time.sleep(1)
                    continue
                frame = cv2.resize(frame, (self.width, self.height))
                self.frames_processed += 1
                score, boxes, motion = self.detector.detect(frame, self.store.settings()["sensitivity"])
                with self.lock:
                    self.score = score
                    self.boxes = boxes
                    self.activity_level = level(score)
                    second = int(time.time())
                    if self.activity_seconds and self.activity_seconds[-1][0] == second:
                        previous = self.activity_seconds[-1][1]
                        ranks = {"idle": 0, "low": 1, "medium": 2, "high": 3}
                        self.activity_seconds[-1] = (second, max(previous, self.activity_level, key=lambda x: ranks[x]))
                    else:
                        self.activity_seconds.append((second, self.activity_level))
                    self.online = True
                    self.state = "calibrating" if self.detector.samples <= self.detector.warmup else ("motion" if motion else "idle")
                    self.last_frame = time.time()
                    self.error = ""

                cfg = self.store.settings()
                can_record = cfg["record_enabled"]
                if motion:
                    motion_streak += 1
                    last_motion = now
                else:
                    motion_streak = 0
                was_active = encoder is not None
                if encoder is None and motion_streak >= cfg["min_motion_frames"] and can_record:
                    # Camera clips only start if there's enough free space and room in the quota.
                    if self.store.allow_new_recording() and (
                            self.require_mount is None or os.path.ismount(self.require_mount)):
                        event_ts = buffer[0][0] if buffer else time.time()
                        event_id = uuid.uuid4().hex[:18]
                        day = datetime.fromtimestamp(event_ts).strftime("%Y-%m-%d")
                        name = datetime.fromtimestamp(event_ts).strftime("%H-%M-%S") + f"_{event_id[:8]}.mp4"
                        rel = f"recordings/{day}/{name}"
                        poster = f"thumbnails/{day}/{name[:-4]}.jpg"
                        try:
                            encoder = VideoEncoder(self.store.path / rel, self.width, self.height, self.fps)
                            for _, old_frame in buffer:
                                encoder.append(old_frame)
                            self.store.create_event(event_id, event_ts, rel, poster)
                            ev = {"id": event_id, "start": event_ts, "score": score,
                                  "thumb": poster, "last_update": now}
                            thumb_frame = buffer[-1][1] if buffer else frame
                            (self.store.path / poster).parent.mkdir(parents=True, exist_ok=True)
                            cv2.imwrite(str(self.store.path / poster), thumb_frame, [cv2.IMWRITE_JPEG_QUALITY, 79])
                            with self.lock:
                                self.last_event_id = event_id
                                self.rec_active = True
                        except Exception as ex:
                            log.exception("start recording failed")
                            with self.lock:
                                self.error = str(ex)
                            if encoder:
                                try:
                                    encoder.close()
                                except Exception:
                                    pass
                            encoder, ev = None, None
                if encoder:
                    try:
                        encoder.append(frame)
                        ev["score"] = max(score, ev["score"])
                        if now - ev["last_update"] >= 1.5:
                            self.store.progress_event(ev["id"], encoder.frame_count / self.fps, ev["score"])
                            ev["last_update"] = now
                    except Exception as ex:
                        log.exception("append recording")
                        with self.lock:
                            self.error = str(ex)
                        # attempt finalize; file may not be playable
                        self._close_encoder(encoder, ev)
                        encoder, ev = None, None
                    else:
                        if (not can_record or now - last_motion >= cfg["post_roll_seconds"]
                                or encoder.frame_count / self.fps >= cfg["max_clip_seconds"]):
                            self._close_encoder(encoder, ev)
                            encoder, ev = None, None
                            if now - last_motion < cfg["post_roll_seconds"]:
                                motion_streak = 0
                buffer.append((time.time(), frame.copy()))
                if now - last_preview > 0.38:
                    shown = frame.copy()
                    with self.lock:
                        state = self.state
                        active = self.rec_active
                    if cfg["show_boxes"]:
                        for x, y, ww, hh in boxes:
                            cv2.rectangle(shown, (x, y), (x + ww, y + hh), (48, 182, 255), 2)
                    caption = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
                    cv2.rectangle(shown, (0, self.height - 35), (self.width, self.height), (19, 25, 35), -1)
                    cv2.putText(shown, caption, (13, self.height - 12), cv2.FONT_HERSHEY_SIMPLEX, .60, (242, 246, 248), 1, cv2.LINE_AA)
                    if active:
                        cv2.circle(shown, (self.width - 120, self.height - 18), 6, (58, 63, 234), -1)
                        cv2.putText(shown, "REC", (self.width - 106, self.height - 12), cv2.FONT_HERSHEY_SIMPLEX, .58, (235, 235, 247), 1)
                    if state == "motion":
                        cv2.putText(shown, "MOTION", (self.width - 195, 25), cv2.FONT_HERSHEY_SIMPLEX, .65, (25, 186, 248), 2)
                    ok_jpg, img_jpg = cv2.imencode(".jpg", shown, [cv2.IMWRITE_JPEG_QUALITY, 76])
                    if ok_jpg:
                        with self.condition:
                            self.jpeg = img_jpg.tobytes()
                            self.seq += 1
                            self.condition.notify_all()
                    last_preview = now
        except Exception as exc:
            log.exception("camera worker unexpectedly stopped")
            with self.lock:
                self.error = f"采集线程异常停止：{exc}"
        finally:
            if encoder and ev:
                self._close_encoder(encoder, ev)
            if cap:
                cap.release()
            with self.lock:
                self.online, self.rec_active, self.state = False, False, "offline"

    def _close_encoder(self, encoder: VideoEncoder, ev: dict):
        try:
            duration = encoder.close()
            path = encoder.path
            if duration >= .25 and path.is_file() and path.stat().st_size > 1000:
                self.store.finish_event(ev["id"], duration, ev["score"], path.stat().st_size)
            else:
                self.store.remove_event(ev["id"])
                path.unlink(missing_ok=True)
        except Exception as ex:
            self.store.remove_event(ev["id"])
            encoder.path.unlink(missing_ok=True)
            log.exception("video finalize failed")
            with self.lock:
                self.error = f"录制文件完成失败: {ex}"
        finally:
            with self.lock:
                self.rec_active = False
