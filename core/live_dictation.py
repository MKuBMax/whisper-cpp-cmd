#!/usr/bin/env python3
"""
实时听写会话 - 录音中持续转写并更新当前输入框
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

import numpy as np

from .clipboard import Clipboard
from .model import ModelEngine
from .audio_source import AudioSource
from .dictation_trace import DictationTrace
from .text_normalizer import normalize_chinese_script


logger = logging.getLogger(__name__)


@dataclass
class LiveDictationConfig:
    update_interval: float = 0.35
    window_seconds: float = 4.0
    min_audio_seconds: float = 0.65
    silence_rms_threshold: float = 0.008
    chinese_script: str = "simplified"
    reconcile_interval: float = 2.0
    reconcile_preview_idle_seconds: float = 1.0
    reconcile_min_audio_seconds: float = 1.25
    mutable_tail_chars: int = 80
    max_overlap_chars: int = 120
    full_reconcile_diff_ratio: float = 0.65


class LiveDictationSession:
    """
    录音时的实时预览会话。

    设计目标：
    - 高频预览保持响应
    - 后台全局重整纠正前文
    - 松开按键后由最终结果收尾
    """

    def __init__(
        self,
        audio_source: AudioSource,
        model_engine: ModelEngine,
        clipboard: Clipboard,
        config: Optional[LiveDictationConfig] = None,
    ):
        self.audio_source = audio_source
        self.model_engine = model_engine
        self.clipboard = clipboard
        self.config = config or LiveDictationConfig()

        self._stop_event = threading.Event()
        self._preview_thread: Optional[threading.Thread] = None
        self._reconcile_thread: Optional[threading.Thread] = None
        self._output_lock = threading.Lock()
        self._rendered_text = ""
        self._first_char_ms: Optional[float] = None
        self._last_error: Optional[str] = None
        self._last_preview_at: float = 0.0
        self._last_reconcile_at: float = 0.0
        self.trace: Optional[DictationTrace] = None
        self._generation = 0
        self._model_lock = threading.Lock()

    @property
    def rendered_text(self) -> str:
        return self._rendered_text

    @property
    def first_char_latency_ms(self) -> Optional[float]:
        """首个预览字符出现的延迟（ms，相对按键按下）；未产生过预览返回 None。"""
        return self._first_char_ms

    def start(self) -> bool:
        if self._preview_thread is not None or self._reconcile_thread is not None:
            return False

        self._stop_event.clear()
        self._generation += 1
        self._rendered_text = ""
        self._first_char_ms = None
        self._last_error = None
        self._last_preview_at = 0.0
        self._last_reconcile_at = 0.0
        generation = self._generation

        logger.info(
            "实时预览启动：window_seconds=%.2f update_interval=%.2f reconcile_interval=%.2f",
            self.config.window_seconds,
            self.config.update_interval,
            self.config.reconcile_interval,
        )
        if isinstance(self.trace, DictationTrace):
            logger.info("%s live_dictation.start", self.trace.prefix("live_dictation"))
        self._preview_thread = threading.Thread(
            target=self._preview_worker,
            name="LiveDictationPreview",
            daemon=True,
            args=(generation,),
        )
        self._reconcile_thread = threading.Thread(
            target=self._reconcile_worker,
            name="LiveDictationReconcile",
            daemon=True,
            args=(generation,),
        )
        self._preview_thread.start()
        self._reconcile_thread.start()
        return True

    def stop(self) -> None:
        if isinstance(self.trace, DictationTrace):
            logger.info("%s live_dictation.stop begin", self.trace.prefix("live_dictation"))
        self._stop_event.set()
        if isinstance(self.trace, DictationTrace):
            logger.info("%s live_dictation.stop signal_sent", self.trace.prefix("live_dictation"))

    def finalize(self, final_text: str) -> bool:
        """用最终文本收尾并返回是否确认写入目标输入框。"""

        final_text = normalize_chinese_script(final_text or "", self.config.chinese_script)
        with self._output_lock:
            if final_text == self._rendered_text:
                return True
            if not self.clipboard.replace_typed_text(final_text, self._rendered_text):
                logger.warning("实时听写最终文本插入失败：current_len=%s next_len=%s", len(self._rendered_text), len(final_text))
                return False
            self._rendered_text = final_text
            return True

    def clear(self) -> None:
        with self._output_lock:
            if self._rendered_text:
                self.clipboard.replace_typed_text("", self._rendered_text)
            self._rendered_text = ""

    def _is_active_generation(self, generation: int) -> bool:
        return generation == self._generation and not self._stop_event.is_set()

    def _preview_worker(self, generation: int) -> None:
        try:
            self._preview_loop(generation)
        finally:
            if generation == self._generation:
                self._preview_thread = None

    def _preview_loop(self, generation: int) -> None:
        sample_rate = self.audio_source.config.sample_rate
        min_samples = int(self.config.min_audio_seconds * sample_rate)

        while self._is_active_generation(generation):
            try:
                audio = self.audio_source.get_recent_buffer(self.config.window_seconds)
                if audio is None or len(audio) < min_samples:
                    time.sleep(self.config.update_interval)
                    continue

                if audio.ndim > 1:
                    audio = audio[:, 0]

                if self._is_too_quiet(audio):
                    time.sleep(self.config.update_interval)
                    continue

                transcript = self._transcribe(audio, allow_skip=False)
                if not transcript:
                    time.sleep(self.config.update_interval)
                    continue

                self._apply_preview_transcript(transcript)
                self._last_preview_at = time.time()
                if isinstance(self.trace, DictationTrace):
                    logger.info("%s preview tick len=%s", self.trace.prefix("preview"), len(transcript))

                time.sleep(self.config.update_interval)
            except Exception as e:
                self._last_error = str(e)
                logger.warning("实时听写更新失败：%s", e)
                time.sleep(self.config.update_interval)

    def _reconcile_worker(self, generation: int) -> None:
        try:
            self._reconcile_loop(generation)
        finally:
            if generation == self._generation:
                self._reconcile_thread = None

    def _reconcile_loop(self, generation: int) -> None:
        sample_rate = self.audio_source.config.sample_rate
        min_samples = int(self.config.reconcile_min_audio_seconds * sample_rate)

        while self._is_active_generation(generation):
            started_at = time.time()
            try:
                preview_idle = started_at - self._last_preview_at if self._last_preview_at > 0 else 0.0
                if self._last_preview_at > 0 and preview_idle < self.config.reconcile_preview_idle_seconds:
                    if isinstance(self.trace, DictationTrace):
                        logger.info(
                            "%s reconcile skipped: preview_active idle=%.2fs threshold=%.2fs",
                            self.trace.prefix("reconcile"),
                            preview_idle,
                            self.config.reconcile_preview_idle_seconds,
                        )
                    self._sleep_until_next_reconcile(started_at)
                    continue

                audio = self.audio_source.get_buffer()
                if audio is None or len(audio) < min_samples:
                    self._sleep_until_next_reconcile(started_at)
                    continue

                if audio.ndim > 1:
                    audio = audio[:, 0]

                if self._is_too_quiet(audio):
                    self._sleep_until_next_reconcile(started_at)
                    continue

                transcript = self._transcribe(audio, allow_skip=True)
                if transcript:
                    self._apply_reconcile_transcript(transcript)
                    self._last_reconcile_at = time.time()
                    if isinstance(self.trace, DictationTrace):
                        logger.info("%s reconcile tick len=%s", self.trace.prefix("reconcile"), len(transcript))
            except Exception as e:
                self._last_error = str(e)
                logger.warning("全局重整失败：%s", e)

            self._sleep_until_next_reconcile(started_at)

    def _sleep_until_next_reconcile(self, started_at: float) -> None:
        elapsed = time.time() - started_at
        remaining = max(0.0, self.config.reconcile_interval - elapsed)
        if remaining > 0:
            time.sleep(remaining)

    def _transcribe(self, audio: np.ndarray, allow_skip: bool) -> str:
        if isinstance(self.trace, DictationTrace):
            logger.info("%s live_dictation.transcribe begin samples=%s", self.trace.prefix("transcribe"), len(audio))

        if allow_skip:
            acquired = self._model_lock.acquire(blocking=False)
            if not acquired:
                if isinstance(self.trace, DictationTrace):
                    logger.info("%s live_dictation.transcribe skipped: model busy", self.trace.prefix("transcribe"))
                return ""
        else:
            self._model_lock.acquire()

        try:
            result = self.model_engine.transcribe(audio.astype(np.float32))
            text = result.text if hasattr(result, "text") else str(result or "")
            if isinstance(self.trace, DictationTrace):
                logger.info("%s live_dictation.transcribe done text_len=%s", self.trace.prefix("transcribe"), len(text.strip()))
            return normalize_chinese_script(text.strip(), self.config.chinese_script)
        finally:
            self._model_lock.release()

    def _apply_preview_transcript(self, transcript: str) -> None:
        transcript = transcript.strip()
        if not transcript:
            return
        if self._stop_event.is_set():
            return

        with self._output_lock:
            if self._stop_event.is_set():
                return
            current = self._rendered_text
            if transcript == current:
                return

            next_text = self._merge_preview_text(current, transcript)
            if next_text != current:
                if self._first_char_ms is None and next_text:
                    self._first_char_ms = round(self.trace.elapsed_ms(), 1) if isinstance(self.trace, DictationTrace) else None
                if not self.clipboard.replace_typed_text(next_text, current):
                    logger.warning("实时听写预览插入失败：current_len=%s next_len=%s", len(current), len(next_text))
                    return
                self._rendered_text = next_text
                logger.info("实时预览更新：len=%s", len(next_text))
                if isinstance(self.trace, DictationTrace):
                    logger.info("%s preview applied current_len=%s next_len=%s", self.trace.prefix("preview"), len(current), len(next_text))

    def _apply_reconcile_transcript(self, transcript: str) -> None:
        transcript = transcript.strip()
        if not transcript:
            return
        if self._stop_event.is_set():
            return

        with self._output_lock:
            if self._stop_event.is_set():
                return
            current = self._rendered_text
            if transcript == current:
                return

            similarity = SequenceMatcher(None, current, transcript).ratio() if current else 0.0
            if current and len(transcript) < len(current) and similarity < 0.78:
                logger.info(
                    "全局重整跳过短文本回退：similarity=%.2f current_len=%s transcript_len=%s",
                    similarity,
                    len(current),
                    len(transcript),
                )
                return
            if current and similarity < self.config.full_reconcile_diff_ratio:
                logger.info(
                    "全局重整触发整段替换：similarity=%.2f current_len=%s transcript_len=%s",
                    similarity,
                    len(current),
                    len(transcript),
                )
            else:
                logger.info(
                    "全局重整更新：similarity=%.2f current_len=%s transcript_len=%s",
                    similarity,
                    len(current),
                    len(transcript),
                )

            if not self.clipboard.replace_typed_text(transcript, current):
                logger.warning("实时听写重整插入失败：current_len=%s next_len=%s", len(current), len(transcript))
                return
            self._rendered_text = transcript
            if isinstance(self.trace, DictationTrace):
                logger.info("%s reconcile applied current_len=%s transcript_len=%s", self.trace.prefix("reconcile"), len(current), len(transcript))

    def _merge_preview_text(self, current: str, transcript: str) -> str:
        if not current:
            return transcript

        overlap = self._find_suffix_prefix_overlap(current, transcript)
        if overlap > 0:
            return current[:-overlap] + transcript

        stable_len = max(0, len(current) - self.config.mutable_tail_chars)
        if stable_len == 0:
            if len(transcript) <= len(current):
                return current
            return current + transcript

        return current[:stable_len] + transcript

    def _find_suffix_prefix_overlap(self, left: str, right: str) -> int:
        if not left or not right:
            return 0

        max_len = min(len(left), len(right), self.config.max_overlap_chars)
        for length in range(max_len, 0, -1):
            if left.endswith(right[:length]):
                return length
        return 0

    def _is_too_quiet(self, audio: np.ndarray) -> bool:
        if audio.size == 0:
            return True
        window = audio.astype(np.float32)
        rms = float(np.sqrt(np.mean(np.square(window))))
        return rms < self.config.silence_rms_threshold
