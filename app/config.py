"""Portable validated runtime settings."""
import os
from dataclasses import dataclass
from pathlib import Path


def integer(name, default, minimum, maximum):
    raw = os.getenv(name, str(default))
    try:
        val = int(raw)
    except ValueError as e:
        raise ValueError(f"{name} must be numeric") from e
    if not minimum <= val <= maximum:
        raise ValueError(f"{name} must be {minimum}..{maximum}")
    return val


@dataclass(frozen=True)
class NVRConfig:
    data_dir: Path
    camera: str
    demo: bool
    fps: int
    width: int
    height: int
    require_mount: Path | None


def config():
    data = Path(os.path.expandvars(os.getenv("NVR_DATA_DIR", "~/.local/share/vision-nvr"))).expanduser().resolve()
    raw_mount = os.getenv("NVR_REQUIRE_MOUNT", "").strip()
    mount = Path(os.path.expandvars(raw_mount)).expanduser().resolve() if raw_mount else None
    if mount:
        if not (data == mount or data.is_relative_to(mount)):
            raise RuntimeError(f"录像目录 {data} 不在指定挂载点 {mount} 之下")
        if not os.path.ismount(mount):
            raise RuntimeError(f"目标盘没有挂载：{mount}，拒绝录像以防写到系统盘")
    width = integer("NVR_WIDTH", 640, 160, 1920)
    height = integer("NVR_HEIGHT", 360, 120, 1080)
    if width % 2 or height % 2:
        raise ValueError("H.264 yuv420p requires even frame dimensions")
    return NVRConfig(data, os.getenv("NVR_CAMERA", "/dev/video0"),
                     os.getenv("NVR_DEMO", "0") == "1", integer("NVR_FPS", 8, 1, 30),
                     width, height, mount)
