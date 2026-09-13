#!/usr/bin/env python3
"""系统音频参考采集：常驻 ScreenCaptureKit 子进程，录音时切一段 PCM。

只抓音频不抓画面。macOS 把系统内录放在 ScreenCaptureKit 里，
首次使用会弹屏幕录制权限，即使不录画面也一样。

helper 在开关打开时预热并一直跑，避免每次按键冷启动：冷启动会有
200ms+ 时延，而且第二次建流经常吐全零。录音只标记 begin/end 切段。

协议：stdout 帧 [1字节tag][4字节小端len][payload]，
tag=0 是 float32 PCM，tag=1 是 UTF-8 JSON（ready/stopped/error/audio）。
"""

from __future__ import annotations

import json
import logging
import os
import struct
import subprocess
import threading

import numpy as np

logger = logging.getLogger(__name__)

_HEADER = struct.Struct("<BI")
_TAG_PCM = 0
_TAG_JSON = 1
_READY_TIMEOUT = 8.0
_STOP_TIMEOUT = 5.0
_PREROLL_SAMPLES = 4_000  # 250ms @ 16kHz
_RING_SAMPLES = 32_000    # 2s


_HEADPHONE_MARKERS = (
    "airpods", "headphone", "headphones", "headset", "earphone",
    "earbuds", "buds", "airpods", "耳机", "蓝牙", "bluetooth",
)

_BUILTIN_SPEAKER_MARKERS = ("扬声器", "speaker", "built-in", "macbook")


def is_headphone_output(name) -> bool:
    """默认输出是耳机时返回 True。耳机不串音，跳过 sysref 采集。

    判定只看设备名特征，未知名返回 False（宁可多采一次，不漏消除）。
    """
    lowered = (name or "").lower()
    if not lowered:
        return False
    if any(m in lowered for m in _BUILTIN_SPEAKER_MARKERS):
        return False
    return any(m in lowered for m in _HEADPHONE_MARKERS)


def default_output_device_name() -> str:
    """返回当前默认输出设备名；查询失败返回空串。"""
    try:
        import sounddevice as sd
        idx = sd.default.device[1]
        if idx is None:
            return ""
        return str(sd.query_devices(idx).get("name", "") or "")
    except Exception as e:
        logger.debug("查询默认输出设备失败：%s", e)
        return ""


def _helper_candidates() -> list:
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [os.path.join(here, "sysref_capture")]
    resource = os.environ.get("RESOURCEPATH", "")
    if resource:
        candidates.append(os.path.join(resource, "sysref_capture"))
        candidates.append(os.path.join(resource, "core", "sysref_capture"))
    return candidates


def _helper_path() -> str:
    for path in _helper_candidates():
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return _helper_candidates()[0]


def helper_available() -> bool:
    path = _helper_path()
    return os.path.isfile(path) and os.access(path, os.X_OK)


class SysRefCapture:
    """常驻系统参考采集。start 一次，录音用 begin_segment/end_segment 切段。"""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._err_reader: threading.Thread | None = None
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._stop_reader = threading.Event()
        self._failed = False
        self._ring: list = []
        self._ring_n = 0
        self._seg: list = []
        self._recording = False

    @property
    def active(self) -> bool:
        return self._proc is not None and self._proc.poll() is None and not self._failed

    def start(self) -> bool:
        """启动常驻采集。已在跑则 True。失败 False。"""
        if self.active:
            return True
        if not helper_available():
            logger.info("sysref helper 不存在，跳过系统参考采集")
            return False
        self._failed = False
        self._ready.clear()
        self._stopped.clear()
        self._stop_reader.clear()
        with self._lock:
            self._ring = []
            self._ring_n = 0
            self._seg = []
            self._recording = False
        try:
            self._proc = subprocess.Popen(
                [_helper_path(), "--sysref"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except Exception as e:
            logger.warning("sysref 启动失败：%s", e)
            self._proc = None
            return False
        self._reader = threading.Thread(
            target=self._reader_loop, name="SysRefReader", daemon=True
        )
        self._reader.start()
        self._err_reader = threading.Thread(
            target=self._stderr_loop, name="SysRefStderr", daemon=True
        )
        self._err_reader.start()
        ok = self._ready.wait(timeout=_READY_TIMEOUT)
        if not ok or self._failed or not self.active:
            logger.warning("sysref ready 超时或子进程报错（可能被权限弹窗挡住或无系统音频）")
            self.shutdown()
            return False
        logger.info("sysref 常驻参考采集已启动")
        return True

    def begin_segment(self) -> None:
        """录音开始：带 250ms 预滚，后续 PCM 记入本段。"""
        with self._lock:
            preroll = self._ring_concat_locked()
            if preroll.size > _PREROLL_SAMPLES:
                preroll = preroll[-_PREROLL_SAMPLES:]
            self._seg = [preroll] if preroll.size else []
            self._recording = True

    def end_segment(self) -> np.ndarray | None:
        """录音结束：返回本段 PCM，采集进程继续跑。"""
        with self._lock:
            self._recording = False
            chunks = list(self._seg)
            self._seg = []
        if not chunks:
            logger.info("sysref 本段无参考音频，回退原声")
            return None
        try:
            out = np.concatenate(chunks, axis=0).astype(np.float32)
        except Exception as e:
            logger.warning("sysref 拼接失败：%s", e)
            return None
        rms = float(np.sqrt(np.mean(out.astype(np.float64) ** 2))) if out.size else 0.0
        logger.info("sysref 本段就绪：samples=%s rms=%.5f", int(out.size), rms)
        if out.size == 0:
            return None
        return out

    def shutdown(self) -> None:
        """结束 helper。应用退出或关闭开关时调用。"""
        proc, reader, err_reader = self._proc, self._reader, self._err_reader
        self._proc = None
        self._reader = None
        self._err_reader = None
        with self._lock:
            self._recording = False
            self._seg = []
        if proc is None:
            self._stop_reader.set()
            return
        try:
            if proc.poll() is None and proc.stdin is not None:
                proc.stdin.write(b"stop\n")
                proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=_STOP_TIMEOUT)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        self._stop_reader.set()
        if reader is not None:
            reader.join(timeout=2.0)
        if err_reader is not None:
            err_reader.join(timeout=1.0)
        logger.info("sysref 常驻采集已停止")

    def _ring_concat_locked(self) -> np.ndarray:
        if not self._ring:
            return np.zeros(0, dtype=np.float32)
        try:
            return np.concatenate(self._ring, axis=0).astype(np.float32)
        except Exception:
            return np.zeros(0, dtype=np.float32)

    def _push_pcm(self, arr: np.ndarray) -> None:
        with self._lock:
            self._ring.append(arr)
            self._ring_n += int(arr.size)
            while self._ring_n > _RING_SAMPLES and len(self._ring) > 1:
                old = self._ring.pop(0)
                self._ring_n -= int(old.size)
            if self._recording:
                self._seg.append(arr)

    def _reader_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            while not self._stop_reader.is_set():
                raw = self._readn(proc, _HEADER.size)
                if raw is None:
                    break
                tag, length = _HEADER.unpack(raw)
                payload = self._readn(proc, length)
                if payload is None:
                    break
                self._handle_frame(tag, payload)
        except Exception:
            logger.debug("sysref reader 退出", exc_info=True)

    def _readn(self, proc: subprocess.Popen, n: int) -> bytes | None:
        buf = bytearray()
        stdout = proc.stdout
        if stdout is None:
            return None
        while len(buf) < n:
            if self._stop_reader.is_set():
                return None
            try:
                chunk = os.read(stdout.fileno(), n - len(buf))
            except OSError:
                return None
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def _stderr_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            while not self._stop_reader.is_set():
                line = proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    logger.info("sysref helper：%s", text)
        except Exception:
            logger.debug("sysref stderr 退出", exc_info=True)

    def _handle_frame(self, tag: int, payload: bytes) -> None:
        if tag == _TAG_PCM:
            try:
                arr = np.frombuffer(payload, dtype=np.float32)
            except Exception:
                return
            if arr.size == 0:
                return
            self._push_pcm(arr.copy())
            return
        try:
            obj = json.loads(payload.decode("utf-8"))
        except Exception:
            return
        if "ready" in obj:
            self._ready.set()
        elif "stopped" in obj:
            self._stopped.set()
        elif "audio" in obj:
            logger.info("sysref 首帧：%s", obj.get("audio"))
        elif "error" in obj:
            logger.warning("sysref 子进程错误：%s", obj.get("error"))
            self._failed = True
            self._ready.set()
