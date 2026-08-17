#!/usr/bin/env python3
"""
py2app 启动入口。

职责：
- 将 stdout/stderr 重定向到文件，保留双击启动时的调试输出
- 切换到项目根目录，复用现有相对路径逻辑
- 调用 main.main()
"""

from __future__ import annotations

import os
import sys
import traceback
import faulthandler
from datetime import datetime


PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


def _run_audio_worker() -> None:
    """standalone App 的音频 worker 入口。

    独立 App 的 sys.executable 是内部 Python 启动器，直接执行 ``-m`` 不会
    自动加载 py2app 的资源路径，因此 worker 复用这个已正确启动的 bundle。
    """
    marker = "--whispercpp-audio-worker"
    marker_index = sys.argv.index(marker)
    sys.argv = [sys.argv[0], *sys.argv[marker_index + 1:]]
    from core.audio_worker import main as worker_main

    worker_main()


def setup_stdio() -> None:
    from config.paths import ensure_runtime_dirs, logs_dir

    ensure_runtime_dirs()
    log_dir = logs_dir()
    launcher_log_path = os.path.join(log_dir, "app-launcher.log")

    stream = open(launcher_log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = stream
    sys.stderr = stream
    faulthandler.enable(file=stream, all_threads=True)

    print()
    print(f"===== {datetime.now():%Y-%m-%d %H:%M:%S} WhisperCppCmd.app launch =====")
    print(f"RESOURCE_DIR={PROJECT_DIR}")
    print(f"RUNTIME_DIR={os.path.dirname(log_dir)}")
    print(f"PYTHON_EXECUTABLE={sys.executable}")
    print(f"PID={os.getpid()}")
    print(f"PPID={os.getppid()}")
    print(f"ARGV={sys.argv}")
    print(f"PLATFORM={sys.platform}")
    print(f"PROCESS_ARCH={os.uname().machine}")


def handle_uncaught_exception(exc_type, exc_value, exc_traceback) -> None:
    traceback.print_exception(exc_type, exc_value, exc_traceback, file=sys.stderr)
    sys.stderr.flush()


def main() -> None:
    if "--whispercpp-audio-worker" in sys.argv:
        _run_audio_worker()
        return

    setup_stdio()
    sys.excepthook = handle_uncaught_exception
    from config.paths import runtime_root

    os.makedirs(runtime_root(), exist_ok=True)
    os.chdir(runtime_root())

    from main import main as run_main

    run_main()


if __name__ == "__main__":
    main()
