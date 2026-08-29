"""本地听写统计窗口。"""

from __future__ import annotations

import objc
import AppKit
from Foundation import NSObject


class StatsWindowController(NSObject):
    def initWithApp_(self, app):
        self = objc.super(StatsWindowController, self).init()
        if self is None:
            return None
        self.app = app
        self.window = None
        self.text_view = None
        return self

    @objc.python_method
    def show(self):
        if self.window is None:
            self._build_window()
        self.refresh()
        self.window.makeKeyAndOrderFront_(None)
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    @objc.python_method
    def _build_window(self):
        self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, 520, 420),
            AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        self.window.setTitle_("WhisperCppCmd 统计")
        self.window.setReleasedWhenClosed_(False)
        self.window.center()
        scroll = AppKit.NSScrollView.alloc().initWithFrame_(AppKit.NSMakeRect(24, 62, 472, 330))
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(AppKit.NSBezelBorder)
        self.text_view = AppKit.NSTextView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 455, 320))
        self.text_view.setEditable_(False)
        self.text_view.setSelectable_(True)
        self.text_view.setFont_(AppKit.NSFont.userFixedPitchFontOfSize_(13))
        scroll.setDocumentView_(self.text_view)
        self.window.contentView().addSubview_(scroll)

        refresh = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(24, 20, 100, 30))
        refresh.setTitle_("刷新")
        refresh.setBezelStyle_(AppKit.NSBezelStyleRounded)
        refresh.setTarget_(self)
        refresh.setAction_("refreshButton:")
        self.window.contentView().addSubview_(refresh)

    @objc.python_method
    def refresh(self):
        if self.text_view is not None:
            self.text_view.setString_(self.app.get_stats_text())

    def refreshButton_(self, sender):
        self.refresh()
