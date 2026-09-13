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


def test_align_negative_lag_keeps_ref_tail():
    n = 1000
    preroll = 250
    ref = np.arange(n + preroll, dtype=np.float32)
    aligned = align_ref(ref, n, -preroll)
    assert np.array_equal(aligned, ref[preroll: preroll + n])


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
    assert stats["erle_db"] > 6.0


def test_gate_preserves_loud_speech_over_music():
    sr = 16_000
    music = _sine(440.0, sr, sr, amp=0.2)
    rng = np.random.default_rng(1)
    mic = music.copy()
    burst = rng.standard_normal(sr // 3).astype(np.float32) * 0.9
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


def test_suppress_cancels_tail_when_ref_has_preroll():
    sr = 16_000
    preroll = 4_000
    music = _sine(440.0, sr, sr, amp=0.3)
    ref = np.concatenate([np.zeros(preroll, dtype=np.float32), music])
    out, stats = suppress_with_ref(music, ref, sr)
    assert abs(stats["lag"] + preroll) <= 8
    tail_in = float(np.sqrt(np.mean(music[-sr // 4 :].astype(np.float64) ** 2)))
    tail_out = float(np.sqrt(np.mean(out[-sr // 4 :].astype(np.float64) ** 2)))
    assert tail_out < tail_in * 0.5


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
    assert stats["erle_db"] > 6.0


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


def _rms(x):
    return float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2)))


def test_startup_delay_500ms_is_found():
    sr = 16_000
    delay = 8000
    rng = np.random.default_rng(3)
    voice = rng.standard_normal(sr * 3).astype(np.float32) * 0.3
    mic = np.concatenate([np.zeros(delay, dtype=np.float32), voice])[: len(voice)]
    lag = estimate_lag_samples(mic, voice, sr, max_lag_ms=600)
    assert abs(lag - delay) <= 16


def test_subtracts_delayed_speech_leak():
    sr = 16_000
    delay = 400
    rng = np.random.default_rng(4)
    src = rng.standard_normal(sr * 2).astype(np.float32) * 0.4
    mic = np.zeros_like(src)
    mic[delay:] = src[: src.size - delay] * 0.3
    out, stats = suppress_with_ref(mic, src, sr)
    assert _rms(out) < _rms(mic) * 0.4
    assert abs(stats["lag"] - delay) <= 16
    assert stats["erle_db"] > 6.0


def test_preserves_uncorrelated_near_speech():
    sr = 16_000
    rng = np.random.default_rng(5)
    speech = rng.standard_normal(sr).astype(np.float32) * 0.4
    ref = _sine(440.0, sr, sr, amp=0.2)
    mic = speech + ref * 0.15
    out, _stats = suppress_with_ref(mic, ref, sr)
    assert _rms(out) > _rms(speech) * 0.5


def test_erle_on_pure_leak_is_high():
    sr = 16_000
    rng = np.random.default_rng(9)
    src = rng.standard_normal(sr * 2).astype(np.float32) * 0.35
    delay = 240
    mic = np.zeros_like(src)
    mic[delay:] = src[: src.size - delay] * 0.2
    out, stats = suppress_with_ref(mic, src, sr)
    assert stats["erle_db"] > 10.0
    assert _rms(out) < _rms(mic) * 0.35


def test_double_talk_preserves_near_speech():
    """近端人声只比串音略响时，不能整段压掉。"""
    sr = 16_000
    rng = np.random.default_rng(11)
    far = rng.standard_normal(sr * 2).astype(np.float32) * 0.3
    near = rng.standard_normal(sr * 2).astype(np.float32) * 0.4
    delay = 200
    leak = np.zeros_like(far)
    leak[delay:] = far[: far.size - delay] * 0.25
    mic = (leak + near).astype(np.float32)
    out, _stats = suppress_with_ref(mic, far, sr)
    assert _rms(out) > _rms(near) * 0.45
