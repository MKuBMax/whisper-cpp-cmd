"""A5: ModelEngine.interrupt_backend 自愈委托单测。

验证 watchdog 自愈路径能正确调用后端 _stop_server，
且在后端缺失/无该方法时不崩溃（getattr 委托的健壮性）。
"""

from core.model import ModelEngine


def test_interrupt_calls_backend_stop():
    engine = ModelEngine()
    calls = []

    class FakeBackend:
        def _stop_server(self):
            calls.append("stop")

    engine._backend = FakeBackend()
    engine.interrupt_backend()
    assert calls == ["stop"]


def test_interrupt_safe_when_no_backend():
    engine = ModelEngine()  # _backend 默认 None
    engine.interrupt_backend()  # 不应抛异常


def test_interrupt_safe_when_backend_has_no_stop():
    engine = ModelEngine()
    engine._backend = object()  # 无 _stop_server 方法
    engine.interrupt_backend()  # 不应抛异常


def test_stop_server_idempotent_when_no_process():
    """_server_process=None 时 _stop_server 早退、不抛——三钩子重复调用安全的基础。"""
    from core.model import WhisperCliBackend

    backend = WhisperCliBackend(cli_path="/opt/homebrew/bin/whisper-cli")
    assert backend._server_process is None
    backend._stop_server()  # 不抛、不操作
    assert backend._server_process is None
