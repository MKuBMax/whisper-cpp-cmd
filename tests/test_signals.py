"""main.install_exit_signal_handlers 单测。

确保 SIGINT/SIGTERM/SIGHUP 都注册了 graceful shutdown 处理，
避免 kill / 终端关闭时 whisper-server 子进程泄漏。
"""

import signal as sigmod

import pytest

from main import install_exit_signal_handlers, _EXIT_SIGNALS


class _FakeApp:
    def __init__(self):
        self.shutdown_called = False

    def shutdown(self):
        self.shutdown_called = True


def _save_handlers():
    return {s: sigmod.getsignal(s) for s in _EXIT_SIGNALS}


def _restore(saved):
    for s, h in saved.items():
        sigmod.signal(s, h)


def test_all_exit_signals_registered():
    saved = _save_handlers()
    try:
        install_exit_signal_handlers(_FakeApp())
        for s in _EXIT_SIGNALS:
            assert sigmod.getsignal(s) is not sigmod.SIG_DFL, f"信号 {s} 未注册自定义处理"
    finally:
        _restore(saved)


def test_handler_triggers_shutdown_then_exits():
    app = _FakeApp()
    saved = _save_handlers()
    try:
        install_exit_signal_handlers(app)
        handler = sigmod.getsignal(sigmod.SIGTERM)
        with pytest.raises(SystemExit):
            handler(sigmod.SIGTERM, None)
        assert app.shutdown_called is True
    finally:
        _restore(saved)
