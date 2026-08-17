#!/usr/bin/env python3
"""
语音输入工具 - 启动入口
"""

import atexit
import logging
import os
import signal
import sys
from logging.handlers import RotatingFileHandler

from app.controller import VoiceInputApp
from config.paths import ensure_runtime_dirs, logs_dir


def setup_logging():
    """初始化文件日志，便于排查卡死和后端异常"""
    ensure_runtime_dirs()
    log_dir = logs_dir()
    log_path = os.path.join(log_dir, "whisper-cpp-cmd.log")

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    for handler in root_logger.handlers:
        if isinstance(handler, RotatingFileHandler) and getattr(handler, "baseFilename", "") == log_path:
            return

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s"
        )
    )
    root_logger.addHandler(file_handler)
    root_logger.info("日志系统初始化完成：path=%s pid=%s", log_path, os.getpid())


_EXIT_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


def install_exit_signal_handlers(app):
    """注册退出信号处理：SIGINT/SIGTERM/SIGHUP 都触发 graceful shutdown。

    确保 kill、终端关闭等场景下 whisper-server 子进程被清理，避免泄漏
    （此前只处理 SIGINT；kill 默认发的 SIGTERM 会直接终止而不清理子进程）。
    """
    logger = logging.getLogger(__name__)

    def handler(sig, frame):
        logger.info("收到信号准备退出：sig=%s", sig)
        app.shutdown()
        sys.exit(0)

    for sig in _EXIT_SIGNALS:
        signal.signal(sig, handler)


def main():
    """主函数"""
    setup_logging()

    # 防线1：建 app 前先回收上次崩溃/kill -9 遗留的孤儿 whisper-server（~3GB/个）
    from config.settings import Settings
    from core import process_guard
    process_guard.reclaim_orphan_servers(os.path.abspath(Settings.load().models_dir))
    # 同理回收遗留的孤儿 audio worker（卡在 native Pa_* 的采集子进程，占着麦克风）
    process_guard.reclaim_audio_workers()

    app = VoiceInputApp()
    install_exit_signal_handlers(app)
    # 防线2a：Python 退出兜底清理（救普通/异常退出；signal 与 NSApplicationDelegate 另有入口）
    atexit.register(app.shutdown)
    app.run()


if __name__ == "__main__":
    main()
