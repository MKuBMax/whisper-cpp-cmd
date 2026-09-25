"""Single-record audio playback and transcription details."""

from __future__ import annotations

from datetime import datetime
import os

import AppKit
import objc
from Foundation import NSObject


class AudioRecordingWindowController(NSObject):
    def initWithApp_(self, app):
        self = objc.super(AudioRecordingWindowController, self).init()
        if self is None:
            return None
        self.app = app
        self.window = None
        self.record = None
        self.sound = None
        self.title_label = None
        self.details_label = None
        self.status_label = None
        self.text_view = None
        self.play_button = None
        self.stop_button = None
        return self

    @objc.python_method
    def show_recording(self, record):
        if self.window is None:
            self._build_window()
        self._stop_playback()
        self.record = record or {}
        self._refresh_content()
        self.window.makeKeyAndOrderFront_(None)
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    @objc.python_method
    def _build_window(self):
        self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, 620, 480),
            AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        self.window.setTitle_("语音记录")
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self)
        self.window.center()

        self.title_label = self._label("语音记录", 24, 432, 572, 26, 17)
        self.details_label = self._label("", 24, 403, 572, 22, 12, secondary=True)
        self.status_label = self._label("", 24, 376, 572, 22, 13)

        self.play_button = self._button("播放录音", "playRecording:", 24, 332, 112)
        self.stop_button = self._button("停止", "stopPlayback:", 148, 332, 88)
        self.stop_button.setEnabled_(False)

        self._label("识别内容", 24, 294, 572, 22, 14)
        scroll = AppKit.NSScrollView.alloc().initWithFrame_(
            AppKit.NSMakeRect(24, 24, 572, 256)
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(AppKit.NSBezelBorder)
        self.text_view = AppKit.NSTextView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, 550, 250)
        )
        self.text_view.setEditable_(False)
        self.text_view.setSelectable_(True)
        self.text_view.setRichText_(False)
        self.text_view.setFont_(AppKit.NSFont.systemFontOfSize_(14))
        self.text_view.setTextContainerInset_(AppKit.NSMakeSize(9, 9))
        scroll.setDocumentView_(self.text_view)
        self.window.contentView().addSubview_(scroll)

    @objc.python_method
    def _label(self, title, x, y, width, height, size, secondary=False):
        label = AppKit.NSTextField.labelWithString_(title)
        label.setFrame_(AppKit.NSMakeRect(x, y, width, height))
        label.setFont_(AppKit.NSFont.systemFontOfSize_(size))
        if secondary:
            label.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        self.window.contentView().addSubview_(label)
        return label

    @objc.python_method
    def _button(self, title, action, x, y, width):
        button = AppKit.NSButton.buttonWithTitle_target_action_(title, self, action)
        button.setFrame_(AppKit.NSMakeRect(x, y, width, 30))
        self.window.contentView().addSubview_(button)
        return button

    @objc.python_method
    def _refresh_content(self):
        record = self.record
        if not record:
            self.window.setTitle_("语音记录已不可用")
            self.title_label.setStringValue_("这条语音记录已被清理或删除")
            self.details_label.setStringValue_("")
            self.status_label.setStringValue_("")
            self.text_view.setString_("")
            self.play_button.setEnabled_(False)
            return

        created_at = str(record.get("created_at") or "")
        try:
            timestamp = datetime.fromisoformat(created_at).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            timestamp = created_at.replace("T", " ")[:19] or "时间未知"
        self.window.setTitle_(f"语音记录 · {timestamp}")
        self.title_label.setStringValue_(timestamp)

        duration = float(record.get("duration_seconds") or 0.0)
        model = str(record.get("model") or "未知")
        language = str(record.get("language") or "未知")
        vad = "开启" if record.get("use_vad") else "关闭"
        reference_cancel = "开启" if record.get("ref_cancel_applied") else "未应用"
        self.details_label.setStringValue_(
            f"{duration:.1f} 秒  ·  模型 {model}  ·  语言 {language}  ·  VAD {vad}  ·  参考消除 {reference_cancel}"
        )

        status = record.get("status")
        if status == "failure":
            error = str(record.get("error") or "识别失败")
            error = self.app.truncate_menu_text(error, 58)
            self.status_label.setStringValue_(f"识别失败：{error}")
            self.status_label.setTextColor_(AppKit.NSColor.systemRedColor())
        elif status == "no_speech":
            self.status_label.setStringValue_("识别完成 · 没有返回文字")
            self.status_label.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        elif status == "pending":
            self.status_label.setStringValue_("识别尚未完成")
            self.status_label.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        else:
            self.status_label.setStringValue_("识别完成")
            self.status_label.setTextColor_(AppKit.NSColor.labelColor())

        transcript = str(record.get("recognized_text") or "").strip()
        if not transcript and record.get("error"):
            transcript = str(record["error"])
        elif not transcript:
            transcript = "没有可显示的识别文字。"
        self.text_view.setString_(transcript)

        audio_path = str(record.get("audio_path") or "")
        self.play_button.setEnabled_(bool(audio_path and os.path.isfile(audio_path)))
        if not audio_path or not os.path.isfile(audio_path):
            self.status_label.setStringValue_("录音文件已被删除")

    def playRecording_(self, sender):
        audio_path = str((self.record or {}).get("audio_path") or "")
        if not audio_path or not os.path.isfile(audio_path):
            self.status_label.setStringValue_("录音文件已被删除")
            self.play_button.setEnabled_(False)
            return

        self._stop_playback()
        try:
            sound = AppKit.NSSound.alloc().initWithContentsOfFile_byReference_(audio_path, False)
        except Exception:
            sound = None
        if sound is None:
            self.status_label.setStringValue_("无法打开这段录音")
            return
        sound.setDelegate_(self)
        self.sound = sound
        if not sound.play():
            self.sound = None
            sound.setDelegate_(None)
            self.status_label.setStringValue_("播放失败")
            return
        self.status_label.setStringValue_("正在播放录音…")
        self.play_button.setEnabled_(False)
        self.stop_button.setEnabled_(True)

    def stopPlayback_(self, sender):
        self._stop_playback()
        if self.record:
            self.status_label.setStringValue_("已停止播放")

    @objc.python_method
    def _stop_playback(self):
        sound = self.sound
        self.sound = None
        if sound is not None:
            sound.setDelegate_(None)
            sound.stop()
        if self.play_button is not None:
            audio_path = str((self.record or {}).get("audio_path") or "")
            self.play_button.setEnabled_(bool(audio_path and os.path.isfile(audio_path)))
        if self.stop_button is not None:
            self.stop_button.setEnabled_(False)

    def sound_didFinishPlaying_(self, sound, finished):
        if self.sound is None or sound != self.sound:
            return
        self.sound = None
        self.play_button.setEnabled_(True)
        self.stop_button.setEnabled_(False)
        self.status_label.setStringValue_("播放完成" if finished else "播放已结束")

    def windowWillClose_(self, notification):
        self._stop_playback()
