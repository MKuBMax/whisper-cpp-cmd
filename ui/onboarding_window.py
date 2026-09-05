"""首次启动向导与权限引导中心。"""

from __future__ import annotations

import objc
import AppKit
from Foundation import NSObject, NSNotificationCenter


_PERMISSION_ROWS = (
    (
        "microphone",
        "麦克风权限",
        "用于捕获说话音频并进行本地转写（核心必需）",
        True,
    ),
    (
        "accessibility",
        "辅助功能权限",
        "用于全局监听右 Command 录音热键（核心必需）",
        True,
    ),
    (
        "input_monitoring",
        "输入监控权限",
        "辅助系统级键盘事件捕获（建议开启）",
        False,
    ),
)


class OnboardingWindowController(NSObject):
    """首次启动向导与权限引导窗口。

    提供友好的权限状态展示、一键打开系统设置、以及手动清理旧条目的指引。
    支持正常关闭窗口与跳过，不阻塞用户正常使用。
    """

    def initWithApp_(self, app):
        self = objc.super(OnboardingWindowController, self).init()
        if self is None:
            return None
        self.app = app
        self.window = None
        self._permission_status_labels = {}
        self._permission_detail_labels = {}
        self._permission_buttons = {}
        self._finish_button = None
        self._summary_label = None
        self._permissions_ready = False
        return self

    @objc.python_method
    def show(self):
        if self.window is None:
            self._build_window()
        self.refresh_permissions()
        self.window.orderFrontRegardless()
        self.window.makeKeyWindow()
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    @objc.python_method
    def _build_window(self):
        width, height = 640, 520
        style_mask = (
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
        )
        self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, width, height),
            style_mask,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        self.window.setTitle_("欢迎使用 WhisperCppCmd")
        self.window.setReleasedWhenClosed_(False)
        self.window.setHidesOnDeactivate_(False)
        self.window.setLevel_(AppKit.NSNormalWindowLevel)
        self.window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
        )
        self.window.setDelegate_(self)
        self.window.center()

        # 监听窗口激活通知，当用户从系统设置返回时自动静默刷新权限状态
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self,
            "windowDidBecomeKeyNotification:",
            AppKit.NSWindowDidBecomeKeyNotification,
            self.window,
        )

        content = self.window.contentView()

        # 头部欢迎区
        self._label(content, "🎙️ 欢迎使用 WhisperCppCmd", 36, 462, 560, 32, 22, bold=True)
        self._label(
            content,
            "本地离线语音输入工具：音频捕获与转写完全在本机完成，严格保护隐私。",
            38,
            436,
            560,
            20,
            13,
            secondary=True,
        )

        # 权限卡片区说明
        self._label(content, "1. 授予系统权限", 38, 396, 360, 22, 15, bold=True)
        self._label(
            content,
            "点击对应按钮打开系统设置授权；返回此页面时状态将自动刷新。",
            38,
            374,
            560,
            18,
            12,
            secondary=True,
        )

        # 权限列表行
        for index, (key, title, detail, _required) in enumerate(_PERMISSION_ROWS):
            self._permission_row(content, key, title, detail, 310 - index * 66)

        # 常见问题小贴士
        tip_text = (
            "💡 提示：若系统设置中已勾选辅助功能但仍提示未生效，通常是 macOS 权限缓存问题。\n"
            "   请在系统设置中选中 WhisperCppCmd，点击底部的 '-' 号删除，再点 '+' 重新添加当前 App。"
        )
        self._label(content, tip_text, 40, 96, 560, 36, 11, secondary=True)

        # 状态概要说明
        self._summary_label = self._label(
            content,
            "正在检查系统权限状态…",
            38,
            58,
            560,
            20,
            12,
            secondary=True,
        )

        # 底部操作栏
        self._button(content, "打开模型目录", 36, 18, 116, "openModelsFolder:")
        self._button(content, "偏好设置…", 160, 18, 96, "openSettings:")
        self._button(content, "稍后设置", 420, 18, 90, "skipOnboarding:")
        self._finish_button = self._button(content, "完成", 520, 18, 84, "finish:")
        self._finish_button.setKeyEquivalent_("\r")

    @objc.python_method
    def _label(self, parent, title, x, y, width, height, size=13, secondary=False, bold=False):
        label = AppKit.NSTextField.alloc().initWithFrame_(AppKit.NSMakeRect(x, y, width, height))
        label.setStringValue_(title)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        if bold:
            label.setFont_(AppKit.NSFont.boldSystemFontOfSize_(size))
        else:
            label.setFont_(AppKit.NSFont.systemFontOfSize_(size))
        if secondary:
            label.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        parent.addSubview_(label)
        return label

    @objc.python_method
    def _button(self, parent, title, x, y, width, action):
        button = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(x, y, width, 30))
        button.setTitle_(title)
        button.setBezelStyle_(AppKit.NSBezelStyleRounded)
        button.setTarget_(self)
        button.setAction_(action)
        parent.addSubview_(button)
        return button

    @objc.python_method
    def _permission_row(self, parent, key, title, detail, y):
        self._label(parent, title, 50, y + 24, 250, 20, 13, bold=True)
        detail_label = self._label(parent, detail, 50, y + 4, 340, 18, 11, secondary=True)
        status_label = self._label(parent, "检查中…", 400, y + 14, 110, 20, 13)
        button = self._button(parent, "打开设置", 520, y + 10, 84, "openPermission:")
        button.setRepresentedObject_(key)
        self._permission_detail_labels[key] = detail_label
        self._permission_status_labels[key] = status_label
        self._permission_buttons[key] = button

    def windowDidBecomeKeyNotification_(self, _notification):
        """窗口被激活时（如用户从系统设置切换回来）自动刷新权限状态。"""
        self.refresh_permissions()

    def windowWillClose_(self, _notification):
        """窗口关闭时保存完成标记。"""
        if self.app and hasattr(self.app, "settings"):
            self.app.settings.onboarding_completed = True
            self.app.settings.save()

    @objc.python_method
    def refresh_permissions(self):
        """重新读取权限并刷新页面。"""
        if self.app is None:
            return
        if hasattr(self.app, "refresh_accessibility_permission_status"):
            self.app.refresh_accessibility_permission_status()
        if hasattr(self.app, "get_permission_status"):
            self.update_permission_status(self.app.get_permission_status())

    @objc.python_method
    def update_permission_status(self, statuses):
        statuses = statuses or {}
        for key, _title, _detail, _required in _PERMISSION_ROWS:
            granted = bool(statuses.get(key, False))
            status_label = self._permission_status_labels.get(key)
            if status_label is not None:
                status_label.setStringValue_("✓ 已授权" if granted else "✕ 未授权")
                status_label.setTextColor_(
                    AppKit.NSColor.systemGreenColor()
                    if granted
                    else AppKit.NSColor.systemOrangeColor()
                )
            button = self._permission_buttons.get(key)
            if button is not None:
                button.setTitle_("已授权" if granted else "打开设置")
                button.setEnabled_(not granted)

        self._permissions_ready = all(
            bool(statuses.get(key, False))
            for key, _title, _detail, required in _PERMISSION_ROWS
            if required
        )

        if self._summary_label is not None:
            if self._permissions_ready:
                if statuses.get("input_monitoring", False):
                    summary = "✓ 所有权限已配置完毕，您可以正常使用快捷键进行语音输入。"
                else:
                    summary = "✓ 核心权限已就绪；如需更稳定的键盘监听，可在系统设置中允许「输入监控」。"
                self._summary_label.setTextColor_(AppKit.NSColor.systemGreenColor())
            else:
                summary = "请授权麦克风与辅助功能权限；授权后返回本页面即可自动生效。"
                self._summary_label.setTextColor_(AppKit.NSColor.secondaryLabelColor())
            self._summary_label.setStringValue_(summary)

        if self._finish_button is not None:
            self._finish_button.setTitle_("开始使用" if self._permissions_ready else "完成")

    def openModelsFolder_(self, sender):
        if self.app and hasattr(self.app, "open_models_folder"):
            self.app.open_models_folder()

    def openSettings_(self, sender):
        if self.app and hasattr(self.app, "open_settings"):
            self.app.open_settings()

    def openPermission_(self, sender):
        if self.app and hasattr(self.app, "request_permission"):
            self.app.request_permission(str(sender.representedObject()))

    def skipOnboarding_(self, sender):
        """稍后设置，优雅关闭窗口并标记已阅读，自动打开控制中心。"""
        if self.app and hasattr(self.app, "settings"):
            self.app.settings.onboarding_completed = True
            self.app.settings.save()
        if self.window is not None:
            self.window.orderOut_(None)
        if self.app and hasattr(self.app, "open_dashboard"):
            self.app.open_dashboard()

    def finish_(self, sender):
        """点击完成或开始使用，自动打开控制中心。"""
        self.refresh_permissions()
        if self.app and hasattr(self.app, "settings"):
            self.app.settings.onboarding_completed = True
            self.app.settings.save()
        if self.window is not None:
            self.window.orderOut_(None)
        if self.app and hasattr(self.app, "open_dashboard"):
            self.app.open_dashboard()
