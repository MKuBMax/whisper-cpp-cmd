"""VAD 之外的轻量空录音保护测试。"""

import numpy as np

from core.audio_quality import analyze_audio


def test_digital_silence_is_detected_without_rejecting_short_voice_like_signal():
    silent = analyze_audio(np.zeros(16_000, dtype=np.float32), 16_000)
    quiet_signal = analyze_audio(np.full(16_000, 2e-5, dtype=np.float32), 16_000)

    assert silent.is_digital_silence is True
    assert quiet_signal.is_finite is True
    assert quiet_signal.is_digital_silence is False


def test_non_finite_audio_is_invalid():
    signal = analyze_audio(np.array([0.0, np.nan, 0.0], dtype=np.float32), 16_000)

    assert signal.is_finite is False
    assert signal.is_digital_silence is False

