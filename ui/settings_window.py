"""原生 AppKit 应用设置窗口。"""

from __future__ import annotations

import objc
import AppKit
from Foundation import NSObject


class SettingsWindowController(NSObject):
    def initWithApp_(self, app):
        self = objc.super(SettingsWindowController, self).init()
        if self is None:
            return None
        self.app = app
        self.window = None
        self._controls = {}
        self._status_label = None
        return self

    @objc.python_method
    def show(self):
        if self.window is None:
            self._build_window()
        self._load_values()
        self.window.makeKeyAndOrderFront_(None)
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    @objc.python_method
    def _build_window(self):
        width, height = 540, 370
        self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, width, height),
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskResizable,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        self.window.setTitle_("WhisperCppCmd 设置")
        self.window.setReleasedWhenClosed_(False)
        self.window.setMinSize_(AppKit.NSMakeSize(width, height))
        self.window.center()

        content = self.window.contentView()
        self._label(content, "WhisperCppCmd 设置", 32, height - 52, 500, 28, 22)
        self._label(
            content,
            "应用设置会在保存后生效。",
            34,
            height - 82,
            450,
            20,
            12,
            secondary=True,
        )

        self._label(content, "界面与入口（刘海屏防护）", 34, height - 122, 300, 24, 16)
        self._checkbox(
            content,
            "show_in_dock",
            "在 Dock 栏显示应用图标（推荐：防刘海屏/菜单栏隐藏工具折叠丢失）",
            height - 154,
        )
        self._checkbox(
            content,
            "show_floating_pill",
            "在桌面显示交互悬浮胶囊（支持点击录音、右键菜单、自由拖拽）",
            height - 182,
        )
        self._checkbox(
            content,
            "status_bar_show_title",
            "菜单栏图标显示状态文字（如“🎙️ 语音”，撑开宽度防刘海挤压）",
            height - 210,
        )
        self._checkbox(
            content,
            "update_check_enabled",
            "每天自动检查 GitHub 更新（仅提示，不自动安装）",
            height - 238,
        )
        self._button(content, "dashboard", "打开控制中心…", 34, 70, 130, 30, "openDashboard:")
        self._button(content, "models", "打开模型目录", 170, 70, 130, 30, "openModelsFolder:")
        self._button(content, "stats", "打开统计面板", 306, 70, 130, 30, "showStats:")
        self._status_label = self._label(content, "", 34, 42, 300, 20, 12, secondary=True)
        self._button(content, "save", "保存", width - 190, 24, 78, 32, "save:")
        self._button(content, "close", "关闭", width - 100, 24, 72, 32, "close:")

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
        return label

    @objc.python_method
    def _checkbox(self, parent, name, title, y):
        button = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(52, y, 650, 24))
        button.setButtonType_(AppKit.NSSwitchButton)
        button.setTitle_(title)
        button.setTarget_(self)
        button.setAction_("checkboxChanged:")
        parent.addSubview_(button)
        self._controls[name] = button

    @objc.python_method
    def _button(self, parent, name, title, x, y, width, height, action):
        button = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(x, y, width, height))
        button.setTitle_(title)
        button.setBezelStyle_(AppKit.NSBezelStyleRounded)
        button.setTarget_(self)
        button.setAction_(action)
        parent.addSubview_(button)
        self._controls[name] = button

    @objc.python_method
    def _load_values(self):
        settings = self.app.settings
        dock_control = self._controls.get("show_in_dock")
        if dock_control is not None:
            dock_control.setState_(
                AppKit.NSControlStateValueOn
                if getattr(settings, "show_in_dock", True)
                else AppKit.NSControlStateValueOff
            )
        pill_control = self._controls.get("show_floating_pill")
        if pill_control is not None:
            pill_control.setState_(
                AppKit.NSControlStateValueOn
                if getattr(settings, "show_floating_pill", True)
                else AppKit.NSControlStateValueOff
            )
        title_control = self._controls.get("status_bar_show_title")
        if title_control is not None:
            title_control.setState_(
                AppKit.NSControlStateValueOn
                if getattr(settings, "status_bar_show_title", True)
                else AppKit.NSControlStateValueOff
            )
        update_control = self._controls.get("update_check_enabled")
        if update_control is not None:
            update_control.setState_(
                AppKit.NSControlStateValueOn
                if settings.update_check_enabled
                else AppKit.NSControlStateValueOff
            )
        self._set_status("")

    @objc.python_method
    def _values(self):
        vals = {}
        for key in ("show_in_dock", "show_floating_pill", "status_bar_show_title", "update_check_enabled"):
            if key in self._controls:
                vals[key] = bool(self._controls[key].state())
        return vals

    def openDashboard_(self, sender):
        if self.app and hasattr(self.app, "open_dashboard"):
            self.app.open_dashboard()

    def checkboxChanged_(self, sender):
        # 先更新控件状态，点击“保存”时统一写入，避免每个勾选都重建运行时对象。
        self._set_status("设置将在点击“保存”后生效。")

    def save_(self, sender):
        self.app.apply_app_settings(self._values())
        self._set_status("已保存。")

    def openModelsFolder_(self, sender):
        self.app.open_models_folder()

    def showStats_(self, sender):
        self.app.show_stats()

    def close_(self, sender):
        self.window.orderOut_(None)

    @objc.python_method
    def _set_status(self, value):
        if self._status_label is not None:
            self._status_label.setStringValue_(value)
