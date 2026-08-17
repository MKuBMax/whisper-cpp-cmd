"""键盘监听权限检查：辅助功能与输入监控分别检查，缺失时请求引导。"""

import logging

from app.controller import VoiceInputApp


def test_startup_permission_guidance_checks_without_prompt(monkeypatch):
    app = VoiceInputApp.__new__(VoiceInputApp)
    app._logger = logging.getLogger(__name__)
    app.status_bar = None
    monkeypatch.setattr(app, "_has_input_monitoring_permission", lambda prompt=False: True)
    calls = []

    def fake_check(*, prompt=False):
        calls.append(prompt)
        return True

    monkeypatch.setattr(app, "_has_accessibility_permission", fake_check)

    app._print_permission_guidance_if_needed()

    assert calls == [False]


def test_startup_permission_guidance_prompts_when_permission_is_missing(monkeypatch):
    app = VoiceInputApp.__new__(VoiceInputApp)
    app._logger = logging.getLogger(__name__)
    app.status_bar = None
    monkeypatch.setattr(app, "_has_input_monitoring_permission", lambda prompt=False: True)
    calls = []

    def fake_check(*, prompt=False):
        calls.append(prompt)
        return False

    monkeypatch.setattr(app, "_has_accessibility_permission", fake_check)

    app._print_permission_guidance_if_needed()

    assert calls == [False, True]


def test_permission_transition_restarts_listener(monkeypatch):
    """授权发生在 App 已运行后，旧的未受信任 event tap 必须重建。"""
    app = VoiceInputApp.__new__(VoiceInputApp)
    app._logger = logging.getLogger(__name__)
    app.status_bar = None
    app._is_running = True
    app._accessibility_trusted = False
    app._input_monitoring_trusted = True

    class FakeListener:
        instances = []

        def __init__(self, **callbacks):
            self.callbacks = callbacks
            self.stopped = False
            self.started = False
            self.__class__.instances.append(self)

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

        def join(self, timeout=None):
            return None

        def is_alive(self):
            return self.started and not self.stopped

    old_listener = FakeListener(on_press=None, on_release=None)
    old_listener.start()
    app.listener = old_listener
    monkeypatch.setattr("app.controller.keyboard.Listener", FakeListener)
    monkeypatch.setattr(app, "_has_accessibility_permission", lambda prompt=False: True)
    monkeypatch.setattr(app, "_has_input_monitoring_permission", lambda prompt=False: True)

    assert app._check_accessibility_permission() is True
    assert old_listener.stopped is True
    assert app.listener is not old_listener
    assert app.listener.started is True


def test_input_monitoring_is_required_for_combined_permission(monkeypatch):
    app = VoiceInputApp.__new__(VoiceInputApp)
    app._logger = logging.getLogger(__name__)
    app.status_bar = None
    monkeypatch.setattr(app, "_has_accessibility_permission", lambda prompt=False: True)
    monkeypatch.setattr(app, "_has_input_monitoring_permission", lambda prompt=False: False)

    assert app._check_accessibility_permission() is False
    assert app._accessibility_trusted is True
    assert app._input_monitoring_trusted is False


def test_input_monitoring_action_opens_settings_when_request_has_no_ui(monkeypatch):
    app = VoiceInputApp.__new__(VoiceInputApp)
    app._logger = logging.getLogger(__name__)
    app.status_bar = None
    opened = []
    monkeypatch.setattr(app, "_has_input_monitoring_permission", lambda prompt=False: False)
    monkeypatch.setattr(app, "_open_input_monitoring_settings", lambda: opened.append(True))

    assert app.check_input_monitoring_permission() is False
    assert opened == [True]


def test_permission_guidance_describes_manual_cleanup(monkeypatch):
    app = VoiceInputApp.__new__(VoiceInputApp)
    app._logger = logging.getLogger(__name__)
    app._is_running = True
    app._accessibility_trusted = False
    app._input_monitoring_trusted = False
    app.listener = None
    app._permission_repair_alert_key = None
    app._current_app_bundle_path = lambda: "/Applications/WhisperCppCmd.app"
    opened = []

    class FakeAlert:
        message = None
        information = None

        def init(self):
            return self

        def setMessageText_(self, value):
            self.__class__.message = value

        def setInformativeText_(self, value):
            self.__class__.information = value

        def addButtonWithTitle_(self, _title):
            return None

        def runModal(self):
            return 1001  # 稍后处理

    class FakeAlertFactory:
        @staticmethod
        def alloc():
            return FakeAlert()

    monkeypatch.setattr("app.controller.AppKit.NSAlert", FakeAlertFactory)
    monkeypatch.setattr(app, "_open_accessibility_settings", lambda: opened.append(True))

    app._show_permission_repair_guidance()

    assert "辅助功能" in FakeAlert.information
    assert "输入监控" in FakeAlert.information
    assert "删除列表中指向旧版本" in FakeAlert.information
    assert "/Applications/WhisperCppCmd.app" in FakeAlert.information
    assert opened == []
