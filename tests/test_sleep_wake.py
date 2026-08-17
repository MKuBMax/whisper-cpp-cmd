"""A8: 系统睡眠/唤醒感知单测。

不依赖真实 NSWorkspace 通知：直接测 _on_system_sleep_wake 处理逻辑
与 _SystemSleepWakeObserver 中继。
"""

import logging

from app.controller import VoiceInputApp, _SystemSleepWakeObserver


class _FakeAudioSource:
    def __init__(self):
        self.invalidated = 0

    def invalidate_devices(self):
        self.invalidated += 1


class _FakePipeline:
    def __init__(self, recording=False):
        self.is_recording = recording
        self.audio_source = _FakeAudioSource()


def _make_app(recording=False):
    """绕过 __init__ 构造最小可测 VoiceInputApp。"""
    app = VoiceInputApp.__new__(VoiceInputApp)
    app._logger = logging.getLogger("test")
    app._paused = False
    app._state = "idle"
    app.pipeline = _FakePipeline(recording=recording)
    app._released = []
    app.release_backend_resources = lambda manual=False: app._released.append(manual)
    app._set_state = lambda s: setattr(app, "_state", s)
    app._refresh_status_bar_details = lambda: None
    return app


def test_sleep_idle_releases_backend_and_invalidates():
    app = _make_app(recording=False)
    app._on_system_sleep_wake("sleep")
    assert app.pipeline.audio_source.invalidated == 1
    assert app._released == [False]


def test_sleep_while_recording_does_not_release():
    app = _make_app(recording=True)
    app._on_system_sleep_wake("sleep")
    assert app.pipeline.audio_source.invalidated == 1
    assert app._released == []  # 录音中不释放，避免丢失听写


def test_wake_invalidates_and_sets_idle():
    app = _make_app()
    app._on_system_sleep_wake("wake")
    assert app.pipeline.audio_source.invalidated == 1
    assert app._state == "idle"
    assert app._released == []  # 唤醒不释放后端


def test_handler_safe_when_pipeline_none():
    app = VoiceInputApp.__new__(VoiceInputApp)
    app._logger = logging.getLogger("test")
    app.pipeline = None
    app._on_system_sleep_wake("wake")  # 不应抛异常


def test_observer_relays_sleep_wake():
    received = []
    obs = _SystemSleepWakeObserver.alloc().initWithHandler_(lambda ev: received.append(ev))
    obs.onSleep_(None)
    obs.onWake_(None)
    assert received == ["sleep", "wake"]
