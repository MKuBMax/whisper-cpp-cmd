"""audio source IPC client 单测：帧分发、命令 ack、自愈（respawn）。

绕过 __init__（不 spawn worker 子进程）+ mock _proc，纯单测 client 的 IPC 与
自愈逻辑。覆盖：_handle_frame 分发、_send_cmd 命令+ack、ack 超时/worker dead
触发 respawn、reader EOF/旧 gen/主动关闭 三态、respawn 限流与失败。
"""

import json
import logging
import os
import threading
import time
from collections import deque

import pytest

import core.audio_source as asmod
from core.audio_source import AudioSource, AudioConfig, _TAG_PCM, _TAG_JSON


def _make_client() -> AudioSource:
    """绕过 __init__（避免 spawn worker），手动初始化 client 的全部字段。"""
    c = AudioSource.__new__(AudioSource)
    c.config = AudioConfig()
    c._buffer = deque()
    c._buffer_lock = threading.Lock()
    c._recorded_samples = 0
    c._is_recording = False
    c._overflow = False
    c._fell_back_to_default = False
    c._device_name = None
    c._trace = None
    c._cached_devices = None
    c._devices_cached_at = None
    c._proc = None
    c._worker_log_file = None
    c._reader_thread = None
    c._ready = threading.Event()
    c._stop = threading.Event()
    c._pending = {}
    c._pending_lock = threading.Lock()
    c._stdin_lock = threading.Lock()
    c._next_id = 1
    c._worker_dead = False
    c._last_pcm_time = None
    c._generation = 0
    c._respawn_count = 0
    c._respawn_window_start = time.monotonic()
    c._respawn_lock = threading.Lock()
    c._worker_healthy_since = None
    c._ping_thread = None
    c._half_open_thread = None
    c._warmup_until = 0.0
    return c


class _FakeStdin:
    def __init__(self):
        self.written = []

    def write(self, b):
        self.written.append(b)

    def flush(self):
        pass


class _FakeProc:
    def __init__(self):
        self.stdin = _FakeStdin()


def _eof_proc():
    """构造一个 stdout 立即 EOF 的 _proc（读端，写端关闭）。poll 默认 None（进程仍活）。"""
    r, w = os.pipe()
    os.close(w)

    class _Stdout:
        def fileno(self):
            return r

    return type("P", (), {"stdout": _Stdout(), "poll": lambda: None})


# ---------------- _handle_frame 分发 ----------------

def test_handle_frame_pcm_appends_to_buffer():
    c = _make_client()
    pcm = b"\x00\x00\x80\x3f" * 256  # 256 float32
    c._handle_frame(_TAG_PCM, pcm)
    assert c._recorded_samples == 256
    assert len(c._buffer) == 1


def test_handle_frame_json_ready_sets_event():
    c = _make_client()
    c._handle_frame(_TAG_JSON, json.dumps({"ready": True, "pid": 123}).encode("utf-8"))
    assert c._ready.is_set()


def test_handle_frame_json_ack_dispatches_to_pending_and_pops():
    c = _make_client()
    cid = 1
    ev = threading.Event()
    box = {}
    c._pending[cid] = (ev, box)
    c._handle_frame(_TAG_JSON, json.dumps({"ack": "start", "id": cid, "ok": True}).encode("utf-8"))
    assert ev.is_set()
    assert box["resp"]["ok"] is True
    assert cid not in c._pending  # 已 pop


def test_handle_frame_overflow_event_sets_flag():
    c = _make_client()
    assert c._overflow is False
    c._handle_frame(_TAG_JSON, json.dumps({"event": "overflow"}).encode("utf-8"))
    assert c._overflow is True


# ---------------- _send_cmd 命令 + ack ----------------

def test_send_cmd_sends_command_and_returns_ack():
    c = _make_client()
    c._proc = _FakeProc()

    def respond():
        time.sleep(0.05)
        msg = json.loads(c._proc.stdin.written[0].decode("utf-8").strip())
        c._handle_frame(_TAG_JSON, json.dumps({"ack": msg["cmd"], "id": msg["id"], "ok": True}).encode("utf-8"))

    threading.Thread(target=respond, daemon=True).start()
    resp = c._send_cmd("ping", timeout=2.0)
    assert resp["ok"] is True
    sent = json.loads(c._proc.stdin.written[0].decode("utf-8").strip())
    assert sent["cmd"] == "ping"


def test_send_cmd_includes_device_when_given():
    c = _make_client()
    c._proc = _FakeProc()

    def respond():
        time.sleep(0.05)
        msg = json.loads(c._proc.stdin.written[0].decode("utf-8").strip())
        c._handle_frame(_TAG_JSON, json.dumps({"ack": "start", "id": msg["id"], "ok": True}).encode("utf-8"))

    threading.Thread(target=respond, daemon=True).start()
    c._send_cmd("start", device="Mic", timeout=2.0)
    sent = json.loads(c._proc.stdin.written[0].decode("utf-8").strip())
    assert sent["device"] == "Mic"


# ---------------- 自愈：ack 超时 / worker dead → respawn ----------------

def test_send_cmd_respawns_on_ack_timeout(monkeypatch):
    c = _make_client()
    c._proc = _FakeProc()  # 写命令但不回 ack
    respawn_calls = []
    monkeypatch.setattr(c, "_try_respawn", lambda reason: respawn_calls.append(reason) or True)
    with pytest.raises(TimeoutError):
        c._send_cmd("ping", timeout=0.3)
    assert len(respawn_calls) == 1
    assert "ack timeout" in respawn_calls[0]


def test_send_cmd_respawns_when_worker_dead_before_cmd(monkeypatch):
    c = _make_client()
    c._proc = _FakeProc()
    c._worker_dead = True  # 命令前 worker 已死
    respawn_calls = []

    def _fake_respawn(reason):
        respawn_calls.append(reason)
        c._worker_dead = False  # 模拟 respawn 成功重置
        return True

    monkeypatch.setattr(c, "_try_respawn", _fake_respawn)

    def respond():
        time.sleep(0.05)
        msg = json.loads(c._proc.stdin.written[0].decode("utf-8").strip())
        c._handle_frame(_TAG_JSON, json.dumps({"ack": msg["cmd"], "id": msg["id"], "ok": True}).encode("utf-8"))

    threading.Thread(target=respond, daemon=True).start()
    resp = c._send_cmd("ping", timeout=2.0)
    assert resp["ok"] is True
    assert len(respawn_calls) == 1
    assert "worker dead before" in respawn_calls[0]


# ---------------- 自愈：reader EOF 三态（当前 gen / 旧 gen / 主动关闭）----------------

def test_reader_loop_eof_marks_worker_dead_when_current_gen():
    c = _make_client()
    c._generation = 1
    c._proc = _eof_proc()
    c._reader_loop(gen=1)
    assert c._worker_dead is True


def test_reader_loop_old_gen_does_not_mark_dead():
    """旧代次 reader（已被 respawn 接管）静默退出，不误判卡死。"""
    c = _make_client()
    c._generation = 2  # 当前 gen=2
    c._proc = _eof_proc()
    c._reader_loop(gen=1)  # 旧 reader
    assert c._worker_dead is False


def test_reader_loop_active_stop_does_not_mark_dead():
    """主动关闭（shutdown）不算卡死。"""
    c = _make_client()
    c._generation = 1
    c._stop.set()
    c._proc = _eof_proc()
    c._reader_loop(gen=1)
    assert c._worker_dead is False


# ---------------- 自愈：respawn 限流 / 失败 ----------------

def test_try_respawn_limits_within_window(monkeypatch):
    c = _make_client()
    monkeypatch.setattr(c, "_kill_worker_process", lambda: None)
    monkeypatch.setattr(c, "_start_worker", lambda: None)
    for _ in range(asmod._RESPAWN_MAX):
        assert c._try_respawn("test") is True
    # 第 _RESPAWN_MAX+1 次拒绝，进 degraded
    assert c._try_respawn("test") is False
    assert c._worker_dead is True


def test_try_respawn_returns_false_on_start_failure(monkeypatch):
    c = _make_client()
    monkeypatch.setattr(c, "_kill_worker_process", lambda: None)

    def _fail():
        raise RuntimeError("spawn 失败")

    monkeypatch.setattr(c, "_start_worker", _fail)
    assert c._try_respawn("test") is False
    assert c._worker_dead is True


# ---------------- 虚拟设备 hang 嫌疑 ----------------

def test_virtual_device_suspect_true_when_respawned_and_virtual_present():
    c = _make_client()
    c._cached_devices = [{"name": "Oray", "is_virtual": True}]
    c._respawn_count = 1
    assert c.virtual_device_suspect is True


def test_virtual_device_suspect_false_without_respawn():
    """没 respawn 过（偶发 open 失败，非 hang）不误导提示虚拟设备。"""
    c = _make_client()
    c._cached_devices = [{"name": "Oray", "is_virtual": True}]
    c._respawn_count = 0
    assert c.virtual_device_suspect is False


def test_virtual_device_suspect_false_without_virtual_device():
    """respawn 过但无虚拟设备（其他原因卡死）不提示虚拟设备。"""
    c = _make_client()
    c._cached_devices = [{"name": "MacBook", "is_virtual": False}]
    c._respawn_count = 1
    assert c.virtual_device_suspect is False


# ---------------- 补丁1：respawn 退避 + jitter ----------------

def test_respawn_no_backoff_on_first(monkeypatch):
    """count=1（初次 respawn）不退避，偶发 transient 立即重试。"""
    c = _make_client()
    sleeps = []
    monkeypatch.setattr(asmod.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(c, "_kill_worker_process", lambda: None)
    monkeypatch.setattr(c, "_start_worker", lambda: None)
    assert c._try_respawn("test") is True
    assert sleeps == []


def test_respawn_backoff_on_second(monkeypatch):
    """count=2 退避 base*2^0*jitter。"""
    c = _make_client()
    sleeps = []
    monkeypatch.setattr(asmod.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(asmod.random, "uniform", lambda a, b: 1.0)
    monkeypatch.setattr(c, "_kill_worker_process", lambda: None)
    monkeypatch.setattr(c, "_start_worker", lambda: None)
    c._try_respawn("first")   # count=1，不 sleep
    c._try_respawn("second")  # count=2，sleep 1.0*2^0*1.0 = 1.0
    assert sleeps == [1.0]


def test_respawn_backoff_grows_exponentially(monkeypatch):
    """count=3 退避 base*2^1=2.0，相对 count=2 的 1.0 指数增长。"""
    c = _make_client()
    sleeps = []
    monkeypatch.setattr(asmod.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(asmod.random, "uniform", lambda a, b: 1.0)
    monkeypatch.setattr(c, "_kill_worker_process", lambda: None)
    monkeypatch.setattr(c, "_start_worker", lambda: None)
    c._try_respawn("first")
    c._try_respawn("second")
    c._try_respawn("third")   # count=3，sleep 1.0*2^1*1.0 = 2.0
    assert sleeps == [1.0, 2.0]


# ---------------- 补丁2：_stdin_lock + 空闲态周期 ping ----------------

def test_ping_loop_exits_on_stop():
    """_stop 已 set 时 _ping_loop 立即返回（_stop.wait 返回 True 退出循环）。"""
    c = _make_client()
    c._stop.set()
    t0 = time.monotonic()
    c._ping_loop()
    assert time.monotonic() - t0 < 0.1


def test_ping_once_skipped_when_recording():
    """录音中由 reader 的 PCM-stall 负责健康检测，ping 跳过。"""
    c = _make_client()
    c._proc = _FakeProc()
    c._is_recording = True
    c._ping_once()
    assert c._proc.stdin.written == []


def test_ping_once_skipped_when_worker_dead():
    c = _make_client()
    c._proc = _FakeProc()
    c._worker_dead = True
    c._ping_once()
    assert c._proc.stdin.written == []


def test_ping_once_never_calls_try_respawn(monkeypatch):
    """【关键不变量】ping 线程绝不 respawn，只 mark_dead（防 reader/ping 重入死锁）。"""
    c = _make_client()
    monkeypatch.setattr(asmod, "_SIMPLE_CMD_TIMEOUT", 0.2)  # 加速超时
    c._proc = _FakeProc()  # 写 ping 但不回 ack → 超时返回 None
    respawn_calls = []
    monkeypatch.setattr(c, "_try_respawn", lambda r: respawn_calls.append(r))
    c._ping_once()
    assert respawn_calls == []
    assert c._worker_dead is True


def test_ping_once_ignores_timeout_across_respawn(monkeypatch):
    """【关键防误判】ping 发出后 generation 变（respawn 发生），超时不 mark_dead。"""
    c = _make_client()
    c._proc = _FakeProc()

    def fake_send(*a, **kw):
        c._generation += 1  # 模拟 ping 发出期间 respawn 发生
        return None
    monkeypatch.setattr(c, "_send_cmd_once", fake_send)
    c._worker_dead = False
    c._ping_once()
    assert c._worker_dead is False


def test_ping_once_marks_dead_on_genuine_timeout(monkeypatch):
    """worker 真卡死（generation 未变），ping 超时 mark_dead。"""
    c = _make_client()
    monkeypatch.setattr(asmod, "_SIMPLE_CMD_TIMEOUT", 0.2)  # 加速超时
    c._proc = _FakeProc()  # 不回 ack
    c._ping_once()
    assert c._worker_dead is True


def test_concurrent_send_cmd_does_not_interleave_protocol():
    """_stdin_lock 保证并发 stdin 写入不字节交错——每条 written 是完整 JSON 行。"""
    c = _make_client()
    c._proc = _FakeProc()
    barrier = threading.Barrier(2)

    def fire(cmd):
        barrier.wait()
        c._send_cmd_once(cmd, device=None, timeout=0.01)

    threads = [threading.Thread(target=fire, args=(f"cmd{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(c._proc.stdin.written) == 2
    for b in c._proc.stdin.written:
        parsed = json.loads(b.decode("utf-8").strip())  # 字节交错则 json.loads 抛错
        assert "cmd" in parsed


def test_try_respawn_fails_all_pending_to_prevent_storm(monkeypatch):
    """【补丁2c】respawn 时立即失败所有 pending 命令，防并发 respawn 风暴
    （多线程 pending 各自超时各自 respawn 烧配额）。"""
    c = _make_client()
    monkeypatch.setattr(c, "_kill_worker_process", lambda: None)
    monkeypatch.setattr(c, "_start_worker", lambda: None)
    # 模拟另一线程有一个 pending 命令卡在等 ack
    other_ev = threading.Event()
    other_box: dict = {}
    c._pending[99] = (other_ev, other_box)
    assert c._try_respawn("test") is True
    assert other_ev.is_set()                    # 立即被失败唤醒，不必等自己的超时
    assert other_box["resp"]["ok"] is False
    assert "respawn" in other_box["resp"]["err"]
    assert 99 not in c._pending                 # 已 pop


# ---------------- 补丁3：稳定运行后重置 respawn 计数 ----------------

def test_no_reset_within_30s():
    c = _make_client()
    c._worker_healthy_since = time.monotonic()
    c._respawn_count = 2
    c._maybe_reset_respawn_count()
    assert c._respawn_count == 2


def test_reset_after_31s():
    c = _make_client()
    c._worker_healthy_since = time.monotonic() - 31.0
    c._respawn_count = 2
    c._maybe_reset_respawn_count()
    assert c._respawn_count == 0


def test_no_reset_when_count_zero():
    c = _make_client()
    c._worker_healthy_since = time.monotonic() - 100.0
    c._respawn_count = 0
    c._maybe_reset_respawn_count()
    assert c._respawn_count == 0


def test_no_reset_when_healthy_since_none():
    """_make_client 路径（_worker_healthy_since 未设）不 TypeError。"""
    c = _make_client()
    c._worker_healthy_since = None
    c._respawn_count = 2
    c._maybe_reset_respawn_count()
    assert c._respawn_count == 2


def test_send_cmd_resets_count_after_stable_period(monkeypatch):
    """_send_cmd 成功且稳定 30s+ 后重置计数。"""
    c = _make_client()
    c._proc = _FakeProc()
    c._respawn_count = 2
    c._worker_healthy_since = time.monotonic() - 31.0

    def respond():
        time.sleep(0.05)
        msg = json.loads(c._proc.stdin.written[0].decode("utf-8").strip())
        c._handle_frame(_TAG_JSON, json.dumps({"ack": msg["cmd"], "id": msg["id"], "ok": True}).encode("utf-8"))

    threading.Thread(target=respond, daemon=True).start()
    c._send_cmd("ping", timeout=2.0)
    assert c._respawn_count == 0


# ---------------- 方向2：degraded → HALF_OPEN 自动恢复 ----------------

def test_half_open_once_recovers_degraded(monkeypatch):
    """HALF_OPEN 单次探测 spawn 成功 → 退出 degraded（_worker_dead=False, 计数重置）。"""
    c = _make_client()
    c._worker_dead = True
    c._respawn_count = 5

    def fake_start():
        c._worker_dead = False  # 模拟真实 _start_worker 内部置 False
    monkeypatch.setattr(c, "_kill_worker_process", lambda: None)
    monkeypatch.setattr(c, "_start_worker", fake_start)
    assert c._half_open_once() is True
    assert c._worker_dead is False
    assert c._respawn_count == 0


def test_half_open_once_keeps_degraded_on_spawn_fail(monkeypatch):
    """HALF_OPEN spawn 失败 → 保持 degraded，下个周期再试。"""
    c = _make_client()
    c._worker_dead = True
    c._respawn_count = 5
    monkeypatch.setattr(c, "_kill_worker_process", lambda: None)

    def fail():
        raise RuntimeError("spawn 失败")
    monkeypatch.setattr(c, "_start_worker", fail)
    assert c._half_open_once() is False
    assert c._worker_dead is True


def test_half_open_loop_exits_on_stop():
    """_stop 已 set 时 _half_open_loop 立即返回。"""
    c = _make_client()
    c._stop.set()
    t0 = time.monotonic()
    c._half_open_loop()
    assert time.monotonic() - t0 < 0.1


def test_try_respawn_starts_half_open_on_degraded(monkeypatch):
    """respawn 耗尽进 degraded 时启动 HALF_OPEN 探测（不必须重启 App）。"""
    c = _make_client()
    monkeypatch.setattr(asmod.time, "sleep", lambda s: None)  # 跳过退避 sleep
    monkeypatch.setattr(asmod.random, "uniform", lambda a, b: 1.0)
    monkeypatch.setattr(c, "_kill_worker_process", lambda: None)
    monkeypatch.setattr(c, "_start_worker", lambda: None)
    started = []
    monkeypatch.setattr(c, "_start_half_open_probe", lambda: started.append(1))
    for _ in range(asmod._RESPAWN_MAX):
        c._try_respawn("test")
    assert c._try_respawn("test") is False  # 第 4 次超限进 degraded
    assert c._worker_dead is True
    assert started  # HALF_OPEN 探测已启动


# ---------------- 方向3b：warmup（新 worker 启动宽限）----------------

def test_ping_skipped_during_warmup():
    """warmup 期内 ping 跳过，给新 worker 慢启动宽限。"""
    c = _make_client()
    c._proc = _FakeProc()
    c._warmup_until = time.monotonic() + 10.0  # warmup 中
    c._ping_once()
    assert c._proc.stdin.written == []  # 没发 ping


def test_warmup_cleared_on_first_pcm():
    """首帧 PCM 到达提前结束 warmup。"""
    c = _make_client()
    c._warmup_until = time.monotonic() + 10.0
    c._handle_frame(_TAG_PCM, b"\x00\x00\x80\x3f" * 256)
    assert c._warmup_until == 0.0


# ---------------- 方向3：可观测性——crash/hang 区分 ----------------

def test_reader_eof_classifies_crash(caplog):
    """worker 异常退出（exit!=0，如 SIGSEGV）→ reason 标 crash，提示查崩溃报告。"""
    c = _make_client()
    c._generation = 1
    proc = _eof_proc()
    proc.poll = lambda: -11  # SIGSEGV
    c._proc = proc
    with caplog.at_level(logging.WARNING, logger="core.audio_source"):
        c._reader_loop(gen=1)
    assert c._worker_dead is True
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "崩溃" in msgs
    assert "exit=-11" in msgs


def test_reader_eof_classifies_clean_exit(caplog):
    """worker exit=0（未 shutdown 正常退出）→ 单独分类，不与崩溃混淆。"""
    c = _make_client()
    c._generation = 1
    proc = _eof_proc()
    proc.poll = lambda: 0
    c._proc = proc
    with caplog.at_level(logging.WARNING, logger="core.audio_source"):
        c._reader_loop(gen=1)
    assert c._worker_dead is True
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "正常退出" in msgs


def test_reader_eof_classifies_hang_when_process_alive(caplog):
    """worker 进程仍活（poll=None）但 stdout 断 → 卡死（非崩溃）。"""
    c = _make_client()
    c._generation = 1
    proc = _eof_proc()  # poll 默认 None
    c._proc = proc
    with caplog.at_level(logging.WARNING, logger="core.audio_source"):
        c._reader_loop(gen=1)
    assert c._worker_dead is True
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "进程仍活" in msgs


# ---------------- PCM-stall 录音态卡死检测 ----------------
# reader_loop 在录音态靠 _last_pcm_time 停滞 > _PCM_STALL_TIMEOUT(2.5s) 判定 worker 回调
# 卡死。ping 路径有 4 个用例覆盖空闲态，但 PCM-stall（录音态唯一卡死检测）此前零覆盖——
# 回归 = 录音卡死漏报。select.select 用 monkeypatch 替换为「立即返回空 + 微限速」，
# 模拟「1s 无 PCM 数据」触发 stall 分支，避免真等 select 超时。


def _stall_client(monkeypatch, *, is_recording, last_pcm_offset):
    """构造 stall 测试用 client：select 永远返回空（无 PCM 流），fd 取自空 pipe 读端。"""
    c = _make_client()
    r, w = os.pipe()

    class _Stdout:
        def fileno(self_):
            return r

    c._proc = type("P", (), {"stdout": _Stdout(), "poll": lambda: None})()
    # time.sleep 微限速避免空转吃满 CPU；None or ([],[],[]) → ([],[],[])
    monkeypatch.setattr(asmod.select, "select",
                        lambda *a, **k: (time.sleep(0.002) or ([], [], [])))
    c._is_recording = is_recording
    c._last_pcm_time = (None if last_pcm_offset is None
                        else time.monotonic() - last_pcm_offset)
    c._generation = 0
    return c, (r, w)


def _run_reader_briefly(c, fds, expect_dead, settle=0.2):
    """跑 reader_loop 一小段，断言 _worker_dead 状态后清理线程与 fd。"""
    t = threading.Thread(target=c._reader_loop, args=(0,), daemon=True)
    t.start()
    try:
        deadline = time.monotonic() + settle
        while time.monotonic() < deadline:
            if c._worker_dead:
                break
            time.sleep(0.005)
        assert c._worker_dead is expect_dead
    finally:
        c._stop.set()
        for _fd in fds:
            try:
                os.close(_fd)
            except OSError:
                pass
        t.join(timeout=2.0)


def test_pcm_stall_marks_worker_dead_when_recording(monkeypatch):
    """录音中 PCM 停滞 > 2.5s → _mark_worker_dead('pcm stall')（自愈闭环关键路径）。"""
    c, fds = _stall_client(monkeypatch, is_recording=True, last_pcm_offset=3.0)
    _run_reader_briefly(c, fds, expect_dead=True)


def test_no_pcm_stall_when_not_recording(monkeypatch):
    """空闲态（非录音）即便 _last_pcm_time 很久远也不触发 stall（条件短路，防误杀）。"""
    c, fds = _stall_client(monkeypatch, is_recording=False, last_pcm_offset=100.0)
    _run_reader_briefly(c, fds, expect_dead=False)


def test_no_pcm_stall_when_recording_but_never_received_pcm(monkeypatch):
    """录音中但从未收到首帧（_last_pcm_time=None）不触发 stall——避免启动竞态误杀。"""
    c, fds = _stall_client(monkeypatch, is_recording=True, last_pcm_offset=None)
    _run_reader_briefly(c, fds, expect_dead=False)
