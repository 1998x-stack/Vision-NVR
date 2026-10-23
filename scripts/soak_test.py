#!/usr/bin/env python3
"""Collect real-device NVR health metrics as CSV; NEVER enables recording.

Examples:
  python scripts/soak_test.py --hours 24 --interval 10 --out nvr-24h.csv
  python scripts/soak_test.py --hours .01 --interval 1 --out quick.csv
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlopen


FIELDS = (
    "sample_time", "service_online", "camera_online", "state", "error",
    "capture_fps", "actual_analysis_fps", "processing_p95_ms",
    "frames_captured", "frames_overwritten", "frames_skipped",
    "encoder_queue_depth", "encoder_frames_dropped", "encoder_frames_duplicated",
    "encoder_failures", "encoder_pending_clips", "loop_restarts",
    "storage_online", "storage_guard_stops", "storage_used_bytes",
    "storage_free_bytes", "event_count", "http_error",
)


def check_url(url):
    parsed = urlparse(url)
    if (parsed.scheme != "http" or parsed.hostname not in
            ("localhost", "127.0.0.1", "::1") or
            parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Only loopback HTTP endpoints are permitted")
    return url.rstrip("/")


def sample(base, timeout=4.0):
    entry = dict.fromkeys(FIELDS, "")
    entry["sample_time"] = datetime.now(timezone.utc).isoformat()
    try:
        with urlopen(base + "/api/status", timeout=timeout) as response:
            data = json.load(response)
        cam = data.get("camera") or {}
        storage = data.get("storage") or {}
        entry.update({
            "service_online": 1,
            "camera_online": int(bool(cam.get("online"))),
            "state": cam.get("state", ""),
            "error": cam.get("error", ""),
            "storage_used_bytes": storage.get("used", ""),
            "storage_free_bytes": storage.get("free", ""),
            "event_count": data.get("events", ""),
        })
        for field in FIELDS:
            if field in cam:
                entry[field] = cam[field]
    except (OSError, HTTPError, URLError, ValueError, json.JSONDecodeError) as exc:
        entry["service_online"] = 0
        entry["http_error"] = str(exc)[:200]
    return entry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:7866")
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--interval", type=float, default=10)
    parser.add_argument("--out", type=Path, default=Path("nvr-soak.csv"))
    args = parser.parse_args()
    try:
        base = check_url(args.url)
    except ValueError as exc:
        parser.error(str(exc))
    if not (0 < args.hours <= 168 and 1 <= args.interval <= 3600):
        parser.error("--hours must be 0..168; --interval must be 1..3600 seconds")
    deadline = time.monotonic() + args.hours * 3600
    args.out.parent.mkdir(parents=True, exist_ok=True)
    failures = 0
    total = 0
    with args.out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        while True:
            point = sample(base)
            writer.writerow(point)
            handle.flush()
            total += 1
            if not point["service_online"] or not point["camera_online"]:
                failures += 1
            if time.monotonic() >= deadline:
                break
            time.sleep(min(args.interval, deadline - time.monotonic()))
    print(f"samples={total}, offline_samples={failures}, csv={args.out}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
