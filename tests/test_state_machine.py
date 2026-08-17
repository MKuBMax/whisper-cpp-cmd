"""C6: controller 显式状态机单测。"""

import logging

from app.controller import VoiceInputApp, _STATES, _TRANSITIONS


# ---------------- 转移表正确性 ----------------

def test_normal_flow_transitions_allowed():
    assert "recording" in _TRANSITIONS["idle"]        # 按键
    assert "processing" in _TRANSITIONS["recording"]  # 松键
    assert "idle" in _TRANSITIONS["processing"]       # 转写完成
    assert "error" in _TRANSITIONS["recording"]       # 录音失败
    assert "error" in _TRANSITIONS["processing"]      # 转写失败
    assert "processing" in _TRANSITIONS["idle"]       # 重建（切模型）


def test_processing_cannot_directly_enter_recording():
    """worker 串行，processing 中不应直接进 recording（核心保护点）。"""
    assert "recording" not in _TRANSITIONS["processing"]


def test_all_states_have_transition_entry():
    assert set(_TRANSITIONS) == _STATES


# ---------------- _set_state 行为 ----------------

def _make_app(initial="idle"):
    app = VoiceInputApp.__new__(VoiceInputApp)
    app._state = initial
    app._logger = logging.getLogger("test_state_machine")
    app._current_trace = None
    app.status_bar = None
    app._overlay = None
    app._error_reset_timer = None
    app._cancel_error_reset_timer = lambda: None
    app._schedule_error_reset = lambda: None
    app._show_overlay = lambda: None
    app._hide_overlay = lambda: None
    return app


def test_legal_transition_no_warning(caplog):
    app = _make_app("idle")
    with caplog.at_level(logging.WARNING):
        app._set_state("recording")
    assert app._state == "recording"
    assert not any("非法" in r.getMessage() for r in caplog.records)


def test_illegal_transition_warns_but_still_sets(caplog):
    app = _make_app("processing")
    with caplog.at_level(logging.WARNING):
        app._set_state("recording")
    assert app._state == "recording"  # 不改行为：仍设置
    assert any("非法" in r.getMessage() for r in caplog.records)


def test_unknown_state_rejected(caplog):
    app = _make_app("idle")
    with caplog.at_level(logging.WARNING):
        app._set_state("typo_state")
    assert app._state == "idle"  # 未改变
    assert any("未知状态" in r.getMessage() for r in caplog.records)
