"""audio worker IPC 协议单测：帧格式、命令分发、ready 握手、ack。

worker 进程内的 AudioWorker 逻辑（core.audio_worker）。用 os.pipe 捕获 stdout、
假 _AudioCore 内核、monkeypatch stdin 模拟命令，**不起子进程**，纯单测协议层。
"""

import io
import json
import os
import threading

import core.audio_worker as awmod
from core.audio_worker import AudioWorker, _TAG_PCM, _TAG_JSON, _HEADER


class _FakeCore:
    """假采集内核：记录命令调用、可控返回，提供 _dispatch 需要的接口。"""

    def __init__(self):
        self.calls = []
        self.start_ok = True
        self.start_rec_ok = True
        self._is_recording = False
        self._overflow = False
        self._fell_back = False
        self.devices = [
            {"index": 0, "name": "Mic", "channels": 1, "sample_rate": 48000.0, "is_virtual": False}
        ]

    def start(self, device=None):
        self.calls.append(("start", device))
        return self.start_ok

    def start_recording(self):
        self.calls.append(("start_recording",))
        self._is_recording = True
        return self.start_rec_ok

    def stop_recording(self):
        self.calls.append(("stop_recording",))
        self._is_recording = False
        return True

    def stop(self):
        self.calls.append(("stop",))

    def close(self):
        self.calls.append(("close",))

    def invalidate_devices(self):
        self.calls.append(("invalidate_devices",))

    @property
    def available_devices(self):
        return self.devices

    @property
    def is_recording(self):
        return self._is_recording

    @property
    def overflow(self):
        return self._overflow

    @property
    def fell_back_to_default(self):
        return self._fell_back


def _make_worker():
    """构造 AudioWorker + 假内核 + os.pipe 捕获 stdout，不跑 run()。返回 (worker, read_fd)。"""
    worker = AudioWorker.__new__(AudioWorker)
    worker._core = _FakeCore()
    r, w = os.pipe()
    worker._stdout_fd = w
    worker._stdout_lock = threading.Lock()
    worker._stop = threading.Event()
    return worker, r


def _read_frame(r):
    """从 pipe 读一帧 [tag][len][payload]。"""
    header = b""
    while len(header) < _HEADER.size:
        chunk = os.read(r, _HEADER.size - len(header))
        if not chunk:
            return None
        header += chunk
    tag, length = _HEADER.unpack(header)
    payload = b""
    while len(payload) < length:
        chunk = os.read(r, length - len(payload))
        if not chunk:
            break
        payload += chunk
    return tag, payload


# ---------------- 帧格式 ----------------

def test_send_json_frame_format():
    worker, r = _make_worker()
    worker._send_json({"ready": True, "pid": 123})
    tag, payload = _read_frame(r)
    assert tag == _TAG_JSON
    assert json.loads(payload.decode("utf-8")) == {"ready": True, "pid": 123}


def test_push_chunk_frame_format():
    worker, r = _make_worker()
    pcm = b"\x00\x00\x80\x3f" * 4  # 4 个 float32（1.0）
    worker._push_chunk(pcm)
    tag, payload = _read_frame(r)
    assert tag == _TAG_PCM
    assert payload == pcm


def test_push_event_sends_json_frame():
    worker, r = _make_worker()
    worker._push_event("overflow")
    tag, payload = _read_frame(r)
    assert tag == _TAG_JSON
    assert json.loads(payload.decode("utf-8")) == {"event": "overflow"}


# ---------------- 命令分发（_dispatch）----------------

def test_dispatch_start_calls_core_and_acks():
    worker, r = _make_worker()
    worker._dispatch({"cmd": "start", "id": 1, "device": "Mic"})
    assert worker._core.calls == [("start", "Mic")]
    tag, payload = _read_frame(r)
    assert tag == _TAG_JSON
    ack = json.loads(payload.decode("utf-8"))
    assert ack == {"ack": "start", "id": 1, "ok": True, "fell_back_to_default": False}


def test_dispatch_start_propagates_fell_back_to_default():
    worker, r = _make_worker()
    worker._core._fell_back = True
    worker._core.start_ok = True
    worker._dispatch({"cmd": "start", "id": 1, "device": "Missing"})
    ack = json.loads(_read_frame(r)[1].decode("utf-8"))
    assert ack["fell_back_to_default"] is True


def test_dispatch_start_recording_and_stop_recording():
    worker, r = _make_worker()
    worker._dispatch({"cmd": "start_recording", "id": 1})
    worker._dispatch({"cmd": "stop_recording", "id": 2})
    assert worker._core.calls == [("start_recording",), ("stop_recording",)]
    assert json.loads(_read_frame(r)[1].decode())["ok"] is True
    assert json.loads(_read_frame(r)[1].decode())["ok"] is True


def test_dispatch_query_devices_returns_devices_message():
    worker, r = _make_worker()
    worker._dispatch({"cmd": "query_devices", "id": 7})
    tag, payload = _read_frame(r)
    msg = json.loads(payload.decode("utf-8"))
    assert msg["id"] == 7
    assert msg["devices"] == worker._core.devices
    assert "ack" not in msg  # devices 是独立消息，不是 ack


def test_dispatch_ping_includes_status():
    worker, r = _make_worker()
    worker._core._is_recording = True
    worker._core._overflow = True
    worker._dispatch({"cmd": "ping", "id": 1})
    ack = json.loads(_read_frame(r)[1].decode("utf-8"))
    assert ack == {"ack": "ping", "id": 1, "ok": True, "recording": True, "overflow": True}


def test_dispatch_shutdown_sets_stop():
    worker, r = _make_worker()
    assert not worker._stop.is_set()
    worker._dispatch({"cmd": "shutdown", "id": 1})
    assert worker._stop.is_set()


def test_dispatch_unknown_cmd_acks_error():
    worker, r = _make_worker()
    worker._dispatch({"cmd": "bogus", "id": 9})
    ack = json.loads(_read_frame(r)[1].decode("utf-8"))
    assert ack["ok"] is False
    assert "unknown" in ack["err"]
    assert ack["id"] == 9


# ---------------- 命令循环（_cmd_loop 读 stdin）----------------

def test_cmd_loop_reads_lines_until_shutdown(monkeypatch):
    worker, r = _make_worker()
    lines = '{"cmd":"ping","id":1}\n{"cmd":"shutdown","id":2}\n'
    monkeypatch.setattr(awmod.sys, "stdin", io.StringIO(lines))
    worker._cmd_loop()
    # shutdown 命令设置 _stop → 循环退出
    assert worker._stop.is_set()
    # ping 的 ack 应已写入 stdout
    ack = json.loads(_read_frame(r)[1].decode("utf-8"))
    assert ack["ack"] == "ping"


def test_cmd_loop_ignores_unparseable_lines(monkeypatch):
    worker, r = _make_worker()
    lines = 'not json\n{"cmd":"ping","id":1}\n'
    monkeypatch.setattr(awmod.sys, "stdin", io.StringIO(lines))
    worker._cmd_loop()
    # 坏行被忽略，ping 的 ack 正常
    ack = json.loads(_read_frame(r)[1].decode("utf-8"))
    assert ack["ack"] == "ping"
