"""系统液态玻璃回归：NSGlassEffectView 承载胶囊，无自绘采样管线。"""
import AppKit

import ui.overlay_window as ov


def test_capsule_geometry_grown_for_glass():
    assert ov._WIDTH == 172
    assert ov._HEIGHT == 40


def test_capsule_uses_system_glass():
    app = ov.RecordingOverlay.alloc().init()
    try:
        panel = app._panel
        glass = panel.contentView()
        assert isinstance(glass, AppKit.NSGlassEffectView)
        assert glass.style() == AppKit.NSGlassEffectViewStyleRegular
        content = glass.contentView()
        assert content is not None
        assert len(content.subviews()) == 4
    finally:
        try:
            app.hide()
        except Exception:
            pass


def test_no_self_drawn_backdrop_pipeline():
    src = open("ui/overlay_window.py", encoding="utf-8").read()
    assert "_BackdropBlurView" not in src
    assert "_GlassSkin" not in src
    assert "CGDisplayStreamCreate" not in src
    assert "CGWindowListCreateImage" not in src
    assert "_updateBackdrop" not in src


def test_capsule_layout_derived_from_size():
    layout = ov.capsule_layout(ov._WIDTH, ov._HEIGHT)
    assert layout.dot.x >= 12
    assert layout.label.x > layout.dot.x + layout.dot.w
    assert layout.wave.x > layout.label.x + layout.label.w
    assert layout.wave.x + layout.wave.w <= ov._WIDTH - 12
    assert layout.status.w == ov._WIDTH - 24


def test_tick_rate_unchanged():
    assert abs(ov._TICK_INTERVAL - 1.0 / 60.0) < 1e-9
