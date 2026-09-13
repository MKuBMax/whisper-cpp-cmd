"""P1 RED：PID 级发送。AX 无可写元素但前后同 PID 时应发送一次。"""
import logging
from types import SimpleNamespace

from core.clipboard import Clipboard


def _logs(caplog):
    return "\n".join(record.getMessage() for record in caplog.records)


def _stub_probe(monkeypatch, clipboard, snapshots):
    calls = {"index": 0}

    def fake_probe(phase):
        calls["index"] += 1
        snapshot = snapshots[min(calls["index"] - 1, len(snapshots) - 1)]
        if snapshot is None:
            return None
        item = dict(snapshot)
        item["phase"] = phase
        return item

    monkeypatch.setattr(clipboard, "_probe_snapshot", fake_probe)


def _stub_frontmost(monkeypatch, clipboard, pids):
    calls = {"index": 0}

    def fake_frontmost():
        calls["index"] += 1
        pid = pids[min(calls["index"] - 1, len(pids) - 1)]
        if pid is None:
            return None
        return SimpleNamespace(
            processIdentifier=lambda: pid,
            localizedName=lambda: "微信",
            bundleIdentifier=lambda: "com.tencent.xinWeChat",
        )

    monkeypatch.setattr(clipboard, "_frontmost_app_for_probe", fake_frontmost)


def test_pid_match_sends_once_when_ax_has_no_element(monkeypatch, caplog):
    clipboard = Clipboard()
    monkeypatch.setattr(clipboard, "copy", lambda text: True)
    sent = []
    monkeypatch.setattr(clipboard, "_paste_with_cg_event", lambda: sent.append(True))
    capture = {
        "frontmost_app": "微信", "bundle_id": "com.tencent.xinWeChat",
        "pid": 51183, "role": "-", "verdict": "rejected",
    }
    insert = dict(capture)
    _stub_probe(monkeypatch, clipboard, [capture, insert])
    _stub_frontmost(monkeypatch, clipboard, [51183, 51183])
    clipboard.capture_target()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="core.clipboard"):
        assert clipboard.insert("你好") is True
    assert sent == [True]
    assert clipboard.last_delivery == "sent"
    assert "reason=pid_match_sent" in _logs(caplog)


def test_pid_changed_holds_clipboard(monkeypatch, caplog):
    clipboard = Clipboard()
    monkeypatch.setattr(clipboard, "copy", lambda text: True)
    monkeypatch.setattr(
        clipboard, "_paste_with_cg_event",
        lambda: (_ for _ in ()).throw(AssertionError("must not send")),
    )
    capture = {
        "frontmost_app": "微信", "bundle_id": "com.tencent.xinWeChat",
        "pid": 51183, "role": "-", "verdict": "rejected",
    }
    insert = {
        "frontmost_app": "备忘录", "bundle_id": "com.apple.Notes",
        "pid": 19216, "role": "AXTextArea", "verdict": "rejected",
    }
    _stub_probe(monkeypatch, clipboard, [capture, insert])
    _stub_frontmost(monkeypatch, clipboard, [51183, 19216])
    clipboard.capture_target()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="core.clipboard"):
        assert clipboard.insert("你好") is False
    assert clipboard.last_delivery == "copied"
    assert "reason=pid_changed" in _logs(caplog)
