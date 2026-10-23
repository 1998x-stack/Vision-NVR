"""v3 pipeline and privacy regression tests, synthetic frames only."""
from __future__ import annotations

import math
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from fastapi.testclient import TestClient

from app.camera import FrameGrabber
from app.config import NVRConfig
from app.encoder import VideoEncoder
from app.engine import CameraWorker, MotionDetector
from app.main import create_app
from app.recorder import AsyncEventRecorder
from app.store import Store
from app.zones import detection_mask, privacy_redact, validate_zones


def zones(**overrides):
    return {"include": [], "exclude": [], "privacy": [], **overrides}


class FakeSlowEncoder:
    def __init__(self, path, width, height, fps):
        self.path, self.fps, self.frame_count = path, fps, 0
        path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, frame):
        time.sleep(.025)  # deterministic simulated slow encoder
        self.frame_count += 1

    def close(self):
        self.path.write_bytes(b"x" * 2500)
        return self.frame_count / self.fps


class FailingEncoder(FakeSlowEncoder):
    def append(self, frame):
        raise BrokenPipeError("synthetic encoder failure")


class PipelineTests(unittest.TestCase):
    def test_polygon_validation_limits_and_rejects_invalid(self):
        good = zones(privacy=[[[.1,.1],[.8,.1],[.8,.8]]])
        self.assertEqual(validate_zones(good), good)
        for bad in (
            zones(exclude=[[[0,0],[.5,0],[float("nan"),1]]]),
            zones(privacy=[[[0,0],[.5,0],[1,1]]] * 9),
            zones(include=[[[0,0],[0,0],[0,0]]]),
            {"include":[],"exclude":[]},
        ):
            with self.subTest(bad=repr(bad)[:40]), self.assertRaises(ValueError):
                validate_zones(bad)

    def test_private_pixels_black_and_roi_ignores_outside(self):
        z = zones(
            exclude=[[[0,0],[.45,0],[.45,1],[0,1]]],
            privacy=[[[.6,.2],[1,.2],[1,.8],[.6,.8]]])
        frame = np.full((200, 300, 3), 180, dtype=np.uint8)
        redacted = privacy_redact(frame, z)
        self.assertTrue(np.all(redacted[100, 275] == 0))
        self.assertEqual(int(redacted[100, 150, 0]), 180)
        # Original camera frame must never be modified by redaction.
        self.assertEqual(int(frame[100, 275, 0]), 180)
        roi = detection_mask(300, 200, z)
        self.assertEqual(int(roi[100, 30]), 0)
        self.assertEqual(int(roi[100, 275]), 0)
        self.assertEqual(int(roi[100, 160]), 255)

    def test_mog2_excluded_motion_does_not_trigger(self):
        d = MotionDetector(warmup=8)
        blank = np.full((180,320,3), 55, dtype=np.uint8)
        cfg = zones(exclude=[[[0,0],[.5,0],[.5,1],[0,1]]])
        for _ in range(25):
            d.detect(blank, .7, cfg)
        moving = blank.copy()
        cv2.rectangle(moving, (30, 40), (110, 130), (200,210,220), -1)
        for _ in range(4):
            score, _, hit = d.detect(moving, .7, cfg)
            self.assertFalse(hit, score)

    def test_latest_frame_mailbox_is_bounded(self):
        grabber = FrameGrabber(demo=True, width=320, height=180)
        grabber.running = True
        for i in range(30):
            grabber._publish(np.full((180, 320, 3), i, dtype=np.uint8))
        packet = grabber.get(0, timeout=.01)
        self.assertEqual(packet.seq, 30)
        self.assertEqual(int(packet.frame[0,0,0]), 29)
        self.assertEqual(grabber.status()["frames_overwritten"], 29)
        self.assertIsNone(grabber.get(30, timeout=.001))
        grabber.running = False

    def test_async_queue_overload_reports_drops_and_recovers(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            event_id = "1" * 18
            rel = "recordings/old/slow.mp4"
            store.create_event(event_id, time.time(), rel, "thumb.jpg")
            recorder = AsyncEventRecorder(
                store, event_id, store.path / rel, 80, 60, 8,
                queue_size=3, encoder_factory=FakeSlowEncoder)
            frame = np.full((60,80,3), 100, dtype=np.uint8)
            start = time.monotonic()
            for i in range(120):
                self.assertTrue(recorder.append(frame, start+i/8, 3))
            recorder.finish()
            self.assertTrue(recorder.join(8))
            state = recorder.status()
            self.assertGreater(state["encoder_frames_dropped"], 0)
            self.assertLessEqual(state["encoder_queue_depth"], 3)
            self.assertFalse(state["encoder_error"])
            self.assertEqual(store.count_events(), 1)

    def test_async_encoder_failure_cleans_event(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            event_id = "2" * 18
            rel = "recordings/old/fail.mp4"
            store.create_event(event_id, time.time(), rel, "thumb.jpg")
            recorder = AsyncEventRecorder(
                store, event_id, store.path / rel, 80, 60, 8,
                encoder_factory=FailingEncoder)
            recorder.append(np.zeros((60,80,3),dtype=np.uint8),time.monotonic(),1)
            recorder.finish()
            self.assertTrue(recorder.join(4))
            self.assertTrue(recorder.status()["encoder_error"])
            self.assertIsNone(store.get_event(event_id))

    def test_actual_encoded_video_contains_permanent_redaction(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "redacted.mp4"
            private = zones(privacy=[[[0,0],[.45,0],[.45,1],[0,1]]])
            original = np.full((180,320,3), 200, dtype=np.uint8)
            encoder = VideoEncoder(path, 320, 180, 8)
            for _ in range(20):
                encoder.append(privacy_redact(original, private))
            encoder.close()
            cap = cv2.VideoCapture(str(path))
            good, decoded = cap.read()
            cap.release()
            self.assertTrue(good)
            self.assertLess(float(decoded[90,40].mean()), 12)
            self.assertGreater(float(decoded[90,250].mean()), 160)

    def test_zones_api_persists_and_invalidates_old_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = NVRConfig(Path(directory), "unused", True, 8, 320, 180, None)
            with TestClient(create_app(cfg), base_url="http://127.0.0.1:7866") as client:
                worker = client.app.state.worker
                with worker.lock:
                    worker.jpeg = b"old_image"
                    worker.latest_frame = np.full((180,320,3), 180,dtype=np.uint8)
                privacy = zones(privacy=[[[0,0],[.5,0],[.5,1],[0,1]]])
                res = client.post("/api/settings", json={"zones":privacy},
                                  headers={"x-local-ui":"1"})
                self.assertEqual(res.status_code, 200, res.text)
                self.assertEqual(res.json()["zones"], privacy)
                self.assertIsNone(worker.jpeg)
                self.assertIsNone(worker.latest_frame)
                self.assertEqual(client.get("/api/status").json()["settings"]["zones"], privacy)
                bad = client.post("/api/settings",
                    json={"zones":zones(include=[[[0,0],[1.2,0],[.5,.5]]])},
                    headers={"x-local-ui":"1"})
                self.assertEqual(bad.status_code,422)
            self.assertEqual(Store(Path(directory)).settings()["zones"], privacy)

    def test_storage_remount_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            worker = CameraWorker(store, demo=True, require_mount=Path("/not/mounted"))
            with patch("app.engine.os.path.ismount", return_value=False):
                self.assertFalse(worker._storage_ok())


if __name__ == "__main__":
    unittest.main()
