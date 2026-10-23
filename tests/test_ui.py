"""Security regression: CSP-compatible external assets, no inline handlers."""
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.config import NVRConfig
from app.main import create_app


class WebSecurityTests(unittest.TestCase):
    def test_html_external_assets_csp_and_allowlist(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = NVRConfig(Path(temp), "unused", True, 8, 320, 180, None)
            with TestClient(create_app(cfg), base_url="http://127.0.0.1:7866") as client:
                page = client.get("/")
                self.assertEqual(page.status_code, 200)
                self.assertIn('/static/app.js', page.text)
                self.assertIn('/static/app.css', page.text)
                self.assertNotIn("onclick=", page.text)
                self.assertNotIn("<script>", page.text)
                csp = page.headers.get("content-security-policy", "")
                self.assertIn("script-src 'self'", csp)
                self.assertNotIn("script-src 'unsafe-inline'", csp)
                for name, kind in [("app.js", "text/javascript"),
                                   ("app.css", "text/css")]:
                    asset = client.get("/static/" + name)
                    self.assertEqual(asset.status_code, 200)
                    self.assertTrue(asset.headers["content-type"].startswith(kind))
                self.assertEqual(client.get("/static/main.py").status_code, 404)


if __name__ == "__main__":
    unittest.main()
