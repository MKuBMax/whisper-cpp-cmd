"""C1: 热键可配置单测。"""

import logging
import threading

from pynput import keyboard

import app.controller as controller_module
from app.controller import VoiceInputApp, _HOTKEY_KEYS, _HOTKEY_LABELS
from config.settings import Settings


def _make_app():
    app = VoiceInputApp.__new__(VoiceInputApp)
    app.settings = Settings()
    app._logger = logging.getLogger("test")
    app._refresh_status_bar_dynamic_details = lambda: None
    app._active_trace_lock = threading.Lock()
    app._active_trace = None
    app._hotkey_release_timer = None
    app._pipeline_transitioning = False
    app._worker_busy = False
    app._state = "idle"
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


def test_missing_release_is_recovered_from_physical_key_state(monkeypatch):
    import queue

    app = _make_app()
    app._paused = False
    app.pipeline = object()
    app._dictation_queue = queue.Queue()
    monkeypatch.setattr(app, "_schedule_hotkey_release_watchdog", lambda _trace: None)
    monkeypatch.setattr(app, "_hotkey_is_down", lambda: False)

    app._on_press(keyboard.Key.cmd_r)
    trace = app._active_trace
    assert trace is not None

    # 模拟 pynput 漏掉 release，watchdog 看到物理按键已抬起后补发。
    app._check_hotkey_release(trace)

    assert app._active_trace is None
    assert app._dictation_queue.get_nowait() == ("press", trace)
    assert app._dictation_queue.get_nowait() == ("release", trace)


def test_normal_release_cancels_watchdog_without_duplicate_release(monkeypatch):
    import queue

    app = _make_app()
    app._paused = False
    app.pipeline = object()
    app._dictation_queue = queue.Queue()
    monkeypatch.setattr(app, "_schedule_hotkey_release_watchdog", lambda _trace: None)

    app._on_press(keyboard.Key.cmd_r)
    trace = app._active_trace
    app._on_release(keyboard.Key.cmd_r)
    app._check_hotkey_release(trace)

    assert app._active_trace is None
    assert app._dictation_queue.get_nowait() == ("press", trace)
    assert app._dictation_queue.get_nowait() == ("release", trace)
    assert app._dictation_queue.empty()


def test_release_watchdog_keeps_active_trace_while_key_is_down(monkeypatch):
    import queue

    app = _make_app()
    trace = object()
    app._active_trace = trace
    app._dictation_queue = queue.Queue()
    rescheduled = []
    monkeypatch.setattr(app, "_hotkey_is_down", lambda: True)
    monkeypatch.setattr(
        app,
        "_schedule_hotkey_release_watchdog",
        lambda scheduled_trace: rescheduled.append(scheduled_trace),
    )

    app._check_hotkey_release(trace)

    assert app._active_trace is trace
    assert rescheduled == [trace]
    assert app._dictation_queue.empty()


def test_modifier_watchdog_uses_event_flags(monkeypatch):
    class _FakeQuartz:
        kCGEventSourceStateHIDSystemState = 1
        kCGEventFlagMaskCommand = 1 << 20

        @staticmethod
        def CGEventSourceFlagsState(_state):
            return _FakeQuartz.kCGEventFlagMaskCommand

        @staticmethod
        def CGEventSourceKeyState(*_args):
            raise AssertionError("modifier hotkeys must not use key state")

    app = _make_app()
    monkeypatch.setattr(controller_module, "Quartz", _FakeQuartz)

    assert app._hotkey_is_down() is True
