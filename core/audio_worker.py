#!/usr/bin/env python3
"""
音频采集 worker 子进程。

把 sounddevice/PortAudio 采集隔离进独立子进程，规避 Pa_AbortStream/Pa_StopStream
在 macOS 偶发 hang（PortAudio issue #367，官方未修）导致僵尸 AudioUnit 拖垮整个会话。

主进程（core/audio_source.py 的 IPC client）与本进程的通信协议：

  stdin  ← 主进程发命令：每行一个 JSON，{"cmd": "<name>", "id": <int>, ...}
  stdout → 本进程输出两种帧，统一格式 [1 字节 tag][4 字节小端 len][len 字节 payload]：
            tag=0  PCM 二进制帧（float32，每 block 一帧）
            tag=1  JSON 控制消息（ready / ack / devices / event，UTF-8）
  stderr → 日志（由主进程 Popen 重定向到 logs/whisper-audio-worker-<pid>.log）

自愈逻辑：本进程卡在 native Pa_* 调用时，命令 ack 会超时，主进程据此
SIGTERM→SIGKILL 本进程并 respawn——僵尸 AudioUnit 随本进程退出由 OS 释放。
本进程内部仍保留 AudioService 线程串行化 + open 超时 + abandon（防御纵深），
大部分慢调用在子进程内自行消化，只有真正的 native wedge 才触发主进程级重启。
"""

import json
import logging
import os
import struct
import sys
import threading
import time
from collections import deque  # noqa: F401  (保留与内核一致的 import 风格)
from typing import Any, Callable, List, Optional

import numpy as np
import sounddevice as sd

from .audio_source import AudioConfig

logger = logging.getLogger(__name__)

# ---- 协议常量 --------------------------------------------------------------
_TAG_PCM = 0      # 二进制 PCM 帧
_TAG_JSON = 1     # JSON 控制消息帧
_HEADER = struct.Struct("<BI")   # 1 字节 tag + 4 字节小端 unsigned int 长度

# open（Pa_OpenStream）等待超时：虚拟/远程设备或 CoreAudio 异常时 open 可能挂死。
_OPEN_TIMEOUT = 5.0

# 设备列表缓存 TTL：热插拔后最多延迟此秒数重新枚举
_DEVICE_CACHE_TTL_SECONDS = 60.0

# 虚拟/远控声卡名称特征：这类设备 CoreAudio 驱动常不稳，易引发 Pa_StopStream 卡死。
_VIRTUAL_DEVICE_MARKERS = (
    "virtual", "oray", "blackhole", "soundflower", "loopback",
    "audio hijack", "vb-cable", "aggregate",
)


def _is_virtual_device(name: str) -> bool:
    lowered = (name or "").lower()
    return any(marker in lowered for marker in _VIRTUAL_DEVICE_MARKERS)


# ---------------------------------------------------------------------------
# 采集内核：从 core/audio_source.py 搬运的 AudioService 线程 + 超时 + abandon。
# 与原 AudioSource 的差别：不存本地 buffer，回调把 chunk 经 on_chunk 推给主进程。
# ---------------------------------------------------------------------------
class _AudioCore:
    """子进程内音频采集内核：串行所有 Pa_* 调用，open 带超时，stop/abandon 带兜底。"""

    def __init__(self, config: AudioConfig):
        self.config = config
        self._stream: Optional[sd.InputStream] = None
        self._is_recording: bool = False
        self._recorded_samples: int = 0
        self._max_samples: int = 0
        self._overflow: bool = False
        self._rec_lock = threading.Lock()
        self._stream_lock = threading.Lock()
        self._device_name: Optional[str] = None
        self._cached_devices: Optional[List[dict]] = None
        self._devices_cached_at: Optional[float] = None
        self._fell_back_to_default: bool = False
        self._idle_release_timer: Optional[threading.Timer] = None
        self._idle_release_lock = threading.Lock()
        # 专用音频线程：FIFO 串行执行所有 Pa_* 操作（open/close/abort），绝不阻塞回调。
        self._audio_q: "queue.Queue" = __import__("queue").Queue()
        self._audio_thread = threading.Thread(
            target=self._audio_loop, name="AudioService", daemon=True
        )
        self._audio_thread.start()
        # 由 AudioWorker 注入：把 PCM chunk / 事件推给主进程
        self.on_chunk: Optional[Callable[[bytes], None]] = None
        self.on_event: Optional[Callable[[str], None]] = None

    # ---- 属性 ----
    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @property
    def overflow(self) -> bool:
        return self._overflow

    @property
    def fell_back_to_default(self) -> bool:
        return self._fell_back_to_default

    # ---- 设备枚举 ----
    @property
    def available_devices(self) -> List[dict]:
        if self._cached_devices is not None and self._devices_cache_valid():
            return list(self._cached_devices)
        devices: List[dict] = []
        try:
            for idx, dev in enumerate(sd.query_devices()):
                if dev.get("max_input_channels", 0) > 0:
                    devices.append({
                        "index": idx,
                        "name": dev["name"],
                        "channels": dev.get("max_input_channels", 1),
                        "sample_rate": dev.get("default_samplerate", 48000.0),
                        "is_virtual": _is_virtual_device(dev["name"]),
                    })
        except Exception as e:
            logger.warning("查询音频设备失败：%s", e)
        devices.sort(key=lambda d: d.get("is_virtual", False))  # 真实设备优先（虚拟/远控声卡降权）
        self._cached_devices = devices
        self._devices_cached_at = time.monotonic()
        return list(devices)

    def _devices_cache_valid(self) -> bool:
        return (
            self._devices_cached_at is not None
            and (time.monotonic() - self._devices_cached_at) < _DEVICE_CACHE_TTL_SECONDS
        )

    def invalidate_devices(self) -> None:
        self._cached_devices = None
        self._devices_cached_at = None

    def _resolve_device_index(self, device_name: Optional[str]) -> Optional[int]:
        name = device_name or self.config.device_name
        if name is None:
            return None
        for dev in self.available_devices:
            if dev["name"] == name:
                if dev.get("is_virtual"):
                    logger.warning(
                        "配置的设备疑似虚拟/远控声卡，CoreAudio 状态可能不稳：%s", name,
                    )
                return dev["index"]
        logger.warning("未找到设备：%s，使用系统默认", name)
        return None

    # ---- AudioService 线程：串行所有 Pa_* 调用 ----
    def _audio_loop(self) -> None:
        while True:
            func, done, result, exc = self._audio_q.get()
            if func is None:
                return
            try:
                value = func()
                if result is not None:
                    result[0] = value
            except Exception as e:
                if exc is not None:
                    exc[0] = e
                else:
                    logger.exception("AudioService 线程 op 异常")
            finally:
                if done is not None:
                    done.set()

    def _submit(self, func: Callable[[], Any], timeout: float) -> Any:
        done = threading.Event()
        result: List[Any] = [None]
        exc: List[Any] = [None]
        self._audio_q.put((func, done, result, exc))
        if not done.wait(timeout=timeout):
            logger.warning("AudioService op 排队超时（%.1fs），前序 op 可能仍在卡", timeout)
            return None
        if exc[0] is not None:
            raise exc[0]
        return result[0]

    def _submit_async(self, func: Callable[[], None]) -> None:
        self._audio_q.put((func, None, None, None))

    def _open_with_timeout(self, device_index: Optional[int], timeout: float) -> Optional[sd.InputStream]:
        box: dict = {"stream": None, "err": None}
        done = threading.Event()

        def runner():
            try:
                box["stream"] = self._build_stream(device_index)
            except Exception as e:
                box["err"] = e
            finally:
                done.set()

        worker = threading.Thread(target=runner, name="StreamOp-open", daemon=True)
        worker.start()
        if not done.wait(timeout=timeout):
            logger.warning("open 超时（%.1fs），放弃该流（Pa_OpenStream 可能仍在后台挂起）", timeout)
            return None
        if box["err"] is not None:
            logger.warning("open 操作失败：%s", box["err"])
            return None
        return box["stream"]

    def _build_stream(self, device_index: Optional[int]) -> sd.InputStream:
        label = device_index if device_index is not None else "default"
        logger.info("Pa_OpenStream 开始：device=%s", label)
        t0 = time.monotonic()
        try:
            stream = sd.InputStream(
                samplerate=self.config.sample_rate,
                channels=self.config.channels,
                device=device_index,
                dtype=self.config.dtype,
                blocksize=self.config.block_size,
                latency=self.config.latency,
                callback=self._on_callback,
            )
        except Exception:
            # 方向1：记录 open 耗时分布，供长期统计判断「采样率失配→reconfigure 放大死锁」假设
            logger.warning("Pa_OpenStream 失败：device=%s 耗时=%.1fms",
                           label, (time.monotonic() - t0) * 1000.0)
            raise
        logger.info("Pa_OpenStream 完成：device=%s 耗时=%.1fms",
                    label, (time.monotonic() - t0) * 1000.0)
        return stream

    def _create_stream(self, device_index: Optional[int]) -> Optional[sd.InputStream]:
        try:
            return self._submit(
                lambda: self._open_with_timeout(device_index, _OPEN_TIMEOUT),
                timeout=_OPEN_TIMEOUT + 1.0,
            )
        except Exception as e:
            logger.exception("创建音频流失败：%s", e)
            return None

    def start(self, device_name: Optional[str] = None) -> bool:
        """打开（准备）流，复用已有流。返回是否成功。"""
        self._cancel_idle_release()
        self._fell_back_to_default = False
        device_index = self._resolve_device_index(device_name)
        self._device_name = device_name or self.config.device_name

        with self._stream_lock:
            if self._stream is not None and not self._stream.closed:
                return True

        new_stream = self._create_stream(device_index)
        if new_stream is None and device_index is not None:
            self.invalidate_devices()
            logger.warning("指定设备创建音频流失败，回退到系统默认设备：device=%s", self._device_name)
            self._fell_back_to_default = True
            new_stream = self._create_stream(None)
        if new_stream is None:
            return False

        with self._stream_lock:
            if self._stream is not None and not self._stream.closed:
                try:
                    new_stream.close()
                except Exception:
                    pass
                return True
            self._stream = new_stream
            return True

    def start_recording(self) -> bool:
        """开始录音：清空采集计数 + 启动流 IO（Pa_StartStream）。"""
        self._cancel_idle_release()
        with self._rec_lock:
            self._recorded_samples = 0
            self._overflow = False
            self._max_samples = int(self.config.max_recording_seconds * self.config.sample_rate)
        self._is_recording = True
        with self._stream_lock:
            if self._stream is not None and not self._stream.closed and not self._stream.active:
                try:
                    self._stream.start()
                    logger.info("音频流已启动（worker）")
                    return True
                except Exception as e:
                    logger.exception("启动音频流失败：%s", e)
                    try:
                        self._stream.close()
                    except Exception:
                        pass
                    self._stream = None
                    self.invalidate_devices()
                    return False
            return self._stream is not None and self._stream.active

    def stop_recording(self) -> bool:
        """停止录音：abort 流 IO（Pa_AbortStream，带超时/abandon 兜底）。不返回数据。"""
        self._is_recording = False
        logger.info("停止录音收集，音频流将在 %.1fs 空闲后释放", self.config.idle_release_seconds)
        with self._stream_lock:
            if self._stream is not None and self._stream.active:
                stream = self._stream
                if self._run_stream_op_with_timeout("abort", lambda: stream.abort(), timeout=3.0):
                    logger.info("音频流已停止（worker）")
                else:
                    # abort 超时：放弃该流对象，后台尽力 abort+close 释放设备
                    self._stream = None
                    self._abandon_stream_async(stream, "录音停止超时")
                    logger.warning("音频流停止超时，放弃该流对象（后台清理），下次录音将重建")
        self._schedule_idle_release()
        return True

    def stop(self) -> None:
        self._cancel_idle_release()
        self._dispatch_close("停止音频流")

    def close(self) -> None:
        self._cancel_idle_release()
        self._dispatch_close("关闭音频流")

    def _dispatch_close(self, action_name: str) -> None:
        self._submit_async(lambda: self._close_stream(action_name))

    def _run_stream_op_with_timeout(self, op_name: str, op: Callable[[], Any], timeout: float = 3.0) -> bool:
        done = threading.Event()

        def runner():
            try:
                op()
            except Exception as e:
                logger.warning("%s 操作失败：%s", op_name, e)
            finally:
                done.set()

        worker = threading.Thread(target=runner, name=f"StreamOp-{op_name}", daemon=True)
        worker.start()
        if not done.wait(timeout=timeout):
            logger.warning("%s 超时（%.1fs），放弃该音频流对象", op_name, timeout)
            return False
        return True

    def _close_stream(self, action_name: str) -> None:
        with self._stream_lock:
            if self._stream is None:
                return
            stream = self._stream
            logger.info("%s：关闭 sounddevice 流", action_name)

            def _close():
                if not stream.closed:
                    if stream.active:
                        stream.abort()
                    stream.close()

            if not self._run_stream_op_with_timeout("close", _close, timeout=3.0):
                logger.warning("%s：关闭超时，放弃该流对象（后台清理）", action_name)
                self._abandon_stream_async(stream, f"{action_name}关闭超时")
            self._stream = None
            self._is_recording = False

    def _abandon_stream_async(self, stream: "sd.InputStream", reason: str) -> None:
        """后台尽力回收被放弃的流：abort+close 释放 CoreAudio 资源（子进程内兜底）。"""
        def _cleanup():
            self._run_stream_op_with_timeout("abandon-abort", lambda: stream.abort(), timeout=3.0)
            self._run_stream_op_with_timeout("abandon-close", lambda: stream.close(), timeout=3.0)
            logger.info("被放弃音频流的后台清理结束：reason=%s closed=%s", reason, stream.closed)

        self._submit_async(_cleanup)

    def _schedule_idle_release(self) -> None:
        self._cancel_idle_release()
        idle_seconds = self.config.idle_release_seconds
        if idle_seconds <= 0:
            self._dispatch_close("立即释放音频流")
            return
        timer = threading.Timer(idle_seconds, self._idle_release)
        timer.daemon = True
        timer.name = "AudioIdleRelease"
        with self._idle_release_lock:
            self._idle_release_timer = timer
        timer.start()
        logger.info("音频流空闲释放定时器已启动：%.1fs", idle_seconds)

    def _cancel_idle_release(self) -> None:
        with self._idle_release_lock:
            timer = self._idle_release_timer
            self._idle_release_timer = None
        if timer is not None:
            timer.cancel()

    def _idle_release(self) -> None:
        with self._idle_release_lock:
            self._idle_release_timer = None
        if self._is_recording:
            logger.info("空闲释放被跳过：当前正在录音")
            return
        logger.info("音频流空闲时间到，关闭流释放麦克风")
        self._dispatch_close("空闲释放音频流")

    # ---- PortAudio 回调（在 native 线程）：把 chunk 推给主进程 ----
    # 不变量（Chun-Min Chang《Deadlock when using AudioUnit》）：CoreAudio IO 线程执行回调期间
    # 一直持有框架内部隐藏的 Mutex-AU。回调内【绝不】调任何 Pa_/AudioUnit API；且 _rec_lock/
    # _stdout_lock 不得在任何线程持有它们期间调 Pa_/AudioUnit——否则持锁线程等 Mutex-AU、回调
    # 线程等应用锁，死锁。当前安全仅因：_rec_lock 仅在 start_recording 的 Pa_StartStream 之前
    # 短暂持有，_stdout_lock 仅在 _write_frame 内；无线程在持有它们时碰 Pa_。任何改动必须维持此分离。
    def _on_callback(self, indata, frames, time_info, status):
        if status:
            logger.debug("音频流状态：%s", status)
        if not self._is_recording:
            return
        chunk = indata.copy().flatten()
        with self._rec_lock:
            if self._max_samples > 0 and self._recorded_samples + len(chunk) > self._max_samples:
                if not self._overflow:
                    self._overflow = True
                    if self.on_event:
                        try:
                            self.on_event("overflow")
                        except Exception:
                            logger.exception("on_event(overflow) 失败")
                return
            self._recorded_samples += len(chunk)
        if self.on_chunk:
            try:
                self.on_chunk(chunk.tobytes())
            except Exception:
                # 推送失败（如主进程 pipe 关闭）不应影响 native 回调，吞掉并记录
                logger.debug("on_chunk 推送失败（pipe 可能已关闭）")


# ---------------------------------------------------------------------------
# IPC 包装：命令循环 + stdout 帧输出 + ready 握手
# ---------------------------------------------------------------------------
class AudioWorker:
    def __init__(self, config: Optional[AudioConfig] = None):
        self._core = _AudioCore(config or AudioConfig())
        self._core.on_chunk = self._push_chunk
        self._core.on_event = self._push_event
        self._stdout_fd = sys.stdout.fileno()
        self._stdout_lock = threading.Lock()
        self._cmd_thread = threading.Thread(target=self._cmd_loop, name="CmdLoop", daemon=True)
        self._stop = threading.Event()

    # ---- stdout 帧输出（线程安全：回调线程推 PCM，命令线程推 JSON）----
    def _write_frame(self, tag: int, payload: bytes) -> None:
        with self._stdout_lock:
            os.write(self._stdout_fd, _HEADER.pack(tag, len(payload)) + payload)

    def _send_json(self, obj: dict) -> None:
        self._write_frame(_TAG_JSON, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _push_chunk(self, pcm_bytes: bytes) -> None:
        self._write_frame(_TAG_PCM, pcm_bytes)

    def _push_event(self, name: str) -> None:
        self._send_json({"event": name})

    # ---- 命令循环 ----
    def _cmd_loop(self) -> None:
        stdin = sys.stdin
        while not self._stop.is_set():
            try:
                line = stdin.readline()
            except Exception:
                logger.exception("读 stdin 失败")
                break
            if not line:
                logger.info("stdin EOF，worker 退出")
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception as e:
                logger.warning("无法解析命令行：%r (%s)", line, e)
                continue
            self._dispatch(msg)

    def _ack(self, msg: dict, ok: bool, **extra) -> None:
        resp = {"ack": msg.get("cmd"), "id": msg.get("id"), "ok": ok}
        resp.update(extra)
        self._send_json(resp)

    def _dispatch(self, msg: dict) -> None:
        cmd = msg.get("cmd")
        try:
            if cmd == "start":
                ok = self._core.start(msg.get("device"))
                self._ack(msg, ok, fell_back_to_default=self._core.fell_back_to_default)
            elif cmd == "start_recording":
                ok = self._core.start_recording()
                self._ack(msg, ok)
            elif cmd == "stop_recording":
                ok = self._core.stop_recording()
                self._ack(msg, ok)
            elif cmd == "stop":
                self._core.stop()
                self._ack(msg, True)
            elif cmd == "close":
                self._core.close()
                self._ack(msg, True)
            elif cmd == "invalidate_devices":
                self._core.invalidate_devices()
                self._ack(msg, True)
            elif cmd == "query_devices":
                # devices 单独走一个 message（不是 ack），payload 较大
                self._send_json({"devices": self._core.available_devices, "id": msg.get("id")})
            elif cmd == "ping":
                self._ack(msg, True, recording=self._core.is_recording, overflow=self._core.overflow)
            elif cmd == "shutdown":
                logger.info("收到 shutdown，worker 退出")
                self._ack(msg, True)
                self._stop.set()
            else:
                self._ack(msg, False, err=f"unknown cmd: {cmd}")
        except Exception as e:
            logger.exception("命令处理异常：cmd=%s", cmd)
            self._ack(msg, False, err=str(e))

    # ---- 入口 ----
    def run(self) -> None:
        logger.info("audio worker 启动：pid=%s", os.getpid())
        # ready 握手：主进程等到此帧才认为 worker 就绪（照搬 whisper-server 健康轮询模式）
        self._send_json({"ready": True, "pid": os.getpid()})
        self._cmd_thread.start()
        # 主线程阻塞至 shutdown / stdin EOF
        self._cmd_thread.join()
        try:
            self._core.close()
        except Exception:
            logger.exception("worker 退出时关闭流失败")


def _load_config_from_argv() -> AudioConfig:
    """可选：从 argv[1] 读 JSON 覆盖 AudioConfig 字段。失败回退默认。"""
    if len(sys.argv) > 2 and sys.argv[1] == "--config":
        try:
            data = json.loads(sys.argv[2])
            fields = AudioConfig.__dataclass_fields__  # type: ignore[attr-defined]
            kwargs = {k: v for k, v in data.items() if k in fields}
            return AudioConfig(**kwargs)
        except Exception as e:
            logger.warning("解析 --config 失败，使用默认：%s", e)
    return AudioConfig()


def main() -> None:
    # worker 日志走 stderr，由主进程 Popen 重定向到 logs/whisper-audio-worker-<pid>.log
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    worker = AudioWorker(_load_config_from_argv())
    worker.run()


if __name__ == "__main__":
    main()
