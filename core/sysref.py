#!/usr/bin/env python3
"""系统音频参考采集：经 ScreenCaptureKit 抓系统输出 PCM。

只抓音频不抓画面。macOS 把系统内录放在 ScreenCaptureKit 里，
首次使用会弹屏幕录制权限，即使不录画面也一样。

架构与 core/audio_worker 一致：Swift 子进程做采集，主进程只收 PCM。
子进程源码见 core/sysref_capture.swift，构建见 core/SYSREF_BUILD.md，
产物 core/sysref_capture（arm64，不入库）。
协议与 audio_worker 相同：stdout 帧 [1字节tag][4字节小端len][payload]，
tag=0 是 float32 PCM，tag=1 是 UTF-8 JSON（ready/stopped/error）。

线程模型：start/stop 幂等，stop 经 stdin 发 stop 并等 stopped。
采集失败一律返回 None，调用方回退 raw 链路，不阻塞录音。
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


def _helper_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "sysref_capture")


def helper_available() -> bool:
    path = _helper_path()
    return os.path.isfile(path) and os.access(path, os.X_OK)


class SysRefCapture:
    """抓系统参考音频的短命采集器，随录音启停。"""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._chunks: list = []
        self._lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._stop_reader = threading.Event()
        self._failed = False

    @property
    def active(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> bool:
        """启动参考采集。成功返回 True，失败返回 False（调用方回退）。"""
        if self.active:
            return True
        if not helper_available():
            logger.info("sysref helper 不存在，跳过系统参考采集")
            return False
        self._chunks = []
        self._failed = False
        self._ready.clear()
        self._stopped.clear()
        self._stop_reader.clear()
        try:
            self._proc = subprocess.Popen(
                [_helper_path(), "--sysref"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
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
        ok = self._ready.wait(timeout=_READY_TIMEOUT)
        if not ok or self._failed:
            logger.warning("sysref ready 超时或子进程报错（可能被权限弹窗挡住或无系统音频）")
            self.stop()
            return False
        logger.info("sysref 参考采集已启动")
        return True

    def stop(self) -> np.ndarray | None:
        """停止采集并返回参考 PCM（16kHz float32），失败返回 None。"""
        proc, reader = self._proc, self._reader
        self._proc = None
        self._reader = None
        if proc is None:
            return None
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
        with self._lock:
            chunks = list(self._chunks)
        if not chunks:
            logger.info("sysref 无参考音频（系统静音或权限未给），回退原声")
            return None
        try:
            return np.concatenate(chunks, axis=0).astype(np.float32)
        except Exception as e:
            logger.warning("sysref 拼接失败：%s", e)
            return None

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

    def _handle_frame(self, tag: int, payload: bytes) -> None:
        if tag == _TAG_PCM:
            try:
                arr = np.frombuffer(payload, dtype=np.float32)
            except Exception:
                return
            if arr.size == 0:
                return
            with self._lock:
                self._chunks.append(arr.copy())
            return
        try:
            obj = json.loads(payload.decode("utf-8"))
        except Exception:
            return
        if "ready" in obj:
            self._ready.set()
        elif "stopped" in obj:
            self._stopped.set()
        elif "error" in obj:
            logger.warning("sysref 子进程错误：%s", obj.get("error"))
            self._failed = True
            self._ready.set()
