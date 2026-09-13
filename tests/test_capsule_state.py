"""单胶囊代次：旧结果定时凭代次失效，不误杀新一轮录音胶囊。"""

import inspect
from types import SimpleNamespace

from PyObjCTools import AppHelper

from app.controller import VoiceInputApp
from ui.overlay_window import RecordingOverlay


class _FakeOverlay:
    def __init__(self):
        self.calls = []

    def show(self):
        self.calls.append("show")

    def show_status(self, message, timeout=None, generation=None):
        self.calls.append(("status", message, timeout, generation))


def _make_capsule_app(monkeypatch, show_overlay=True, state="idle"):
    app = VoiceInputApp.__new__(VoiceInputApp)
    app.settings = SimpleNamespace(show_overlay=show_overlay)
    app._overlay = _FakeOverlay()
    app._capsule_generation = 0
    app._state = state
    app._last_transcript = "上次结果"
    app.pipeline = object()
    app.copy_text = lambda text: True
    monkeypatch.setattr(AppHelper, "callAfter", lambda fn, *args: fn(*args))
    return app


def test_capsule_api_exists():
    assert callable(VoiceInputApp._capsule_show_recording)
    assert callable(VoiceInputApp._capsule_show_busy)
    assert callable(VoiceInputApp._capsule_show_result)


def test_status_capsule_hides_after_one_second():
    params = inspect.signature(RecordingOverlay.show_status).parameters
    assert params["timeout"].default == 1.0


def test_status_carries_generation():
    params = inspect.signature(RecordingOverlay.show_status).parameters
    assert "generation" in params


def test_no_first_frame_coupling():
    import pathlib
    controller = pathlib.Path("app/controller.py").read_text(encoding="utf-8")
    assert "_on_first_frame" not in controller
    assert "_arm_first_frame" not in controller
    source = pathlib.Path("core/audio_source.py").read_text(encoding="utf-8")
    assert "_on_first_frame" not in source


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
