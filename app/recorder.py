"""Nonblocking, bounded FFmpeg writer with wall-clock-paced constant-frame-rate video.

The analyzer never writes FFmpeg stdin. When overloaded, oldest pending samples
are discarded; the encoder fills short gaps by repeating the latest frame and
reports both behaviors. No frame queue grows without a bound.
"""
from __future__ import annotations

import logging
from pathlib import Path
import queue
import threading
import time

import numpy as np

from .engine import VideoEncoder

log = logging.getLogger(__name__)


class AsyncEventRecorder:
    def __init__(self, store, event_id: str, path: Path, width: int,
                 height: int, fps: int, *, queue_size=24, encoder_factory=None):
        self.store = store
        self.event_id = event_id
        self.path = path
        self.width, self.height, self.fps = width, height, fps
        self.encoder_factory = encoder_factory or VideoEncoder
        self.queue = queue.Queue(maxsize=queue_size)
        self.lock = threading.Lock()
        self.finish_requested = threading.Event()
        self.complete = threading.Event()
        self.error = ""
        self.dropped_frames = 0
        self.duplicated_frames = 0
        self.encoded_frames = 0
        self.peak_score = 0.0
        self.thread = threading.Thread(
            target=self._run, name=f"encoder-{event_id[:8]}", daemon=True)
        self.thread.start()

    def append(self, frame: np.ndarray, mono_time: float, score: float) -> bool:
        """Never wait on FFmpeg. Returns False if the recorder is closed."""
        with self.lock:
            if self.finish_requested.is_set() or self.complete.is_set():
                return False
            self.peak_score = max(self.peak_score, score)
            sample = (float(mono_time), frame)
            try:
                self.queue.put_nowait(sample)
            except queue.Full:
                try:
                    self.queue.get_nowait()  # oldest not-yet-encoded frame
                    self.dropped_frames += 1
                except queue.Empty:
                    pass
                try:
                    self.queue.put_nowait(sample)
                except queue.Full:
                    self.dropped_frames += 1
        return True

    def finish(self):
        self.finish_requested.set()

    def join(self, timeout=20):
        self.thread.join(timeout)
        return self.complete.is_set()

    def status(self):
        with self.lock:
            return {
                "encoder_queue_depth": self.queue.qsize(),
                "encoder_queue_capacity": self.queue.maxsize,
                "encoder_frames_dropped": self.dropped_frames,
                "encoder_frames_duplicated": self.duplicated_frames,
                "encoder_frames_written": self.encoded_frames,
                "encoder_complete": self.complete.is_set(),
                "encoder_error": self.error,
            }

    def _run(self):
        encoder = None
        start_time = None
        prior = None
        last_progress = time.monotonic()
        try:
            encoder = self.encoder_factory(self.path, self.width, self.height, self.fps)
            while True:
                try:
                    captured_at, frame = self.queue.get(timeout=.15)
                except queue.Empty:
                    if self.finish_requested.is_set():
                        break
                    continue
                if start_time is None:
                    start_time = captured_at
                target_frame = max(0, round((captured_at - start_time) * self.fps))
                # Avoid runaway duration if an upstream connection stalls.
                target_frame = min(target_frame, encoder.frame_count + self.fps * 2)
                if target_frame < encoder.frame_count:
                    # Too many frames for the target CFR; favor the most recent sample.
                    prior = frame
                    continue
                while prior is not None and encoder.frame_count < target_frame:
                    encoder.append(prior)
                    self.duplicated_frames += 1
                encoder.append(frame)
                prior = frame
                self.encoded_frames = encoder.frame_count
                if time.monotonic() - last_progress >= 1.5:
                    self.store.progress_event(
                        self.event_id, encoder.frame_count / self.fps, self.peak_score)
                    last_progress = time.monotonic()
            duration = encoder.close()
            encoder = None
            if duration >= .25 and self.path.is_file() and self.path.stat().st_size > 1000:
                self.store.finish_event(
                    self.event_id, duration, self.peak_score, self.path.stat().st_size)
            else:
                self.store.remove_event(self.event_id)
        except Exception as exc:
            self.error = str(exc)
            log.exception("background video recorder %s failed", self.event_id)
            try:
                self.store.remove_event(self.event_id)
            except Exception:
                log.exception("failed to clean interrupted event")
        finally:
            if encoder is not None:
                try:
                    encoder.close()
                except Exception:
                    pass
            if self.error:
                self.path.unlink(missing_ok=True)
            self.complete.set()
