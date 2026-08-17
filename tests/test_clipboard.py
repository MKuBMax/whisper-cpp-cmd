"""clipboard.copy() 优先级单测：NSPasteboard 优先，pbcopy 仅兜底。

GUI App 内 pbcopy/pbpaste 子进程有 pasteboard 同步竞态（约 45% 校验失败），
NSPasteboard 实测 100% 可靠，故 copy() 应优先 NSPasteboard。
"""

import core.clipboard as cmod
from core.clipboard import Clipboard, ClipboardConfig


def test_copy_prefers_nspasteboard_over_pbcopy(monkeypatch):
    cb = Clipboard(ClipboardConfig())
    monkeypatch.setattr(cb, "_copy_with_pasteboard", lambda t: True)

    def bomb(*args, **kwargs):
        raise AssertionError("NSPasteboard 成功时不应再调用 pbcopy 子进程")

    monkeypatch.setattr(cmod.subprocess, "run", bomb)
    assert cb.copy("你好世界") is True


def test_copy_falls_back_to_pbcopy_when_nspasteboard_fails(monkeypatch):
    cb = Clipboard(ClipboardConfig())
    monkeypatch.setattr(cb, "_copy_with_pasteboard", lambda t: False)

    calls = []

    class _Result:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(cmd, **kwargs):
        calls.append(cmd[0])
        if cmd[0] == "pbcopy":
            return _Result(b"")
        if cmd[0] == "pbpaste":
            return _Result("你好世界".encode("utf-8"))
        raise AssertionError(f"未预期的子进程调用：{cmd}")

    monkeypatch.setattr(cmod.subprocess, "run", fake_run)
    assert cb.copy("你好世界") is True
    assert calls == ["pbcopy", "pbpaste"]  # NSPasteboard 失败后走兜底


def test_copy_returns_false_on_empty():
    cb = Clipboard(ClipboardConfig())
    assert cb.copy("") is False
