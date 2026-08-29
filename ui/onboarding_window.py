"""首次启动向导。"""

from __future__ import annotations

import objc
import AppKit
from Foundation import NSObject


class OnboardingWindowController(NSObject):
    def initWithApp_(self, app):
        self = objc.super(OnboardingWindowController, self).init()
        if self is None:
            return None
        self.app = app
        self.window = None
        return self

    @objc.python_method
    def show(self):
        if self.window is None:
            self._build_window()
        self.window.makeKeyAndOrderFront_(None)
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    @objc.python_method
    def _build_window(self):
        width, height = 620, 430
        self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, width, height),
            AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        self.window.setTitle_("欢迎使用 WhisperCppCmd")
        self.window.setReleasedWhenClosed_(False)
        self.window.center()
        content = self.window.contentView()
        self._label(content, "欢迎使用 WhisperCppCmd", 34, 360, 500, 32, 24)
        self._label(
            content,
            "录音和转写全部在本机完成，不会上传音频或听写文本。",
            36,
            326,
            540,
            22,
            13,
            secondary=True,
        )
        body = (
            "开始使用只需要完成三件事：\n\n"
            "1. 在模型目录中放入 ggml-large-v3.bin（也可以使用其他 GGML 模型）。\n"
            "2. 在系统设置中允许麦克风、辅助功能和输入监控。\n"
            "3. 按住右 Command 说话，松开后文字会进入当前输入框。\n\n"
            "放入模型后，从菜单栏“切换模型 → 重新加载当前模型”即可开始使用；"
            "菜单栏中还可以切换语言、麦克风、VAD 和浮窗。"
        )
        text = AppKit.NSTextView.alloc().initWithFrame_(AppKit.NSMakeRect(36, 118, 548, 184))
        text.setString_(body)
        text.setEditable_(False)
        text.setSelectable_(True)
        text.setDrawsBackground_(False)
        text.setFont_(AppKit.NSFont.systemFontOfSize_(14))
        content.addSubview_(text)

        self._button(content, "打开模型目录", 36, 72, 140, "openModelsFolder:")
        self._button(content, "打开设置", 190, 72, 110, "openSettings:")
        self._button(content, "打开权限设置", 314, 72, 130, "checkPermissions:")
        self._button(content, "我知道了", 472, 24, 112, "finish:")
        self._button(content, "稍后提醒", 350, 24, 112, "later:")

    @objc.python_method
    def _label(self, parent, title, x, y, width, height, size=13, secondary=False):
        label = AppKit.NSTextField.alloc().initWithFrame_(AppKit.NSMakeRect(x, y, width, height))
        label.setStringValue_(title)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setFont_(AppKit.NSFont.systemFontOfSize_(size))
        if secondary:
            label.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        parent.addSubview_(label)

    @objc.python_method
    def _button(self, parent, title, x, y, width, action):
        button = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(x, y, width, 30))
        button.setTitle_(title)
        button.setBezelStyle_(AppKit.NSBezelStyleRounded)
        button.setTarget_(self)
        button.setAction_(action)
        parent.addSubview_(button)

    def openModelsFolder_(self, sender):
        self.app.open_models_folder()

    def openSettings_(self, sender):
        self.app.open_settings()

    def checkPermissions_(self, sender):
        self.app.request_permissions()

    def finish_(self, sender):
        self.app.settings.onboarding_completed = True
        self.app.settings.save()
        self.window.orderOut_(None)

    def later_(self, sender):
        self.window.orderOut_(None)
