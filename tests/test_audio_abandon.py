"""P0：被放弃音频流的后台清理单测。

stop/close 超时丢弃流后，必须异步 abort+close 释放 CoreAudio 资源，
避免僵尸 AudioUnit 占用设备拖垮后续 Pa_OpenStream（卡死根因）。
通过 _FakeStream 模拟 sounddevice.InputStream，无需真实音频设备。

注：采集内核（含 _abandon_stream_async/_close_stream/stop_recording）已隔离进
core.audio_worker 子进程，本测试直接测其 _AudioCore 内核类。
"""

import logging
import threading
import time

import core.audio_worker as awmod
from core.audio_source import AudioConfig
from core.audio_worker import _AudioCore


class _FakeStream:
    """最小化伪音频流，记录 abort/stop/close 调用顺序。"""

    def __init__(self, active=False, closed=False):
        self.active = active
        self.closed = closed
        self.abort_called = False
        self.stop_called = False
        self.close_called = False
        self.call_order = []

    def abort(self):
        self.abort_called = True
        self.active = False
        self.call_order.append("abort")

    def stop(self):
        self.stop_called = True
        self.active = False
        self.call_order.append("stop")

    def close(self):
        self.close_called = True
        self.closed = True
        self.call_order.append("close")


def _wait_for(predicate, timeout=2.0, interval=0.01):
    """轮询断言：等 predicate 为真，超时返回其最终值。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# ---------------- _abandon_stream_async 直接验证 ----------------

def test_abandon_stream_async_calls_abort_then_close():
    core = _AudioCore(AudioConfig())
    stream = _FakeStream(active=True)

    core._abandon_stream_async(stream, "测试")

    assert _wait_for(lambda: stream.close_called)
    assert stream.abort_called
    assert stream.close_called
    # abort 必须先于 close：先打断卡死状态，再释放设备
    assert stream.call_order == ["abort", "close"]


def test_abandon_stream_async_does_not_block_caller():
    """清理在 daemon 线程异步执行，调用方必须立即返回，
    不得被卡住的流拖住——否则治本修复自己又卡死。"""
    core = _AudioCore(AudioConfig())
    blocker = threading.Event()

    class _BlockingStream(_FakeStream):
        def abort(self):
            blocker.wait(5.0)  # 模拟僵尸流 abort 卡死
            super().abort()

    stream = _BlockingStream(active=True)
    t0 = time.monotonic()
    core._abandon_stream_async(stream, "测试")
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5  # 立即返回
    blocker.set()  # 放行，避免 daemon 线程泄漏到后续测试
    assert _wait_for(lambda: stream.close_called)


# ---------------- stop_recording 超时路径 ----------------

def test_stop_recording_abandons_stream_on_stop_timeout(monkeypatch):
    core = _AudioCore(AudioConfig())
    stream = _FakeStream(active=True)
    core._stream = stream
    core._is_recording = True

    real = core._run_stream_op_with_timeout

    def fake_run_op(op_name, op, timeout=3.0):
        if op_name == "abort":
            return False  # P1：正常停止用 abort，模拟它卡死超时
        return real(op_name, op, timeout)  # abandon 的 abort/close 走真实路径

    monkeypatch.setattr(core, "_run_stream_op_with_timeout", fake_run_op)

    abandoned = []
    real_abandon = core._abandon_stream_async

    def spy_abandon(s, reason):
        abandoned.append(reason)
        return real_abandon(s, reason)

    monkeypatch.setattr(core, "_abandon_stream_async", spy_abandon)

    core.stop_recording()

    assert core._stream is None  # 死流已解除引用
    assert abandoned == ["录音停止超时"]
    assert _wait_for(lambda: stream.close_called)  # 后台清理确实执行


# ---------------- _close_stream 超时路径 ----------------

def test_close_stream_abandons_on_close_timeout(monkeypatch):
    core = _AudioCore(AudioConfig())
    stream = _FakeStream(active=True)
    core._stream = stream

    real = core._run_stream_op_with_timeout

    def fake_run_op(op_name, op, timeout=3.0):
        if op_name == "close":
            return False  # 模拟 close 卡死超时
        return real(op_name, op, timeout)

    monkeypatch.setattr(core, "_run_stream_op_with_timeout", fake_run_op)

    abandoned = []
    real_abandon = core._abandon_stream_async

    def spy_abandon(s, reason):
        abandoned.append(reason)
        return real_abandon(s, reason)

    monkeypatch.setattr(core, "_abandon_stream_async", spy_abandon)

    core._close_stream("关闭音频流")

    assert core._stream is None
    assert len(abandoned) == 1
    assert "关闭音频流" in abandoned[0]
    assert _wait_for(lambda: stream.close_called)


# ---------------- P1：正常停止用 abort 而非 stop ----------------

def test_stop_recording_uses_abort_not_stop():
    """正常停止录音调 abort（不等设备确认），避开 BlockWhileAudioUnitIsRunning 卡死。"""
    core = _AudioCore(AudioConfig())
    stream = _FakeStream(active=True)
    core._stream = stream
    core._is_recording = True

    core.stop_recording()

    assert stream.abort_called
    assert not stream.stop_called


def test_close_stream_uses_abort():
    """关闭流前的停止也用 abort，不残留对 stop 的依赖。"""
    core = _AudioCore(AudioConfig())
    stream = _FakeStream(active=True)
    core._stream = stream

    core._close_stream("关闭音频流")

    assert stream.abort_called
    assert not stream.stop_called
    assert stream.close_called
    assert core._stream is None


# ---------------- 方向1：open 耗时统计（验证采样率失配假设的数据采集）----------------

def test_build_stream_logs_open_elapsed(caplog, monkeypatch):
    """_build_stream 记录 Pa_OpenStream 耗时，供长期统计判断采样率失配假设。"""
    core = _AudioCore(AudioConfig())
    constructed = []

    class _FakeInputStream:
        def __init__(self, **kw):
            constructed.append(kw)

    monkeypatch.setattr(awmod.sd, "InputStream", _FakeInputStream)
    with caplog.at_level(logging.INFO, logger="core.audio_worker"):
        stream = core._build_stream(0)

    assert isinstance(stream, _FakeInputStream)
    assert constructed  # 确实构造了 InputStream
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "Pa_OpenStream 完成" in msgs
    assert "耗时=" in msgs


# ---------------- 方向3c：虚拟设备预过滤（真实设备优先）----------------

def test_available_devices_sorts_real_before_virtual(monkeypatch):
    """虚拟/远控设备降权排后，真实设备优先（避免默认选到 OrayVirtualAudioDevice 等高危设备）。"""
    core = _AudioCore(AudioConfig())
    fake_devs = [
        {"max_input_channels": 1, "name": "OrayVirtualAudioDevice", "default_samplerate": 48000.0},
        {"max_input_channels": 1, "name": "MacBook Pro麦克风", "default_samplerate": 48000.0},
        {"max_input_channels": 1, "name": "BlackHole", "default_samplerate": 44100.0},
        {"max_input_channels": 0, "name": "仅输出设备"},  # 无输入通道，应被过滤
    ]
    monkeypatch.setattr(awmod.sd, "query_devices", lambda: fake_devs)
    core.invalidate_devices()
    names = [d["name"] for d in core.available_devices]
    assert "仅输出设备" not in names  # 无输入通道的被过滤
    # 真实设备排在所有虚拟设备之前
    assert names.index("MacBook Pro麦克风") < names.index("OrayVirtualAudioDevice")
    assert names.index("MacBook Pro麦克风") < names.index("BlackHole")
