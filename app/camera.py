"""Dedicated camera grabber: a one-frame mailbox prevents analysis from backing up V4L2.

The camera remains local by default. No streams are shared outside the NVR.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import logging
import os
import threading
import time

import cv2
import numpy as np

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FramePacket:
    seq: int
    wall_time: float
    mono_time: float
    frame: np.ndarray


class FrameGrabber:
    def __init__(self, source="/dev/video0", *, demo=False, fps=8, width=640, height=360):
        self.source = source
        self.demo = demo
        self.fps = fps
        self.width, self.height = width, height
        self.condition = threading.Condition()
        self.running = False
        self.thread: threading.Thread | None = None
        self.packet: FramePacket | None = None
        self.seq = 0
        self.consumed_seq = 0
        self.captured = 0
        self.overwritten = 0
        self.failures = 0
        self.reconnects = 0
        self.online = False
        self.error = ""
        self.last_frame = 0.0
        self.samples = deque(maxlen=240)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, name="nvr-grabber", daemon=True)
        self.thread.start()

    def stop(self):
        with self.condition:
            self.running = False
            self.condition.notify_all()
        if self.thread:
            self.thread.join(timeout=4)

    def get(self, last_seq: int, timeout=1.0) -> FramePacket | None:
        with self.condition:
            self.condition.wait_for(
                lambda: not self.running or self.seq != last_seq, timeout=timeout)
            if not self.running or self.seq == last_seq:
                return None
            self.consumed_seq = self.seq
            return self.packet

    def status(self):
        with self.condition:
            now = time.monotonic()
            recent = [t for t in self.samples if now - t <= 5]
            fps = ((len(recent) - 1) / (recent[-1] - recent[0])
                   if len(recent) > 1 and recent[-1] > recent[0] else 0.0)
            return {
                "capture_fps": round(fps, 2),
                "frames_captured": self.captured,
                "frames_overwritten": self.overwritten,
                "camera_failures": self.failures,
                "camera_reconnects": self.reconnects,
                "grabber_alive": bool(self.thread and self.thread.is_alive()),
                "camera_online": self.online,
                "camera_error": self.error,
                "camera_last_frame": self.last_frame,
            }

    def _demo_frame(self, t):
        frame = np.empty((self.height, self.width, 3), np.uint8)
        frame[:] = (23, 28, 32)
        x1, y1 = int(self.width * .05), int(self.height * .18)
        x2, y2 = int(self.width * .95), int(self.height * .9)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (40, 48, 57), -1)
        cv2.putText(frame, "DEMO CAMERA - NOT REAL VIDEO",
                    (max(5, int(self.width * .1)), max(20, int(self.height * .09))),
                    cv2.FONT_HERSHEY_SIMPLEX, .48, (190, 190, 190), 1, cv2.LINE_AA)
        phase = t % 18
        if 5 < phase < 12:
            px = int(self.width * (.07 + (phase - 5) / 7 * .8))
            cv2.circle(frame, (px, self.height // 2), max(8, self.height // 11),
                       (160, 180, 220), -1)
        return frame

    def _publish(self, frame):
        wall, mono = time.time(), time.monotonic()
        with self.condition:
            if self.seq > self.consumed_seq:
                self.overwritten += 1
            self.seq += 1
            self.packet = FramePacket(self.seq, wall, mono, frame)
            self.last_frame = wall
            self.captured += 1
            self.online = True
            self.error = ""
            self.samples.append(mono)
            self.condition.notify_all()

    def _run(self):
        cap = None
        retries = 0
        first = time.monotonic()
        next_demo = 0.0
        while self.running:
            try:
                now = time.monotonic()
                if self.demo:
                    if now < next_demo:
                        time.sleep(min(.025, next_demo - now))
                        continue
                    next_demo = now + 1 / self.fps
                    self._publish(self._demo_frame(now - first))
                    continue
                if cap is None:
                    if now < retries:
                        time.sleep(.2)
                        continue
                    source = int(self.source) if self.source.isdecimal() else self.source
                    network = isinstance(source, str) and source.lower().startswith(
                        ("rtsp://", "rtsps://", "http://", "https://"))
                    backend = cv2.CAP_FFMPEG if network else (
                        cv2.CAP_V4L2 if os.name == "posix" else cv2.CAP_ANY)
                    cap = cv2.VideoCapture(source, backend)
                    if not cap.isOpened():
                        cap.release()
                        cap = None
                        retries = time.monotonic() + 4
                        with self.condition:
                            self.online = False
                            self.error = f"无法打开摄像头 {self.source}"
                            self.failures += 1
                        continue
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                    cap.set(cv2.CAP_PROP_FPS, self.fps)
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    with self.condition:
                        self.reconnects += 1
                ok, frame = cap.read()
                if not ok or frame is None:
                    cap.release()
                    cap = None
                    with self.condition:
                        self.online = False
                        self.error = "摄像头读取失败，正在重连"
                        self.failures += 1
                    retries = time.monotonic() + 1
                    continue
                self._publish(frame)
            except Exception:
                log.exception("grabber failure")
                with self.condition:
                    self.failures += 1
                    self.online = False
                    self.error = "采集异常，正在重试"
                if cap is not None:
                    cap.release()
                    cap = None
                time.sleep(.3)
        if cap is not None:
            cap.release()
        with self.condition:
            self.online = False
            self.condition.notify_all()
