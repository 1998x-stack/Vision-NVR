"""Low-level FFmpeg H.264 encoder. Used only by a background writer in v3."""
from __future__ import annotations
import os
import select
import time
from pathlib import Path
import shutil
import subprocess
import tempfile
import numpy as np

class VideoEncoder:
    def __init__(self, path: Path, width: int, height: int, fps: int):
        binary = shutil.which("ffmpeg")
        if not binary:
            raise RuntimeError("找不到 FFmpeg。请先激活含 ffmpeg 的 Conda 环境。")
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._stderr_log = tempfile.TemporaryFile(mode="w+b")
        self.proc = subprocess.Popen([
            binary, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pixel_format", "bgr24", "-video_size", f"{width}x{height}",
            "-framerate", str(fps), "-i", "pipe:0", "-an", "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "29", "-pix_fmt", "yuv420p",
            "-threads", "2", "-g", str(fps * 2),
            "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
            "-f", "mp4", str(path)
        ], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self._stderr_log)
        self.frame_count = 0
        self.fps = fps
        # Direct unbuffered writes avoid indefinitely blocking BufferedWriter.write.
        os.set_blocking(self.proc.stdin.fileno(), False)
        self.write_timeout_seconds = 5.0

    def append(self, frame: np.ndarray):
        if self.proc.poll() is not None:
            msg = self._error_tail()
            raise RuntimeError(f"FFmpeg 录制中断：{msg}")
        data = memoryview(np.ascontiguousarray(frame)).cast("B")
        offset = 0
        fd = self.proc.stdin.fileno()
        deadline = time.monotonic() + self.write_timeout_seconds
        try:
            while offset < len(data):
                if self.proc.poll() is not None:
                    raise BrokenPipeError("FFmpeg process exited")
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([], [fd], [], remaining)[1]:
                    raise TimeoutError("FFmpeg stdin write exceeded deadline")
                try:
                    count = os.write(fd, data[offset:])
                except BlockingIOError:
                    continue
                if count <= 0:
                    raise BrokenPipeError("FFmpeg pipe closed")
                offset += count
            self.frame_count += 1
        except (OSError, TimeoutError) as exc:
            raise RuntimeError("FFmpeg 写入失败或超时；检查 CPU 和磁盘空间") from exc

    def _error_tail(self):
        self._stderr_log.seek(0, os.SEEK_END)
        end = self._stderr_log.tell()
        self._stderr_log.seek(max(0, end - 4096))
        return self._stderr_log.read().decode("utf-8", errors="replace")[-700:]

    def close(self):
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                try:
                    self.proc.stdin.close()
                except BrokenPipeError:
                    pass
            try:
                rc = self.proc.wait(timeout=12)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("FFmpeg 结束录制超时") from exc
            message = self._error_tail()
            if rc != 0:
                raise RuntimeError(f"FFmpeg 编码错误（exit {rc}）：{message}")
            return self.frame_count / self.fps
        finally:
            if self.proc.poll() is None:
                self.proc.kill()
                self.proc.wait()
            self._stderr_log.close()


