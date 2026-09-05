"""Unified native window controller smoke test."""

from config.settings import Settings
from ui.dashboard_window import DashboardWindowController
from ui.status_bar import StatusBarController


class DummyApp:
    settings = Settings()


def test_dashboard_controller_init():
    app = DummyApp()
    dash = DashboardWindowController.alloc().initWithApp_(app)
    assert dash is not None
    assert dash.app is app


def test_explicit_close_action_keeps_app_running():
    closed = []
    controller = StatusBarController.alloc().init()
    controller.app = type("App", (), {"close_dashboard": lambda _self: closed.append(True)})()
    assert controller.respondsToSelector_("closeDashboard:")
    controller.closeDashboard_(None)
    assert closed == [True]
