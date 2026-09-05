"""Brief nonactivating feedback, with the same translucent skin as recording."""
import AppKit
import objc
from Foundation import NSObject


class FeedbackController(NSObject):
    def init(self):
        self = objc.super(FeedbackController, self).init()
        if self is None:
            return None
        self.panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, 330, 44),
            AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered, False)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        self.panel.setLevel_(AppKit.NSFloatingWindowLevel)
        self.panel.setIgnoresMouseEvents_(True)
        self.panel.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary)
        view = self.panel.contentView()
        view.setWantsLayer_(True)
        view.layer().setCornerRadius_(22)
        view.layer().setBackgroundColor_(AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.12, 0.82).CGColor())
        view.layer().setBorderWidth_(0.5)
        view.layer().setBorderColor_(AppKit.NSColor.colorWithCalibratedWhite_alpha_(1, 0.3).CGColor())
        self.label = AppKit.NSTextField.labelWithString_("")
        self.label.setFrame_(AppKit.NSMakeRect(12, 12, 306, 20))
        self.label.setAlignment_(AppKit.NSTextAlignmentCenter)
        self.label.setTextColor_(AppKit.NSColor.whiteColor())
        view.addSubview_(self.label)
        return self

    @objc.python_method
    def show_message(self, text, timeout=2.5):
        NSObject.cancelPreviousPerformRequestsWithTarget_(self)
        frame = AppKit.NSScreen.mainScreen().visibleFrame()
        self.panel.setFrameOrigin_(AppKit.NSMakePoint(frame.origin.x + (frame.size.width - 330) / 2, frame.origin.y + 60))
        self.label.setStringValue_(text)
        self.panel.orderFrontRegardless()
        if timeout is not None:
            self.performSelector_withObject_afterDelay_("hide:", None, timeout)

    def hide_(self, sender):
        self.panel.orderOut_(None)
