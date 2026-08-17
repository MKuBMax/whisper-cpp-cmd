"""C3: perf 日志单测。"""

import json
import logging
import os

from core.perf_log import append_perf_log


# ---------------- append_perf_log ----------------

def test_append_writes_valid_json(tmp_path):
    path = str(tmp_path / "perf.jsonl")
    append_perf_log(path, {"ts": "2026-06-28T12:00:00", "model": "large-v3", "rtf": 0.3})
    lines = open(path, encoding="utf-8").read().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["model"] == "large-v3" and rec["rtf"] == 0.3


def test_append_appends_multiple(tmp_path):
    path = str(tmp_path / "perf.jsonl")
    append_perf_log(path, {"i": 1})
    append_perf_log(path, {"i": 2})
    lines = open(path, encoding="utf-8").read().splitlines()
    assert len(lines) == 2
    assert [json.loads(l)["i"] for l in lines] == [1, 2]


def test_append_creates_missing_dir(tmp_path):
    path = str(tmp_path / "sub" / "perf.jsonl")
    append_perf_log(path, {"ok": True})
    assert os.path.exists(path)


def test_append_handles_unicode(tmp_path):
    path = str(tmp_path / "perf.jsonl")
    append_perf_log(path, {"text": "你好世界"})
    rec = json.loads(open(path, encoding="utf-8").read())
    assert rec["text"] == "你好世界"


# ---------------- controller._log_perf ----------------

class _FakeResult:
    recording_duration = 2.5
    processing_time = 1.0
    rtf = 0.4
    text = "你好"
    success = True


class _FakeTrace:
    trace_id = "abc12345"


def _make_app():
    from app.controller import VoiceInputApp
    from config.settings import Settings
    app = VoiceInputApp.__new__(VoiceInputApp)
    app.settings = Settings()
    app.settings.dictation_mode = "preview"
    app.settings.use_vad = True
    app._logger = logging.getLogger("test")
    app._live_dictation = None
    app._perf_log_path = "/tmp/test_perf.jsonl"
    return app


def test_log_perf_builds_full_record(monkeypatch):
    captured = []
    monkeypatch.setattr("app.controller.append_perf_log", lambda path, rec: captured.append(rec))
    app = _make_app()
    app._log_perf(_FakeResult(), _FakeTrace())
    assert len(captured) == 1
    rec = captured[0]
    assert rec["trace_id"] == "abc12345"
    assert rec["model"] == app.settings.current_model
    assert rec["mode"] == "preview"
    assert rec["use_vad"] is True
    assert rec["duration_s"] == 2.5
    assert rec["processing_s"] == 1.0
    assert rec["rtf"] == 0.4
    assert rec["text_len"] == 2
    assert rec["first_char_ms"] is None  # _live_dictation 为 None
    assert rec["success"] is True
    assert "ts" in rec


def test_log_perf_swallows_errors(monkeypatch):
    """写 perf 失败不应影响听写主流程。"""
    def boom(path, rec):
        raise OSError("disk full")
    monkeypatch.setattr("app.controller.append_perf_log", boom)
    app = _make_app()
    app._log_perf(_FakeResult(), _FakeTrace())  # 不应抛异常
