"""Pipeline 在 VAD 之前的空录音保护回归测试。"""

from types import SimpleNamespace

import numpy as np

from core.audio_source import AudioConfig
from core.output import OutputConfig, OutputHandler
from core.pipeline import Pipeline, PipelineConfig
from core.processor import Processor


def _pipeline_for_audio(audio):
    pipeline = Pipeline.__new__(Pipeline)
    pipeline.config = PipelineConfig(
        audio=AudioConfig(sample_rate=16_000),
        output=OutputConfig(history_file="", verbose=False),
    )
    pipeline.recorder = SimpleNamespace(
        is_recording=True,
        stop=lambda: None,
    )
    pipeline.audio_source = SimpleNamespace(
        is_recording=True,
        stop_recording=lambda: audio,
    )
    pipeline.processor = Processor()
    pipeline.model_engine = SimpleNamespace(
        transcribe=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("数字静音不应请求模型")
        ),
    )
    pipeline.processor = SimpleNamespace(
        process=lambda audio, _sample_rate: audio,
    )
    pipeline.output_handler = OutputHandler(pipeline.config.output)
    pipeline.trace = None
    pipeline._on_complete_callback = None
    return pipeline


def test_digital_silence_returns_no_speech_without_model_request():
    result = _pipeline_for_audio(np.zeros(16_000, dtype=np.float32)).stop_recording()

    assert result.success is True
    assert result.no_speech is True
    assert result.text == ""


def test_empty_model_result_is_no_speech_and_is_not_pasted_or_saved(tmp_path):
    pipeline = _pipeline_for_audio(np.full(16_000, 0.02, dtype=np.float32))
    pasted = []
    pipeline.model_engine = SimpleNamespace(
        transcribe=lambda *_args, **_kwargs: SimpleNamespace(
            text="",
            model_name="large-v3",
            success=True,
            error=None,
            rtf=0.1,
        ),
    )
    pipeline.clipboard = SimpleNamespace(insert=lambda text: pasted.append(text))
    pipeline.config.output.history_file = str(tmp_path / "history.json")

    result = pipeline.stop_recording(paste_output=True)

    assert result.success is True
    assert result.no_speech is True
    assert pasted == []
    assert not (tmp_path / "history.json").exists()
