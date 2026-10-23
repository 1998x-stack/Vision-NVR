"""Regression tests for v2 reliability and security. No physical camera required."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

from app.engine import CameraWorker, MotionDetector, VideoEncoder
from app.main import create_app
from app.store import Store


class StabilityTests(unittest.TestCase):
    def test_global_lighting_change_settles_without_trigger(self):
        detector = MotionDetector(warmup=12)
        rng = np.random.default_rng(42)
        base = rng.integers(40, 100, (360, 640, 3), dtype=np.uint8)
        for _ in range(55):
            detector.detect(base, .7)
        shifted = np.clip(base.astype(np.int16) + 20, 0, 255).astype(np.uint8)
        flags = [detector.detect(shifted, .7)[2] for _ in range(30)]
        self.assertLessEqual(sum(flags), 2, flags)
        self.assertGreaterEqual(detector.lighting_events, 1)

    def test_private_files_and_batched_cleanup_keep_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            self.assertEqual(stat.S_IMODE(store.path.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(store.db_path.stat().st_mode), 0o600)
            old = time.time() - 15 * 86400
            for i in range(45):
                identifier = f"{i:018x}"
                video = f"recordings/old/{identifier}.mp4"
                file = store.path / video
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(b"dummy video")
                store.create_event(identifier, old + i, video, "thumbnails/old/missing.jpg")
                store.finish_event(identifier, 4, 1, 1_000_000_000)
                if i == 44:
                    store.toggle_protect(identifier, True)
            store.update_settings({"retention_days": 1, "max_storage_gb": 1})
            self.assertEqual(store.cleanup(), 44)
            self.assertEqual(store.count_events(), 1)
            self.assertEqual(store.bytes_used(), 1_000_000_000)
            self.assertIsNotNone(store.get_event(f"{44:018x}"))

    def test_reconcile_recovers_playable_and_removes_invalid_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            video = store.path / "recordings/2020-01-01/valid.mp4"
            video.parent.mkdir(parents=True, exist_ok=True)
            encoder = VideoEncoder(video, 320, 180, 8)
            for i in range(24):
                img = np.zeros((180, 320, 3), dtype=np.uint8)
                img[20:120, i * 4:i * 4 + 90] = (90, 160, 220)
                encoder.append(img)
            encoder.close()
            store.create_event("a" * 18, time.time() - 60, str(video.relative_to(store.path)), "thumb.jpg")
            store.create_event("b" * 18, time.time() - 50, "recordings/2020-01-01/missing.mp4", "missing.jpg")
            outcome = store.reconcile()
            self.assertEqual(outcome["recovered"], 1)
            self.assertEqual(outcome["discarded"], 1)
            self.assertEqual(store.count_events(), 1)
            self.assertIsNone(store.get_event("b" * 18))
            self.assertGreater(store.get_event("a" * 18)["duration"], 2)

    def test_supervisor_restarts_failed_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = CameraWorker(Store(Path(directory)), demo=True)
            called = []
            def crash_twice():
                called.append(1)
                if len(called) <= 2:
                    raise RuntimeError("synthetic camera worker failure")
                worker.running = False
            worker.running = True
            with patch.object(worker, "_loop", side_effect=crash_twice):
                thread = threading.Thread(target=worker._supervise)
                thread.start()
                thread.join(timeout=6)
            self.assertFalse(thread.is_alive())
            self.assertEqual(worker.loop_restarts, 2)
            self.assertEqual(len(called), 3)

    def test_host_rebinding_forbidden_and_range_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"NVR_ALLOWED_HOSTS": "127.0.0.1,localhost"}):
                from app.config import NVRConfig
                cfg = NVRConfig(Path(directory), "unused", True, 8, 320, 180, None)
                with TestClient(create_app(cfg), base_url="http://127.0.0.1:7866") as client:
                    bad = {"host": "evil.example:7866",
                           "origin": "http://evil.example:7866", "x-local-ui": "1"}
                    self.assertEqual(client.get("/api/status", headers=bad).status_code, 400)
                    self.assertEqual(
                        client.post("/api/settings", json={"record_enabled": True},
                                    headers=bad).status_code, 400)
                    allowed = {"x-local-ui": "1", "origin": "http://127.0.0.1:7866"}
                    self.assertEqual(
                        client.post("/api/settings", json={"record_enabled": True},
                                    headers=allowed).status_code, 200)
                    store = client.app.state.store
                    identifier = "d" * 18
                    name = "recordings/sample.mp4"
                    file = store.path / name
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_bytes(b"abcdefghij")
                    store.create_event(identifier, time.time(), name, "sample.jpg")
                    store.finish_event(identifier, 1, 1, 10)
                    response = client.get(
                        f"/api/events/{identifier}/video", headers={"range": "bytes=1-3"})
                    self.assertEqual(response.status_code, 206, response.text)
                    self.assertEqual(response.content, b"bcd")
            self.assertFalse(Store(Path(directory)).settings()["record_enabled"])


if __name__ == "__main__":
    unittest.main()
