"""防线1：孤儿 whisper-server 回收单测。

ps 输出通过 ps_runner 注入（不依赖真实 ps）；os.kill / time.sleep 用 monkeypatch，
避免真发信号与真等待。
"""

import signal

from core import process_guard


_PROJECT_MODELS = "/Users/test/proj/models"

# 模拟 `ps -eo pid,ppid,command` 输出：
#   12345 本项目模型 + PPID=1            -> 孤儿（应回收）
#   12346 本项目模型 + PPID=500          -> 正常子进程（不误杀）
#   12347 别的项目模型 + PPID=1          -> 不是本项目的（不回收）
#   12348 whisper-server 但无本项目 -m   -> 无关进程（不回收）
_PS_STUB = """  PID  PPID COMMAND
12345     1 /opt/homebrew/bin/whisper-server -m {m}/ggml-large-v3.bin --host 0.0.0.0 --port 59999 -l zh -t 8 -p 1 -nt
12346   500 /opt/homebrew/bin/whisper-server -m {m}/ggml-large-v3.bin --host 0.0.0.0 --port 60000 -l zh
12347     1 /opt/homebrew/bin/whisper-server -m /other/proj/models/ggml-large-v3.bin --port 60001
12348     1 /opt/homebrew/bin/whisper-server --host 0.0.0.0 --port 60002
""".format(m=_PROJECT_MODELS)


def _runner(text=_PS_STUB):
    return lambda: text


def _pids(orphans):
    return [pid for pid, _ppid, _cmd in orphans]


# ---------------- find_orphans 双重判断 ----------------

def test_find_orphans_matches_project_models_dir_and_ppid1():
    assert 12345 in _pids(process_guard.find_orphans(_PROJECT_MODELS, ps_runner=_runner()))


def test_find_orphans_skips_non_orphan_child():
    # 12346 是本项目模型但 PPID=500（正常子进程），不能误杀
    assert 12346 not in _pids(process_guard.find_orphans(_PROJECT_MODELS, ps_runner=_runner()))


def test_find_orphans_skips_other_projects_server():
    # 12347 PPID=1 但模型路径属于别的项目
    assert 12347 not in _pids(process_guard.find_orphans(_PROJECT_MODELS, ps_runner=_runner()))


def test_find_orphans_skips_unrelated_whisper_server():
    # 12348 是 whisper-server 但命令行无本项目 -m 路径
    assert 12348 not in _pids(process_guard.find_orphans(_PROJECT_MODELS, ps_runner=_runner()))


def test_list_audio_worker_processes_accepts_standalone_flag():
    ps = """  PID  PPID COMMAND
12349     1 /tmp/WhisperCppCmd.app/Contents/MacOS/WhisperCppCmd --whispercpp-audio-worker --config '{}'
12350     1 /usr/bin/python3 -m core.audio_worker --config '{}'
12351     1 /usr/bin/python3 -m unrelated.worker
"""
    pids = [
        pid
        for pid, _ppid, _cmd in process_guard.list_audio_worker_processes(
            ps_runner=lambda: ps
        )
    ]
    assert pids == [12349, 12350]


# ---------------- kill_pid ----------------

def _make_fake_kill(dies_on_sigterm=True):
    """可控的 os.kill 替身，记录发送的信号序列。"""
    state = {"alive": True, "sent": []}

    def fake_kill(pid, sig):
        state["sent"].append(sig)
        if sig == 0:  # 探活
            if not state["alive"]:
                raise ProcessLookupError()
            return
        if sig == signal.SIGTERM and dies_on_sigterm:
            state["alive"] = False
        elif sig == signal.SIGKILL:
            state["alive"] = False

    return fake_kill, state


def test_kill_pid_returns_true_when_already_dead(monkeypatch):
    def _always_dead(pid, sig):
        raise ProcessLookupError()

    monkeypatch.setattr(process_guard.os, "kill", _always_dead)
    monkeypatch.setattr(process_guard.time, "sleep", lambda *_: None)
    assert process_guard.kill_pid(999999) is True


def test_kill_pid_sigterm_succeeds_without_sigkill(monkeypatch):
    fake_kill, state = _make_fake_kill(dies_on_sigterm=True)
    monkeypatch.setattr(process_guard.os, "kill", fake_kill)
    monkeypatch.setattr(process_guard.time, "sleep", lambda *_: None)
    assert process_guard.kill_pid(12345, term_wait=0.01, kill_wait=0.01) is True
    assert signal.SIGKILL not in state["sent"]


def test_kill_pid_falls_back_to_sigkill(monkeypatch):
    fake_kill, state = _make_fake_kill(dies_on_sigterm=False)  # SIGTERM 杀不掉
    monkeypatch.setattr(process_guard.os, "kill", fake_kill)
    monkeypatch.setattr(process_guard.time, "sleep", lambda *_: None)
    assert process_guard.kill_pid(12345, term_wait=0.01, kill_wait=0.01) is True
    assert signal.SIGKILL in state["sent"]


# ---------------- reclaim_orphan_servers 容错 ----------------

def test_reclaim_does_not_raise_when_kill_fails(monkeypatch):
    monkeypatch.setattr(process_guard, "kill_pid", lambda *a, **k: False)
    monkeypatch.setattr(process_guard.time, "sleep", lambda *_: None)
    result = process_guard.reclaim_orphan_servers(_PROJECT_MODELS, ps_runner=_runner())
    assert 12345 in _pids(result)
