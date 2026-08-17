#!/usr/bin/env python3
"""
结构化性能日志（JSONL）：每次听写追加一行，作为延迟/RTF 度量基线。

追加写、不轮转——logs/ 已 gitignore，体积增长很慢（~150B/次），
需要时可手动清空 perf.jsonl。
"""

import json
import os
import threading

_lock = threading.Lock()


def append_perf_log(path: str, record: dict) -> None:
    """追加一行 JSON 到 perf 日志（线程安全）。"""
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _lock:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
