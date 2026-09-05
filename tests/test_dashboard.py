"""Unified native window controller smoke test."""

from config.settings import Settings
from ui.dashboard_window import DashboardWindowController


class DummyApp:
    settings = Settings()


def test_dashboard_controller_init():
    app = DummyApp()
    dash = DashboardWindowController.alloc().initWithApp_(app)
    assert dash is not None
    assert dash.app is app
