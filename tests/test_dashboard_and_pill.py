"""控制中心与桌面悬浮胶囊单测。"""

from ui.floating_pill import _rms_to_level, FloatingPillController
from ui.dashboard_window import DashboardWindowController
from config.settings import Settings


class DummyApp:
    def __init__(self):
        self.settings = Settings()
        self.recorded = False

    def is_recording(self):
        return self.recorded

    def start_recording(self):
        self.recorded = True

    def stop_recording(self):
        self.recorded = False

    def _overlay_rms(self):
        return 0.02

    def get_recent_history(self, count=3):
        return ["测试识别文本 1", "测试识别文本 2"]

    def list_audio_devices(self):
        return [{"name": "MacBook Pro麦克风"}]


def test_rms_to_level_endpoints():
    assert _rms_to_level(0.0) == 0.0
    assert _rms_to_level(-0.5) == 0.0
    assert _rms_to_level(0.003) == 0.0  # 底噪归零
    assert _rms_to_level(0.06) == 1.0   # 顶满
    mid = _rms_to_level(0.015)
    assert 0.5 < mid < 0.9


def test_floating_pill_lifecycle():
    app = DummyApp()
    pill = FloatingPillController.alloc().initWithApp_(app)
    assert pill is not None
    assert pill._current_state == "idle"

    pill.on_recording_started()
    assert pill._current_state == "recording"

    pill.on_recording_stopped()
    assert pill._current_state == "processing"

    pill.on_transcription_completed("你好世界")
    assert pill._current_state == "idle"


def test_dashboard_controller_init():
    app = DummyApp()
    dash = DashboardWindowController.alloc().initWithApp_(app)
    assert dash is not None
    assert dash.app is app
