"""主线程 watchdog 单测：心跳停滞检测、去重、恢复重置。

跳过 __init__ 构造最小 app（不依赖 Settings/PyObjC runloop），只测
_evaluate_main_thread_watchdog 的纯逻辑。
"""

import logging

from app.controller import (
    VoiceInputApp,
    _MAIN_THREAD_WATCHDOG_THRESHOLD,
    _WATCHDOG_POLL_INTERVAL,
)


def _make_app():
    app = VoiceInputApp.__new__(VoiceInputApp)
    app._logger = logging.getLogger("test_main_thread_watchdog")
    app._main_thread_heartbeat = None
    app._main_thread_dumped = False
    app._main_thread_healthy_streak = 0
    app._watchdog_dumped = False
    return app


# ---------------- 启动宽限 ----------------

def test_no_heartbeat_skipped_at_startup(monkeypatch):
    """heartbeat 为 None（runEventLoop 未开始 tick）时跳过，不误报。"""
    app = _make_app()
    dumped = []
    monkeypatch.setattr("app.controller.diagnostics.dump_report", lambda *a, **k: dumped.append(a))
    app._evaluate_main_thread_watchdog(now=100.0)
    assert app._main_thread_dumped is False
    assert dumped == []


# ---------------- 冻结检测 + 去重 ----------------

def test_freeze_triggers_dump_once_and_suppresses(monkeypatch):
    """停滞超阈值 → dump 一次，并设 _watchdog_dumped 抑制 worker/P2b 重复 dump。"""
    app = _make_app()
    app._main_thread_heartbeat = 0.0  # 心跳停在 0
    dumped = []
    monkeypatch.setattr("app.controller.diagnostics.dump_report", lambda *a, **k: dumped.append(a))

    app._evaluate_main_thread_watchdog(now=_MAIN_THREAD_WATCHDOG_THRESHOLD + 1)
    assert app._main_thread_dumped is True
    assert app._watchdog_dumped is True  # 抑制 worker watchdog / _run_on_main_thread
    assert len(dumped) == 1

    # 仍冻结，再调一次不重复 dump
    app._evaluate_main_thread_watchdog(now=_MAIN_THREAD_WATCHDOG_THRESHOLD + 6)
    assert len(dumped) == 1


def test_below_threshold_no_dump(monkeypatch):
    """灰色区间（poll~阈值）不 dump。"""
    app = _make_app()
    app._main_thread_heartbeat = 0.0
    dumped = []
    monkeypatch.setattr("app.controller.diagnostics.dump_report", lambda *a, **k: dumped.append(a))

    app._evaluate_main_thread_watchdog(now=_WATCHDOG_POLL_INTERVAL + 1)  # 6s 灰色区间
    assert app._main_thread_dumped is False
    assert dumped == []


# ---------------- 恢复重置（防抖）----------------

def test_recovery_needs_two_healthy_rounds(monkeypatch):
    """心跳恢复后需连续 2 轮健康才重置 _main_thread_dumped。"""
    app = _make_app()
    app._main_thread_dumped = True
    app._watchdog_dumped = True
    monkeypatch.setattr("app.controller.diagnostics.dump_report", lambda *a, **k: None)

    # 第一轮健康（心跳刚更新，elapsed 小）
    app._main_thread_heartbeat = 100.0
    app._evaluate_main_thread_watchdog(now=100.0 + 1.0)
    assert app._main_thread_dumped is True  # 还没连续 2 轮

    # 第二轮健康
    app._main_thread_heartbeat = 105.0
    app._evaluate_main_thread_watchdog(now=105.0 + 1.0)
    assert app._main_thread_dumped is False
    assert app._main_thread_healthy_streak == 0


def test_gray_zone_resets_streak():
    """灰色区间清零 healthy_streak，防止半冻结状态误判恢复。"""
    app = _make_app()
    app._main_thread_heartbeat = 0.0
    app._main_thread_healthy_streak = 1

    app._evaluate_main_thread_watchdog(now=_WATCHDOG_POLL_INTERVAL + 1)  # 6s 灰色区间
    assert app._main_thread_healthy_streak == 0


# ---------------- 正常运转不干扰 ----------------

def test_healthy_no_op_when_not_dumped():
    """未 dump 过时，心跳健康只累加 streak，不产生副作用。"""
    app = _make_app()
    app._main_thread_heartbeat = 100.0
    app._evaluate_main_thread_watchdog(now=100.0 + 1.0)
    assert app._main_thread_dumped is False
    assert app._main_thread_healthy_streak == 1
