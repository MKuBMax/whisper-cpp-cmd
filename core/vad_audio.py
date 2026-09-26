"""Materialize the speech-only audio produced by whisper.cpp's Silero VAD.

The transcription server applies VAD inside its worker process and does not
return the reduced waveform.  This module calls the same whisper.cpp VAD C API
on the already-processed input so the archive can retain that waveform for
later comparison.  It deliberately loads only the CPU ggml backend: VAD
export is diagnostic data and should not initialize a second Metal context in
the GUI process.
"""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
import glob
import logging
import os
import threading
from typing import Optional

import numpy as np


logger = logging.getLogger(__name__)


_SAMPLE_RATE = 16_000
_LIBRARY_MODE = getattr(ctypes, "RTLD_GLOBAL", 0)


class _WhisperVADParams(ctypes.Structure):
    _fields_ = [
        ("threshold", ctypes.c_float),
        ("min_speech_duration_ms", ctypes.c_int),
        ("min_silence_duration_ms", ctypes.c_int),
        ("max_speech_duration_s", ctypes.c_float),
        ("speech_pad_ms", ctypes.c_int),
        ("samples_overlap", ctypes.c_float),
    ]


class _WhisperVADContextParams(ctypes.Structure):
    _fields_ = [
        ("n_threads", ctypes.c_int),
        ("use_gpu", ctypes.c_bool),
        ("gpu_device", ctypes.c_int),
    ]


@dataclass(frozen=True)
class VADExtraction:
    """Speech-only waveform and source-time segments returned by Silero."""

    audio: np.ndarray
    segments: tuple[tuple[float, float], ...]


class VADAudioExtractor:
    """Reuse whisper.cpp's VAD context to concatenate speech segments."""

    _loaded_runtime: dict[tuple[str, str], tuple[ctypes.CDLL, ctypes.CDLL]] = {}
    _log_callbacks: dict[tuple[str, str], object] = {}
    _runtime_lock = threading.Lock()

    def __init__(self, model_path: str, runtime_binary: str, n_threads: int = 4):
        self.model_path = os.path.abspath(os.path.expanduser(str(model_path)))
        self.runtime_binary = os.path.abspath(os.path.expanduser(str(runtime_binary)))
        self.n_threads = max(1, int(n_threads))
        self._whisper: Optional[ctypes.CDLL] = None
        self._context: Optional[int] = None
        self._log_callback = None
        self._lock = threading.Lock()

    def close(self) -> None:
        """Release the per-extractor VAD context."""
        with self._lock:
            if self._context and self._whisper is not None:
                self._whisper.whisper_vad_free(ctypes.c_void_p(self._context))
            self._context = None
            self._whisper = None
            self._log_callback = None

    def extract(self, audio: np.ndarray, sample_rate: int) -> VADExtraction:
        """Return the concatenated speech portions of a 16 kHz waveform.

        whisper.cpp exposes VAD timestamps in centiseconds.  The returned
        ``segments`` use seconds and are clipped to the supplied waveform.
        Adjacent/overlapping ranges are merged so overlap padding never
        duplicates samples in the archived WAV.
        """
        if int(sample_rate) != _SAMPLE_RATE:
            raise ValueError(f"VAD 只支持 {_SAMPLE_RATE} Hz 音频")

        samples = np.ascontiguousarray(np.asarray(audio, dtype=np.float32))
        if samples.ndim != 1:
            samples = samples.reshape(-1)
        if samples.size == 0:
            return VADExtraction(np.empty(0, dtype=np.float32), ())
        if not np.isfinite(samples).all():
            raise ValueError("VAD 输入包含非有限采样值")

        with self._lock:
            self._ensure_context()
            assert self._whisper is not None
            assert self._context is not None

            params = self._whisper.whisper_vad_default_params()
            segments_ptr = self._whisper.whisper_vad_segments_from_samples(
                ctypes.c_void_p(self._context),
                params,
                samples.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                int(samples.size),
            )
            if not segments_ptr:
                raise RuntimeError("whisper.cpp VAD 未返回片段")

            try:
                count = int(
                    self._whisper.whisper_vad_segments_n_segments(segments_ptr)
                )
                ranges: list[tuple[int, int]] = []
                for index in range(count):
                    # whisper_vad_segments stores timestamps as centiseconds.
                    start_seconds = float(
                        self._whisper.whisper_vad_segments_get_segment_t0(
                            segments_ptr, index
                        )
                    ) / 100.0
                    end_seconds = float(
                        self._whisper.whisper_vad_segments_get_segment_t1(
                            segments_ptr, index
                        )
                    ) / 100.0
                    start = max(0, min(samples.size, round(start_seconds * sample_rate)))
                    end = max(0, min(samples.size, round(end_seconds * sample_rate)))
                    if end > start:
                        ranges.append((start, end))
            finally:
                self._whisper.whisper_vad_free_segments(segments_ptr)

        merged = self._merge_ranges(ranges)
        speech_audio = (
            np.concatenate([samples[start:end] for start, end in merged])
            if merged
            else np.empty(0, dtype=np.float32)
        )
        speech_segments = tuple(
            (round(start / sample_rate, 3), round(end / sample_rate, 3))
            for start, end in merged
        )
        return VADExtraction(speech_audio, speech_segments)

    def _ensure_context(self) -> None:
        if self._context is not None and self._whisper is not None:
            return
        if not os.path.isfile(self.model_path):
            raise FileNotFoundError(f"VAD 模型不存在：{self.model_path}")

        ggml_path, whisper_path, cpu_backend_path = self._runtime_paths()
        cache_key = (ggml_path, cpu_backend_path)
        with self._runtime_lock:
            handles = self._loaded_runtime.get(cache_key)
            if handles is None:
                ggml = ctypes.CDLL(ggml_path, mode=_LIBRARY_MODE)
                whisper = ctypes.CDLL(whisper_path, mode=_LIBRARY_MODE)
                callback = self._install_quiet_log_callback(whisper)
                ggml.ggml_backend_load.argtypes = [ctypes.c_char_p]
                ggml.ggml_backend_load.restype = ctypes.c_void_p
                if not ggml.ggml_backend_load(cpu_backend_path.encode("utf-8")):
                    raise RuntimeError(f"无法加载 ggml CPU backend：{cpu_backend_path}")
                handles = (ggml, whisper)
                self._loaded_runtime[cache_key] = handles
                self._log_callbacks[cache_key] = callback
            self._whisper = handles[1]

        self._configure_api(self._whisper)
        context_params = self._whisper.whisper_vad_default_context_params()
        context_params.n_threads = self.n_threads
        context_params.use_gpu = False
        context_params.gpu_device = 0
        context = self._whisper.whisper_vad_init_from_file_with_params(
            self.model_path.encode("utf-8"), context_params
        )
        if not context:
            self._whisper = None
            raise RuntimeError("whisper.cpp VAD 初始化失败")
        self._context = int(context)

    def _configure_api(self, whisper: ctypes.CDLL) -> None:
        whisper.whisper_vad_default_params.restype = _WhisperVADParams
        whisper.whisper_vad_default_context_params.restype = _WhisperVADContextParams
        whisper.whisper_vad_init_from_file_with_params.argtypes = [
            ctypes.c_char_p,
            _WhisperVADContextParams,
        ]
        whisper.whisper_vad_init_from_file_with_params.restype = ctypes.c_void_p
        whisper.whisper_vad_segments_from_samples.argtypes = [
            ctypes.c_void_p,
            _WhisperVADParams,
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_int,
        ]
        whisper.whisper_vad_segments_from_samples.restype = ctypes.c_void_p
        whisper.whisper_vad_segments_n_segments.argtypes = [ctypes.c_void_p]
        whisper.whisper_vad_segments_n_segments.restype = ctypes.c_int
        whisper.whisper_vad_segments_get_segment_t0.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        whisper.whisper_vad_segments_get_segment_t0.restype = ctypes.c_float
        whisper.whisper_vad_segments_get_segment_t1.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        whisper.whisper_vad_segments_get_segment_t1.restype = ctypes.c_float
        whisper.whisper_vad_free_segments.argtypes = [ctypes.c_void_p]
        whisper.whisper_vad_free.argtypes = [ctypes.c_void_p]

    def _install_quiet_log_callback(self, whisper: ctypes.CDLL):
        callback_type = ctypes.CFUNCTYPE(
            None, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p
        )

        def _quiet(_level, _text, _user_data):
            return None

        callback = callback_type(_quiet)
        whisper.whisper_log_set.argtypes = [callback_type, ctypes.c_void_p]
        whisper.whisper_log_set.restype = None
        whisper.whisper_log_set(callback, None)
        self._log_callback = callback
        return callback

    def _runtime_paths(self) -> tuple[str, str, str]:
        binary_dir = os.path.dirname(os.path.realpath(self.runtime_binary))
        runtime_root = os.path.dirname(binary_dir)

        whisper_candidates = [
            os.path.join(runtime_root, "lib", "libwhisper.1.dylib"),
            *sorted(glob.glob(os.path.join(runtime_root, "lib", "libwhisper*.dylib"))),
            os.path.join(binary_dir, "libwhisper.1.dylib"),
        ]
        ggml_candidates = [
            os.path.join(runtime_root, "ggml", "lib", "libggml.0.dylib"),
            os.path.join(runtime_root, "lib", "libggml.0.dylib"),
            "/opt/homebrew/opt/ggml/lib/libggml.0.dylib",
            "/usr/local/opt/ggml/lib/libggml.0.dylib",
            *sorted(glob.glob("/opt/homebrew/Cellar/ggml/*/lib/libggml*.dylib")),
        ]

        whisper_path = self._first_file(whisper_candidates)
        ggml_path = self._first_file(ggml_candidates)
        if not whisper_path or not ggml_path:
            raise FileNotFoundError(
                "未找到 whisper.cpp VAD runtime（libwhisper/libggml）"
            )

        ggml_dir = os.path.dirname(ggml_path)
        backend_candidates = [
            os.path.join(binary_dir, "libggml-cpu-apple_m1.so"),
            os.path.join(ggml_dir, "..", "libexec", "libggml-cpu-apple_m1.so"),
            os.path.join(ggml_dir, "..", "libexec", "libggml-cpu-apple_m2_m3.so"),
            os.path.join(ggml_dir, "..", "libexec", "libggml-cpu-apple_m4.so"),
            *sorted(glob.glob(os.path.join(binary_dir, "libggml-cpu-*.so"))),
            *sorted(glob.glob(os.path.join(ggml_dir, "..", "libexec", "libggml-cpu-*.so"))),
        ]
        cpu_backend_path = self._first_file(backend_candidates)
        if not cpu_backend_path:
            raise FileNotFoundError("未找到 ggml CPU backend")
        return ggml_path, whisper_path, cpu_backend_path

    @staticmethod
    def _first_file(candidates: list[str]) -> Optional[str]:
        seen = set()
        for candidate in candidates:
            candidate = os.path.realpath(candidate)
            if candidate in seen:
                continue
            seen.add(candidate)
            if os.path.isfile(candidate):
                return candidate
        return None

    @staticmethod
    def _merge_ranges(ranges: list[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
        if not ranges:
            return ()
        merged: list[list[int]] = []
        for start, end in sorted(ranges):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        return tuple((start, end) for start, end in merged)
