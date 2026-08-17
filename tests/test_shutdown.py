"""防线2 并发加固：shutdown 幂等 + 锁防并发重入。

atexit / signal / NSApplicationDelegate 三入口最终都落到同一个 shutdown()，
靠 _is_running 标志 + _shutdown_lock 保证只真正清理一次。
"""

import logging
import threading

import pytest

from app.controller import VoiceInputApp
from PyObjCTools import AppHelper


class _FakePipeline:
    def __init__(self):
        self.shutdown_calls = 0

    def shutdown(self):
        self.shutdown_calls += 1


def _make_app():
    """绕过 __init__（不起 server / 不建 UI），只赋 shutdown 用到的属性。"""
    app = VoiceInputApp.__new__(VoiceInputApp)
    app._logger = logging.getLogger("test_shutdown")
    app._is_running = True
    app._shutdown_lock = threading.Lock()
    app._sleep_wake_observer = None
    app._set_state = lambda *_a, **_k: None        # 避开状态机 UI 副作用
    app._cancel_error_reset_timer = lambda: None
    app._cancel_idle_release_timer = lambda: None
    app._live_dictation = None
    app.listener = None
    app._watchdog_stop = threading.Event()
    app._watchdog_thread = None
    app._dictation_queue = None
    app._dictation_worker = None
    app.pipeline = _FakePipeline()
    app._refresh_status_bar_details = lambda: None
    return app


@pytest.fixture(autouse=True)
def _no_stop_event_loop(monkeypatch):
    # shutdown 末尾会 stopEventLoop；测试无 runloop，吞掉
    monkeypatch.setattr(AppHelper, "stopEventLoop", lambda *a, **k: None)


def test_shutdown_idempotent_double_call():
    app = _make_app()
    app.shutdown()
    app.shutdown()  # 第二次早退
    assert app._is_running is False
    assert app.pipeline.shutdown_calls == 1   # 只真正清理一次


def test_shutdown_concurrent_lock_safe():
    app = _make_app()
    barrier = threading.Barrier(2)

    def go():
        barrier.wait()
        app.shutdown()

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert app._is_running is False
    assert app.pipeline.shutdown_calls == 1   # 并发不重复清理
