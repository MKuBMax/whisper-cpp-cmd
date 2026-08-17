#!/usr/bin/env python3
"""
音频源模块 - IPC client（实际采集在 core.audio_worker 子进程）。

为什么是子进程：PortAudio 的 Pa_AbortStream/Pa_StopStream 在 macOS 偶发 hang
（PortAudio issue #367，官方未修），卡死后兜底 abandon 也 hang，产生僵尸
AudioUnit（closed=False）拖垮同进程后续所有 Pa_OpenStream——整个会话废掉，
只能重启 App。把采集隔离进子进程后，hang 只卡子进程，本进程 kill + respawn，
僵尸随旧子进程退出由 OS 释放，主 App + GUI 无感。

自愈（Phase 3）：worker 命令 ack 超时 / stdout EOF / 录音中 PCM 流停滞 任一发生，
判定 worker 卡死，_try_respawn 杀旧起新（限流防风暴）。respawn 统一在调用线程
（_send_cmd）触发，reader 只标记 _worker_dead，避免 reader 线程调 respawn 的死锁。

对外接口与历史单进程版本完全一致（pipeline/controller/live_dictation/diagnostics
无感）。本类职责：spawn/管理 worker 子进程、收 PCM 入本地 buffer、发命令等 ack、
健康检测 + respawn。
"""

import json
import logging
import os
import random
import select
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

import numpy as np

from config.paths import (
    app_executable,
    ensure_runtime_dirs,
    is_standalone_bundle,
    logs_dir,
    runtime_root,
)

logger = logging.getLogger(__name__)

_PROJECT_DIR = runtime_root()
_LOG_DIR = logs_dir()
_AUDIO_WORKER_FLAG = "--whispercpp-audio-worker"

# 命令 ack 超时。worker 内部 open 5s / abort 3s，加上 abandon 兜底（最坏 6s）排在前序
# op 之后，start 可能要等到前序 abandon 跑完——故 open 类给足余量。真正卡死时触发 respawn。
_OPEN_CMD_TIMEOUT = 12.0    # start（Pa_OpenStream）
_IO_CMD_TIMEOUT = 8.0       # start_recording / stop_recording（Pa_StartStream / abort）
_SIMPLE_CMD_TIMEOUT = 5.0   # ping / invalidate_devices / close / query_devices
_READY_TIMEOUT = 15.0       # worker 启动 + import sounddevice 握手

# 健康检测
_PCM_STALL_TIMEOUT = 2.5    # 录音中 PCM 流停滞超过此秒，判定 worker 回调卡死
_RESPAWN_MAX = 3            # 时间窗内最多 respawn 次数，超过则放弃（进 degraded）
_RESPAWN_WINDOW = 60.0      # respawn 计数窗口（秒）
_RESPAWN_BACKOFF_BASE = 1.0  # respawn 退避基数（秒）；第 1 次不退避，第 2 次起指数增长
_RESPAWN_BACKOFF_CAP = 8.0   # respawn 退避上限（当前 _RESPAWN_MAX=3 下最大 ~2s，留未来余量）
_PING_INTERVAL = 8.0         # 空闲态周期 ping 间隔（秒）；补空闲态无声卡死盲区
_RESPAWN_RESET_AFTER = 30.0  # worker 稳定运行超过此秒后归还 respawn 配额（区分偶发 vs 持续故障）
_HALF_OPEN_INTERVAL = 60.0   # 进 degraded 后 HALF_OPEN 试探性恢复的间隔（秒）
_WARMUP_SECONDS = 5.0        # respawn 后新 worker warmup 窗口；期内 ping 跳过，给慢启动宽限

# 设备列表缓存 TTL（热插拔后最多延迟此秒数重新枚举）
_DEVICE_CACHE_TTL_SECONDS = 60.0

# 协议帧（与 core.audio_worker 一致）：[1 字节 tag][4 字节小端 len][len 字节 payload]
_TAG_PCM = 0
_TAG_JSON = 1
_HEADER = struct.Struct("<BI")

# 虚拟/远控声卡名称特征（保留供 tests/test_audio_virtual_devices 复用）
_VIRTUAL_DEVICE_MARKERS = (
    "virtual", "oray", "blackhole", "soundflower", "loopback",
    "audio hijack", "vb-cable", "aggregate",
)


def _is_virtual_device(name: str) -> bool:
    """判断设备名是否匹配已知虚拟/远控声卡特征。"""
    lowered = (name or "").lower()
    return any(marker in lowered for marker in _VIRTUAL_DEVICE_MARKERS)


@dataclass
class AudioConfig:
    """音频配置"""
    sample_rate: int = 16000
    channels: int = 1
    dtype: str = "float32"
    block_size: int = 256
    latency: str = "low"
    device_name: Optional[str] = None
    # 录音结束后音频流保持运行的空闲秒数，期间再次录音可复用，避免麦克风被一直占用
    idle_release_seconds: float = 30.0
    # 单次录音最大时长（秒），超过后停止追加缓冲防止内存无限增长；<=0 表示不限制
    max_recording_seconds: float = 300.0
    # 已废弃（保留字段仅为向后兼容）：音频流的 open/close/abort 现串行在 worker
    # 子进程内的 AudioService daemon 线程执行，不再依赖外部执行器。
    main_thread_executor: Optional[Callable[[Callable[[], Any]], Any]] = None


class AudioSource:
    """音频源 IPC client：采集在 worker 子进程，本类管 buffer + 命令 + 生命周期 + 自愈。"""

    def __init__(self, config: Optional[AudioConfig] = None):
        self.config = config or AudioConfig()
        # 本地 buffer（reader 填充）
        self._buffer: "deque[np.ndarray]" = deque()
        self._buffer_lock = threading.Lock()
        self._recorded_samples = 0
        # 本地状态
        self._is_recording = False
        self._overflow = False
        self._fell_back_to_default = False
        self._device_name: Optional[str] = None
        self._trace = None
        # 设备缓存
        self._cached_devices: Optional[List[dict]] = None
        self._devices_cached_at: Optional[float] = None
        # IPC
        self._proc: Optional[subprocess.Popen] = None
        self._worker_log_file = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stdin_lock = threading.Lock()  # 并发写 worker stdin 互斥（query_devices/DictationWorker/主线程/ping 多写者）
        self._ping_thread: Optional[threading.Thread] = None
        self._half_open_thread: Optional[threading.Thread] = None
        self._warmup_until = 0.0  # respawn 后 warmup 截止时刻；期内 ping 跳过，首帧到达提前结束
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._pending: dict = {}
        self._pending_lock = threading.Lock()
        self._next_id = 1
        self._worker_dead = False
        # 健康检测 / respawn
        self._last_pcm_time: Optional[float] = None
        self._generation = 0          # 每次 spawn 新 worker +1；reader 据此识别自己是否已过期
        self._respawn_count = 0
        self._respawn_window_start = time.monotonic()
        self._respawn_lock = threading.Lock()
        self._worker_healthy_since: Optional[float] = None  # 当前 worker 就绪时刻；稳定运行后归还配额
        # 启动 worker + 空闲态健康探测
        self._start_worker()
        self._ping_thread = threading.Thread(
            target=self._ping_loop, name="AudioWorkerPing", daemon=True
        )
        self._ping_thread.start()

    # ============================================================
    # worker 子进程生命周期
    # ============================================================
    def _worker_python(self) -> str:
        """worker 子进程用的 python 解释器。
        py2app alias 模式下主进程 sys.executable 可能是不含项目依赖（numpy/sounddevice）
        的系统 python（主进程靠 __boot__.py 注入 .venv site-packages 才能 import），但 worker
        是 spawn 出来的新进程、不继承该注入，会 import 失败。独立包则复用 App 自身
        的启动器，让 py2app 负责注入内置 Python 和依赖。"""
        if is_standalone_bundle():
            bundled_app = app_executable()
            if bundled_app:
                return bundled_app

        venv_python = os.path.join(_PROJECT_DIR, ".venv-arm64", "bin", "python")
        if os.path.exists(venv_python):
            return venv_python
        return sys.executable

    def _worker_argv(self) -> List[str]:
        if is_standalone_bundle():
            cmd = [self._worker_python(), _AUDIO_WORKER_FLAG]
        else:
            cmd = [self._worker_python(), "-m", "core.audio_worker"]
        cfg = {
            "sample_rate": self.config.sample_rate,
            "channels": self.config.channels,
            "dtype": self.config.dtype,
            "block_size": self.config.block_size,
            "latency": self.config.latency,
            "device_name": self.config.device_name,
            "idle_release_seconds": self.config.idle_release_seconds,
            "max_recording_seconds": self.config.max_recording_seconds,
        }
        cmd += ["--config", json.dumps(cfg)]
        return cmd

    def _start_worker(self) -> None:
        """spawn 一个新 worker + 起 reader + 等 ready。每次 respawn 都调它。"""
        ensure_runtime_dirs()
        os.makedirs(_LOG_DIR, exist_ok=True)
        log_path = os.path.join(_LOG_DIR, f"whisper-audio-worker-{int(time.time())}.log")
        # respawn 时关掉旧日志文件句柄
        if self._worker_log_file is not None:
            try:
                self._worker_log_file.close()
            except Exception:
                pass
        self._worker_log_file = open(log_path, "ab", buffering=0)
        self._generation += 1
        gen = self._generation
        logger.info("启动 audio worker（gen=%d）：log=%s", gen, log_path)
        try:
            self._proc = subprocess.Popen(
                self._worker_argv(),
                cwd=_PROJECT_DIR,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._worker_log_file,
                bufsize=0,
            )
        except Exception:
            try:
                self._worker_log_file.close()
            except Exception:
                pass
            raise
        self._ready.clear()
        self._worker_dead = False
        self._last_pcm_time = None
        self._reader_thread = threading.Thread(
            target=self._reader_loop, args=(gen,), name="AudioWorkerReader", daemon=True
        )
        self._reader_thread.start()
        if not self._ready.wait(timeout=_READY_TIMEOUT):
            raise RuntimeError("audio worker ready 握手超时")
        self._worker_healthy_since = time.monotonic()
        self._warmup_until = time.monotonic() + _WARMUP_SECONDS

    def _reader_loop(self, gen: int) -> None:
        """读 worker stdout 帧。gen 是本 reader 对应的 worker 代次，过期则静默退出。"""
        fd = self._proc.stdout.fileno()
        try:
            while not self._stop.is_set():
                r, _, _ = select.select([fd], [], [], 1.0)
                if not r:
                    # 1s 无数据：录音中 PCM 长期停滞 → 回调卡死，标记 worker dead
                    if (self._is_recording and self._last_pcm_time is not None
                            and time.monotonic() - self._last_pcm_time > _PCM_STALL_TIMEOUT):
                        stall = time.monotonic() - self._last_pcm_time
                        logger.warning("录音中 PCM 流停滞 %.1fs，判定 worker 回调卡死", stall)
                        self._mark_worker_dead("pcm stall")
                    continue
                header = self._readn(fd, _HEADER.size)
                if header is None:
                    break
                tag, length = _HEADER.unpack(header)
                payload = self._readn(fd, length)
                if payload is None:
                    break
                self._handle_frame(tag, payload)
        except Exception:
            logger.exception("audio worker reader 异常（gen=%d）", gen)
        # 退出处理：区分主动关闭 / 旧 reader 过期 / 真 EOF
        if self._stop.is_set():
            logger.info("audio worker reader 退出（主动关闭，gen=%d）", gen)
        elif gen != self._generation:
            logger.info("audio worker reader 退出（旧代次 gen=%d，已被 respawn 接管）", gen)
        else:
            # 可观测性：区分 worker 崩溃（异常退出码，如 CoreAudio EXC_BAD_ACCESS段错误）
            # vs 正常退出（未 shutdown）vs 卡死（进程仍活但 stdout 断），排查时对症
            rc = self._proc.poll() if self._proc is not None else None
            if rc is not None and rc != 0:
                logger.warning("audio worker 崩溃（exit=%s，gen=%d）——疑似 CoreAudio 段错误，"
                               "排查见 ~/Library/Logs/DiagnosticReports/", rc, gen)
                self._mark_worker_dead(f"worker crash (exit={rc})")
            elif rc == 0:
                logger.warning("audio worker 正常退出但未 shutdown（gen=%d）", gen)
                self._mark_worker_dead("worker exit without shutdown")
            else:
                logger.warning("audio worker stdout EOF 但进程仍活（gen=%d）——疑似 native 卡死", gen)
                self._mark_worker_dead("worker stdout closed (process alive)")

    def _ping_loop(self) -> None:
        """空闲态周期 ping worker：补 reader 的 PCM-stall 盲区（流常驻但不录音时无声卡死）。
        ping 线程绝不 respawn——只 mark_dead，respawn 仍由调用线程在 _send_cmd 触发（防
        reader/ping 重入死锁）。_stop.wait 既做间隔又做即时退出（shutdown 时立即返回 True）。"""
        while not self._stop.wait(timeout=_PING_INTERVAL):
            try:
                self._ping_once()
            except Exception:
                logger.exception("ping loop 异常")

    def _ping_once(self) -> None:
        if self._is_recording or self._worker_dead or self._stop.is_set():
            return
        if time.monotonic() < self._warmup_until:
            return  # warmup 期内跳过 ping，给新 worker 慢启动宽限，避免误判
        gen = self._generation  # 绑定发出代次：respawn 期间发出的 ping 超时不可信
        try:
            resp = self._send_cmd_once("ping", device=None, timeout=_SIMPLE_CMD_TIMEOUT)
        except Exception:
            logger.exception("ping 异常")
            return
        if resp is not None:
            return
        if self._worker_dead:
            return                      # 别处已发现死亡
        if gen != self._generation:
            return                      # ping 发给了上一代 worker（respawn 已发生），超时不可信
        logger.warning("idle ping 超时，判定 worker 空闲态卡死")
        self._mark_worker_dead("idle ping timeout")

    def _mark_worker_dead(self, reason: str) -> None:
        """标记 worker 已死：失败所有 pending 命令（调用方据此 respawn）。不在 reader 里 respawn。"""
        self._worker_dead = True
        self._fail_all_pending(reason)

    def _readn(self, fd: int, n: int) -> Optional[bytes]:
        buf = bytearray()
        while len(buf) < n:
            if self._stop.is_set():
                return None
            try:
                chunk = os.read(fd, n - len(buf))
            except OSError:
                return None
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def _handle_frame(self, tag: int, payload: bytes) -> None:
        if tag == _TAG_PCM:
            self._on_pcm(payload)
            return
        try:
            obj = json.loads(payload.decode("utf-8"))
        except Exception:
            logger.warning("无法解析 worker JSON 帧：%r", payload[:64])
            return
        if "ready" in obj:
            self._ready.set()
            logger.info("audio worker ready：pid=%s gen=%d", obj.get("pid"), self._generation)
        elif "event" in obj:
            self._on_event(obj["event"])
        else:
            # ack / devices，按 id 匹配 pending
            cid = obj.get("id")
            with self._pending_lock:
                entry = self._pending.pop(cid, None)
            if entry is not None:
                ev, box = entry
                box["resp"] = obj
                ev.set()

    def _on_pcm(self, payload: bytes) -> None:
        arr = np.frombuffer(payload, dtype=np.float32)
        self._last_pcm_time = time.monotonic()
        self._warmup_until = 0.0  # 首帧到达，提前结束 warmup
        with self._buffer_lock:
            self._buffer.append(arr)
            self._recorded_samples += len(arr)

    def _on_event(self, name: str) -> None:
        if name == "overflow":
            self._overflow = True
            logger.warning("audio worker 上报 overflow（达到录音时长上限）")

    # ============================================================
    # 命令通道（含 respawn 自愈）
    # ============================================================
    def _next_cid(self) -> int:
        cid = self._next_id
        self._next_id += 1
        return cid

    def _send_cmd(self, cmd: str, *, device: Optional[str] = None,
                  timeout: float = _SIMPLE_CMD_TIMEOUT) -> dict:
        """发命令并等 ack。worker dead 或 ack 超时则 respawn 一次并重试。"""
        for attempt in range(2):  # 原始 + respawn 后重试一次
            if self._worker_dead:
                if attempt == 0:
                    if not self._try_respawn(f"worker dead before {cmd}"):
                        raise RuntimeError(f"worker 已死且 respawn 失败（cmd={cmd}）")
                    # respawn 成功，继续本次 attempt 发命令
                else:
                    raise RuntimeError(f"worker respawn 后仍 dead（cmd={cmd}）")
            resp = self._send_cmd_once(cmd, device=device, timeout=timeout)
            if resp is not None:
                self._maybe_reset_respawn_count()
                return resp
            # ack 超时
            if attempt == 0:
                logger.warning("worker 命令 %s ack 超时，触发 respawn 后重试", cmd)
                if not self._try_respawn(f"{cmd} ack timeout"):
                    raise TimeoutError(f"worker 命令 {cmd} ack 超时且 respawn 失败（{timeout}s）")
            else:
                raise TimeoutError(f"worker 命令 {cmd} respawn 后仍 ack 超时（{timeout}s）")
        raise RuntimeError(f"unreachable: cmd={cmd}")

    def _send_cmd_once(self, cmd: str, *, device: Optional[str],
                       timeout: float) -> Optional[dict]:
        """发一次命令等 ack；成功返回 resp dict，ack 超时返回 None（由调用方 respawn）。"""
        if self._worker_dead:
            return None
        cid = self._next_cid()
        ev = threading.Event()
        box: dict = {}
        with self._pending_lock:
            self._pending[cid] = (ev, box)
        msg = {"cmd": cmd, "id": cid}
        if device is not None:
            msg["device"] = device
        try:
            data = (json.dumps(msg) + "\n").encode("utf-8")
            with self._stdin_lock:
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
        except Exception as e:
            with self._pending_lock:
                self._pending.pop(cid, None)
            self._mark_worker_dead(f"send {cmd} failed: {e}")
            return None
        if not ev.wait(timeout=timeout):
            with self._pending_lock:
                self._pending.pop(cid, None)
            return None  # 超时 → 调用方 respawn
        if self._worker_dead:
            return None  # 等待期间 worker 被 stall/EOF 标记死亡 → 调用方 respawn
        return box.get("resp")

    def _fail_all_pending(self, reason: str) -> None:
        with self._pending_lock:
            pending = self._pending
            self._pending = {}
        for ev, box in pending.values():
            box["resp"] = {"ok": False, "err": reason}
            ev.set()

    # ============================================================
    # respawn（kill 旧 worker + spawn 新）
    # ============================================================
    def _try_respawn(self, reason: str) -> bool:
        """限流后 kill 旧 worker、spawn 新。成功 True，放弃/失败 False。"""
        with self._respawn_lock:
            now = time.monotonic()
            if now - self._respawn_window_start > _RESPAWN_WINDOW:
                self._respawn_count = 0
                self._respawn_window_start = now
            self._respawn_count += 1
            count = self._respawn_count
        if count > _RESPAWN_MAX:
            logger.error("worker respawn 达上限（%d 次/%.0fs），放弃，进入 degraded（HALF_OPEN 探测中）。reason=%s",
                         count, _RESPAWN_WINDOW, reason)
            self._worker_dead = True
            self._start_half_open_probe()
            return False
        logger.warning("audio worker respawn（第 %d 次）：reason=%s", count, reason)
        self._fail_all_pending("respawn")  # 立即失败其他线程的 pending 命令，防并发 respawn 风暴
        try:
            self._kill_worker_process()
            if count > 1:  # 第 1 次不退避（偶发 transient 立即重试）；后续指数退避 + jitter
                backoff = min(_RESPAWN_BACKOFF_CAP, _RESPAWN_BACKOFF_BASE * (2 ** (count - 2)))
                time.sleep(backoff * random.uniform(0.3, 1.0))
            # 旧 reader 会在旧 stdout EOF 后自行退出（gen 过期 → 静默）
            self._start_worker()
            logger.info("audio worker respawn 成功（gen=%d）", self._generation)
            if self._has_virtual_devices():
                logger.warning(
                    "检测到虚拟/远控音频设备，卡死可能由此引起；若反复卡死，"
                    "建议退出向日葵等虚拟声卡应用，或执行 "
                    "`sudo launchctl kickstart -kp system/com.apple.audio.coreaudiod` 重启音频守护"
                )
            return True
        except Exception:
            logger.exception("audio worker respawn 失败：reason=%s", reason)
            self._worker_dead = True
            return False

    def _maybe_reset_respawn_count(self) -> None:
        """worker 稳定运行 _RESPAWN_RESET_AFTER 后归还 respawn 配额。
        区分偶发 transient（稳跑后清零）vs 真持续故障（频繁卡死耗尽配额进 degraded）。
        对齐 systemd ResetFailedState / Supervisor startsecs。"""
        with self._respawn_lock:
            if self._worker_healthy_since is None:
                return
            if (self._respawn_count > 0
                    and time.monotonic() - self._worker_healthy_since > _RESPAWN_RESET_AFTER):
                self._respawn_count = 0

    def _start_half_open_probe(self) -> None:
        """进入 degraded 后启动 HALF_OPEN 探测：长间隔试探 spawn 新 worker，成功则退出 degraded。
        解决「3 次 respawn 耗尽必须重启 App」。对齐 Resilience4j CircuitBreaker HALF_OPEN。
        探测线程是 daemon，shutdown 时 _stop.set() 让 _half_open_loop 退出。"""
        if self._half_open_thread is not None and self._half_open_thread.is_alive():
            return
        self._half_open_thread = threading.Thread(
            target=self._half_open_loop, name="AudioHalfOpen", daemon=True
        )
        self._half_open_thread.start()

    def _half_open_loop(self) -> None:
        while not self._stop.wait(timeout=_HALF_OPEN_INTERVAL):
            if self._half_open_once():
                return

    def _half_open_once(self) -> bool:
        """HALF_OPEN 单次探测：尝试 spawn 新 worker，成功则退出 degraded。True=已恢复/无需探测。"""
        if not self._worker_dead:
            return True
        logger.info("degraded 试探性恢复（HALF_OPEN）...")
        try:
            self._kill_worker_process()
            self._start_worker()  # 成功返回即 worker 已 ready；_start_worker 内部已置 _worker_dead=False
        except Exception:
            logger.warning("HALF_OPEN spawn 失败，保持 degraded，下个周期再试")
            self._worker_dead = True
            return False
        with self._respawn_lock:
            self._respawn_count = 0
        logger.info("HALF_OPEN 探测成功，退出 degraded，worker 恢复可用")
        return True

    def _has_virtual_devices(self) -> bool:
        """系统是否存在虚拟/远控音频设备（Pa_* 卡死的已知远因嫌疑）。"""
        return any(d.get("is_virtual") for d in (self._cached_devices or []))

    @property
    def virtual_device_suspect(self) -> bool:
        """最近 respawn 过且存在虚拟音频设备——hang 嫌疑，供 controller 给用户可见提示。"""
        return self._respawn_count > 0 and self._has_virtual_devices()

    def _kill_worker_process(self) -> None:
        """kill 当前 worker 进程（SIGTERM→SIGKILL）。不设 _stop（respawn 时用）。"""
        proc = self._proc
        if proc is None:
            return
        try:
            proc.stdin.close()
        except Exception:
            pass
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    pass

    # ============================================================
    # 对外接口（保持与单进程版本一致）
    # ============================================================
    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @property
    def overflow(self) -> bool:
        """录音是否达到最大时长上限被截断"""
        return self._overflow

    @property
    def fell_back_to_default(self) -> bool:
        """本次 start() 是否因指定设备失败而回退到系统默认设备"""
        return self._fell_back_to_default

    @property
    def recorded_seconds(self) -> float:
        """本次录音已采集的秒数"""
        if self.config.sample_rate:
            return self._recorded_samples / self.config.sample_rate
        return 0.0

    @property
    def trace(self):
        return self._trace

    @trace.setter
    def trace(self, value):
        self._trace = value

    def start(self, device_name: Optional[str] = None) -> bool:
        """启动音频流（准备监听麦克风）。复用已有流。"""
        self._device_name = device_name or self.config.device_name
        try:
            resp = self._send_cmd("start", device=self._device_name, timeout=_OPEN_CMD_TIMEOUT)
        except (TimeoutError, RuntimeError) as e:
            logger.error("start 失败：%s", e)
            return False
        ok = bool(resp.get("ok", False))
        self._fell_back_to_default = bool(resp.get("fell_back_to_default", False))
        return ok

    def start_recording(self) -> bool:
        """开始录音（清空缓冲）。"""
        with self._buffer_lock:
            self._buffer.clear()
            self._recorded_samples = 0
        self._overflow = False
        try:
            resp = self._send_cmd("start_recording", timeout=_IO_CMD_TIMEOUT)
        except (TimeoutError, RuntimeError) as e:
            logger.error("start_recording 失败：%s", e)
            self._is_recording = False
            return False
        ok = bool(resp.get("ok", False))
        self._is_recording = ok
        return ok

    def stop_recording(self) -> Optional[np.ndarray]:
        """停止录音并返回音频数据。worker 的 abort 即使卡死，本地 buffer 仍可返回。"""
        self._is_recording = False
        try:
            self._send_cmd("stop_recording", timeout=_IO_CMD_TIMEOUT)
        except (TimeoutError, RuntimeError) as e:
            logger.warning("stop_recording ack 异常（buffer 仍返回）：%s", e)
        # 给 reader 一点时间 drain worker 停 IO 前 in-flight 的最后几帧 PCM
        time.sleep(0.05)
        audio = None
        with self._buffer_lock:
            if self._buffer:
                audio = np.concatenate(list(self._buffer), axis=0)
                self._buffer.clear()
                self._recorded_samples = 0
        return audio

    def stop(self) -> None:
        """停止音频流（关闭麦克风）。"""
        try:
            self._send_cmd("stop", timeout=_SIMPLE_CMD_TIMEOUT)
        except (TimeoutError, RuntimeError) as e:
            logger.warning("stop 异常：%s", e)

    def close(self) -> None:
        """关闭音频流（应用退出或切换设备时调用）。worker 保持，靠 stdin EOF 自杀。"""
        try:
            self._send_cmd("close", timeout=_SIMPLE_CMD_TIMEOUT)
        except (TimeoutError, RuntimeError) as e:
            logger.warning("close 异常：%s", e)

    def invalidate_devices(self) -> None:
        """失效设备缓存，下次查询重新枚举。"""
        self._cached_devices = None
        self._devices_cached_at = None
        try:
            self._send_cmd("invalidate_devices", timeout=_SIMPLE_CMD_TIMEOUT)
        except (TimeoutError, RuntimeError) as e:
            logger.warning("invalidate_devices 异常：%s", e)

    @property
    def available_devices(self) -> List[dict]:
        if self._cached_devices is not None and self._devices_cache_valid():
            return list(self._cached_devices)
        try:
            resp = self._send_cmd("query_devices", timeout=_SIMPLE_CMD_TIMEOUT)
            devices = resp.get("devices", [])
        except (TimeoutError, RuntimeError) as e:
            logger.warning("query_devices 失败：%s", e)
            devices = self._cached_devices or []
        self._cached_devices = devices
        self._devices_cached_at = time.monotonic()
        return list(devices)

    def _devices_cache_valid(self) -> bool:
        return (
            self._devices_cached_at is not None
            and (time.monotonic() - self._devices_cached_at) < _DEVICE_CACHE_TTL_SECONDS
        )

    def get_buffer(self) -> Optional[np.ndarray]:
        """获取缓冲的音频数据"""
        with self._buffer_lock:
            if not self._buffer:
                return None
            return np.concatenate(list(self._buffer), axis=0)

    def get_recent_buffer(self, max_seconds: float) -> Optional[np.ndarray]:
        """获取最近一段缓冲音频"""
        with self._buffer_lock:
            if not self._buffer:
                return None
            audio = np.concatenate(list(self._buffer), axis=0)
        if max_seconds <= 0:
            return audio
        max_samples = int(max_seconds * self.config.sample_rate)
        if max_samples > 0 and len(audio) > max_samples:
            return audio[-max_samples:]
        return audio

    def get_recent_rms(self, seconds: float = 0.1) -> float:
        """返回最近 seconds 秒音频的 RMS（供录音浮窗显示电平）。线程安全。"""
        audio = self.get_recent_buffer(seconds)
        if audio is None or audio.size == 0:
            return 0.0
        window = audio.astype(np.float32)
        return float(np.sqrt(np.mean(window ** 2)))

    def clear_buffer(self) -> None:
        """清空缓冲区"""
        with self._buffer_lock:
            self._buffer.clear()
            self._recorded_samples = 0

    def _terminate_worker(self) -> None:
        self._stop.set()
        self._kill_worker_process()
        if self._worker_log_file is not None:
            try:
                self._worker_log_file.close()
            except Exception:
                pass

    def shutdown(self) -> None:
        """显式关闭 worker 子进程（应用退出时调用，比 close() 更彻底）。"""
        try:
            self._send_cmd_once("shutdown", device=None, timeout=2.0)
        except Exception:
            pass
        self._terminate_worker()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.shutdown()
