import time
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from app.engine import MotionDetector, VideoEncoder
from app.store import Store


class CoreTests(unittest.TestCase):
    def test_mog2_motion_after_warmup(self):
        d = MotionDetector(warmup=12)
        still = np.zeros((360, 640, 3), dtype=np.uint8) + 32
        for _ in range(20):
            d.detect(still)
        moved = still.copy()
        cv2.rectangle(moved, (120, 60), (280, 260), (200, 220, 235), -1)
        score, boxes, found = d.detect(moved, .4)
        self.assertTrue(found, f"score={score} boxes={boxes}")
        self.assertTrue(len(boxes) > 0)
        self.assertGreater(score, .4)

    def test_store_retention_and_protection(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d))
            store.update_settings({"record_enabled": True, "retention_days": 1})
            old = time.time() - 86400 * 8
            for id, protect in [("a" * 18, False), ("b" * 18, True)]:
                file = store.path / "recordings" / "2020-01-01" / (id + ".mp4")
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(b"testdata" * 100)
                store.create_event(id, old, str(file.relative_to(store.path)), "thumbnails/old.jpg")
                store.finish_event(id, 4, 2.5, file.stat().st_size)
                if protect:
                    store.toggle_protect(id, True)
            self.assertEqual(store.cleanup(), 1)
            self.assertIsNone(store.get_event("a" * 18))
            self.assertIsNotNone(store.get_event("b" * 18))
            self.assertFalse(store.delete("b" * 18))

    def test_restart_requires_explicit_recording_consent(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d))
            store.update_settings({"record_enabled": True, "retention_days": 30})
            restarted = Store(Path(d))
            self.assertFalse(restarted.settings()["record_enabled"])
            self.assertEqual(restarted.settings()["retention_days"], 30)

    def test_archive_path_cannot_escape_root(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d))
            with self.assertRaises(ValueError):
                store.safe_path("../secrets.txt")

    def test_encoder_mp4(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "event.mp4"
            enc = VideoEncoder(target, 320, 180, 8)
            for i in range(24):
                f = np.zeros((180, 320, 3), dtype=np.uint8)
                cv2.rectangle(f, (i * 4, 40), (i * 4 + 70, 110), (60, 155, 212), -1)
                enc.append(f)
            duration = enc.close()
            self.assertAlmostEqual(duration, 3)
            self.assertGreater(target.stat().st_size, 2000)
            cap = cv2.VideoCapture(str(target))
            self.assertTrue(cap.isOpened())
            self.assertEqual(round(cap.get(cv2.CAP_PROP_FPS)), 8)
            self.assertEqual(round(cap.get(cv2.CAP_PROP_FRAME_COUNT)), 24)
            cap.release()


if __name__ == '__main__':
    unittest.main()
