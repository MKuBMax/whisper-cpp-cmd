"""权限检查：状态持续刷新，缺失时由欢迎页提供引导。"""

import logging
import sys
import types

from app import controller
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

    assert calls == [False]


def test_microphone_permission_reads_av_audio_application(monkeypatch):
    app = VoiceInputApp.__new__(VoiceInputApp)
    app._logger = logging.getLogger(__name__)

    class FakeAudioApplication:
        @staticmethod
        def sharedInstance():
            return FakeAudioApplication

        @staticmethod
        def recordPermission():
            return int.from_bytes(b"grnt", "big")

    monkeypatch.setattr(
        "app.controller.objc.lookUpClass",
        lambda name: FakeAudioApplication if name == "AVAudioApplication" else (_ for _ in ()).throw(AssertionError(name)),
    )

    assert app._has_microphone_permission() is True


def test_microphone_permission_request_uses_class_method_with_block_metadata(monkeypatch):
    app = VoiceInputApp.__new__(VoiceInputApp)
    app._logger = logging.getLogger(__name__)
    app.refresh_accessibility_permission_status = lambda: None
    callbacks = []
    metadata = []
    refreshed = []

    class FakeAudioApplication:
        @classmethod
        def sharedInstance(cls):
            return cls

        @classmethod
        def recordPermission(cls):
            return int.from_bytes(b"undt", "big")

        @classmethod
        def requestRecordPermissionWithCompletionHandler_(cls, callback):
            callbacks.append(callback)

    monkeypatch.setattr(
        "app.controller.objc.lookUpClass",
        lambda name: FakeAudioApplication if name == "AVAudioApplication" else (_ for _ in ()).throw(AssertionError(name)),
    )
    monkeypatch.setattr(
        "app.controller.objc.registerMetaDataForSelector",
        lambda *args: metadata.append(args),
    )
    monkeypatch.setattr(
        "app.controller.AppHelper.callAfter",
        lambda callback: (refreshed.append(True), callback()),
    )

    assert app._request_microphone_permission() is False
    assert len(callbacks) == 1
    assert metadata and metadata[0][0:2] == (
        b"AVAudioApplication",
        b"requestRecordPermissionWithCompletionHandler:",
    )

    callbacks[0](True)
    assert refreshed == [True]


def test_darwin_listener_skips_background_carbon_layout_lookup(monkeypatch):
    original_context = object()
    observed_contexts = []
    fake_darwin_module = types.SimpleNamespace(keycode_context=original_context)

    class FakeListener:
        def __init__(self, **_callbacks):
            pass

        def _run(self):
            observed_contexts.append(fake_darwin_module.keycode_context)

    FakeListener.__module__ = "pynput.keyboard._darwin"
    monkeypatch.setattr(controller.sys, "platform", "darwin")
    monkeypatch.setattr(controller.keyboard, "Listener", FakeListener)
    monkeypatch.setitem(sys.modules, "pynput.keyboard._darwin", fake_darwin_module)

    listener = controller._new_keyboard_listener()
    listener._run()

    assert observed_contexts == [controller._empty_pynput_keycode_context]
    assert fake_darwin_module.keycode_context is original_context


def test_input_monitoring_prompt_does_not_call_blocking_hid_api(monkeypatch):
    """权限请求通过系统设置完成，不能同步阻塞 AppKit 主线程。"""
    app = VoiceInputApp.__new__(VoiceInputApp)
    called = []
    monkeypatch.setattr(
        "app.controller._IOHID_REQUEST_ACCESS",
        lambda *_args: called.append(True),
    )

    assert app._has_input_monitoring_permission(prompt=True) is False
    assert called == []


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


def test_permission_repair_guidance_does_not_show_modal_alert(monkeypatch, caplog):
    app = VoiceInputApp.__new__(VoiceInputApp)
    app._logger = logging.getLogger(__name__)
    app._is_running = True
    app._accessibility_trusted = False
    app._input_monitoring_trusted = False
    app.listener = None
    app._permission_repair_alert_key = None
    class ExplodingAlertFactory:
        def __call__(self, *_args, **_kwargs):
            raise AssertionError("权限异常不应再创建一次性 NSAlert")

    monkeypatch.setattr("app.controller.AppKit.NSAlert", ExplodingAlertFactory())

    with caplog.at_level(logging.INFO):
        app._show_permission_repair_guidance()

    assert "常驻欢迎页" in caplog.text


def test_onboarding_reopens_when_core_permission_is_missing(monkeypatch):
    app = VoiceInputApp.__new__(VoiceInputApp)
    app.settings = type("Settings", (), {"onboarding_completed": True, "model_exists": lambda self: True})()
    app._onboarding_window = None
    app._dashboard_window = None
    app._check_accessibility_permission = lambda: False
    app.get_permission_status = lambda: {
        "microphone": True,
        "accessibility": False,
        "input_monitoring": True,
    }
    shown = []

    class FakeOnboarding:
        @classmethod
        def alloc(cls):
            return cls()

        def initWithApp_(self, app):
            self.app = app
            return self

        def show(self):
            shown.append(True)

    monkeypatch.setattr("app.controller.DashboardWindowController", FakeOnboarding)

    app.show_onboarding_if_needed()

    assert shown == [True]


def test_open_onboarding_allocates_and_shows_window(monkeypatch):
    app = VoiceInputApp.__new__(VoiceInputApp)
    app._onboarding_window = None
    app._dashboard_window = None
    shown = []

    class FakeOnboarding:
        @classmethod
        def alloc(cls):
            return cls()

        def initWithApp_(self, app):
            self.app = app
            return self

        def show(self):
            shown.append(True)

    monkeypatch.setattr("app.controller.DashboardWindowController", FakeOnboarding)
    app.open_onboarding()
    assert shown == [True]
    assert app._onboarding_window is not None


def test_onboarding_skip_marks_completed_and_orders_out():
    from ui.onboarding_window import OnboardingWindowController

    saved = []
    ordered_out = []
    fake_settings = type("Settings", (), {"onboarding_completed": False, "save": lambda *args: saved.append(True)})()
    fake_app = type("App", (), {"settings": fake_settings})()
    fake_window = type("Window", (), {"orderOut_": lambda _self, arg: ordered_out.append(arg)})()

    controller = OnboardingWindowController.alloc().initWithApp_(fake_app)
    controller.window = fake_window
    controller.windowWillClose_(None)

    assert fake_settings.onboarding_completed is True
    assert saved == [True]
    assert ordered_out == []  # Closing never opens another window.

