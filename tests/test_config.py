"""Cross-machine video/camera configuration regression tests."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import config


class NVRConfigTests(unittest.TestCase):
    def test_defaults_do_not_require_original_laptop_mount(self):
        with patch.dict(os.environ, {"NVR_DATA_DIR": "~/portable-test", "NVR_REQUIRE_MOUNT": "", "NVR_CAMERA": "/dev/video3"}):
            c = config()
            self.assertIsNone(c.require_mount)
            self.assertEqual(c.camera, "/dev/video3")
            self.assertEqual((c.width, c.height, c.fps), (640, 360, 8))

    def test_rejects_odd_h264_dimensions(self):
        with patch.dict(os.environ, {"NVR_WIDTH": "641", "NVR_REQUIRE_MOUNT": ""}):
            with self.assertRaisesRegex(ValueError, 'even'):
                config()

    def test_rejects_unmounted_recording_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            disk = Path(tmp) / 'not-mounted'
            disk.mkdir()
            with patch.dict(os.environ, {
                "NVR_DATA_DIR": str(disk / 'NVR'),
                "NVR_REQUIRE_MOUNT": str(disk),
            }):
                with self.assertRaisesRegex(RuntimeError, '没有挂载'):
                    config()

    def test_rejects_recording_path_outside_required_mount(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {
                "NVR_DATA_DIR": str(Path(tmp) / 'different'),
                "NVR_REQUIRE_MOUNT": str(Path(tmp) / 'disk'),
            }):
                with self.assertRaisesRegex(RuntimeError, '不在指定挂载点'):
                    config()


if __name__ == '__main__':
    unittest.main()
