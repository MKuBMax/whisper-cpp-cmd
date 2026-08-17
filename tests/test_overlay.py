"""C2: 录音浮窗纯逻辑单测：rms_to_bar_level + 波形历史 + get_recent_rms。

窗口外观（透明/置顶/全屏可见）由用户 GUI 实测，这里只测可纯测的逻辑。
get_recent_rms 在 IPC client（core.audio_source.AudioSource）的本地 buffer 上计算，
buffer 由 reader 线程从 worker 收的 PCM 填充；这里绕过 __init__（避免 spawn worker）
直接填 buffer，单测其计算逻辑。
"""

import threading
from collections import deque

import numpy as np

from ui.overlay_window import (
    rms_to_bar_level,
    _BAR_COUNT,
    _TICK_INTERVAL,
    _WaveformView,
)
from core.audio_source import AudioSource, AudioConfig


# ---------------- rms_to_bar_level（dB 域电平映射） ----------------

def test_overlay_refreshes_at_least_60fps():
    """录制浮窗的逻辑刷新不能回退到低于 60Hz。"""
    assert _TICK_INTERVAL <= 1.0 / 60.0

def test_rms_to_bar_level_endpoints_and_clamp():
    assert rms_to_bar_level(0.0) == 0.0
    assert rms_to_bar_level(-1.0) == 0.0
    assert rms_to_bar_level(0.003) == 0.0     # 底噪(-50.5dB)在 floor 之下，归零
    assert rms_to_bar_level(0.06) == 1.0      # -24.4dB 在 ceil 之上，顶满


def test_rms_to_bar_level_speech_range():
    """按 2026-08-15 overlay tick 日志实测标定：正常说话须落在中高段（可见跳动），
    大声接近顶满——上一版线性映射正常说话趴底的问题由本测试锁住。"""
    quiet = rms_to_bar_level(0.008)   # -42dB 正常说话低段（实测）
    normal = rms_to_bar_level(0.015)  # -36.5dB 正常说话（实测）
    loud = rms_to_bar_level(0.03)     # -30.5dB 大声（实测）
    assert 0.4 < quiet < 0.7
    assert 0.6 < normal < 0.9
    assert loud > 0.8
    assert quiet < normal < loud  # 单调


def test_rms_to_bar_level_monotonic():
    levels = [rms_to_bar_level(r / 1000.0) for r in range(1, 60)]
    assert all(a <= b for a, b in zip(levels, levels[1:]))


# ---------------- _WaveformView（波形电平历史，纯逻辑部分） ----------------

def test_waveform_history_scrolls_and_caps():
    """持续高电平：历史长度恒为 _BAR_COUNT，最新帧最大、旧零帧被挤出。"""
    v = _WaveformView.alloc().init()
    assert len(v._history) == _BAR_COUNT
    for _ in range(30):  # 远超容量，验证环形队列挤出最旧帧
        v.setLevel_(1.0)
        v.updateSmooth()
    assert len(v._history) == _BAR_COUNT
    assert v._history[-1] > v._history[0]


def test_waveform_reset_clears_history():
    v = _WaveformView.alloc().init()
    for _ in range(10):
        v.setLevel_(1.0)
        v.updateSmooth()
    v.resetSmooth()
    assert list(v._history) == [0.0] * _BAR_COUNT


# ---------------- AudioSource.get_recent_rms（本地 buffer 计算）----------------

def _make_client(sample_rate: int = 16000) -> AudioSource:
    """绕过 __init__（避免 spawn worker 子进程），只初始化 get_recent_rms 需要的本地字段。"""
    src = AudioSource.__new__(AudioSource)
    src.config = AudioConfig(sample_rate=sample_rate)
    src._buffer: "deque[np.ndarray]" = deque()
    src._buffer_lock = threading.Lock()
    src._recorded_samples = 0
    return src


def test_get_recent_rms_zero_when_empty():
    src = _make_client()
    assert src.get_recent_rms() == 0.0


def test_get_recent_rms_constant_amplitude():
    src = _make_client()
    chunk = np.full(256, 0.5, dtype=np.float32)
    for _ in range(10):
        src._buffer.append(chunk)
    # 全 0.5 → RMS = 0.5
    assert abs(src.get_recent_rms(1.0) - 0.5) < 1e-5


def test_get_recent_rms_silent():
    src = _make_client()
    chunk = np.zeros(256, dtype=np.float32)
    for _ in range(10):
        src._buffer.append(chunk)
    assert src.get_recent_rms(1.0) == 0.0
