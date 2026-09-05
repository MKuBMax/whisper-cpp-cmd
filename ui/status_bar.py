#!/usr/bin/env python3
"""
菜单栏 UI - 负责 macOS StatusBar 和菜单项
"""

import os

import objc
import AppKit
from Foundation import NSObject

from config.paths import resource_path
from core import login_item


# 使用系统标准自适应宽度，并保持 16x16 图标尺寸
_STATUS_ITEM_LENGTH = AppKit.NSVariableStatusItemLength
_STATUS_ICON_SIZE = 16.0


class StatusBarController(NSObject):
    """macOS 菜单栏控制器"""

    def initWithApp_(self, app):
        self = objc.super(StatusBarController, self).init()
        if self is None:
            return None

        self.app = app
        self._logger = getattr(app, "_logger", None)
        self.status_item = None
        self._last_visibility_signature = None
        self.status_menu = None
        self.status_title_item = None
        self.model_item = None
        self.model_menu_item = None
        self.model_submenu = None
        self.mic_menu_item = None
        self.mic_submenu = None
        self.language_menu_item = None
        self.language_submenu = None
        self.chinese_script_menu_item = None
        self.chinese_script_submenu = None
        self.dictation_mode_menu_item = None
        self.dictation_mode_submenu = None
        self.hotkey_menu_item = None
        self.hotkey_submenu = None
        self.accessibility_permission_item = None
        self.input_monitoring_permission_item = None
        self.dashboard_item = None
        self.auto_paste_item = None
        self.show_in_dock_item = None
        self.show_floating_pill_item = None
        self.status_bar_title_item = None
        self.login_at_startup_item = None
        self.vad_item = None
        self.duck_item = None
        self.duck_submenu = None
        self.duck_enable_item = None
        self.duck_headphones_item = None
        self.overlay_item = None
        self.overlay_menu_item = None
        self.overlay_submenu = None
        self.overlay_follow_mouse_item = None
        self.backend_item = None
        self.last_result_item = None
        self.pause_item = None
        self.release_backend_item = None
        self.auto_release_menu_item = None
        self.auto_release_submenu = None
        self.auto_release_countdown_item = None
        self.copy_last_result_item = None
        self.history_menu_item = None
        self.history_submenu = None
        self.settings_item = None
        self.stats_item = None
        self.update_item = None
        self.icons = {}
        self._setup_status_item()
        return self

    def _setup_status_item(self):
        self.status_item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(28.0)
        self.status_item.setBehavior_(0)
        self.status_item.setAutosaveName_("WhisperCppCmd.StatusItem")
        self.status_item.setVisible_(True)
        self._configure_status_button(self.status_item.button())
        self._load_icons()
        self.status_menu = AppKit.NSMenu.alloc().init()
        self.status_menu.setDelegate_(self)
        self.preferences_menu = AppKit.NSMenu.alloc().init()

        def item(menu, attr, title, action=None, key=""):
            value = menu.addItemWithTitle_action_keyEquivalent_(title, action, key)
            value.setTarget_(self)
            if action is None:
                value.setEnabled_(False)
            setattr(self, attr, value)
            return value

        def submenu(attr, title, menu_attr):
            value = item(self.preferences_menu, attr, title)
            value.setEnabled_(True)
            menu = AppKit.NSMenu.alloc().init()
            value.setSubmenu_(menu)
            setattr(self, menu_attr, menu)

        item(self.status_menu, "status_title_item", "按住右 Command 开始录音")
        item(self.status_menu, "dashboard_item", "打开 WhisperCppCmd…", "openDashboard:", ",")
        self.status_menu.addItem_(AppKit.NSMenuItem.separatorItem())
        item(self.status_menu, "copy_last_result_item", "复制最近结果", "copyLastResult:")
        self.copy_last_result_item.setEnabled_(False)
        item(self.status_menu, "pause_item", "暂停监听", "togglePause:")
        preferences = item(self.status_menu, "settings_item", "输入偏好")
        preferences.setSubmenu_(self.preferences_menu)
        preferences.setEnabled_(True)
        submenu("mic_menu_item", "麦克风", "mic_submenu")
        submenu("language_menu_item", "识别语言", "language_submenu")
        submenu("chinese_script_menu_item", "中文输出", "chinese_script_submenu")
        submenu("hotkey_menu_item", "录音快捷键", "hotkey_submenu")
        item(self.preferences_menu, "login_at_startup_item", "登录时启动", "toggleLoginAtStartup:")
        item(self.preferences_menu, "show_in_dock_item", "在 Dock 显示", "toggleShowInDock:")
        item(self.preferences_menu, "overlay_item", "显示录音浮窗", "toggleOverlay:")
        item(self.preferences_menu, "duck_enable_item", "录音时降低其他声音", "toggleDuckMedia:")
        item(self.preferences_menu, "edit_glossary_item", "编辑术语表…", "editGlossary:")
        item(self.preferences_menu, "reload_glossary_item", "应用术语表", "reloadGlossary:")
        self.status_menu.addItem_(AppKit.NSMenuItem.separatorItem())
        item(self.status_menu, "update_item", "检查更新…", "checkForUpdates:")
        item(self.status_menu, "export_diagnostic_item", "导出诊断报告…", "exportDiagnostic:")
        item(self.status_menu, "quit_item", "退出 WhisperCppCmd", "quitApp:", "q")
        self.status_item.setMenu_(self.status_menu)
        if hasattr(self.status_item, "setLength_"):
            self.status_item.setLength_(28.0)
        self.status_item.setVisible_(True)
        self._configure_status_button(self.status_item.button())
        self.setState_("idle")

    def _load_icons(self):
        # 优先加载本地 22x22 Retina 模板图片，确保在任何第三方菜单栏管理器下尺寸与渲染 100% 确定
        icon_map = {
            "idle": "mic_idle.png",
            "recording": "mic_recording.png",
            "processing": "mic_processing.png",
            "error": "mic_error.png",
            "paused": "mic_error.png",
        }
        for state, filename in icon_map.items():
            path = resource_path("icons", filename)
            if os.path.isfile(path):
                image = AppKit.NSImage.alloc().initByReferencingFile_(path)
                if image and image.isValid():
                    image.setTemplate_(True)
                    image.setSize_(AppKit.NSMakeSize(_STATUS_ICON_SIZE, _STATUS_ICON_SIZE))
                    self.icons[state] = image

        if len(self.icons) >= 4:
            return

        symbol_map = {
            "idle": "mic",
            "recording": "mic.fill",
            "processing": "waveform",
            "error": "exclamationmark.triangle",
            "paused": "mic.slash",
        }
        for state, symbol_name in symbol_map.items():
            if state in self.icons:
                continue
            image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                symbol_name,
                None
            )
            if image is not None:
                image = image.copy()
                image.setTemplate_(True)
                image.setSize_(AppKit.NSMakeSize(_STATUS_ICON_SIZE, _STATUS_ICON_SIZE))
                self.icons[state] = image

    @objc.python_method
    def _log_warning(self, message, *args, **kwargs):
        if self._logger is not None:
            self._logger.warning(message, *args, **kwargs)

    @objc.python_method
    def _configure_status_button(self, button):
        """Apply only properties owned by the app; AppKit owns the button frame."""
        if button is None:
            return
        button.setHidden_(False)
        button.setEnabled_(True)
        button.setImagePosition_(AppKit.NSImageOnly)
        button.setImageScaling_(AppKit.NSImageScaleProportionallyDown)
        button.setToolTip_("语音输入运行中")
        if hasattr(button, "setAccessibilityLabel_"):
            button.setAccessibilityLabel_("WhisperCppCmd")

    @objc.python_method
    def ensure_visible(self):
        """确保状态栏项正常显示。"""
        if self.status_item is None:
            self._setup_status_item()
            return
        try:
            self.status_item.setVisible_(True)
            button = self.status_item.button()
            if button is not None:
                self._configure_status_button(button)
        except Exception:
            self._log_warning("恢复 WhisperCppCmd 菜单栏状态项失败", exc_info=True)

    def setState_(self, state):
        state_text = {
            "idle": "空闲",
            "recording": "录音中",
            "processing": "处理中",
            "error": "错误",
            "paused": "已暂停",
        }.get(state, "空闲")

        if self.status_title_item is not None:
            self.status_title_item.setTitle_(f"状态：{state_text}")

        if self.status_item is None:
            return
        button = self.status_item.button()
        if button is None:
            return
        button.setToolTip_(f"语音输入运行中 - {state_text}")

        image = self.icons.get(state) or self.icons.get("idle")

        button.setTitle_("" if image is not None else "录")
        button.setImage_(image)
        button.setImagePosition_(AppKit.NSImageOnly if image is not None else AppKit.NSNoImage)


    def setModelName_(self, model_name):
        if self.model_item is not None:
            self.model_item.setTitle_(f"模型：{model_name}")
        if self.model_menu_item is not None:
            self._set_menu_item_mixed_title(self.model_menu_item, "切换模型：", model_name)

    def setModelOptions_(self, model_payload):
        if self.model_submenu is None:
            return
        self._replace_radio_menu(self.model_submenu, model_payload, "selectModel:")
        self._append_model_actions()

    def _append_model_actions(self):
        """在模型子菜单尾部追加模型目录、下载和重载动作项。"""
        self.model_submenu.addItem_(AppKit.NSMenuItem.separatorItem())
        folder_item = self.model_submenu.addItemWithTitle_action_keyEquivalent_(
            "在 Finder 中打开模型文件夹",
            "openModelsFolder:",
            "",
        )
        folder_item.setTarget_(self)
        download_item = self.model_submenu.addItemWithTitle_action_keyEquivalent_(
            "下载模型…",
            "openModelDownloadPage:",
            "",
        )
        download_item.setTarget_(self)
        reload_item = self.model_submenu.addItemWithTitle_action_keyEquivalent_(
            "重新加载当前模型",
            "reloadModel:",
            "",
        )
        reload_item.setTarget_(self)

    def setMicOptions_(self, mic_payload):
        if self.mic_submenu is None:
            return
        self._replace_radio_menu(self.mic_submenu, mic_payload, "selectMic:")
        self._update_current_value_title(self.mic_menu_item, mic_payload, "麦克风")

    def setLanguageOptions_(self, language_payload):
        if self.language_submenu is None:
            return
        self._replace_radio_menu(self.language_submenu, language_payload, "selectLanguage:")
        self._update_current_value_title(self.language_menu_item, language_payload, "识别语言")

    def setChineseScriptOptions_(self, script_payload):
        if self.chinese_script_submenu is None:
            return
        self._replace_radio_menu(self.chinese_script_submenu, script_payload, "selectChineseScript:")
        self._update_current_value_title(self.chinese_script_menu_item, script_payload, "中文脚本")

    def setDictationModeOptions_(self, mode_payload):
        if self.dictation_mode_submenu is None:
            return
        self._replace_radio_menu(self.dictation_mode_submenu, mode_payload, "selectDictationMode:")
        self._update_current_value_title(self.dictation_mode_menu_item, mode_payload, "听写模式")

    def setHotkeyOptions_(self, hotkey_payload):
        if self.hotkey_submenu is None:
            return
        self._replace_radio_menu(self.hotkey_submenu, hotkey_payload, "selectHotkey:")
        self._update_current_value_title(self.hotkey_menu_item, hotkey_payload, "热键")

    def setBackendStatus_(self, backend_text):
        if self.backend_item is not None:
            self.backend_item.setTitle_(f"后端：{backend_text}")

    def setLastResult_(self, result_text):
        if self.last_result_item is not None:
            self.last_result_item.setTitle_(f"最近结果：{result_text}")
        if self.copy_last_result_item is not None:
            self.copy_last_result_item.setEnabled_(result_text != "无")

    def setAutoPaste_(self, enabled):
        if self.auto_paste_item is not None:
            self.auto_paste_item.setState_(
                AppKit.NSControlStateValueOn if enabled else AppKit.NSControlStateValueOff
            )

    def setLoginAtStartup_(self, enabled):
        if self.login_at_startup_item is not None:
            self.login_at_startup_item.setState_(
                AppKit.NSControlStateValueOn if enabled else AppKit.NSControlStateValueOff
            )

    def setShowInDock_(self, enabled):
        if self.show_in_dock_item is not None:
            self.show_in_dock_item.setState_(
                AppKit.NSControlStateValueOn if enabled else AppKit.NSControlStateValueOff
            )

    def setShowFloatingPill_(self, enabled):
        if self.show_floating_pill_item is not None:
            self.show_floating_pill_item.setState_(
                AppKit.NSControlStateValueOn if enabled else AppKit.NSControlStateValueOff
            )

    def setStatusBarShowTitle_(self, enabled):
        if self.status_bar_title_item is not None:
            self.status_bar_title_item.setState_(
                AppKit.NSControlStateValueOn if enabled else AppKit.NSControlStateValueOff
            )

    def setAccessibilityPermission_(self, enabled):
        if self.accessibility_permission_item is None:
            return
        self.accessibility_permission_item.setTitle_(
            "辅助功能权限：已允许"
            if enabled
            else "辅助功能权限：未允许（点击授权）"
        )

    def setInputMonitoringPermission_(self, enabled):
        if self.input_monitoring_permission_item is None:
            return
        self.input_monitoring_permission_item.setTitle_(
            "输入监控权限：已允许"
            if enabled
            else "输入监控权限：未允许（打开设置）"
        )

    def setVad_(self, enabled):
        if self.vad_item is not None:
            self.vad_item.setState_(
                AppKit.NSControlStateValueOn if enabled else AppKit.NSControlStateValueOff
            )

    def setDuckMedia_(self, enabled):
        if self.duck_enable_item is not None:
            self.duck_enable_item.setState_(
                AppKit.NSControlStateValueOn if enabled else AppKit.NSControlStateValueOff
            )

    def setDuckHeadphones_(self, enabled):
        if self.duck_headphones_item is not None:
            self.duck_headphones_item.setState_(
                AppKit.NSControlStateValueOn if enabled else AppKit.NSControlStateValueOff
            )

    def setOverlay_(self, enabled):
        if self.overlay_item is not None:
            self.overlay_item.setState_(
                AppKit.NSControlStateValueOn if enabled else AppKit.NSControlStateValueOff
            )

    def setOverlayFollowMouse_(self, enabled):
        if self.overlay_follow_mouse_item is not None:
            self.overlay_follow_mouse_item.setState_(
                AppKit.NSControlStateValueOn if enabled else AppKit.NSControlStateValueOff
            )

    def setPaused_(self, paused):
        if self.pause_item is not None:
            self.pause_item.setTitle_("恢复监听" if paused else "暂停监听")

    def setReleaseBackendEnabled_(self, enabled):
        if self.release_backend_item is not None:
            self.release_backend_item.setEnabled_(enabled)

    def setAutoReleaseMinutes_(self, minutes):
        if self.auto_release_menu_item is None or self.auto_release_submenu is None:
            return

        title = "自动释放：关闭" if minutes <= 0 else f"自动释放：{minutes} 分钟"
        self.auto_release_menu_item.setTitle_(title)

        while self.auto_release_submenu.numberOfItems() > 0:
            self.auto_release_submenu.removeItemAtIndex_(0)

        options = [
            (0, "关闭"),
            (5, "5 分钟"),
            (10, "10 分钟"),
            (30, "30 分钟"),
        ]
        for value, label in options:
            item = self.auto_release_submenu.addItemWithTitle_action_keyEquivalent_(
                label,
                "selectAutoRelease:",
                ""
            )
            item.setTarget_(self)
            item.setRepresentedObject_(value)
            item.setState_(AppKit.NSControlStateValueOn if value == minutes else AppKit.NSControlStateValueOff)

    def setAutoReleaseCountdown_(self, countdown_text):
        if self.auto_release_countdown_item is not None:
            self.auto_release_countdown_item.setTitle_(f"剩余自动释放：{countdown_text}")

    def setHistoryItems_(self, history_items):
        if self.history_submenu is None:
            return

        while self.history_submenu.numberOfItems() > 0:
            self.history_submenu.removeItemAtIndex_(0)

        if not history_items:
            item = self.history_submenu.addItemWithTitle_action_keyEquivalent_(
                "暂无历史",
                None,
                ""
            )
            item.setEnabled_(False)
            return

        for entry in history_items:
            text = entry.get("text", "").strip()
            title = self.app.truncate_menu_text(text, 32) if text else "空内容"
            item = self.history_submenu.addItemWithTitle_action_keyEquivalent_(
                title,
                "copyHistoryItem:",
                ""
            )
            item.setTarget_(self)
            item.setRepresentedObject_(text)

    def _replace_radio_menu(self, menu, items, action_name):
        while menu.numberOfItems() > 0:
            menu.removeItemAtIndex_(0)

        if not items:
            item = menu.addItemWithTitle_action_keyEquivalent_("暂无可用项", None, "")
            item.setEnabled_(False)
            return

        for item_data in items:
            item = menu.addItemWithTitle_action_keyEquivalent_(
                item_data["title"],
                action_name,
                ""
            )
            item.setTarget_(self)
            item.setRepresentedObject_(item_data["value"])
            item.setState_(
                AppKit.NSControlStateValueOn if item_data.get("selected") else AppKit.NSControlStateValueOff
            )

    def _update_current_value_title(self, menu_item, items, label):
        if menu_item is None:
            return

        current_value = None
        for item_data in items:
            if item_data.get("selected"):
                current_value = item_data.get("title") or item_data.get("value")
                break

        if current_value is None:
            menu_item.setTitle_(label)
            return

        self._set_menu_item_mixed_title(menu_item, f"{label}：", str(current_value))

    def _set_menu_item_mixed_title(self, menu_item, prefix, suffix):
        title = AppKit.NSMutableAttributedString.alloc().init()
        default_color = AppKit.NSColor.labelColor()
        subtle_color = AppKit.NSColor.secondaryLabelColor()

        title.appendAttributedString_(
            AppKit.NSAttributedString.alloc().initWithString_attributes_(
                prefix,
                {AppKit.NSForegroundColorAttributeName: default_color}
            )
        )
        title.appendAttributedString_(
            AppKit.NSAttributedString.alloc().initWithString_attributes_(
                suffix,
                {AppKit.NSForegroundColorAttributeName: subtle_color}
            )
        )
        menu_item.setAttributedTitle_(title)

    def showStatus_(self, sender):
        self.app.print_status_to_console()

    def menuWillOpen_(self, menu):
        self.app.refresh_accessibility_permission_status()
        permissions = self.app.get_permission_status()
        if self.app.pipeline is None:
            self.status_title_item.setTitle_("请先下载或加载模型")
        elif not all(permissions.get(k) for k in ("microphone", "accessibility")):
            self.status_title_item.setTitle_("需要允许系统权限")
        else:
            self.setState_(self.app._state)

    def exportDiagnostic_(self, sender):
        self.app.export_diagnostic_report()

    def openOnboarding_(self, sender):
        self.app.open_onboarding()

    def openSettings_(self, sender):
        self.app.open_settings()

    def showStats_(self, sender):
        self.app.show_stats()

    def checkForUpdates_(self, sender):
        self.app.check_for_updates()

    def togglePause_(self, sender):
        self.app.toggle_pause()

    def toggleAutoPaste_(self, sender):
        self.app.toggle_auto_paste()

    def toggleLoginAtStartup_(self, sender):
        self.app.toggle_login_at_startup()

    def toggleShowInDock_(self, sender):
        self.app.toggle_show_in_dock()

    def openDashboard_(self, sender):
        if self.app and hasattr(self.app, "open_dashboard"):
            self.app.open_dashboard()

    def checkAccessibility_(self, sender):
        self.app.check_accessibility_permission()

    def checkInputMonitoring_(self, sender):
        self.app.check_input_monitoring_permission()

    def toggleVad_(self, sender):
        self.app.toggle_vad()

    def toggleDuckMedia_(self, sender):
        self.app.toggle_duck_media()

    def toggleDuckHeadphones_(self, sender):
        self.app.toggle_duck_headphones()

    def toggleOverlay_(self, sender):
        self.app.toggle_overlay()

    def toggleOverlayFollowMouse_(self, sender):
        self.app.toggle_overlay_follow_mouse()

    def copyLastResult_(self, sender):
        self.app.copy_last_result()

    def releaseBackend_(self, sender):
        self.app.release_backend_resources(manual=True)

    def editGlossary_(self, sender):
        self.app.edit_glossary()

    def reloadGlossary_(self, sender):
        self.app.reload_glossary()

    def selectAutoRelease_(self, sender):
        minutes = sender.representedObject()
        self.app.set_auto_release_minutes(int(minutes))

    def selectModel_(self, sender):
        self.app.select_model(str(sender.representedObject()))

    def openModelsFolder_(self, sender):
        self.app.open_models_folder()

    def openModelDownloadPage_(self, sender):
        self.app.open_model_download_page()

    def reloadModel_(self, sender):
        self.app.reload_model()

    def selectMic_(self, sender):
        value = sender.representedObject()
        self.app.select_microphone(None if value == "__default__" else str(value))

    def selectLanguage_(self, sender):
        self.app.select_language(str(sender.representedObject()))

    def selectChineseScript_(self, sender):
        self.app.select_chinese_script(str(sender.representedObject()))

    def selectDictationMode_(self, sender):
        self.app.select_dictation_mode(str(sender.representedObject()))

    def selectHotkey_(self, sender):
        self.app.select_hotkey(str(sender.representedObject()))

    def copyHistoryItem_(self, sender):
        text = sender.representedObject()
        if text:
            self.app.copy_text(text)

    def quitApp_(self, sender):
        self.app.shutdown()

    @objc.python_method
    def log_screen_position(self):
        button = self.status_item.button()
        window = button.window() if button is not None else None
        if window is not None and self._logger is not None:
            self._logger.info(
                "菜单栏状态项：visible=%s button_hidden=%s button_frame=%s window_frame=%s",
                self.status_item.isVisible(),
                button.isHidden(),
                button.frame(),
                window.frame(),
            )
