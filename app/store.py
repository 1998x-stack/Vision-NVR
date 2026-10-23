"""SQLite event index, bounded retention, safe local file serving."""
from __future__ import annotations
from datetime import datetime
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time

DEFAULTS = {
    "record_enabled": False,
    "sensitivity": 0.7,             # changed significant area percentage
    "min_motion_frames": 3,
    "post_roll_seconds": 3.0,
    "max_clip_seconds": 180,
    "retention_days": 30,
    "max_storage_gb": 20,
    "min_free_gb": 2,
    "show_boxes": True,
}


class Store:
    def __init__(self, root: Path):
        self.path = root.expanduser().resolve()
        self.path.mkdir(parents=True, exist_ok=True)
        self.file_cfg = self.path / "settings.json"
        self.db_path = self.path / "nvr.sqlite3"
        self.lock = threading.RLock()
        cfg = dict(DEFAULTS)
        if self.file_cfg.is_file():
            try:
                saved = json.loads(self.file_cfg.read_text("utf-8"))
                cfg.update({k: v for k, v in saved.items() if k in cfg})
            except Exception:
                pass
        cfg["record_enabled"] = False  # require fresh operator opt-in every server launch
        self.cfg = cfg
        self._init_db()

    @contextmanager
    def _db(self):
        """SQLite context that commits or rolls back and ALWAYS closes the handle."""
        conn = sqlite3.connect(str(self.db_path), timeout=12)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=10000")
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                start REAL NOT NULL,
                day TEXT NOT NULL,
                duration REAL NOT NULL DEFAULT 0,
                peak_score REAL NOT NULL DEFAULT 0,
                video_path TEXT NOT NULL,
                thumb_path TEXT NOT NULL,
                bytes INTEGER NOT NULL DEFAULT 0,
                protected INTEGER NOT NULL DEFAULT 0,
                done INTEGER NOT NULL DEFAULT 0
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_events_start ON events(start DESC)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_events_day ON events(day)")

    def settings(self):
        with self.lock:
            return dict(self.cfg)

    def update_settings(self, change: dict):
        allowed = {
            "record_enabled": lambda v: type(v) is bool,
            "sensitivity": lambda v: type(v) in (float, int) and .15 <= v <= 12,
            "min_motion_frames": lambda v: type(v) is int and 1 <= v <= 15,
            "post_roll_seconds": lambda v: type(v) in (float, int) and 1 <= v <= 15,
            "retention_days": lambda v: type(v) is int and 1 <= v <= 365,
            "max_storage_gb": lambda v: type(v) in (float, int) and 1 <= v <= 2000,
            "min_free_gb": lambda v: type(v) in (float, int) and .1 <= v <= 500,
            "show_boxes": lambda v: type(v) is bool,
        }
        for k, v in change.items():
            if k not in allowed or not allowed[k](v):
                raise ValueError(f"无效设置 {k}")
        with self.lock:
            self.cfg.update(change)
            # Persist preferences but do not restart recording without explicit click.
            saved = dict(self.cfg)
            saved["record_enabled"] = False
            tmp = self.file_cfg.with_suffix(".tmp")
            tmp.write_text(json.dumps(saved, ensure_ascii=False, indent=2), "utf-8")
            tmp.replace(self.file_cfg)
            return dict(self.cfg)

    def create_event(self, event_id, start, video_path, thumb_path):
        with self.lock, self._db() as db:
            db.execute("INSERT INTO events(id,start,day,video_path,thumb_path) VALUES (?,?,?,?,?)",
                       (event_id, start, datetime.fromtimestamp(start).strftime("%Y-%m-%d"), video_path, thumb_path))

    def progress_event(self, event_id, duration, score):
        with self.lock, self._db() as db:
            db.execute("UPDATE events SET duration=?, peak_score=? WHERE id=?", (duration, score, event_id))

    def finish_event(self, event_id, duration, score, count):
        with self.lock, self._db() as db:
            db.execute("UPDATE events SET duration=?, peak_score=?, bytes=?, done=1 WHERE id=?",
                       (duration, score, count, event_id))

    def remove_event(self, event_id):
        with self.lock, self._db() as db:
            row = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            if row:
                for p in (row["thumb_path"],):
                    self.safe_path(p).unlink(missing_ok=True)
                db.execute("DELETE FROM events WHERE id=?", (event_id,))

    def safe_path(self, relative):
        candidate = (self.path / relative).resolve()
        if not candidate.is_relative_to(self.path):
            raise ValueError("Invalid archive path")
        return candidate

    def get_event(self, event_id):
        with self.lock, self._db() as db:
            row = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            return dict(row) if row else None

    def events(self, day=None, limit=250):
        with self.lock, self._db() as db:
            if day:
                rows = db.execute("SELECT * FROM events WHERE day=? AND done=1 ORDER BY start DESC LIMIT ?", (day, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM events WHERE done=1 ORDER BY start DESC LIMIT ?", (limit,)).fetchall()
            return [dict(row) for row in rows]

    def days(self):
        with self.lock, self._db() as db:
            rows = db.execute("SELECT day, COUNT(*) AS count, SUM(bytes) AS bytes, SUM(duration) AS duration FROM events WHERE done=1 GROUP BY day ORDER BY day DESC").fetchall()
            return [dict(r) for r in rows]

    def toggle_protect(self, event_id, protect):
        with self.lock, self._db() as db:
            cur = db.execute("UPDATE events SET protected=? WHERE id=? AND done=1", (int(protect), event_id))
            return bool(cur.rowcount)

    def delete(self, event_id, *, allow_protected=False):
        with self.lock, self._db() as db:
            row = db.execute("SELECT * FROM events WHERE id=? AND done=1", (event_id,)).fetchone()
            if not row or (row["protected"] and not allow_protected):
                return False
            for field in ("video_path", "thumb_path"):
                self.safe_path(row[field]).unlink(missing_ok=True)
            db.execute("DELETE FROM events WHERE id=?", (event_id,))
            return True

    def bytes_used(self):
        with self.lock, self._db() as db:
            return int(db.execute("SELECT COALESCE(SUM(bytes),0) FROM events WHERE done=1").fetchone()[0])

    def storage(self):
        disk = shutil.disk_usage(self.path)
        return {"used": self.bytes_used(), "free": disk.free, "total": disk.total,
                "root": str(self.path), "max_bytes": int(self.settings()["max_storage_gb"] * (1024 ** 3))}

    def allow_new_recording(self):
        s = self.storage()
        cfg = self.settings()
        return s["free"] > cfg["min_free_gb"] * 1024 ** 3 and s["used"] < s["max_bytes"]

    def cleanup(self):
        with self.lock:
            cfg = self.settings()
            expired = time.time() - cfg["retention_days"] * 86400
            removed = 0
            # Old recordings first; protected clips are never auto-deleted.
            with self._db() as db:
                candidates = db.execute("SELECT id FROM events WHERE done=1 AND protected=0 AND start < ? ORDER BY start", (expired,)).fetchall()
            for r in candidates:
                removed += int(self.delete(r["id"]))
            # Enforce total quota by removing oldest unlocked clips.
            if self.bytes_used() > cfg["max_storage_gb"] * 1024 ** 3:
                with self._db() as db:
                    candidates = db.execute("SELECT id FROM events WHERE done=1 AND protected=0 ORDER BY start").fetchall()
                for r in candidates:
                    if self.bytes_used() <= cfg["max_storage_gb"] * 1024 ** 3:
                        break
                    removed += int(self.delete(r["id"]))
            return removed
