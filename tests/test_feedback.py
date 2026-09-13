"""单胶囊状态提示回归：RecordingOverlay 仍提供 show / hide / show_status。"""

from ui.overlay_window import RecordingOverlay


def test_status_method_exists_alongside_show_hide():
    assert callable(RecordingOverlay.show)
    assert callable(RecordingOverlay.hide)
    assert callable(RecordingOverlay.show_status)
