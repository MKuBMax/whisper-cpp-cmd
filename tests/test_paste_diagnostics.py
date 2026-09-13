"""粘贴诊断日志：每个失败分支必须有原因码，且不泄露文本与 AXValue 内容。"""
import logging
from types import SimpleNamespace

import core.clipboard as cmod
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
    monkeypatch.setattr(clipboard, "_frontmost_app_for_probe", lambda: None)


def test_capture_logs_reason_code(monkeypatch, caplog):
    clipboard = Clipboard()
    _stub_probe(monkeypatch, clipboard, [None])
    with caplog.at_level(logging.INFO, logger="core.clipboard"):
        clipboard.capture_target()
    text = _logs(caplog)
    assert "phase=capture" in text
    assert "reason=" in text


def test_insert_without_target_logs_reason(monkeypatch, caplog):
    clipboard = Clipboard()
    monkeypatch.setattr(clipboard, "copy", lambda text: True)
    _stub_probe(monkeypatch, clipboard, [None, None])
    clipboard.capture_target()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="core.clipboard"):
        assert clipboard.insert("秘密文本内容") is False
    text = _logs(caplog)
    assert "phase=insert" in text
    assert "reason=" in text
    assert "秘密文本内容" not in text
    assert clipboard.last_delivery == "copied"


def test_insert_target_changed_logs_comparison(monkeypatch, caplog):
    clipboard = Clipboard()
    monkeypatch.setattr(clipboard, "copy", lambda text: True)
    _stub_probe(
        monkeypatch,
        clipboard,
        [
            {
                "frontmost_app": "编辑器",
                "bundle_id": "com.example.editor",
                "pid": 12,
                "role": "AXTextArea",
                "verdict": "editable",
                "element": "original",
            },
            {
                "frontmost_app": "编辑器",
                "bundle_id": "com.example.editor",
                "pid": 13,
                "role": "AXTextArea",
                "verdict": "editable",
                "element": "other",
            },
        ],
    )
    monkeypatch.setattr(
        "core.clipboard.CoreFoundation.CFEqual", lambda a, b: a == b
    )
    clipboard.capture_target()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="core.clipboard"):
        assert clipboard.insert("秘密文本内容") is False
    text = _logs(caplog)
    assert "phase=insert" in text
    assert "pid_changed=" in text
    assert "same_ax=" in text
    assert "秘密文本内容" not in text
    assert clipboard.last_delivery == "copied"


def test_probe_never_logs_ax_value_content(monkeypatch, caplog):
    clipboard = Clipboard()
    ax_value_content = "绝对不能进日志的输入框现有文本"
    dictation_text = "听写新文本不能进日志"

    def fake_ax_copy(element, attribute):
        if attribute == "AXValue":
            return 0, ax_value_content
        if attribute == "AXSelectedTextRange":
            return 0, object()
        if attribute == "AXRole":
            return 0, "AXStaticText"
        if attribute == "AXSubrole":
            return 0, "AXUnknown"
        return -1, None

    class _App:
        def processIdentifier(self):
            return 12

        def localizedName(self):
            return "编辑器"

        def bundleIdentifier(self):
            return "com.example.editor"

    monkeypatch.setattr(clipboard, "_ax_copy_attribute_value", fake_ax_copy)
    monkeypatch.setattr(
        clipboard, "_ax_is_attribute_settable", lambda element, attr: (0, False)
    )
    monkeypatch.setattr(clipboard, "_get_focused_ui_element", lambda: (0, object()))
    # capture 用 _App，insert 的 probe 用 _App，insert 的 pid 查询用 None，
    # 覆盖 capture、insert-probe、insert-pid 三次前台查询。
    apps = [_App(), _App(), None]
    calls = {"index": 0}

    def fake_frontmost():
        calls["index"] += 1
        return apps[min(calls["index"] - 1, len(apps) - 1)]

    monkeypatch.setattr(clipboard, "_frontmost_app_for_probe", fake_frontmost)
    monkeypatch.setattr(clipboard, "copy", lambda text: True)
    with caplog.at_level(logging.INFO, logger="core.clipboard"):
        assert clipboard.editable_target() is None
        clipboard.capture_target()
        assert clipboard.insert(dictation_text) is False
    assert ax_value_content not in _logs(caplog)
    assert dictation_text not in _logs(caplog)
