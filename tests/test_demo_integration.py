"""Full-stack demo smoke: camera -> detector -> async encoder -> SQLite -> seek."""
from __future__ import annotations
from pathlib import Path
import tempfile
import time
import unittest

import cv2
from fastapi.testclient import TestClient

from app.config import NVRConfig
from app.main import create_app


class DemoEndToEndTests(unittest.TestCase):
    def test_motion_creates_playable_archive_without_hardware(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = NVRConfig(Path(directory), "unused", True, 8, 320, 180, None)
            app = create_app(cfg)
            with TestClient(app, base_url="http://127.0.0.1:7866") as client:
                response = client.post(
                    "/api/settings",
                    json={"record_enabled": True, "sensitivity": 0.15,
                          "min_motion_frames": 1, "post_roll_seconds": 2.0},
                    headers={"x-local-ui": "1"})
                self.assertEqual(response.status_code, 200, response.text)
                limit = time.monotonic() + 19
                while time.monotonic() < limit and app.state.store.count_events() == 0:
                    time.sleep(.35)
                events = app.state.store.events()
                self.assertGreater(len(events), 0, "demo clip not archived in 19s")
                self.assertGreater(events[0]["bytes"], 1000)
                self.assertGreater(events[0]["duration"], .25)
                self.assertLess(events[0]["duration"], 40)
                response = client.get(
                    f"/api/events/{events[0]['id']}/video",
                    headers={"Range": "bytes=0-31"})
                self.assertEqual(response.status_code, 206)
                self.assertEqual(len(response.content), 32)
                path = app.state.store.safe_path(events[0]["video_path"])
                capture = cv2.VideoCapture(str(path))
                self.assertTrue(capture.isOpened())
                self.assertGreater(capture.get(cv2.CAP_PROP_FRAME_COUNT), 2)
                capture.release()
                state = client.get("/api/status").json()["camera"]
                self.assertGreater(state["frames_captured"], 20)
                self.assertGreater(state["frames_processed"], 20)


if __name__ == "__main__":
    unittest.main()
