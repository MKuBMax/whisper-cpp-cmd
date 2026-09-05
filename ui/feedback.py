"""短暂结果提示，使用与录音浮窗一致的胶囊外观。"""
import AppKit
import objc
from Foundation import NSObject


_WIDTH = 148
_HEIGHT = 34
_MARGIN_BOTTOM = 80


class FeedbackController(NSObject):
    """Nonactivating result/status capsule sharing the recording overlay geometry."""

    def init(self):
        self = objc.super(FeedbackController, self).init()
        if self is None:
            return None
        self.panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, _WIDTH, _HEIGHT),
            AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered, False)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        self.panel.setLevel_(AppKit.NSScreenSaverWindowLevel)
        self.panel.setIgnoresMouseEvents_(True)
        self.panel.setHasShadow_(False)
        self.panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        view = self.panel.contentView()
        view.setWantsLayer_(True)
        layer = view.layer()
        layer.setCornerRadius_(_HEIGHT / 2.0)
        layer.setBackgroundColor_(AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.20).CGColor())
        layer.setBorderWidth_(0.7)
        layer.setBorderColor_(AppKit.NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.42).CGColor())
        self.label = AppKit.NSTextField.labelWithString_("")
        self.label.setFrame_(AppKit.NSMakeRect(10, 8, _WIDTH - 20, 18))
        self.label.setAlignment_(AppKit.NSTextAlignmentCenter)
        self.label.setFont_(AppKit.NSFont.systemFontOfSize_(12.0))
        self.label.setTextColor_(AppKit.NSColor.whiteColor())
        view.addSubview_(self.label)
        return self

    @objc.python_method
    def show_message(self, text, timeout=1.0):
        NSObject.cancelPreviousPerformRequestsWithTarget_(self)
        screen = AppKit.NSScreen.mainScreen()
        frame = screen.visibleFrame() if screen is not None else AppKit.NSMakeRect(0, 0, 1440, 900)
        self.panel.setFrameOrigin_(AppKit.NSMakePoint(
            frame.origin.x + (frame.size.width - _WIDTH) / 2,
            frame.origin.y + _MARGIN_BOTTOM,
        ))
        value = str(text or "")
        if len(value) > 18:
            value = value[:17] + "…"
        self.label.setStringValue_(value)
        self.panel.setAlphaValue_(1.0)
        self.panel.orderFrontRegardless()
        if timeout is not None:
            self.performSelector_withObject_afterDelay_("hide:", None, timeout)

    def hide_(self, sender):
        self.panel.orderOut_(None)
