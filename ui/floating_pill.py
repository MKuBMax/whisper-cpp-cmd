#!/usr/bin/env python3
"""桌面常驻悬浮交互胶囊 (Floating Pill)。

专为带刘海屏的 MacBook 设计：当系统菜单栏图标被刘海遮挡挤压时，
桌面上始终有一枚半透明精致胶囊作为视觉与交互中心。

核心交互能力：
- 状态指示：空闲（绿色就绪点+模型名）、录音中（红色脉冲+时长+跳动波形）、处理中（黄色转写提示）；
- 鼠标左键单击：一键切换开始/停止录音（免键盘长按）；
- 鼠标右键单击：弹出与菜单栏完全一致的完整功能菜单（模型切换、设备选择、偏好设置、退出等）；
- 鼠标双击：立即呼出主控制中心面板；
- 鼠标按住拖拽：随意将胶囊放置在屏幕任何位置（避开日常工作区域）。
"""

from __future__ import annotations

import logging
import math
import time
from typing import Callable, Optional

import objc
import AppKit
from Foundation import NSObject, NSMakeRect, NSMakeSize, NSColor, NSTimer
from Quartz import (
    CGColorCreateGenericRGB,
)

logger = logging.getLogger(__name__)

_PILL_WIDTH = 156
_PILL_HEIGHT = 34
_DOT_SIZE = 6.0

_LEGIBILITY_SHADOW_CG = CGColorCreateGenericRGB(0.0, 0.0, 0.0, 1.0)
_LEGIBILITY_SHADOW_OPACITY = 0.60
_LEGIBILITY_SHADOW_RADIUS = 2.5
_LEGIBILITY_SHADOW_OFFSET = (0.0, -1.0)

_LEVEL_DB_FLOOR = -50.0
_LEVEL_DB_CEIL = -26.0


def _rms_to_level(rms: float) -> float:
    if rms <= 0.0:
        return 0.0
    db = 20.0 * math.log10(rms)
    norm = (db - _LEVEL_DB_FLOOR) / (_LEVEL_DB_CEIL - _LEVEL_DB_FLOOR)
    norm = 0.0 if norm < 0.0 else (1.0 if norm > 1.0 else norm)
    return math.sqrt(norm)


class _PillContainerView(AppKit.NSView):
    """胶囊背景渲染与鼠标事件捕获视图。"""

    _controller = None

    def acceptsFirstMouse_(self, event):
        return True

    def drawRect_(self, dirtyRect):
        bounds = self.bounds()
        radius = bounds.size.height / 2.0
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, radius, radius
        )

        # 深色半透明材质底色
        AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.10, 0.11, 0.14, 0.78).setFill()
        path.fill()

        # 细微高光描边（防深色背景融入）
        AppKit.NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.22).setStroke()
        path.setLineWidth_(1.0)
        path.stroke()

    def mouseDown_(self, event):
        if self._controller is not None:
            self._controller.handleMouseDown_(event)

    def mouseDragged_(self, event):
        if self._controller is not None:
            self._controller.handleMouseDragged_(event)

    def mouseUp_(self, event):
        if self._controller is not None:
            self._controller.handleMouseUp_(event)

    def rightMouseDown_(self, event):
        if self._controller is not None:
            self._controller.handleRightMouseDown_(event)


class FloatingPillController(NSObject):
    """桌面悬浮胶囊控制器。"""

    def initWithApp_(self, app):
        self = objc.super(FloatingPillController, self).init()
        if self is None:
            return None
        self.app = app
        self._panel: Optional[AppKit.NSPanel] = None
        self._container: Optional[_PillContainerView] = None
        self._dot: Optional[AppKit.NSView] = None
        self._label: Optional[AppKit.NSTextField] = None
        self._timer: Optional[NSTimer] = None
        self._level_history = [0.0] * 7
        self._drag_start_screen = None
        self._drag_start_window = None
        self._has_dragged = False
        self._record_start_time = 0.0
        self._current_state = "idle"
        self._build_panel()
        return self

    @objc.python_method
    def _build_panel(self):
        screen = AppKit.NSScreen.mainScreen()
        if screen is not None:
            sf = screen.frame()
            # 默认停留在屏幕底部偏右或者底部居中（y=90）
            x = sf.origin.x + (sf.size.width - _PILL_WIDTH) / 2.0
            y = sf.origin.y + 90.0
        else:
            x, y = 500.0, 90.0

        frame = NSMakeRect(x, y, _PILL_WIDTH, _PILL_HEIGHT)
        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            frame,
            AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        panel.setLevel_(AppKit.NSFloatingWindowLevel)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setMovable_(False)
        panel.setReleasedWhenClosed_(False)
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        )

        container = _PillContainerView.alloc().initWithFrame_(NSMakeRect(0, 0, _PILL_WIDTH, _PILL_HEIGHT))
        container._controller = self

        # 状态小圆点
        dot = AppKit.NSView.alloc().initWithFrame_(
            NSMakeRect(14, (_PILL_HEIGHT - _DOT_SIZE) / 2.0, _DOT_SIZE, _DOT_SIZE)
        )
        dot.setWantsLayer_(True)
        layer = dot.layer()
        layer.setCornerRadius_(_DOT_SIZE / 2.0)
        layer.setBackgroundColor_(CGColorCreateGenericRGB(0.2, 0.85, 0.3, 1.0))
        container.addSubview_(dot)
        self._dot = dot

        # 文字标签
        label = AppKit.NSTextField.alloc().initWithFrame_(
            NSMakeRect(28, (_PILL_HEIGHT - 18) / 2.0, _PILL_WIDTH - 38, 18)
        )
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        font = AppKit.NSFont.systemFontOfSize_weight_(12.0, 0.2)
        label.setFont_(font)
        label.setTextColor_(NSColor.whiteColor())
        label.setStringValue_("🎙️ Whisper 就绪")
        container.addSubview_(label)
        self._label = label

        panel.setContentView_(container)
        self._panel = panel
        self._container = container

    @objc.python_method
    def show(self):
        if self._panel is None:
            self._build_panel()
        self._panel.orderFrontRegardless()
        self._start_timer()
        self.update_state()

    @objc.python_method
    def hide(self):
        if self._panel is not None:
            self._panel.orderOut_(None)
        self._stop_timer()

    @objc.python_method
    def set_visible(self, visible: bool):
        if visible:
            self.show()
        else:
            self.hide()

    @objc.python_method
    def _start_timer(self):
        self._stop_timer()
        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.08, self, "pillTick:", None, True
        )

    @objc.python_method
    def _stop_timer(self):
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None

    def pillTick_(self, _timer):
        if self._panel is None or not self._panel.isVisible():
            return

        is_rec = hasattr(self.app, "is_recording") and self.app.is_recording()
        if is_rec:
            self._current_state = "recording"
            elapsed = time.monotonic() - self._record_start_time
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            # 获取实时电平并渲染迷你波形符号
            rms = self.app._overlay_rms() if hasattr(self.app, "_overlay_rms") else 0.0
            lvl = _rms_to_level(rms)
            self._level_history.append(lvl)
            if len(self._level_history) > 4:
                self._level_history.pop(0)

            bars = "".join([" ▂▃▅▆"[min(4, int(v * 4.9))] for v in self._level_history])
            if self._label is not None:
                self._label.setStringValue_(f"{mins:02d}:{secs:02d} {bars}")
            if self._dot is not None and self._dot.layer() is not None:
                # 录音中呼吸红点
                self._dot.layer().setBackgroundColor_(CGColorCreateGenericRGB(0.95, 0.25, 0.25, 1.0))
        else:
            if self._current_state == "recording":
                self._current_state = "idle"
                self.update_state()

    @objc.python_method
    def on_recording_started(self):
        self._record_start_time = time.monotonic()
        self._current_state = "recording"
        self._level_history = [0.0] * 4
        self.show()

    @objc.python_method
    def on_recording_stopped(self):
        self._current_state = "processing"
        if self._label is not None:
            self._label.setStringValue_("⏳ 转写中…")
        if self._dot is not None and self._dot.layer() is not None:
            self._dot.layer().setBackgroundColor_(CGColorCreateGenericRGB(0.95, 0.75, 0.2, 1.0))

    @objc.python_method
    def on_transcription_completed(self, text: str = ""):
        self._current_state = "idle"
        if self._label is not None:
            if text and text.strip():
                preview = text.strip().replace("\n", " ")
                if len(preview) > 10:
                    preview = preview[:9] + "…"
                self._label.setStringValue_(f"✓ {preview}")
            else:
                self._label.setStringValue_("🎙️ Whisper 就绪")
        if self._dot is not None and self._dot.layer() is not None:
            self._dot.layer().setBackgroundColor_(CGColorCreateGenericRGB(0.2, 0.85, 0.3, 1.0))
        # 1.5秒后切回标准模型名称
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.5, self, "restoreIdleText:", None, False
        )

    def restoreIdleText_(self, _timer):
        if self._current_state == "idle":
            self.update_state()

    @objc.python_method
    def update_state(self):
        if self._current_state != "idle":
            return
        model = "Whisper"
        if self.app and hasattr(self.app, "settings"):
            model = getattr(self.app.settings, "current_model", "large-v3")
        if self._label is not None:
            self._label.setStringValue_(f"🎙️ {model}")
        if self._dot is not None and self._dot.layer() is not None:
            self._dot.layer().setBackgroundColor_(CGColorCreateGenericRGB(0.2, 0.85, 0.3, 1.0))

    # ---------------- 鼠标交互事件 ----------------

    def handleMouseDown_(self, event):
        self._drag_start_screen = AppKit.NSEvent.mouseLocation()
        if self._panel is not None:
            self._drag_start_window = self._panel.frame().origin
        self._has_dragged = False

        # 检测双击打开控制中心
        if event.clickCount() == 2:
            if self.app and hasattr(self.app, "open_dashboard"):
                self.app.open_dashboard()

    def handleMouseDragged_(self, event):
        current_loc = AppKit.NSEvent.mouseLocation()
        if self._drag_start_screen and self._drag_start_window and self._panel:
            dx = current_loc.x - self._drag_start_screen.x
            dy = current_loc.y - self._drag_start_screen.y
            if abs(dx) > 3 or abs(dy) > 3:
                self._has_dragged = True
                new_origin = AppKit.NSMakePoint(
                    self._drag_start_window.x + dx,
                    self._drag_start_window.y + dy,
                )
                self._panel.setFrameOrigin_(new_origin)

    def handleMouseUp_(self, event):
        if not self._has_dragged and event.clickCount() == 1:
            # 单击切换录音
            if self.app is None:
                return
            if hasattr(self.app, "is_recording") and self.app.is_recording():
                if hasattr(self.app, "stop_recording"):
                    self.app.stop_recording()
            else:
                if hasattr(self.app, "start_recording"):
                    self.app.start_recording()

    def handleRightMouseDown_(self, event):
        # 右键呼出完整的状态菜单
        if self.app and hasattr(self.app, "status_bar") and self.app.status_bar:
            menu = getattr(self.app.status_bar, "status_menu", None)
            if menu and self._panel and self._container:
                AppKit.NSMenu.popUpContextMenu_withEvent_forView_(menu, event, self._container)
