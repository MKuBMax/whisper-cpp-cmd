"""系统音频参考消除（reference cancel）纯函数测试。"""

import numpy as np

from core.ref_cancel import (
    align_ref,
    estimate_lag_samples,
    suppress_with_ref,
)


def _sine(freq, n, sr=16_000, amp=0.3):
    t = np.arange(n, dtype=np.float64) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_align_finds_delay():
    sr = 16_000
    music = _sine(440.0, sr, sr)
    delay = 160  # 10ms，麦克风滞后
    mic = np.concatenate([np.zeros(delay, dtype=np.float32), music])[:sr]
    lag = estimate_lag_samples(mic, music, sr, max_lag_ms=50)
    assert abs(lag - delay) <= 8


def test_estimate_uses_loudest_window():
    sr = 16_000
    delay = 160
    rng = np.random.default_rng(7)
    quiet = np.zeros(sr * 3, dtype=np.float32)
    loud = (rng.standard_normal(sr * 4).astype(np.float32)) * 0.3
    ref = np.concatenate([quiet, loud])
    mic = np.concatenate([np.zeros(delay, dtype=np.float32), ref])[: len(ref)]
    lag = estimate_lag_samples(mic, ref, sr, max_lag_ms=200)
    assert abs(lag - delay) <= 8


def test_gate_suppresses_music_only():
    sr = 16_000
    music = _sine(440.0, sr, sr, amp=0.3)
    out, stats = suppress_with_ref(music, music.copy(), sr)
    assert len(out) == len(music)
    in_rms = float(np.sqrt(np.mean(music.astype(np.float64) ** 2)))
    out_rms = float(np.sqrt(np.mean(out.astype(np.float64) ** 2)))
    assert out_rms < in_rms * 0.5
    assert stats["suppressed_ratio"] > 0.5


def test_gate_preserves_loud_speech_over_music():
    sr = 16_000
    music = _sine(440.0, sr, sr, amp=0.2)
    mic = music.copy()
    burst = _sine(880.0, sr // 3, sr, amp=0.9)
    mic[sr // 3: sr // 3 + sr // 3] += burst
    mic = np.clip(mic, -1.0, 1.0).astype(np.float32)
    out, _stats = suppress_with_ref(mic, music.copy(), sr)
    seg_in = mic[sr // 3: sr // 3 + sr // 3].astype(np.float64)
    seg_out = out[sr // 3: sr // 3 + sr // 3].astype(np.float64)
    in_rms = float(np.sqrt(np.mean(seg_in ** 2)))
    out_rms = float(np.sqrt(np.mean(seg_out ** 2)))
    assert out_rms > in_rms * 0.4


def test_empty_ref_returns_mic_unchanged():
    sr = 16_000
    mic = _sine(440.0, sr, sr)
    out, stats = suppress_with_ref(mic, np.zeros(0, dtype=np.float32), sr)
    assert np.array_equal(out, mic)
    assert stats["suppressed_ratio"] == 0.0


def test_length_mismatch_handled():
    sr = 16_000
    mic = _sine(440.0, sr, sr)
    short_ref = _sine(440.0, sr // 2, sr)
    out, _stats = suppress_with_ref(mic, short_ref, sr)
    assert len(out) == len(mic)
    assert np.all(np.isfinite(out))


def test_align_ref_puts_peak_on_mic_peak():
    sr = 16_000
    ref = np.zeros(sr, dtype=np.float32)
    ref[1000] = 1.0
    mic = np.zeros(sr, dtype=np.float32)
    mic[1160] = 1.0
    lag = estimate_lag_samples(mic, ref, sr)
    aligned = align_ref(ref, len(mic), lag)
    assert int(np.argmax(aligned)) == 1160


def test_align_ref_negative_lag():
    ref = np.zeros(100, dtype=np.float32)
    ref[50] = 1.0
    aligned = align_ref(ref, 100, -20)
    assert int(np.argmax(aligned)) == 30


def test_align_ref_output_length_matches_mic():
    ref = np.arange(100, dtype=np.float32)
    out = align_ref(ref, 60, 10)
    assert len(out) == 60
    assert out[10] == 0.0
    assert out[11] == 1.0


def test_suppress_after_real_delay():
    sr = 16_000
    music = _sine(440.0, sr, sr, amp=0.3)
    delay = 160
    mic = np.concatenate([np.zeros(delay, dtype=np.float32), music])[:sr]
    out, stats = suppress_with_ref(mic, music.copy(), sr)
    in_rms = float(np.sqrt(np.mean(mic.astype(np.float64) ** 2)))
    out_rms = float(np.sqrt(np.mean(out.astype(np.float64) ** 2)))
    assert out_rms < in_rms * 0.5
    assert stats["suppressed_ratio"] > 0.5


def test_long_audio_finishes_fast():
    import time
    sr = 16_000
    n = sr * 6
    rng = np.random.default_rng(0)
    mic = (rng.standard_normal(n).astype(np.float32)) * 0.2
    ref = (rng.standard_normal(n).astype(np.float32)) * 0.2
    start = time.time()
    out, _stats = suppress_with_ref(mic, ref, sr)
    assert time.time() - start < 0.5
    assert len(out) == n
