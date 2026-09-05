"""One native window for setup, readiness and everyday preferences."""
import AppKit
import objc
from Foundation import NSObject


class DashboardWindowController(NSObject):
    def initWithApp_(self, app):
        self = objc.super(DashboardWindowController, self).init()
        if self is None:
            return None
        self.app = app
        self.window = None
        self._permission_status_labels = {}
        self._permission_buttons = {}
        self._model_names = []
        return self

    @objc.python_method
    def show(self):
        if self.window is None:
            self._build_window()
        self.refresh()
        self.window.makeKeyAndOrderFront_(None)
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    @objc.python_method
    def _label(self, title, x, y, w, h=22, size=13, secondary=False):
        label = AppKit.NSTextField.labelWithString_(title)
        label.setFrame_(AppKit.NSMakeRect(x, y, w, h))
        label.setFont_(AppKit.NSFont.systemFontOfSize_(size))
        label.setTextColor_(AppKit.NSColor.secondaryLabelColor() if secondary else AppKit.NSColor.labelColor())
        self.window.contentView().addSubview_(label)
        return label

    @objc.python_method
    def _button(self, title, action, x, y, w=110):
        button = AppKit.NSButton.buttonWithTitle_target_action_(title, self, action)
        button.setFrame_(AppKit.NSMakeRect(x, y, w, 30))
        self.window.contentView().addSubview_(button)
        return button

    @objc.python_method
    def _build_window(self):
        self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, 560, 600),
            AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable,
            AppKit.NSBackingStoreBuffered, False)
        self.window.setTitle_("WhisperCppCmd")
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self)
        self.window.center()
        self._label("说完，就输入。", 32, 535, 490, 40, 30)
        self._instruction = self._label("按住右 Command 说话，松开后自动输入。", 34, 502, 490, size=14)
        self._label("没有输入光标时，文字会复制到剪贴板并提示你。", 34, 476, 490, secondary=True)
        self._status = self._label("正在检查…", 34, 432, 490, 28, 16)
        self._label("语音模型", 34, 388, 300, size=15)
        self._model = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(AppKit.NSMakeRect(32, 349, 310, 30), False)
        self._model.setTarget_(self)
        self._model.setAction_("selectModel:")
        self.window.contentView().addSubview_(self._model)
        self._download_button = self._button("下载推荐模型", "downloadModel:", 350, 349, 176)
        self._model_detail = self._label("Turbo · 约 600 MB · 下载后完全离线", 34, 320, 490, secondary=True)
        self._button("打开模型文件夹", "openModelsFolder:", 30, 284, 145)
        self._reload = self._button("重新加载", "reloadModel:", 182, 284)
        self._label("系统权限", 34, 242, 300, size=15)
        for key, title, y in (("microphone", "麦克风", 205), ("accessibility", "辅助功能", 166)):
            self._label(title, 34, y + 5, 150)
            self._permission_status_labels[key] = self._label("检查中", 205, y + 5, 175, secondary=True)
            button = self._button("允许", "openPermission:", 408, y, 118)
            button.setIdentifier_(key)
            self._permission_buttons[key] = button
        self._label("仅用于录音与快捷键输入，音频和文字不会上传。", 34, 136, 490, secondary=True)
        self._button("输入偏好…", "showPreferences:", 30, 83, 135)
        self._button("快捷键未响应？", "inputMonitoring:", 350, 83, 176)
        self._button("关闭窗口", "close:", 408, 26, 118).setKeyEquivalent_("\r")
        self._label("随时从菜单栏或 Dock 再次打开", 34, 32, 355, secondary=True)

    @objc.python_method
    def refresh(self):
        if self.window is None:
            return
        settings = self.app.settings
        hotkeys = {o["value"]: o["title"] for o in self.app._get_hotkey_options()}
        self._instruction.setStringValue_(f"按住{hotkeys.get(settings.hotkey, chr(8984))} 说话，松开后自动输入。")
        permissions = self.app.get_permission_status()
        self.update_permission_status(permissions)
        names = sorted(settings.list_available_models())
        if names != self._model_names or self._model.numberOfItems() == 0:
            self._model_names = names
            self._model.removeAllItems()
            self._model.addItemsWithTitles_(names or ["尚未下载模型"])
        if settings.current_model in names:
            self._model.selectItemWithTitle_(settings.current_model)
        busy = getattr(self.app, "_pipeline_transitioning", False)
        active = self.app._state in {"recording", "processing"}
        downloader = self.app._model_download
        self._model.setEnabled_(bool(names) and not busy and not active)
        self._reload.setEnabled_(bool(names) and not busy and not active and not downloader.active)
        self._download_button.setTitle_("取消下载" if downloader.active else "下载推荐模型")
        self._download_button.setEnabled_(downloader.active or (not busy and not active))
        pipeline = self.app.pipeline
        model_ready = bool(pipeline and pipeline.is_initialized and not self.app._model_setup_required)
        if downloader.message:
            self._model_detail.setStringValue_(downloader.message)
        elif model_ready:
            self._model_detail.setStringValue_(f"{settings.current_model} · 本地识别")
        else:
            self._model_detail.setStringValue_("Turbo · 约 600 MB · 下载后完全离线")
        if busy:
            state = "正在加载模型…"
        elif not model_ready:
            state = "下载或选择一个模型，即可开始设置"
        elif not all(permissions.get(k) for k in ("microphone", "accessibility")):
            state = "还需要允许下方系统权限"
        elif not self.app._keyboard_listener_is_healthy():
            state = "快捷键尚未就绪，请检查系统权限"
        else:
            state = {"recording": "正在听你说话…", "processing": "正在识别…", "paused": "已暂停，可从菜单栏恢复", "error": "上次录音未完成，请重试"}.get(self.app._state, "已就绪 · 随时按住快捷键开始")
        self._status.setStringValue_(state)

    @objc.python_method
    def update_permission_status(self, statuses):
        for key, label in self._permission_status_labels.items():
            granted = bool(statuses.get(key))
            label.setStringValue_("已允许" if granted else "需要允许")
            self._permission_buttons[key].setTitle_("已允许" if granted else "允许")
            self._permission_buttons[key].setEnabled_(not granted)

    def openPermission_(self, sender):
        self.app.request_permission(str(sender.identifier()))

    def downloadModel_(self, sender):
        if self.app._model_download.active:
            self.app._model_download.cancel()
        else:
            self.app.download_recommended_model()
        self.refresh()

    def selectModel_(self, sender):
        self.app.load_model_async(str(sender.titleOfSelectedItem()))

    def reloadModel_(self, sender):
        name = self.app.settings.current_model
        if name not in self._model_names and self._model_names:
            name = self._model_names[0]
        self.app.load_model_async(name, force=True)

    def openModelsFolder_(self, sender):
        self.app.open_models_folder()

    def inputMonitoring_(self, sender):
        self.app.check_input_monitoring_permission()

    def showPreferences_(self, sender):
        menu = self.app.status_bar.preferences_menu
        menu.popUpMenuPositioningItem_atLocation_inView_(None, AppKit.NSMakePoint(34, 82), self.window.contentView())

    def close_(self, sender):
        self.window.performClose_(None)

    def cancelOperation_(self, sender):
        self.close_(sender)

    def windowWillClose_(self, notification):
        self.app.settings.onboarding_completed = True
        self.app.settings.save()
