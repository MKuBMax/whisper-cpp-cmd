#!/usr/bin/env python3
"""
孤儿 whisper-server 进程回收（防线1）。

app 异常退出（Cmd+Q 走 NSApplication 默认流程 / 崩溃 / kill -9）时，whisper-server
子进程会变成孤儿（PPID 被 launchd 收养为 1），每份模型泄漏 ~3GB。本模块在 app 启动
早期扫描并回收这些残留——这是唯一能救 kill -9 / 崩溃 / 首次部署前遗留的方式。

匹配策略（双重判断，安全优先）：
- 命令行 -m 含本项目 models 目录的绝对路径（强项目归属证据，排除别人跑别的模型）
- PPID == 1（已被 launchd 收养 = 真孤儿；本项目正常运行时 server 的 PPID 是 app pid，
  绝不可能是 1，故该条件天然排除误杀当前实例的子进程）

纯函数 + ps_runner 依赖注入，便于不依赖真实 ps 的单测。
"""

import logging
import os
import signal
import subprocess
import time
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _default_ps_runner() -> str:
    """跑 ps 取全部进程的 pid/ppid/command。"""
    return subprocess.run(
        ["ps", "-eo", "pid,ppid,command"],
        capture_output=True,
        text=True,
        encoding="utf-8",  # py2app bundle 下默认 locale 可能是 ascii，ps 输出含中文 command 会解码崩溃
        errors="replace",
        timeout=5,
    ).stdout


def list_whisper_server_processes(
    ps_runner: Optional[Callable[[], str]] = None,
) -> List[Tuple[int, int, str]]:
    """返回 [(pid, ppid, cmdline), ...]，仅含 whisper-server 进程。

    ps_runner 可注入测试 stub（返回多行 ps 文本）；生产传 None 用真实 ps。
    """
    out = (ps_runner or _default_ps_runner)()
    results: List[Tuple[int, int, str]] = []
    for line in out.splitlines()[1:]:  # 跳过表头
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        cmdline = parts[2]
        if "whisper-server" in cmdline:
            results.append((pid, ppid, cmdline))
    return results


def find_orphans(
    models_dir_abs: str,
    ps_runner: Optional[Callable[[], str]] = None,
) -> List[Tuple[int, int, str]]:
    """双重判断：cmdline 含本项目 models 路径 且 PPID==1。"""
    marker = os.path.abspath(models_dir_abs)
    return [
        (pid, ppid, cmdline)
        for pid, ppid, cmdline in list_whisper_server_processes(ps_runner)
        if marker in cmdline and ppid == 1
    ]


def _is_alive(pid: int) -> bool:
    """进程是否存活（os.kill(pid, 0) 不抛即存活）。"""
    try:
        os.kill(pid, 0)
        return True
    except OSError:  # 含 ProcessLookupError（已不存在）
        return False


def _wait_until_dead(pid: int, timeout: float) -> bool:
    """轮询探活，进程消失返回 True，超时仍存活返回 False。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _is_alive(pid):
            return True
        time.sleep(0.1)
    return not _is_alive(pid)


def kill_pid(pid: int, term_wait: float = 3.0, kill_wait: float = 2.0) -> bool:
    """终止进程：SIGTERM → 探活 → 必要时 SIGKILL。进程消失返回 True。

    ProcessLookupError（进程已不存在）视为已清理成功。
    """
    if not _is_alive(pid):
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError:
        return False

    if _wait_until_dead(pid, term_wait):
        return True

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except OSError:
        pass

    return _wait_until_dead(pid, kill_wait)


def reclaim_orphan_servers(
    models_dir_abs: str,
    ps_runner: Optional[Callable[[], str]] = None,
) -> List[Tuple[int, int, str]]:
    """启动期自愈：扫描并回收本项目遗留的孤儿 whisper-server。

    main() 在创建 VoiceInputApp 之前调用。失败仅记日志不抛（防线2 仍会兜住后续）。
    返回回收到的孤儿列表。
    """
    orphans = find_orphans(models_dir_abs, ps_runner)
    for pid, _ppid, cmdline in orphans:
        logger.warning("发现孤儿 whisper-server：pid=%s cmdline=%s", pid, cmdline)
        if kill_pid(pid):
            logger.info("已回收孤儿 whisper-server：pid=%s", pid)
        else:
            logger.error("回收孤儿 whisper-server 失败：pid=%s（需手动 kill）", pid)
    return orphans


# ---------------------------------------------------------------------------
# audio worker 子进程回收（与 reclaim_orphan_servers 同构）。
# audio worker = `python -m core.audio_worker`（音频采集子进程，见 core/audio_source.py）。
# 正常退出时 worker 靠 stdin EOF 自杀；但 worker 卡在 native Pa_* 调用（PortAudio #367）
# 时不读 stdin，主进程被 kill -9 后它会残留为孤儿，占着麦克风/AudioUnit。启动期扫一次清掉。
# ---------------------------------------------------------------------------
_AUDIO_WORKER_MARKER = "core.audio_worker"
_AUDIO_WORKER_FLAG = "--whispercpp-audio-worker"


def list_audio_worker_processes(
    ps_runner: Optional[Callable[[], str]] = None,
) -> List[Tuple[int, int, str]]:
    """返回 [(pid, ppid, cmdline), ...]，仅含本项目的 audio worker 子进程。"""
    out = (ps_runner or _default_ps_runner)()
    results: List[Tuple[int, int, str]] = []
    for line in out.splitlines()[1:]:  # 跳过表头
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        cmdline = parts[2]
        if (
            (_AUDIO_WORKER_MARKER in cmdline and " -m " in cmdline)
            or _AUDIO_WORKER_FLAG in cmdline
        ):
            results.append((pid, ppid, cmdline))
    return results


def find_audio_worker_orphans(
    ps_runner: Optional[Callable[[], str]] = None,
) -> List[Tuple[int, int, str]]:
    """audio worker 孤儿：PPID==1（已被 launchd 收养 = 主进程已死）。"""
    return [
        (pid, ppid, cmdline)
        for pid, ppid, cmdline in list_audio_worker_processes(ps_runner)
        if ppid == 1
    ]


def reclaim_audio_workers(
    ps_runner: Optional[Callable[[], str]] = None,
) -> List[Tuple[int, int, str]]:
    """启动期自愈：扫描并回收遗留的孤儿 audio worker。失败仅记日志不抛。"""
    orphans = find_audio_worker_orphans(ps_runner)
    for pid, _ppid, cmdline in orphans:
        logger.warning("发现孤儿 audio worker：pid=%s cmdline=%s", pid, cmdline)
        if kill_pid(pid):
            logger.info("已回收孤儿 audio worker：pid=%s", pid)
        else:
            logger.error("回收孤儿 audio worker 失败：pid=%s（需手动 kill）", pid)
    return orphans
