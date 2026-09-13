#!/usr/bin/env python3
"""系统音频参考消除：对齐后做频域维纳相减，再对回声主导帧压残差。

位置：录音结束、峰值归一化之前。16kHz 单声道 float32。
流程：互相关估时延 → 对齐 → STFT 平滑维纳滤波 → 高相干帧非线性压残差。
人声与参考不相干，作为残差留下；视频/音乐串音相干，被减掉。

失败一律回退原声。调用方在 normalize 之前调用。
"""

from __future__ import annotations

import logging
import time as _time

import numpy as np

logger = logging.getLogger(__name__)

TARGET_SR = 16_000
FRAME_MS = 20
MAX_LAG_MS = 500
ESTIMATE_SECONDS = 2.0
_MIN_REF_RMS = 1e-4

_N_FFT = 512
_HOP = 256
_SMOOTH = 0.85
_H_MAX = 8.0
_COH_BIN = 0.2
_NLP_COH = 0.6
_NLP_GAIN = 0.03


def estimate_lag_samples(mic: np.ndarray, ref: np.ndarray, sr: int,
                         max_lag_ms: int = MAX_LAG_MS) -> int:
    """互相关估计麦克风相对参考的滞后采样数。正值表示麦克风滞后，需把参考右移对齐。

    用参考能量最高的 ESTIMATE_SECONDS 窗口估计。时延在预热后的常驻采集里
    主要是扬声器到麦的声学延迟，搜索 500ms 足够覆盖预滚和少量时钟差。
    """
    m = np.asarray(mic, dtype=np.float64).ravel()
    r = np.asarray(ref, dtype=np.float64).ravel()
    n = min(m.size, r.size)
    if n < int(sr * 0.05):
        return 0
    win = int(sr * ESTIMATE_SECONDS)
    if n > win:
        ref_f = r[:n].astype(np.float32)
        frame = max(1, int(sr * FRAME_MS / 1000))
        best_start, best_energy = 0, -1.0
        for start in range(0, n - win + 1, frame):
            seg = ref_f[start: start + win].astype(np.float64)
            energy = float(np.dot(seg, seg))
            if energy > best_energy:
                best_energy = energy
                best_start = start
        m = m[best_start: best_start + win]
        r = r[best_start: best_start + win]
        n = min(m.size, r.size)
    if n < int(sr * 0.05):
        return 0
    m = m[:n] - m[:n].mean()
    r = r[:n] - r[:n].mean()
    if float(np.dot(m, m)) < 1e-12 or float(np.dot(r, r)) < 1e-12:
        return 0
    max_lag = max(1, min(n - 1, int(sr * max_lag_ms / 1000)))
    corr = np.correlate(m, r, mode="full")
    center = n - 1
    lo = max(0, center - max_lag)
    hi = min(corr.size, center + max_lag + 1)
    peak = int(np.argmax(corr[lo:hi])) + lo
    return int(peak - center)


def align_ref(ref: np.ndarray, n: int, lag: int) -> np.ndarray:
    """按时延把参考对齐到麦克风长度。lag>0 表示麦克风滞后，参考右移 lag；超界补零，不插值。"""
    out = np.zeros(n, dtype=np.float32)
    r = np.asarray(ref, dtype=np.float32).ravel()
    if r.size == 0 or n <= 0:
        return out
    if lag >= 0:
        if lag < n:
            seg = r[: n - lag]
            out[lag: lag + seg.size] = seg
    else:
        seg = r[-lag: n] if -lag < n else r[:0]
        out[: seg.size] = seg
    return out


def _stft_cancel(mic: np.ndarray, ref: np.ndarray) -> tuple:
    """重叠相加的平滑维纳滤波。高相干帧再压残差，避免 Whisper 捡到漏音。"""
    n = mic.size
    window = np.hanning(_N_FFT).astype(np.float64)
    out = np.zeros(n + _N_FFT, dtype=np.float64)
    wsum = np.zeros(n + _N_FFT, dtype=np.float64)
    srr = smm = smr = None
    echo_frames = 0
    total = 0
    min_rr = (_MIN_REF_RMS ** 2) * _N_FFT
    for start in range(0, n, _HOP):
        take = min(_N_FFT, n - start)
        m_f = np.zeros(_N_FFT, dtype=np.float64)
        r_f = np.zeros(_N_FFT, dtype=np.float64)
        m_f[:take] = mic[start: start + take]
        r_f[:take] = ref[start: start + take]
        m_f *= window
        r_f *= window
        M = np.fft.rfft(m_f)
        R = np.fft.rfft(r_f)
        cur_rr = np.abs(R) ** 2
        cur_mm = np.abs(M) ** 2
        cur_mr = M * np.conj(R)
        ref_active = float(cur_rr.mean()) > min_rr / _N_FFT
        if srr is None:
            srr = cur_rr.copy()
            smm = cur_mm.copy()
            smr = cur_mr.copy()
        elif ref_active:
            a = _SMOOTH
            srr = a * srr + (1.0 - a) * cur_rr
            smm = a * smm + (1.0 - a) * cur_mm
            smr = a * smr + (1.0 - a) * cur_mr
        H = smr / (srr + 1e-8)
        mag = np.abs(H)
        over = mag > _H_MAX
        if np.any(over):
            H = np.where(over, H * (_H_MAX / (mag + 1e-12)), H)
        E = M - H * R
        coh = np.clip(np.abs(smr) ** 2 / (smm * srr + 1e-8), 0.0, 1.0)
        E *= 1.0 - 0.9 * np.maximum(coh - _COH_BIN, 0.0) / max(1.0 - _COH_BIN, 1e-6)
        total += 1
        if ref_active:
            thr = max(float(np.max(cur_rr)) * 0.05, 1e-10)
            strong = cur_rr > thr
            mean_coh = float(np.mean(coh[strong])) if np.any(strong) else 0.0
            if mean_coh >= _NLP_COH:
                echo = np.fft.irfft(H * R, n=_N_FFT)
                echo_rms = float(np.sqrt(np.mean((echo * window) ** 2)))
                near_rms = float(np.sqrt(np.mean(m_f ** 2)))
                if near_rms <= 2.5 * echo_rms + 1e-6:
                    E *= _NLP_GAIN
                    echo_frames += 1
        e = np.fft.irfft(E, n=_N_FFT) * window
        out[start: start + _N_FFT] += e
        wsum[start: start + _N_FFT] += window ** 2
    wsum = np.maximum(wsum, 1e-8)
    y = (out / wsum)[:n].astype(np.float32)
    ratio = (echo_frames / total) if total else 0.0
    return y, ratio


def _rms(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2)))


def suppress_with_ref(mic: np.ndarray, ref: np.ndarray,
                      sr: int = TARGET_SR) -> tuple:
    """对齐后频域相减。返回 (output, stats)。
    stats 含 suppressed_ratio、lag、ref_rms、erle_db，供日志诊断。
    """
    empty = {"suppressed_ratio": 0.0, "lag": 0, "ref_rms": 0.0, "erle_db": 0.0, "gain": 0.0}
    try:
        m = np.asarray(mic, dtype=np.float32).ravel()
    except (TypeError, ValueError):
        return mic, empty
    if m.size == 0 or not bool(np.isfinite(m).all()):
        return m, empty
    try:
        r = np.asarray(ref, dtype=np.float32).ravel()
    except (TypeError, ValueError):
        return m, empty
    if r.size == 0 or not bool(np.isfinite(r).all()):
        logger.info("ref cancel skipped: ref empty or non-finite")
        return m, empty
    n = m.size
    raw_ref_n = r.size
    r = r[:n] if r.size > n else np.pad(r, (0, n - r.size))
    valid_n = min(raw_ref_n, n)
    ref_rms_all = _rms(r[:valid_n]) if valid_n else 0.0
    if ref_rms_all < _MIN_REF_RMS:
        logger.info("ref cancel skipped: ref_rms=%.6f below %.1e", ref_rms_all, _MIN_REF_RMS)
        return m, {**empty, "ref_rms": ref_rms_all}
    cancel_start = _time.time()
    lag = estimate_lag_samples(m, r, sr)
    aligned = align_ref(r, n, lag)
    out, ratio = _stft_cancel(m.astype(np.float64), aligned.astype(np.float64))
    mic_rms = _rms(m)
    out_rms = _rms(out)
    erle = 10.0 * np.log10((mic_rms ** 2) / (out_rms ** 2 + 1e-12)) if mic_rms > 0 else 0.0
    stats = {
        "suppressed_ratio": ratio,
        "lag": lag,
        "ref_rms": ref_rms_all,
        "erle_db": float(erle),
        "gain": 0.0,
        "residual_rms": out_rms,
    }
    logger.info(
        "ref cancel：lag=%d (%.0fms) ref_rms=%.5f erle=%.1fdB residual_rms=%.5f nlp=%.2f elapsed=%.2fs",
        lag, 1000.0 * lag / sr if sr else 0.0, ref_rms_all, erle, out_rms, ratio,
        _time.time() - cancel_start,
    )
    return out, stats
