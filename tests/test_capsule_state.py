"""单胶囊代次：旧结果定时凭代次失效，不误杀新一轮录音胶囊。"""

import inspect

from app.controller import VoiceInputApp
from ui.overlay_window import RecordingOverlay


def test_capsule_api_exists():
    assert callable(VoiceInputApp._capsule_show_recording)
    assert callable(VoiceInputApp._capsule_show_busy)
    assert callable(VoiceInputApp._capsule_show_result)


def test_status_capsule_hides_after_one_second():
    params = inspect.signature(RecordingOverlay.show_status).parameters
    assert params["timeout"].default == 1.0


def test_status_carries_generation():
    params = inspect.signature(RecordingOverlay.show_status).parameters
    assert "generation" in params


def test_no_first_frame_coupling():
    import pathlib
    controller = pathlib.Path("app/controller.py").read_text(encoding="utf-8")
    assert "_on_first_frame" not in controller
    assert "_arm_first_frame" not in controller
    source = pathlib.Path("core/audio_source.py").read_text(encoding="utf-8")
    assert "_on_first_frame" not in source
