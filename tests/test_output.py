"""OutputHandler 单测：历史记录的原子写、截断、坏 JSON 容错。

core/output.py 此前零直接测试——_save_to_history 的 .tmp→fsync→os.replace
原子写、history_max_entries 截断、坏 history.json 容错都是数据丢失高风险路径。
回归 = 用户直接感知丢数据，故补回归保护。
"""

import json
import os
from datetime import datetime

from core.output import OutputConfig, OutputHandler, TextOutput


def _handler(tmp_path, **overrides) -> OutputHandler:
    """构造一个指向 tmp_path/history.json 的 handler，默认安静（verbose=False）。"""
    cfg = OutputConfig(history_file=str(tmp_path / "history.json"), verbose=False)
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return OutputHandler(cfg)


def _entry(text: str) -> TextOutput:
    return TextOutput(text=text, model="large-v3", language="zh",
                      timestamp=datetime(2026, 7, 9, 12, 0, 0), success=True)


def test_save_to_history_writes_atomically(tmp_path):
    h = _handler(tmp_path)
    h._save_to_history(_entry("你好"))

    path = str(tmp_path / "history.json")
    # 原子写：成功后无 .tmp 残留（写一半崩溃时旧文件仍完整可读）
    assert not os.path.exists(path + ".tmp")
    data = json.loads(open(path, encoding="utf-8").read())
    assert len(data) == 1
    assert data[0]["text"] == "你好"
    assert data[0]["model"] == "large-v3"


def test_history_truncated_to_max_entries(tmp_path):
    h = _handler(tmp_path, history_max_entries=3)
    for i in range(5):
        h._save_to_history(_entry(f"t{i}"))

    data = json.loads(open(str(tmp_path / "history.json"), encoding="utf-8").read())
    assert len(data) == 3                  # 超额被截断到 max
    assert [d["text"] for d in data] == ["t2", "t3", "t4"]  # 丢弃最旧，保留最新


def test_save_to_history_tolerates_corrupt_json(tmp_path):
    path = tmp_path / "history.json"
    path.write_text("{not valid json", encoding="utf-8")  # 预置损坏文件

    h = _handler(tmp_path)
    h._save_to_history(_entry("恢复"))  # 不应抛

    data = json.loads(open(str(path), encoding="utf-8").read())
    assert len(data) == 1                  # 坏 JSON 被当空 list 重置，新条目正常写入
    assert data[0]["text"] == "恢复"

def test_save_to_history_noop_without_file_path(tmp_path):
    h = OutputHandler(OutputConfig(history_file="", verbose=False))  # 空路径
    h._save_to_history(_entry("x"))       # 不应抛、不写任何文件
    assert list(tmp_path.iterdir()) == []


def test_process_persists_output_text_to_history(tmp_path):
    # 端到端：process() 的 normalize 结果应被持久化到 history（两条通路一致性）
    h = _handler(tmp_path)
    result = h.process(text="测试", model="large-v3", language="zh", success=True)

    assert result.success
    data = json.loads(open(str(tmp_path / "history.json"), encoding="utf-8").read())
    assert data[0]["text"] == result.text  # 写入的即 process 返回的（normalize 通路由 test_text_normalizer 覆盖）

def test_process_skips_history_on_failure(tmp_path):
    # 失败转写不应写入 history（process 仅 success and save_history 时存）
    h = _handler(tmp_path)
    h.process(text="", model="large-v3", language="zh", success=False, error="超时")

    assert not (tmp_path / "history.json").exists()
