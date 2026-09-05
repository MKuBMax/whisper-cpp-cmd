"""C1: 热键可配置单测。"""

import logging

from pynput import keyboard

from app.controller import VoiceInputApp, _HOTKEY_KEYS, _HOTKEY_LABELS
from config.settings import Settings


def _make_app():
    app = VoiceInputApp.__new__(VoiceInputApp)
    app.settings = Settings()
    app._logger = logging.getLogger("test")
    app._refresh_status_bar_dynamic_details = lambda: None
    return app


def test_hotkey_target_default():
    assert _make_app()._hotkey_target() == keyboard.Key.cmd_r


def test_hotkey_target_custom():
    app = _make_app()
    app.settings.hotkey = "f13"
    assert app._hotkey_target() == keyboard.Key.f13


def test_hotkey_target_unknown_falls_back():
    app = _make_app()
    app.settings.hotkey = "nonexistent"
    assert app._hotkey_target() == keyboard.Key.cmd_r


def test_get_hotkey_options_marks_selected():
    app = _make_app()
    app.settings.hotkey = "alt_r"
    opts = app._get_hotkey_options()
    assert len(opts) == len(_HOTKEY_LABELS)
    selected = [o for o in opts if o["selected"]]
    assert len(selected) == 1 and selected[0]["value"] == "alt_r"


def test_select_hotkey_changes_and_saves(monkeypatch):
    app = _make_app()
    saved = {}
    monkeypatch.setattr(app.settings, "save", lambda: saved.update({"hotkey": app.settings.hotkey}))
    app.select_hotkey("f14")
    assert app.settings.hotkey == "f14"
    assert saved == {"hotkey": "f14"}


def test_select_hotkey_ignores_unknown_and_same(monkeypatch):
    app = _make_app()
    orig = app.settings.hotkey
    monkeypatch.setattr(app.settings, "save", lambda: None)
    app.select_hotkey("nonexistent")  # 未知 → 忽略
    assert app.settings.hotkey == orig
    app.select_hotkey(orig)  # 相同 → 忽略
    assert app.settings.hotkey == orig


def test_all_labels_have_valid_pynput_keys():
    assert set(_HOTKEY_LABELS) == set(_HOTKEY_KEYS)
    for key in _HOTKEY_KEYS.values():
        assert key is not None


def test_processing_does_not_queue_delayed_recording():
    import queue
    app = _make_app()
    app._paused = False
    app.pipeline = object()
    app._active_trace = None
    app._state = "processing"
    app._dictation_queue = queue.Queue()
    app._on_press(keyboard.Key.cmd_r)
    assert app._dictation_queue.empty()


def test_repeat_press_keeps_original_release_trace():
    import queue
    app = _make_app()
    app._paused = False
    app.pipeline = object()
    original = object()
    app._active_trace = original
    app._dictation_queue = queue.Queue()
    app._on_press(keyboard.Key.cmd_r)
    assert app._active_trace is original
    assert app._dictation_queue.empty()
