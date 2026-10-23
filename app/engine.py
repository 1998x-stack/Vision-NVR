"""Camera capture, lightweight background subtraction, event-triggered H.264 encoder."""
from __future__ import annotations

from collections import deque
import json
from datetime import datetime
import logging
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid

from .camera import FrameGrabber
from .encoder import VideoEncoder  # public backwards-compatibility alias
from .recorder import AsyncEventRecorder
from .zones import detection_mask, privacy_redact

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
        self.stable_mean: float | None = None
        self.settle_remaining = 0
        self.lighting_events = 0
        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))

    def detect(self, frame: np.ndarray, threshold: float = 0.65, zones: dict | None = None):
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (self.width, max(1, round(h * self.width / w))))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        mean = float(gray.mean())
        # A light switch causes a full-frame MOG2 transient lasting many samples.
        # Use both a shift from the slow luminance baseline and the raw mask area.
        mask = self.subtractor.apply(
            gray, learningRate=0.4 if self.settle_remaining else -1)
        raw_changed = float(np.count_nonzero(mask == 255)) / mask.size * 100.0
        if self.stable_mean is None:
            self.stable_mean = mean
        if (self.samples > self.warmup and self.settle_remaining == 0
                and abs(mean - self.stable_mean) >= 12 and raw_changed >= 35):
            self.settle_remaining = 20
            self.lighting_events += 1
            # Rapidly relearn the global exposure while suppressing transients.
            self.subtractor.apply(gray, learningRate=0.5)
            self.stable_mean = mean
        self.samples += 1
        mask = cv2.threshold(mask, 250, 255, cv2.THRESH_BINARY)[1]  # suppress gray shadow label
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self.kernel)
        mask = cv2.dilate(mask, self.kernel, iterations=1)
        allowed_pixels = mask.size
        if zones is not None:
            roi = detection_mask(mask.shape[1], mask.shape[0], zones)
            if roi is not None:
                allowed_pixels = int(np.count_nonzero(roi))
                mask = cv2.bitwise_and(mask, roi)
                if not allowed_pixels:
                    return 0.0, [], False
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        min_blob = max(20, int(allowed_pixels * 0.002))
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
        pct = 100.0 * significant_area / allowed_pixels
        self.last_mean = mean
        if self.samples <= self.warmup:
            self.stable_mean = mean
            return 0.0, [], False
        if self.settle_remaining:
            self.settle_remaining -= 1
            self.stable_mean = mean
            return 0.0, [], False
        self.stable_mean = 0.98 * self.stable_mean + 0.02 * mean
        return round(pct, 3), rectangles[:8], pct >= threshold


def level(score: float) -> str:
    if score >= 8:
        return "high"
    if score >= 2:
        return "medium"
    if score >= 0.5:
        return "low"
    return "idle"



class CameraWorker:
    """Analyzer/event state machine. Camera reads and video encoding are separate threads."""

    def __init__(self, store, *, camera: str = "/dev/video0", demo: bool = False,
                 fps: int = 8, width: int = 640, height: int = 360,
                 require_mount: Path | None = None):
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
        self.grabber = FrameGrabber(camera, demo=demo, fps=fps, width=width, height=height)
        self.jpeg: bytes | None = None
        self.latest_frame: np.ndarray | None = None
        self.preview_viewers = 0
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
        self.frames_skipped = 0
        self.loop_restarts = 0
        self.last_cleanup_seconds = 0.0
        self.last_cleanup_removed = 0
        self.janitor_stop = threading.Event()
        self.janitor_thread: threading.Thread | None = None
        self.detector = MotionDetector()
        self.activity_seconds: deque[tuple[int, str]] = deque(maxlen=180)
        self.process_timestamps: deque[float] = deque(maxlen=240)
        self.process_cost_ms: deque[float] = deque(maxlen=240)
        self.storage_device = store.path.stat().st_dev
        self.storage_online = True
        self.storage_guard_stops = 0
        self.encoder_drops_total = 0
        self.encoder_duplicates_total = 0
        self.encoder_failures = 0
        self.last_encoder_error = ""
        self._active: AsyncEventRecorder | None = None
        self._pending: list[AsyncEventRecorder] = []

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.running = True
        self.janitor_stop.clear()
        self.grabber.start()
        self.janitor_thread = threading.Thread(
            target=self._janitor, name="nvr-janitor", daemon=True)
        self.janitor_thread.start()
        self.thread = threading.Thread(
            target=self._supervise, name="nvr-analyzer", daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        self.janitor_stop.set()
        with self.condition:
            self.condition.notify_all()
        self.grabber.stop()
        if self.thread:
            self.thread.join(timeout=35)
        if self.janitor_thread:
            self.janitor_thread.join(timeout=3)

    def _janitor(self):
        while not self.janitor_stop.is_set():
            begin = time.monotonic()
            try:
                deleted = self.store.cleanup()
                with self.lock:
                    self.last_cleanup_removed = deleted
            except Exception:
                log.exception("background retention failed")
            with self.lock:
                self.last_cleanup_seconds = time.monotonic() - begin
            if self.janitor_stop.wait(120):
                break

    def _supervise(self):
        while self.running:
            try:
                self._loop()
            except Exception:
                log.exception("camera analyzer failure")
            if self.running:
                with self.lock:
                    self.loop_restarts += 1
                    self.online = False
                    self.state = "recovering"
                time.sleep(1)

    def status(self):
        with self.lock:
            recent = [t for t in self.process_timestamps
                      if time.monotonic() - t <= 5]
            actual_fps = ((len(recent) - 1) / (recent[-1] - recent[0])
                          if len(recent) > 1 and recent[-1] > recent[0] else 0)
            costs = sorted(self.process_cost_ms)
            p95 = costs[int((len(costs)-1)*.95)] if costs else 0
            active = self._active.status() if self._active else {}
            pending_depth = sum(r.status()["encoder_queue_depth"] for r in self._pending)
            state = {
                "online": self.online, "state": self.state, "error": self.error,
                "score": self.score, "level": self.activity_level,
                "recording_clip": self.rec_active,
                "last_frame": self.last_frame, "fps_target": self.fps,
                "actual_analysis_fps": round(actual_fps, 2),
                "processing_p95_ms": round(p95, 2),
                "frames_processed": self.frames_processed,
                "frames_skipped": self.frames_skipped,
                "worker_alive": bool(self.thread and self.thread.is_alive()),
                "loop_restarts": self.loop_restarts,
                "lighting_changes_ignored": self.detector.lighting_events,
                "last_cleanup_seconds": round(self.last_cleanup_seconds, 3),
                "last_cleanup_removed": self.last_cleanup_removed,
                "source": "模拟演示画面" if self.demo else self.camera,
                "last_event_id": self.last_event_id,
                "activity_seconds": list(self.activity_seconds),
                "preview_clients": self.preview_viewers,
                "encoder_queue_depth": active.get("encoder_queue_depth", 0) + pending_depth,
                "encoder_frames_dropped": self.encoder_drops_total + active.get("encoder_frames_dropped", 0),
                "encoder_frames_duplicated": self.encoder_duplicates_total + active.get("encoder_frames_duplicated", 0),
                "encoder_pending_clips": len(self._pending),
                "encoder_failures": self.encoder_failures,
                "last_encoder_error": self.last_encoder_error,
                "storage_online": self.storage_online,
                "storage_guard_stops": self.storage_guard_stops,
            }
        state.update(self.grabber.status())
        return state

    def invalidate_preview(self):
        with self.condition:
            self.jpeg = None
            self.latest_frame = None
            self.seq += 1
            self.condition.notify_all()

    def snapshot(self) -> bytes | None:
        with self.lock:
            frame = self.latest_frame
        if frame is None:
            return None
        # Apply current masks again: newly added privacy zones are never missed
        # by one-shot snapshots during a live settings update.
        zones = self.store.settings()["zones"]
        protected = privacy_redact(frame, zones)
        ok, jpg = cv2.imencode(".jpg", protected, [cv2.IMWRITE_JPEG_QUALITY, 78])
        return jpg.tobytes() if ok else None

    def frames(self):
        previous = -1
        with self.condition:
            self.preview_viewers += 1
        try:
            while self.running:
                with self.condition:
                    self.condition.wait_for(
                        lambda: self.seq != previous or not self.running, timeout=3)
                    if not self.running:
                        return
                    previous = self.seq
                    jpg = self.jpeg
                if jpg:
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                           + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
        finally:
            with self.condition:
                self.preview_viewers = max(0, self.preview_viewers - 1)

    def _letterbox(self, frame):
        h, w = frame.shape[:2]
        scale = min(self.width / w, self.height / h)
        resized = cv2.resize(frame, (max(1, round(w * scale)),
                                     max(1, round(h * scale))))
        out = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        rh, rw = resized.shape[:2]
        x, y = (self.width - rw) // 2, (self.height - rh) // 2
        out[y:y + rh, x:x + rw] = resized
        return out

    def _storage_ok(self):
        try:
            if self.require_mount and not os.path.ismount(self.require_mount):
                return False
            return self.store.path.stat().st_dev == self.storage_device
        except OSError:
            return False

    def _reap_encoders(self):
        remaining = []
        for recorder in self._pending:
            if recorder.complete.is_set():
                stat = recorder.status()
                self.encoder_drops_total += stat["encoder_frames_dropped"]
                self.encoder_duplicates_total += stat["encoder_frames_duplicated"]
                if stat["encoder_error"]:
                    self.encoder_failures += 1
                    self.last_encoder_error = stat["encoder_error"]
            else:
                remaining.append(recorder)
        self._pending = remaining

    def _finish_active(self):
        if self._active:
            self._active.finish()
            self._pending.append(self._active)
            self._active = None
        with self.lock:
            self.rec_active = False

    def _start_event(self, history, ts, score, zones):
        if not self.storage_online or len(self._pending) >= 2:
            return False
        # Recheck *at the actual start* to avoid writing to the underlying SSD
        # when the target mount disappeared between periodic health probes.
        if not self._storage_ok():
            self.storage_online = False
            return False
        if not self.store.allow_new_recording():
            return False
        event_wall = history[0][0] if history else time.time()
        ident = uuid.uuid4().hex[:18]
        day = datetime.fromtimestamp(event_wall).strftime("%Y-%m-%d")
        name = datetime.fromtimestamp(event_wall).strftime("%H-%M-%S") + f"_{ident[:8]}.mp4"
        relative = f"recordings/{day}/{name}"
        thumbnail = f"thumbnails/{day}/{name[:-4]}.jpg"
        try:
            self.store.create_event(ident, event_wall, relative, thumbnail)
            poster = self.store.path / thumbnail
            poster.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            frame = history[-1][2]
            if not cv2.imwrite(str(poster), frame,
                               [cv2.IMWRITE_JPEG_QUALITY, 79]):
                raise RuntimeError("Cannot write event thumbnail")
            recorder = AsyncEventRecorder(
                self.store, ident, self.store.path / relative,
                self.width, self.height, self.fps)
            for wall, mono, image in history:
                recorder.append(image, mono, score)
            self._active = recorder
            with self.lock:
                self.last_event_id = ident
                self.rec_active = True
            return True
        except Exception as exc:
            log.exception("failed to initiate event")
            self.store.remove_event(ident)
            with self.lock:
                self.error = str(exc)
            return False

    def _render_preview(self, frame, boxes, state, active):
        shown = frame.copy()
        if self.store.settings()["show_boxes"]:
            for x, y, w, h in boxes:
                cv2.rectangle(shown, (x, y), (x+w, y+h), (48, 182, 255), 2)
        cv2.rectangle(shown, (0, self.height - 35),
                      (self.width, self.height), (19, 25, 35), -1)
        cv2.putText(shown, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    (13, self.height - 12), cv2.FONT_HERSHEY_SIMPLEX,
                    .55, (242, 246, 248), 1, cv2.LINE_AA)
        if active:
            cv2.putText(shown, "REC", (self.width-80, self.height-12),
                        cv2.FONT_HERSHEY_SIMPLEX, .55, (58, 63, 234), 1)
        if state == "motion":
            cv2.putText(shown, "MOTION", (self.width-185, 25),
                        cv2.FONT_HERSHEY_SIMPLEX, .6, (25, 186, 248), 2)
        ok, jpg = cv2.imencode(".jpg", shown, [cv2.IMWRITE_JPEG_QUALITY, 76])
        if ok:
            with self.condition:
                self.jpeg = jpg.tobytes()
                self.seq += 1
                self.condition.notify_all()

    def _loop(self):
        buffer = deque(maxlen=self.fps * 3)
        last_seq = 0
        last_reconnect = -1
        last_motion = 0.0
        motion_streak = 0
        last_preview = 0.0
        last_good_mono = time.monotonic()
        zone_key = None
        last_disk_check = 0.0
        clip_begin = 0.0
        next_analysis = 0.0
        try:
            while self.running:
                # V4L2 may deliver 30 fps while analysis/recording is set to 8.
                # Sample the newest image at target fps so 3*fps really means
                # about three seconds of preroll, not 24/30 of a second.
                if time.monotonic() < next_analysis:
                    time.sleep(min(.025, next_analysis - time.monotonic()))
                    continue
                tic = time.monotonic()
                packet = self.grabber.get(last_seq, timeout=.5)
                self._reap_encoders()
                if packet is None:
                    cap = self.grabber.status()
                    if not cap["camera_online"] or time.monotonic()-last_good_mono > 2:
                        self._finish_active()
                        buffer.clear()
                        with self.lock:
                            self.online = False
                            self.state = "offline"
                            self.error = cap["camera_error"] or "摄像头画面超时"
                    continue
                if packet.seq <= last_seq:
                    continue
                next_analysis = time.monotonic() + 1.0 / self.fps
                if last_seq:
                    self.frames_skipped += max(0, packet.seq-last_seq-1)
                last_seq = packet.seq
                last_good_mono = time.monotonic()
                reconnects = self.grabber.status()["camera_reconnects"]
                if reconnects != last_reconnect:
                    last_reconnect = reconnects
                    self._finish_active()
                    self.detector = MotionDetector()
                    buffer.clear()
                    motion_streak = 0
                cfg = self.store.settings()
                new_zone_key = json.dumps(cfg["zones"], sort_keys=True)
                if zone_key is not None and new_zone_key != zone_key:
                    # A privacy rule changed: split the clip and discard *all*
                    # preroll cached under the previous redaction policy.
                    self._finish_active()
                    buffer.clear()
                    motion_streak = 0
                    self.detector = MotionDetector()
                    self.invalidate_preview()
                zone_key = new_zone_key
                frame = privacy_redact(self._letterbox(packet.frame), cfg["zones"])
                score, boxes, motion = self.detector.detect(
                    frame, cfg["sensitivity"], cfg["zones"])
                if self._active or packet.mono_time-last_disk_check > 5:
                    last_disk_check = packet.mono_time
                    disk_ok = self._storage_ok()
                    if not disk_ok and self._active:
                        self.storage_guard_stops += 1
                        self._finish_active()
                    self.storage_online = disk_ok
                if motion:
                    motion_streak += 1
                    last_motion = packet.mono_time
                else:
                    motion_streak = 0
                if (self._active is None and motion_streak >= cfg["min_motion_frames"]
                        and cfg["record_enabled"] and self.storage_online):
                    if self._start_event(
                            buffer, packet.mono_time, score, cfg["zones"]):
                        clip_begin = buffer[0][1] if buffer else packet.mono_time
                if self._active:
                    self._active.append(frame, packet.mono_time, score)
                    if (not cfg["record_enabled"] or
                            packet.mono_time-last_motion >= cfg["post_roll_seconds"] or
                            packet.mono_time-clip_begin >= cfg["max_clip_seconds"]):
                        self._finish_active()
                        motion_streak = 0
                buffer.append((packet.wall_time, packet.mono_time, frame))
                new_state = ("calibrating" if self.detector.samples <= self.detector.warmup
                             else ("motion" if motion else "idle"))
                with self.lock:
                    self.score = score
                    self.boxes = boxes
                    self.activity_level = level(score)
                    sec = int(packet.wall_time)
                    ranks = {"idle":0,"low":1,"medium":2,"high":3}
                    if self.activity_seconds and self.activity_seconds[-1][0] == sec:
                        prior = self.activity_seconds[-1][1]
                        self.activity_seconds[-1] = (
                            sec, max(prior, self.activity_level, key=lambda x: ranks[x]))
                    else:
                        self.activity_seconds.append((sec, self.activity_level))
                    self.latest_frame = frame
                    self.online = True
                    self.state = new_state
                    self.last_frame = packet.wall_time
                    self.error = ""
                    self.frames_processed += 1
                if packet.mono_time-last_preview > .38:
                    with self.lock:
                        viewers = self.preview_viewers
                    if viewers:
                        self._render_preview(frame, boxes, new_state, self._active is not None)
                    last_preview = packet.mono_time
                with self.lock:
                    self.process_timestamps.append(time.monotonic())
                    self.process_cost_ms.append((time.monotonic()-tic)*1000)
        finally:
            self._finish_active()
            # Shut down writers gracefully, never run orphan FFmpeg children after
            # a worker restart. Active stream remains done=0 if it times out and
            # will be inspected by the startup reconciler.
            for pending in self._pending:
                pending.join(timeout=12)
            self._reap_encoders()
            with self.lock:
                self.online = False
                self.rec_active = False
                self.state = "offline"
