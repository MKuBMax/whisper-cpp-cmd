"""A6: 录音时长硬上限单测。

通过模拟 sounddevice 回调验证：超过 max_recording_seconds 后停止推送 chunk 并置 overflow，
无需真实音频设备。

注：采集内核已隔离进 core.audio_worker 子进程，本测试直接测其 _AudioCore 内核类。
内核回调不存本地 buffer，而是经 on_chunk 把 PCM 推给主进程；测试用收集器接住 chunk。
"""

import numpy as np

from core.audio_source import AudioConfig
from core.audio_worker import _AudioCore


def _make_core(**cfg) -> tuple:
    """构造 _AudioCore 并挂上 chunk 收集器，返回 (core, collected_chunks)。"""
    core = _AudioCore(AudioConfig(**cfg))
    chunks: list = []
    core.on_chunk = lambda b: chunks.append(np.frombuffer(b, dtype=np.float32))
    return core, chunks


def _feed(core, n_blocks, block=256):
    chunk = np.ones((block, 1), dtype=np.float32)
    for _ in range(n_blocks):
        core._on_callback(chunk, block, None, None)


def test_recording_caps_at_max_duration():
    # 0.5s 上限 @16kHz = 8000 样本，每块 256
    core, chunks = _make_core(sample_rate=16000, block_size=256, max_recording_seconds=0.5)
    core.start_recording()
    _feed(core, 100)  # 故意远超

    total = sum(len(c) for c in chunks)
    # 推送的样本不超过上限（允许最后一次追加略超一个 block）
    assert total <= int(0.5 * 16000) + 256
    assert core.overflow is True


def test_recording_under_cap_no_overflow():
    core, chunks = _make_core(sample_rate=16000, block_size=256, max_recording_seconds=1.0)
    core.start_recording()
    _feed(core, 10)  # 2560 样本 < 16000

    assert core.overflow is False
    assert sum(len(c) for c in chunks) == 2560


def test_no_cap_when_disabled():
    core, chunks = _make_core(sample_rate=16000, block_size=256, max_recording_seconds=0)
    core.start_recording()
    _feed(core, 100)  # 25600 样本

    assert core.overflow is False
    assert sum(len(c) for c in chunks) == 25600


def test_overflow_resets_on_next_recording():
    core, chunks = _make_core(sample_rate=16000, block_size=256, max_recording_seconds=0.5)
    core.start_recording()
    _feed(core, 100)
    assert core.overflow is True

    # 再次录音应重置
    core.start_recording()
    assert core.overflow is False
    assert core._recorded_samples == 0
