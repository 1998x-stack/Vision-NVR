"""Private SQLite event index with bounded, nonblocking retention and crash recovery."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import threading
import time

log = logging.getLogger(__name__)
DEFAULTS = {
    "record_enabled": False,
    "sensitivity": 0.7,
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
        # Applies to every launch path, not only scripts/run.sh. Never relax it.
        os.umask(0o077)
        self.path = root.expanduser().resolve()
        self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.chmod(0o700)
        self.file_cfg = self.path / "settings.json"
        self.db_path = self.path / "nvr.sqlite3"
        self.lock = threading.RLock()
        cfg = dict(DEFAULTS)
        if self.file_cfg.is_file():
            try:
                saved = json.loads(self.file_cfg.read_text("utf-8"))
                cfg.update({k: v for k, v in saved.items() if k in cfg})
            except (ValueError, OSError, TypeError):
                log.warning("Could not read persisted settings; using defaults")
            self.file_cfg.chmod(0o600)
        cfg["record_enabled"] = False  # never persist operator consent across launches
        self.cfg = cfg
        self._init_db()
        self.db_path.chmod(0o600)
        for suffix in ("-wal", "-shm"):
            file = Path(str(self.db_path) + suffix)
            if file.exists():
                file.chmod(0o600)

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(str(self.db_path), timeout=12)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("PRAGMA synchronous=NORMAL")
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
            db.execute("CREATE INDEX IF NOT EXISTS idx_events_retention ON events(done, protected, start)")

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
        for key, value in change.items():
            if key not in allowed or not allowed[key](value):
                raise ValueError(f"无效设置 {key}")
        with self.lock:
            updated = dict(self.cfg)
            updated.update(change)
            saved = dict(updated)
            saved["record_enabled"] = False
            tmp = self.file_cfg.with_suffix(".tmp")
            tmp.write_text(json.dumps(saved, ensure_ascii=False, indent=2), "utf-8")
            tmp.chmod(0o600)
            tmp.replace(self.file_cfg)
            self.cfg = updated
            return dict(self.cfg)

    def create_event(self, event_id, start, video_path, thumb_path):
        with self.lock, self._db() as db:
            db.execute("INSERT INTO events(id,start,day,video_path,thumb_path) VALUES (?,?,?,?,?)",
                       (event_id, start, datetime.fromtimestamp(start).strftime("%Y-%m-%d"),
                        video_path, thumb_path))

    def progress_event(self, event_id, duration, score):
        with self.lock, self._db() as db:
            db.execute("UPDATE events SET duration=?, peak_score=? WHERE id=?", (duration, score, event_id))

    def finish_event(self, event_id, duration, score, count):
        with self.lock, self._db() as db:
            db.execute("UPDATE events SET duration=?, peak_score=?, bytes=?, done=1 WHERE id=?",
                       (duration, score, count, event_id))

    def safe_path(self, relative):
        candidate = (self.path / relative).resolve()
        if not candidate.is_relative_to(self.path):
            raise ValueError("Invalid archive path")
        return candidate

    def _remove_files(self, rows):
        """File IO deliberately outside the DB/global settings lock."""
        for row in rows:
            for field in ("video_path", "thumb_path"):
                try:
                    path = self.safe_path(row[field])
                    path.unlink(missing_ok=True)
                    parent = path.parent
                    if parent != self.path and parent.parent != self.path:
                        try:
                            parent.rmdir()  # only if empty
                        except OSError:
                            pass
                except (ValueError, OSError) as exc:
                    log.warning("Cannot remove archive file for %s: %s", row["id"], exc)

    def remove_event(self, event_id):
        with self.lock, self._db() as db:
            row = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            if row:
                db.execute("DELETE FROM events WHERE id=?", (event_id,))
        if row:
            self._remove_files([dict(row)])

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

    def count_events(self):
        with self.lock, self._db() as db:
            return int(db.execute("SELECT COUNT(*) FROM events WHERE done=1").fetchone()[0])

    def toggle_protect(self, event_id, protect):
        with self.lock, self._db() as db:
            cur = db.execute("UPDATE events SET protected=? WHERE id=? AND done=1",
                             (int(protect), event_id))
            return bool(cur.rowcount)

    def delete(self, event_id, *, allow_protected=False):
        with self.lock, self._db() as db:
            row = db.execute("SELECT * FROM events WHERE id=? AND done=1", (event_id,)).fetchone()
            if not row or (row["protected"] and not allow_protected):
                return False
            db.execute("DELETE FROM events WHERE id=?", (event_id,))
        self._remove_files([dict(row)])
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
        """One DB pass and transaction, then filesystem deletes without the lock.

        A crash after deleting DB rows can leave unindexed files; reconciliation
        reports these for manual review rather than silently deleting footage.
        """
        with self.lock, self._db() as db:
            cfg = dict(self.cfg)
            expired = time.time() - cfg["retention_days"] * 86400
            quota = int(cfg["max_storage_gb"] * 1024 ** 3)
            used = int(db.execute(
                "SELECT COALESCE(SUM(bytes),0) FROM events WHERE done=1").fetchone()[0])
            rows = [dict(r) for r in db.execute(
                "SELECT * FROM events WHERE done=1 AND protected=0 ORDER BY start").fetchall()]
            pending = []
            for row in rows:
                if row["start"] < expired or used > quota:
                    pending.append(row)
                    used -= row["bytes"]
            if pending:
                db.executemany("DELETE FROM events WHERE id=? AND done=1 AND protected=0",
                               [(r["id"],) for r in pending])
        self._remove_files(pending)
        return len(pending)

    def reconcile(self):
        """Inspect incomplete events at startup before the camera worker starts.

        Probe valid fragmented MP4s; unplayable partial files are removed.
        Unindexed videos are only counted and never discarded automatically.
        """
        with self.lock, self._db() as db:
            unfinished = [dict(r) for r in db.execute(
                "SELECT * FROM events WHERE done=0").fetchall()]
            indexed = {r[0] for r in db.execute("SELECT video_path FROM events").fetchall()}
        result = {"recovered": 0, "discarded": 0, "unindexed": 0}
        probe = shutil.which("ffprobe")
        for row in unfinished:
            try:
                path = self.safe_path(row["video_path"])
                size = path.stat().st_size
            except (ValueError, OSError):
                size = 0
            duration = 0.0
            if size > 1000 and probe:
                try:
                    completed = subprocess.run(
                        [probe, "-v", "error", "-show_entries", "format=duration",
                         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                        capture_output=True, text=True, timeout=8, check=False)
                    if completed.returncode == 0:
                        duration = float(completed.stdout.strip())
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    pass
            if .25 <= duration < 86400:
                self.finish_event(row["id"], duration, row["peak_score"], size)
                result["recovered"] += 1
            elif not probe and size > 1000:
                # Do not erase footage just because ffprobe isn't installed.
                log.warning("ffprobe unavailable; preserving incomplete clip %s", row["id"])
            else:
                self.remove_event(row["id"])
                result["discarded"] += 1
        folder = self.path / "recordings"
        if folder.is_dir():
            for file in folder.glob("*/*.mp4"):
                if not re.fullmatch(r"\d{2}-\d{2}-\d{2}_[0-9a-f]{8}\.mp4", file.name):
                    continue
                if file.relative_to(self.path).as_posix() not in indexed:
                    # Log, don't destroy footage with unknown provenance.
                    result["unindexed"] += 1
        return result
