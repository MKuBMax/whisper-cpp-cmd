"""A7: 音频设备热插拔容错单测。

覆盖：设备缓存 TTL/失效、指定设备创建失败回退默认、start_recording 异常丢弃死流。
通过 monkeypatch sounddevice 与 fake stream，无需真实音频设备。

注：采集内核已隔离进 core.audio_worker 子进程，本测试直接测其 _AudioCore 内核类。
"""

import core.audio_worker as asmod
from core.audio_source import AudioConfig
from core.audio_worker import _AudioCore


class _FakeStream:
    """最小化的伪音频流，模拟 sounddevice.InputStream 的关键接口。"""

    def __init__(self, active=False, closed=False, start_raises=None):
        self.active = active
        self.closed = closed
        self._start_raises = start_raises

    def start(self):
        if self._start_raises:
            raise self._start_raises
        self.active = True

    def stop(self):
        self.active = False

    def close(self):
        self.closed = True


def _fake_devices():
    return [
        {"name": "外接麦克风", "max_input_channels": 1, "default_samplerate": 48000.0},
    ]


# ---------------- 设备缓存 TTL / 失效 ----------------

def test_device_cache_hits_and_invalidates(monkeypatch):
    calls = []
    monkeypatch.setattr(asmod.sd, "query_devices", lambda: (calls.append(1), _fake_devices())[1])

    core = _AudioCore(AudioConfig())
    core.invalidate_devices()
    core.available_devices
    assert len(calls) == 1
    core.available_devices  # 缓存命中
    assert len(calls) == 1
    core.invalidate_devices()
    core.available_devices  # 失效后重新枚举
    assert len(calls) == 2


def test_device_cache_expires_after_ttl(monkeypatch):
    calls = []
    monkeypatch.setattr(asmod.sd, "query_devices", lambda: (calls.append(1), _fake_devices())[1])

    core = _AudioCore(AudioConfig())
    core.invalidate_devices()
    core.available_devices
    assert len(calls) == 1

    # 把缓存时间拨到 TTL 之外
    core._devices_cached_at -= asmod._DEVICE_CACHE_TTL_SECONDS + 1
    core.available_devices
    assert len(calls) == 2


# ---------------- 创建失败回退默认设备 ----------------

def test_start_falls_back_to_default_on_create_failure(monkeypatch):
    monkeypatch.setattr(asmod.sd, "query_devices", _fake_devices)
    core = _AudioCore(AudioConfig())

    created = []
    default_stream = _FakeStream()

    def fake_create(device_index):
        created.append(device_index)
        return None if device_index is not None else default_stream

    monkeypatch.setattr(core, "_create_stream", fake_create)

    assert core.start("外接麦克风") is True
    # 先尝试指定设备(index 0)，失败后回退 None(系统默认)
    assert created == [0, None]
    assert core.fell_back_to_default is True


def test_start_no_fallback_when_device_ok(monkeypatch):
    monkeypatch.setattr(asmod.sd, "query_devices", _fake_devices)
    core = _AudioCore(AudioConfig())

    created = []

    def fake_create(device_index):
        created.append(device_index)
        return _FakeStream()

    monkeypatch.setattr(core, "_create_stream", fake_create)

    assert core.start("外接麦克风") is True
    assert created == [0]  # 没回退
    assert core.fell_back_to_default is False


def test_start_returns_false_when_default_also_fails(monkeypatch):
    monkeypatch.setattr(asmod.sd, "query_devices", _fake_devices)
    core = _AudioCore(AudioConfig())
    monkeypatch.setattr(core, "_create_stream", lambda idx: None)  # 全失败

    assert core.start("外接麦克风") is False
    assert core.fell_back_to_default is True  # 确实尝试过回退


# ---------------- start_recording 异常丢弃死流 ----------------

def test_start_recording_discards_dead_stream():
    core = _AudioCore(AudioConfig())
    core._stream = _FakeStream(closed=False, start_raises=RuntimeError("boom"))
    core.start_recording()  # 不应抛异常

    assert core._stream is None  # 死流被丢弃，下次 start() 会重建
