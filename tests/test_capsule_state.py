"""单胶囊控制流：按下即 show，松开 busy→结果，开关关闭不碰浮窗。"""

import logging
from types import SimpleNamespace

from PyObjCTools import AppHelper

from app.controller import VoiceInputApp
from core.dictation_trace import DictationTrace


class _FakeOverlay:
    def __init__(self):
        self.calls = []

    def show(self):
        self.calls.append("show")

    def hide(self):
        self.calls.append("hide")

    def show_status(self, message, timeout=None, generation=None):
        self.calls.append(("status", message, timeout, generation))


def _make_capsule_app(monkeypatch, show_overlay=True, state="idle"):
    app = VoiceInputApp.__new__(VoiceInputApp)
    app.settings = SimpleNamespace(show_overlay=show_overlay, dictation_mode="quick", save=lambda: None)
    app._overlay = _FakeOverlay()
    app._capsule_generation = 0
    app._state = state
    app._paused = False
    app._last_transcript = "上次结果"
    app._logger = logging.getLogger("test_capsule_state")
    app._current_trace = None
    app.status_bar = None
    app._backend_released = False
    app._cancel_error_reset_timer = lambda: None
    app._refresh_status_bar_details = lambda: None
    app._start_sysref = lambda: None
    app._start_backend_warmup = lambda: None
    app._log_perf = lambda *_a, **_k: None
    app._schedule_idle_release_timer = lambda: None
    app.pipeline = object()
    app.copy_text = lambda text: True
    monkeypatch.setattr(AppHelper, "callAfter", lambda fn, *args: fn(*args))
    return app


class _RecordingPipeline:
    def __init__(self, delivery="sent"):
        self.is_recording = False
        self.is_initialized = True
        self.audio_source = SimpleNamespace(
            fell_back_to_default=False, trace=None, overflow=False,
        )
        self.model_engine = SimpleNamespace(is_loaded=True, trace=None)
        self.clipboard = SimpleNamespace(last_delivery=delivery)
        self.trace = None
        self.starts = 0

    def start_recording(self):
        self.starts += 1
        self.is_recording = True
        return True

    def stop_recording(self, paste_output=True):
        self.is_recording = False
        return SimpleNamespace(
            no_speech=False,
            success=True,
            text="你好",
            recording_duration=1.0,
            processing_time=0.2,
            rtf=0.2,
            error=None,
        )


def _recording_pipeline():
    return _RecordingPipeline()


def test_capsule_api_exists():
    assert callable(VoiceInputApp._capsule_show_recording)
    assert callable(VoiceInputApp._capsule_show_busy)
    assert callable(VoiceInputApp._capsule_show_result)
    assert callable(VoiceInputApp._capsule_hide)
    assert not hasattr(VoiceInputApp, "_show_overlay")
    assert not hasattr(VoiceInputApp, "_hide_overlay")


def test_capsule_hidden_when_overlay_disabled(monkeypatch):
    app = _make_capsule_app(monkeypatch, show_overlay=False)
    app._capsule_show_recording()
    app._capsule_show_busy()
    app._capsule_show_result("完成")
    assert app._overlay.calls == []
    assert app._capsule_generation == 0


def test_capsule_shows_when_overlay_enabled(monkeypatch):
    app = _make_capsule_app(monkeypatch, show_overlay=True)
    app._capsule_show_recording()
    app._capsule_show_busy()
    app._capsule_show_result("完成")
    assert app._overlay.calls[0] == "show"
    assert app._overlay.calls[1][0] == "status"
    assert app._overlay.calls[1][1] == "正在识别…"
    assert app._overlay.calls[2][1] == "完成"
    assert app._capsule_generation == 1


def test_copy_last_result_skips_capsule_while_recording(monkeypatch):
    app = _make_capsule_app(monkeypatch, state="recording")
    assert app.copy_last_result() is True
    assert app._overlay.calls == []


def test_copy_last_result_skips_capsule_while_processing(monkeypatch):
    app = _make_capsule_app(monkeypatch, state="processing")
    assert app.copy_last_result() is True
    assert app._overlay.calls == []


def test_copy_last_result_shows_capsule_when_idle(monkeypatch):
    app = _make_capsule_app(monkeypatch, state="idle")
    assert app.copy_last_result() is True
    assert app._overlay.calls == [("status", "已复制到剪贴板", 1.0, 0)]


def test_press_does_not_show_when_overlay_disabled(monkeypatch):
    app = _make_capsule_app(monkeypatch, show_overlay=False)
    app.pipeline = _recording_pipeline()
    app._handle_press(DictationTrace.create())
    assert app._overlay.calls == []
    assert app._state == "recording"


def test_press_shows_when_overlay_enabled(monkeypatch):
    app = _make_capsule_app(monkeypatch, show_overlay=True)
    app.pipeline = _recording_pipeline()
    app._handle_press(DictationTrace.create())
    assert app.pipeline.starts == 1
    assert app._overlay.calls == ["show"]
    assert app._state == "recording"


def test_press_release_records_show_busy_result(monkeypatch):
    app = _make_capsule_app(monkeypatch, show_overlay=True)
    app.pipeline = _recording_pipeline()
    trace = DictationTrace.create()
    app._handle_press(trace)
    app._handle_release(trace)
    assert app._overlay.calls == [
        "show",
        ("status", "正在识别…", None, 1),
        ("status", "已发送到输入光标", 1.0, 1),
    ]
    assert app._state == "idle"


def test_toggle_overlay_off_hides_capsule(monkeypatch):
    app = _make_capsule_app(monkeypatch, show_overlay=True)
    app.toggle_overlay()
    assert app.settings.show_overlay is False
    assert app._overlay.calls == ["hide"]
