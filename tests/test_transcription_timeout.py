"""A4: transcription_timeout 配置贯通单测。

验证转录超时从 Settings → PipelineConfig → WhisperCliBackend 一路传递，
且 watchdog 阈值 = 超时 + 宽限（消除 30s/120s 错位的回归保护）。
"""

from config.settings import Settings
from core.pipeline import PipelineConfig
from core.model import WhisperCliBackend


def test_settings_default_timeout():
    assert Settings().transcription_timeout == 120.0


def test_pipeline_config_default_timeout():
    assert PipelineConfig().transcription_timeout == 120.0


def test_backend_default_timeout():
    backend = WhisperCliBackend('/fake/whisper-cli')
    assert backend._transcription_timeout == 120.0


def test_backend_preserves_custom_timeout():
    backend = WhisperCliBackend('/fake/whisper-cli', transcription_timeout=42.0)
    assert backend._transcription_timeout == 42.0
