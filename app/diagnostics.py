#!/usr/bin/env python3
"""诊断报告工具：抓取全部线程堆栈 + 应用状态 + 最近日志，落盘供卡死分析。

手动（菜单「导出诊断报告」）与自动（watchdog）均调用 dump_report。
诊断工具本身不能因采集失败而崩溃，故各处用 try/except 兜底。
"""

import glob
import os
import sys
import threading
import time
import traceback

from config.paths import logs_dir

MAX_REPORTS = 20
LOG_TAIL_LINES = 100


def collect_report(controller, reason: str) -> str:
    """收集诊断信息为文本。内部异常兜底，保证不抛出。"""
    lines = [
        "=" * 60,
        "WhisperCppCmd 诊断报告",
        "=" * 60,
        f"时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}",
        f"触发：{reason}",
        f"pid：{os.getpid()}",
        f"Python：{sys.version.split()[0]}",
        f"线程数：{threading.active_count()}",
        "",
    ]

    _append_thread_stacks(lines)
    _append_controller_state(lines, controller)
    _append_pipeline_state(lines, controller)
    _append_recent_log(lines)

    lines += ["", "=" * 60, "报告结束", "=" * 60]
    return "\n".join(lines)


def dump_report(controller, reason: str) -> str:
    """收集报告写入 logs/diagnostic-report-<时间>.txt，轮转保留最近 MAX_REPORTS 份，返回路径。"""
    text = collect_report(controller, reason)
    log_dir = _log_dir()
    os.makedirs(log_dir, exist_ok=True)
    base = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    path = os.path.join(log_dir, f"diagnostic-report-{base}.txt")
    counter = 2
    while os.path.exists(path):
        path = os.path.join(log_dir, f"diagnostic-report-{base}-{counter}.txt")
        counter += 1
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    _rotate(log_dir)
    return path


def _append_thread_stacks(lines):
    lines += ["-" * 60, "线程堆栈(sys._current_frames)", "-" * 60]
    try:
        frames = sys._current_frames()
        names = {t.ident: t.name for t in threading.enumerate()}
        for ident, frame in frames.items():
            lines.append(f"\n>>> Thread {names.get(ident, f'Thread-{ident}')} (ident={ident})")
            lines.append("".join(traceback.format_stack(frame)).rstrip("\n"))
    except Exception as e:
        lines.append(f"收集线程堆栈失败：{e}")


def _append_controller_state(lines, controller):
    lines += ["", "-" * 60, "应用状态(controller)", "-" * 60]
    try:
        worker = getattr(controller, "_dictation_worker", None)
        queue = getattr(controller, "_dictation_queue", None)
        heartbeat = getattr(controller, "_worker_heartbeat", None)
        lines += [
            f"state：{getattr(controller, '_state', '?')}",
            f"paused：{getattr(controller, '_paused', '?')}",
            f"pipeline_transitioning：{getattr(controller, '_pipeline_transitioning', '?')}",
            f"backend_released：{getattr(controller, '_backend_released', '?')}",
            f"current_trace：{getattr(controller, '_current_trace', '?')}",
            f"active_trace：{getattr(controller, '_active_trace', '?')}",
            f"worker_alive：{worker.is_alive() if worker else None}",
            f"worker_busy：{getattr(controller, '_worker_busy', '?')}",
            f"queue_qsize：{queue.qsize() if queue else None}",
        ]
        if heartbeat is not None:
            lines.append(f"worker_heartbeat_age：{time.monotonic() - heartbeat:.1f}s")
        lines.append(f"watchdog_dumped：{getattr(controller, '_watchdog_dumped', '?')}")
    except Exception as e:
        lines.append(f"收集应用状态失败：{e}")


def _append_pipeline_state(lines, controller):
    lines += ["", "-" * 60, "流水线状态", "-" * 60]
    try:
        pipeline = getattr(controller, "pipeline", None)
        if pipeline is None:
            lines.append("pipeline：None")
            return
        lines.append(f"pipeline.get_status：{pipeline.get_status()}")
        audio = getattr(pipeline, "audio_source", None)
        if audio is not None:
            lines.append(f"audio.is_recording：{audio.is_recording}")
            lines.append(f"audio._stream is None：{getattr(audio, '_stream', None) is None}")
    except Exception as e:
        lines.append(f"收集流水线状态失败：{e}")


def _append_recent_log(lines):
    lines += ["", "-" * 60, f"最近日志(末尾 {LOG_TAIL_LINES} 行)", "-" * 60]
    try:
        path = _log_path()
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-LOG_TAIL_LINES:]
            lines.append("".join(tail).rstrip("\n"))
        else:
            lines.append(f"(未找到日志：{path})")
    except Exception as e:
        lines.append(f"读取日志失败：{e}")


def _rotate(log_dir: str):
    """保留最近 MAX_REPORTS 份诊断报告（按修改时间排序，避免同秒多份时文件名序号字典序错位）。"""
    try:
        files = glob.glob(os.path.join(log_dir, "diagnostic-report-*.txt"))
        files.sort(key=os.path.getmtime)
        for old in files[:-MAX_REPORTS]:
            os.remove(old)
    except Exception:
        pass


def _log_dir() -> str:
    return logs_dir()


def _log_path() -> str:
    return os.path.join(_log_dir(), "whisper-cpp-cmd.log")
