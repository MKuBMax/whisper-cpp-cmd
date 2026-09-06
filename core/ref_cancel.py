#!/usr/bin/env python3
"""系统音频参考消除：用一路干净音乐参考压制麦克风里的扬声器串音。

位置：录音结束、转写之前，对 16kHz 单声道 float32 做块级门控。
不是逐采样 AEC：先估计时延做粗对齐，再按块对比麦克风和参考的能量，
只在参考主导的块上压制，人声盖过音乐的块原样保留。

失败一律回退原声：参考缺失、长度异常、非有限值、对齐失败都不改麦克风。
调用方（pipeline）在 processed_audio 之后、transcribe 之前调用。
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

TARGET_SR = 16_000
FRAME_MS = 20
MAX_LAG_MS = 200
GATE_DB = 6.0
FLOOR_GAIN = 0.15
_MIN_REF_RMS = 1e-4


def resample_mono(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """线性重采样到目标采样率。整数比时用均值下采样保能量。"""
    arr = np.asarray(x, dtype=np.float32).ravel()
    if arr.size == 0 or int(sr_in) == int(sr_out):
        return arr.astype(np.float32, copy=False)
    ratio = float(sr_out) / float(sr_in)
    n_out = max(1, int(round(arr.size * ratio)))
    if int(sr_in) % int(sr_out) == 0:
        step = int(sr_in) // int(sr_out)
        trimmed = arr[: (arr.size // step) * step].reshape(-1, step)
        return trimmed.mean(axis=1).astype(np.float32)
    idx = np.linspace(0, arr.size - 1, n_out)
    lo = np.floor(idx).astype(np.int64)
    hi = np.minimum(lo + 1, arr.size - 1)
    frac = (idx - lo).astype(np.float32)
    return ((1.0 - frac) * arr[lo] + frac * arr[hi]).astype(np.float32)


def estimate_lag_samples(mic: np.ndarray, ref: np.ndarray, sr: int,
                         max_lag_ms: int = MAX_LAG_MS) -> int:
    """互相关估计麦克风相对参考的滞后采样数。正值表示麦克风滞后，需把参考右移对齐。"""
    m = np.asarray(mic, dtype=np.float64).ravel()
    r = np.asarray(ref, dtype=np.float64).ravel()
    n = min(m.size, r.size)
    if n < int(sr * 0.05):
        return 0
    m = m[:n] - m[:n].mean()
    r = r[:n] - r[:n].mean()
    if float(np.dot(m, m)) < 1e-12 or float(np.dot(r, r)) < 1e-12:
        return 0
    max_lag = max(1, int(sr * max_lag_ms / 1000))
    corr = np.correlate(m, r, mode="full")
    center = n - 1
    lo = max(0, center - max_lag)
    hi = min(corr.size, center + max_lag + 1)
    peak = int(np.argmax(corr[lo:hi])) + lo
    lag = center - peak
    return int(-lag)


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


def suppress_with_ref(mic: np.ndarray, ref: np.ndarray,
                      sr: int = TARGET_SR) -> tuple:
    """块级门控：参考主导块压到 FLOOR_GAIN，人声块保留。

    返回 (output, stats)。stats 含 suppressed_ratio 和 lag，供日志诊断。
    任何异常输入返回原声，suppressed_ratio 为 0。
    """
    try:
        m = np.asarray(mic, dtype=np.float32).ravel()
    except (TypeError, ValueError):
        return mic, {"suppressed_ratio": 0.0, "lag": 0}
    if m.size == 0 or not bool(np.isfinite(m).all()):
        return m, {"suppressed_ratio": 0.0, "lag": 0}
    try:
        r = np.asarray(ref, dtype=np.float32).ravel()
    except (TypeError, ValueError):
        return m, {"suppressed_ratio": 0.0, "lag": 0}
    if r.size == 0 or not bool(np.isfinite(r).all()):
        return m, {"suppressed_ratio": 0.0, "lag": 0}
    n = m.size
    r = r[:n] if r.size > n else np.pad(r, (0, n - r.size))
    ref_rms_all = float(np.sqrt(np.mean(r.astype(np.float64) ** 2)))
    if ref_rms_all < _MIN_REF_RMS:
        return m, {"suppressed_ratio": 0.0, "lag": 0}
    lag = estimate_lag_samples(m, r, sr)
    aligned = align_ref(r, n, lag)
    frame = max(1, int(sr * FRAME_MS / 1000))
    gate = 10.0 ** (GATE_DB / 20.0)
    out = m.copy()
    suppressed = 0
    total = 0
    for start in range(0, n, frame):
        seg_m = m[start: start + frame].astype(np.float64)
        seg_r = aligned[start: start + frame].astype(np.float64)
        total += 1
        rms_m = float(np.sqrt(np.mean(seg_m ** 2))) if seg_m.size else 0.0
        rms_r = float(np.sqrt(np.mean(seg_r ** 2))) if seg_r.size else 0.0
        if rms_r < _MIN_REF_RMS:
            continue
        if rms_m > rms_r * gate:
            continue
        out[start: start + frame] = (m[start: start + frame] * FLOOR_GAIN).astype(np.float32)
        suppressed += 1
    stats = {"suppressed_ratio": (suppressed / total) if total else 0.0, "lag": lag}
    logger.info("ref cancel：lag=%d suppressed=%.2f", lag, stats["suppressed_ratio"])
    return out, stats
