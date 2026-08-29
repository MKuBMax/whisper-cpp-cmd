#!/usr/bin/env python3
"""录音结束后的轻量音频质量判断。

这不是第二套 VAD，也不尝试判断“这段声音一定是人声”。它只负责识别
没有任何有效采样值的电气静音/损坏数据，并把真正的语音判断留给
whisper.cpp + Silero VAD。这样可以减少空请求和静音幻觉，同时不会因为
置信度或能量阈值把正常但较轻的语音结果丢掉。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


# 只拦截接近数字零的输入。普通麦克风底噪和低声量语音会继续交给模型。
DIGITAL_SILENCE_PEAK = 1e-5
DIGITAL_SILENCE_RMS = 1e-6


@dataclass(frozen=True)
class AudioSignal:
    sample_count: int
    sample_rate: int
    peak: float
    rms: float
    is_finite: bool

    @property
    def duration(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return self.sample_count / self.sample_rate

    @property
    def is_digital_silence(self) -> bool:
        return (
            self.is_finite
            and self.sample_count > 0
            and self.peak <= DIGITAL_SILENCE_PEAK
            and self.rms <= DIGITAL_SILENCE_RMS
        )


def analyze_audio(audio: np.ndarray, sample_rate: int) -> AudioSignal:
    """计算不会修改输入的基础信号统计。

    非有限值单独标记为无效；调用方应拒绝把它送入归一化或模型，
    避免 ``NaN`` 在预处理阶段被放大成不可预测的结果。
    """

    try:
        array = np.asarray(audio, dtype=np.float32)
    except (TypeError, ValueError):
        return AudioSignal(0, sample_rate, 0.0, 0.0, False)

    if array.ndim > 1:
        array = array.reshape(-1)
    else:
        array = array.ravel()

    if array.size == 0:
        return AudioSignal(0, sample_rate, 0.0, 0.0, True)

    finite = bool(np.isfinite(array).all())
    if not finite:
        return AudioSignal(int(array.size), sample_rate, float("inf"), float("inf"), False)

    absolute = np.abs(array)
    peak = float(np.max(absolute))
    rms = float(np.sqrt(np.mean(np.square(array), dtype=np.float64)))
    return AudioSignal(int(array.size), sample_rate, peak, rms, True)

