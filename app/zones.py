"""Normalized polygon zones shared by the API, detector, and privacy redaction.

Coordinates are fractions of the rendered frame (0..1). Privacy redaction is
permanent in *new* encoded footage, not merely a browser overlay.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

ZONE_TYPES = ("include", "exclude", "privacy")
MAX_POLYGONS = 8
MAX_VERTICES = 32


def validate_zones(value: object) -> dict:
    """Return a canonical defensive copy; reject extra keys and large inputs."""
    if type(value) is not dict or set(value) != set(ZONE_TYPES):
        raise ValueError("zones 必须包含 include、exclude、privacy 三个数组")
    output = {}
    for kind in ZONE_TYPES:
        polygons = value[kind]
        if type(polygons) is not list or len(polygons) > MAX_POLYGONS:
            raise ValueError(f"zones.{kind} 最多 {MAX_POLYGONS} 个多边形")
        clean = []
        for polygon in polygons:
            if type(polygon) is not list or not (3 <= len(polygon) <= MAX_VERTICES):
                raise ValueError("每个多边形需要 3–32 个点")
            points = []
            for point in polygon:
                if type(point) is not list or len(point) != 2:
                    raise ValueError("点必须是 [x,y]")
                if any(type(p) not in (int, float) or not math.isfinite(p)
                       or not 0 <= p <= 1 for p in point):
                    raise ValueError("坐标必须是 0..1 之间的有限数字")
                points.append([float(point[0]), float(point[1])])
            area = abs(sum(points[i][0] * points[(i+1) % len(points)][1]
                           - points[(i+1) % len(points)][0] * points[i][1]
                           for i in range(len(points))) / 2)
            if area < 0.00005:
                raise ValueError("多边形面积过小")
            clean.append(points)
        output[kind] = clean
    return output


def _mask(polygons: list, width: int, height: int) -> np.ndarray:
    mask = np.zeros((height, width), dtype=np.uint8)
    for polygon in polygons:
        points = np.array(
            [[round(x * (width - 1)), round(y * (height - 1))]
             for x, y in polygon], dtype=np.int32)
        cv2.fillPoly(mask, [points], 255)
    return mask


def privacy_redact(frame: np.ndarray, zones: dict) -> np.ndarray:
    polygons = zones.get("privacy", [])
    if not polygons:
        return frame
    h, w = frame.shape[:2]
    mask = _mask(polygons, w, h)
    result = frame.copy()
    result[mask > 0] = (0, 0, 0)
    return result


def detection_mask(width: int, height: int, zones: dict) -> np.ndarray | None:
    """Mask out ignored AND private pixels, optionally restrict to inclusions."""
    if not any(zones.get(k) for k in ZONE_TYPES):
        return None
    included = zones.get("include", [])
    target = _mask(included, width, height) if included else np.full(
        (height, width), 255, dtype=np.uint8)
    for kind in ("exclude", "privacy"):
        if zones.get(kind):
            target[_mask(zones[kind], width, height) > 0] = 0
    return target
