"""P2 RED：verbose 门限。无语音高概率且低对数概率时判 no_speech。"""
from types import SimpleNamespace

import numpy as np

from core.model import filter_hallucination


def _result(text, segments):
    return {"text": text, "segments": segments}


def test_high_no_speech_prob_is_no_speech():
    out = filter_hallucination(
        _result(
            "请不吝点赞订阅转发打赏支持明镜与点点栏目",
            [{"text": "请不吝点赞订阅转发打赏支持明镜与点点栏目",
              "no_speech_prob": 0.85, "avg_logprob": -0.9}],
        )
    )
    assert out["no_speech"] is True
    assert out["text"] == ""


def test_real_speech_passes_through():
    out = filter_hallucination(
        _result(
            "你好，这是一个测试。",
            [{"text": "你好，这是一个测试。",
              "no_speech_prob": 0.002, "avg_logprob": -0.03}],
        )
    )
    assert out["no_speech"] is False
    assert out["text"] == "你好，这是一个测试。"


def test_missing_segments_never_blocks():
    out = filter_hallucination(_result("你好", []))
    assert out["no_speech"] is False
    assert out["text"] == "你好"
