#!/usr/bin/env python3
"""原生 AppKit 主控制中心面板 (Dashboard)。

提供集中的模型选择、麦克风设备切换、实时麦克风音量跳动条、
桌面悬浮胶囊开关、菜单栏图标重挂载、以及最近听写历史快速复制。
专为解决带刘海屏 MacBook 上菜单栏图标容易被遮挡或折叠的痛点设计。
"""

from __future__ import annotations

import os
from typing import Optional

import objc
import AppKit
from Foundation import NSObject, NSMakeRect, NSMakeSize, NSTimer

from config.version import APP_VERSION


class DashboardWindowController(NSObject):
    """WhisperCppCmd 控制中心主窗口控制器。"""

    def initWithApp_(self, app):
        self = objc.super(DashboardWindowController, self).init()
        if self is None:
            return None
        self.app = app
        self.window = None
        self._level_timer: Optional[NSTimer] = None
        self._model_popup = None
        self._mic_popup = None
        self._dictation_popup = None
        self._chinese_popup = None
        self._auto_paste_chk = None
        self._duck_chk = None
        self._dock_chk = None
        self._pill_chk = None
        self._status_title_chk = None
        self._status_badge = None
        self._record_button = None
        self._level_bar = None
        self._history_container = None
        self._history_labels = []
        self._history_buttons = []
        return self

    @objc.python_method
    def show(self):
        if self.window is None:
            self._build_window()
        self._refresh_all_data()
        self.window.makeKeyAndOrderFront_(None)
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self._start_level_timer()

    @objc.python_method
    def _build_window(self):
        width, height = 620, 680
        style_mask = (
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskMiniaturizable
        )
        self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, width, height),
            style_mask,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        self.window.setTitle_(f"WhisperCppCmd 控制中心 (v{APP_VERSION})")
        self.window.setReleasedWhenClosed_(False)
        self.window.center()
        self.window.setDelegate_(self)

        content = self.window.contentView()

        # ---------------- 顶部标题与状态 ----------------
        self._label(content, f"🎙️ WhisperCppCmd 控制中心", 32, height - 48, 360, 28, 20, bold=True)
        self._label(
            content,
            f"本地离线语音输入 · v{APP_VERSION}",
            34,
            height - 70,
            300,
            18,
            12,
            secondary=True,
        )

        # 状态徽标与快捷录音按钮
        self._status_badge = self._label(
            content, "状态：空闲", width - 260, height - 46, 120, 22, 13, bold=True
        )
        self._status_badge.setTextColor_(AppKit.NSColor.systemGreenColor())

        self._record_button = AppKit.NSButton.alloc().initWithFrame_(
            NSMakeRect(width - 130, height - 52, 98, 32)
        )
        self._record_button.setTitle_("开始录音")
        self._record_button.setBezelStyle_(AppKit.NSBezelStyleRounded)
        self._record_button.setTarget_(self)
        self._record_button.setAction_("toggleRecord:")
        content.addSubview_(self._record_button)

        # 麦克风实时电平指示条
        self._label(content, "输入电平：", 34, height - 98, 70, 18, 12, secondary=True)
        self._level_bar = AppKit.NSLevelIndicator.alloc().initWithFrame_(
            NSMakeRect(104, height - 98, 240, 16)
        )
        self._level_bar.setLevelIndicatorStyle_(AppKit.NSContinuousCapacityLevelIndicatorStyle)
        self._level_bar.setMinValue_(0.0)
        self._level_bar.setMaxValue_(1.0)
        self._level_bar.setWarningValue_(0.7)
        self._level_bar.setCriticalValue_(0.9)
        self._level_bar.setDoubleValue_(0.0)
        content.addSubview_(self._level_bar)

        self._label(
            content,
            "（说话时跳动即表示麦克风正常）",
            350,
            height - 98,
            240,
            18,
            11,
            secondary=True,
        )

        # 分割线
        self._separator(content, 32, height - 114, width - 64)

        # ---------------- 核心配置区 ----------------
        self._label(content, "核心转写设置", 34, height - 144, 200, 22, 15, bold=True)

        # 模型选择
        self._label(content, "当前模型：", 48, height - 176, 80, 20, 13)
        self._model_popup = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(130, height - 180, 260, 26), False
        )
        self._model_popup.setTarget_(self)
        self._model_popup.setAction_("modelChanged:")
        content.addSubview_(self._model_popup)

        open_models_btn = AppKit.NSButton.alloc().initWithFrame_(
            NSMakeRect(400, height - 180, 110, 26)
        )
        open_models_btn.setTitle_("打开模型目录")
        open_models_btn.setBezelStyle_(AppKit.NSBezelStyleRounded)
        open_models_btn.setTarget_(self)
        open_models_btn.setAction_("openModelsFolder:")
        content.addSubview_(open_models_btn)

        # 麦克风选择
        self._label(content, "输入设备：", 48, height - 212, 80, 20, 13)
        self._mic_popup = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(130, height - 216, 260, 26), False
        )
        self._mic_popup.setTarget_(self)
        self._mic_popup.setAction_("micChanged:")
        content.addSubview_(self._mic_popup)

        # 听写模式
        self._label(content, "听写模式：", 48, height - 248, 80, 20, 13)
        self._dictation_popup = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(130, height - 252, 160, 26), False
        )
        self._dictation_popup.addItemsWithTitles_(["实时预览 (preview)", "快速听写 (quick)"])
        self._dictation_popup.setTarget_(self)
        self._dictation_popup.setAction_("dictationChanged:")
        content.addSubview_(self._dictation_popup)

        # 中文脚本
        self._label(content, "中文输出：", 304, height - 248, 80, 20, 13)
        self._chinese_popup = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(384, height - 252, 140, 26), False
        )
        self._chinese_popup.addItemsWithTitles_(["简体中文", "繁体中文"])
        self._chinese_popup.setTarget_(self)
        self._chinese_popup.setAction_("chineseScriptChanged:")
        content.addSubview_(self._chinese_popup)

        # 行为开关
        self._auto_paste_chk = self._checkbox(
            content, "自动粘贴识别文字到活动光标处", 48, height - 286, "toggleAutoPaste:"
        )
        self._duck_chk = self._checkbox(
            content, "录音时自动压低其他媒体播放音量", 310, height - 286, "toggleDuck:"
        )

        # 分割线
        self._separator(content, 32, height - 304, width - 64)

        # ---------------- 防刘海遮挡与界面入口 ----------------
        self._label(content, "界面与显示入口（针对刘海屏优化）", 34, height - 334, 340, 22, 15, bold=True)

        self._dock_chk = self._checkbox(
            content,
            "在 Dock 栏常驻显示应用图标（推荐：菜单栏被刘海遮挡时最可靠的入口）",
            48,
            height - 364,
            "toggleDock:",
        )
        self._pill_chk = self._checkbox(
            content,
            "在桌面显示交互悬浮胶囊（支持点击录音、右键完整菜单、双击控制中心、自由拖拽）",
            48,
            height - 392,
            "toggleFloatingPill:",
        )
        self._status_title_chk = self._checkbox(
            content,
            "菜单栏图标显示状态文字（如“🎙️ 语音”，撑开宽度防刘海挤压）",
            48,
            height - 420,
            "toggleStatusTitle:",
        )

        reanchor_btn = AppKit.NSButton.alloc().initWithFrame_(
            NSMakeRect(48, height - 454, 180, 26)
        )
        reanchor_btn.setTitle_("重新挂载菜单栏图标 🔄")
        reanchor_btn.setBezelStyle_(AppKit.NSBezelStyleRounded)
        reanchor_btn.setTarget_(self)
        reanchor_btn.setAction_("reanchorStatusBar:")
        content.addSubview_(reanchor_btn)

        self._label(
            content,
            "（若菜单栏图标丢失，点击此按钮可强制向系统重新注册）",
            236,
            height - 450,
            340,
            18,
            11,
            secondary=True,
        )

        # 分割线
        self._separator(content, 32, height - 468, width - 64)

        # ---------------- 最近识别历史 ----------------
        self._label(content, "最近识别历史", 34, height - 498, 200, 22, 15, bold=True)

        # 历史记录列表槽位（展示最近 3 条）
        self._history_labels = []
        self._history_buttons = []
        for i in range(3):
            row_y = height - 530 - i * 32
            lbl = self._label(content, "（暂无历史）", 48, row_y, 450, 20, 12, secondary=True)
            btn = AppKit.NSButton.alloc().initWithFrame_(NSMakeRect(506, row_y - 2, 60, 24))
            btn.setTitle_("复制")
            btn.setBezelStyle_(AppKit.NSBezelStyleRounded)
            btn.setTarget_(self)
            btn.setAction_("copyHistoryItem:")
            btn.setRepresentedObject_(i)
            btn.setEnabled_(False)
            content.addSubview_(btn)
            self._history_labels.append(lbl)
            self._history_buttons.append(btn)

        # ---------------- 底部操作栏 ----------------
        self._separator(content, 32, 62, width - 64)

        onboarding_btn = AppKit.NSButton.alloc().initWithFrame_(NSMakeRect(32, 20, 130, 32))
        onboarding_btn.setTitle_("欢迎与权限向导…")
        onboarding_btn.setBezelStyle_(AppKit.NSBezelStyleRounded)
        onboarding_btn.setTarget_(self)
        onboarding_btn.setAction_("openOnboarding:")
        content.addSubview_(onboarding_btn)

        prefs_btn = AppKit.NSButton.alloc().initWithFrame_(NSMakeRect(170, 20, 96, 32))
        prefs_btn.setTitle_("偏好设置…")
        prefs_btn.setBezelStyle_(AppKit.NSBezelStyleRounded)
        prefs_btn.setTarget_(self)
        prefs_btn.setAction_("openSettings:")
        content.addSubview_(prefs_btn)

        update_btn = AppKit.NSButton.alloc().initWithFrame_(NSMakeRect(274, 20, 88, 32))
        update_btn.setTitle_("检查更新")
        update_btn.setBezelStyle_(AppKit.NSBezelStyleRounded)
        update_btn.setTarget_(self)
        update_btn.setAction_("checkUpdate:")
        content.addSubview_(update_btn)

        close_btn = AppKit.NSButton.alloc().initWithFrame_(NSMakeRect(width - 120, 20, 88, 32))
        close_btn.setTitle_("完成")
        close_btn.setBezelStyle_(AppKit.NSBezelStyleRounded)
        close_btn.setTarget_(self)
        close_btn.setAction_("closeWindow:")
        content.addSubview_(close_btn)

    @objc.python_method
    def _label(self, parent, title, x, y, width, height, size=13, secondary=False, bold=False):
        label = AppKit.NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
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
    def _checkbox(self, parent, title, x, y, action):
        button = AppKit.NSButton.alloc().initWithFrame_(NSMakeRect(x, y, 520, 22))
        button.setButtonType_(AppKit.NSSwitchButton)
        button.setTitle_(title)
        button.setTarget_(self)
        button.setAction_(action)
        parent.addSubview_(button)
        return button

    @objc.python_method
    def _separator(self, parent, x, y, width):
        box = AppKit.NSBox.alloc().initWithFrame_(NSMakeRect(x, y, width, 1))
        box.setBoxType_(AppKit.NSBoxSeparator)
        parent.addSubview_(box)

    # ---------------- 数据加载与状态同步 ----------------

    @objc.python_method
    def _refresh_all_data(self):
        if self.app is None:
            return
        settings = getattr(self.app, "settings", None)
        if settings is None:
            return

        # 刷新模型列表
        models = settings.list_available_models()
        self._model_popup.removeAllItems()
        self._model_popup.addItemsWithTitles_(models if models else ["(未检测到模型)"])
        if settings.current_model in models:
            self._model_popup.selectItemWithTitle_(settings.current_model)

        # 刷新麦克风设备
        self._mic_popup.removeAllItems()
        devices = []
        if hasattr(self.app, "list_audio_devices"):
            try:
                devices = self.app.list_audio_devices() or []
            except Exception:
                devices = []
        device_names = [d.get("name", "") for d in devices if d.get("name")]
        current_mic = getattr(settings, "audio_device_name", "") or "系统默认"
        if not device_names:
            device_names = [current_mic or "系统默认"]
        self._mic_popup.addItemsWithTitles_(device_names)
        if current_mic in device_names:
            self._mic_popup.selectItemWithTitle_(current_mic)

        # 听写模式
        cur_mode = getattr(settings, "dictation_mode", "preview")
        self._dictation_popup.selectItemAtIndex_(0 if cur_mode == "preview" else 1)

        # 中文脚本
        cur_script = getattr(settings, "chinese_script", "simplified")
        self._chinese_popup.selectItemAtIndex_(0 if cur_script == "simplified" else 1)

        # 开关
        self._auto_paste_chk.setState_(
            AppKit.NSControlStateValueOn
            if getattr(settings, "auto_paste", True)
            else AppKit.NSControlStateValueOff
        )
        self._duck_chk.setState_(
            AppKit.NSControlStateValueOn
            if getattr(settings, "duck_media", True)
            else AppKit.NSControlStateValueOff
        )
        self._dock_chk.setState_(
            AppKit.NSControlStateValueOn
            if getattr(settings, "show_in_dock", True)
            else AppKit.NSControlStateValueOff
        )
        self._pill_chk.setState_(
            AppKit.NSControlStateValueOn
            if getattr(settings, "show_floating_pill", True)
            else AppKit.NSControlStateValueOff
        )
        self._status_title_chk.setState_(
            AppKit.NSControlStateValueOn
            if getattr(settings, "status_bar_show_title", True)
            else AppKit.NSControlStateValueOff
        )

        # 状态徽标与录音按钮
        is_rec = hasattr(self.app, "is_recording") and self.app.is_recording()
        self._update_record_ui(is_rec)

        # 刷新历史记录
        self._refresh_history()

    @objc.python_method
    def _update_record_ui(self, is_recording: bool):
        if self._status_badge is not None:
            if is_recording:
                self._status_badge.setStringValue_("状态：🔴 录音中")
                self._status_badge.setTextColor_(AppKit.NSColor.systemRedColor())
            else:
                self._status_badge.setStringValue_("状态：🟢 空闲")
                self._status_badge.setTextColor_(AppKit.NSColor.systemGreenColor())

        if self._record_button is not None:
            self._record_button.setTitle_("结束录音" if is_recording else "开始录音")

    @objc.python_method
    def _refresh_history(self):
        items = []
        if self.app and hasattr(self.app, "get_recent_history"):
            try:
                items = self.app.get_recent_history(count=3) or []
            except Exception:
                items = []

        for i in range(3):
            if i < len(self._history_labels):
                lbl = self._history_labels[i]
                btn = self._history_buttons[i]
                if i < len(items) and items[i]:
                    text = str(items[i]).strip()
                    display_text = text if len(text) <= 38 else text[:35] + "…"
                    lbl.setStringValue_(f"{i+1}. {display_text}")
                    lbl.setTextColor_(AppKit.NSColor.labelColor())
                    btn.setEnabled_(True)
                else:
                    lbl.setStringValue_("（暂无历史）")
                    lbl.setTextColor_(AppKit.NSColor.secondaryLabelColor())
                    btn.setEnabled_(False)

    # ---------------- 定时器与电平监听 ----------------

    @objc.python_method
    def _start_level_timer(self):
        self._stop_level_timer()
        self._level_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.1, self, "levelTick:", None, True
        )

    @objc.python_method
    def _stop_level_timer(self):
        if self._level_timer is not None:
            self._level_timer.invalidate()
            self._level_timer = None

    def levelTick_(self, _timer):
        if self.window is None or not self.window.isVisible():
            self._stop_level_timer()
            return
        if self.app and hasattr(self.app, "_overlay_rms"):
            rms = self.app._overlay_rms()
            import math
            if rms > 0:
                db = 20.0 * math.log10(rms)
                val = max(0.0, min(1.0, (db + 50.0) / 30.0))
            else:
                val = 0.0
            if self._level_bar is not None:
                self._level_bar.setDoubleValue_(val)

        is_rec = hasattr(self.app, "is_recording") and self.app.is_recording()
        self._update_record_ui(is_rec)

    def windowWillClose_(self, _notification):
        self._stop_level_timer()

    # ---------------- 交互动作 ----------------

    def toggleRecord_(self, sender):
        if self.app is None:
            return
        if hasattr(self.app, "is_recording") and self.app.is_recording():
            if hasattr(self.app, "stop_recording"):
                self.app.stop_recording()
        else:
            if hasattr(self.app, "start_recording"):
                self.app.start_recording()
        self._refresh_all_data()

    def modelChanged_(self, sender):
        chosen = sender.titleOfSelectedItem()
        if self.app and hasattr(self.app, "select_model") and chosen:
            self.app.select_model(chosen)

    def micChanged_(self, sender):
        chosen = sender.titleOfSelectedItem()
        if self.app and hasattr(self.app, "select_mic") and chosen:
            self.app.select_mic(chosen)

    def dictationChanged_(self, sender):
        mode = "preview" if sender.indexOfSelectedItem() == 0 else "quick"
        if self.app and hasattr(self.app, "select_dictation_mode"):
            self.app.select_dictation_mode(mode)

    def chineseScriptChanged_(self, sender):
        script = "simplified" if sender.indexOfSelectedItem() == 0 else "traditional"
        if self.app and hasattr(self.app, "select_chinese_script"):
            self.app.select_chinese_script(script)

    def toggleAutoPaste_(self, sender):
        if self.app and hasattr(self.app, "toggle_auto_paste"):
            self.app.toggle_auto_paste()

    def toggleDuck_(self, sender):
        if self.app and hasattr(self.app, "toggle_duck_media"):
            self.app.toggle_duck_media()

    def toggleDock_(self, sender):
        if self.app and hasattr(self.app, "toggle_show_in_dock"):
            self.app.toggle_show_in_dock()

    def toggleFloatingPill_(self, sender):
        if self.app and hasattr(self.app, "toggle_floating_pill"):
            self.app.toggle_floating_pill()

    def toggleStatusTitle_(self, sender):
        if self.app and hasattr(self.app, "toggle_status_bar_title"):
            self.app.toggle_status_bar_title()

    def reanchorStatusBar_(self, sender):
        if self.app and hasattr(self.app, "reanchor_status_bar"):
            self.app.reanchor_status_bar()

    def copyHistoryItem_(self, sender):
        idx = sender.representedObject()
        if self.app and hasattr(self.app, "get_recent_history"):
            items = self.app.get_recent_history(count=3)
            if 0 <= idx < len(items) and items[idx]:
                text = str(items[idx])
                pasteboard = AppKit.NSPasteboard.generalPasteboard()
                pasteboard.clearContents()
                pasteboard.setString_forType_(text, AppKit.NSPasteboardTypeString)
                sender.setTitle_("已复制!")
                NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    1.0, self, "restoreCopyButton:", sender, False
                )

    def restoreCopyButton_(self, timer):
        btn = timer.userInfo()
        if btn is not None:
            btn.setTitle_("复制")

    def openModelsFolder_(self, sender):
        if self.app and hasattr(self.app, "open_models_folder"):
            self.app.open_models_folder()

    def openOnboarding_(self, sender):
        if self.app and hasattr(self.app, "open_onboarding"):
            self.app.open_onboarding()

    def openSettings_(self, sender):
        if self.app and hasattr(self.app, "open_settings"):
            self.app.open_settings()

    def checkUpdate_(self, sender):
        if self.app and hasattr(self.app, "check_for_updates"):
            self.app.check_for_updates(manual=True)

    def closeWindow_(self, sender):
        if self.window is not None:
            self.window.orderOut_(None)
