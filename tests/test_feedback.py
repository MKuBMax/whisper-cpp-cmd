"""单胶囊状态提示回归：RecordingOverlay.show_status 复用录音 panel，timeout 语义与旧结果胶囊一致。"""
import inspect

from ui.overlay_window import RecordingOverlay


def test_status_capsule_hides_after_one_second():
    params = inspect.signature(RecordingOverlay.show_status).parameters
    assert params["timeout"].default == 1.0


def test_status_method_exists_alongside_show_hide():
    assert callable(RecordingOverlay.show)
    assert callable(RecordingOverlay.hide)
    assert callable(RecordingOverlay.show_status)
