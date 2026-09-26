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
        self.original_play_button = None
        self.vad_play_button = None
        self.stop_button = None
        self._playing_button = None
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

        self.original_play_button = self._button(
            "播放原始音频", "playOriginalRecording:", 24, 332, 112
        )
        self.play_button = self._button(
            "播放送入引擎", "playRecording:", 148, 332, 112
        )
        self.vad_play_button = self._button(
            "播放 VAD 后", "playVadRecording:", 272, 332, 112
        )
        self.stop_button = self._button("停止", "stopPlayback:", 396, 332, 80)
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
            self.original_play_button.setEnabled_(False)
            self.play_button.setEnabled_(False)
            self.vad_play_button.setEnabled_(False)
            return

        created_at = str(record.get("created_at") or "")
        try:
            timestamp = datetime.fromisoformat(created_at).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            timestamp = created_at.replace("T", " ")[:19] or "时间未知"
        self.window.setTitle_(f"语音记录 · {timestamp}")
        self.title_label.setStringValue_(timestamp)

        duration = float(
            record.get("recording_duration_seconds")
            or record.get("original_duration_seconds")
            or record.get("duration_seconds")
            or 0.0
        )
        model = str(record.get("model") or "未知")
        language = str(record.get("language") or "未知")
        vad = "开启" if record.get("use_vad") else "关闭"
        reference_cancel = "开启" if record.get("ref_cancel_applied") else "未应用"
        vad_duration = record.get("vad_duration_seconds")
        vad_detail = f"  · VAD 后 {float(vad_duration):.1f} 秒" if vad_duration is not None else ""
        self.details_label.setStringValue_(
            f"{duration:.1f} 秒{vad_detail}  ·  模型 {model}  ·  语言 {language}  ·  VAD {vad}  ·  参考消除 {reference_cancel}"
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

        original_path = str(record.get("original_audio_path") or "")
        audio_path = str(record.get("audio_path") or "")
        vad_path = str(record.get("vad_audio_path") or "")
        self.original_play_button.setEnabled_(
            bool(original_path and os.path.isfile(original_path))
        )
        self.play_button.setEnabled_(bool(audio_path and os.path.isfile(audio_path)))
        self.vad_play_button.setEnabled_(bool(vad_path and os.path.isfile(vad_path)))
        if not audio_path or not os.path.isfile(audio_path):
            self.status_label.setStringValue_("录音文件已被删除")
        elif record.get("use_vad") and not vad_path:
            self.status_label.setStringValue_("VAD 后音频未生成，可播放送入引擎的音频")

    def playRecording_(self, sender):
        self._play_audio(
            str((self.record or {}).get("audio_path") or ""),
            self.play_button,
            "送入引擎的音频",
        )

    def playOriginalRecording_(self, sender):
        self._play_audio(
            str((self.record or {}).get("original_audio_path") or ""),
            self.original_play_button,
            "原始音频",
        )

    def playVadRecording_(self, sender):
        self._play_audio(
            str((self.record or {}).get("vad_audio_path") or ""),
            self.vad_play_button,
            "VAD 后音频",
        )

    def _play_audio(self, audio_path, button, label):
        if not audio_path or not os.path.isfile(audio_path):
            self.status_label.setStringValue_("录音文件已被删除")
            if button is not None:
                button.setEnabled_(False)
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
        self._playing_button = button
        self.status_label.setStringValue_(f"正在播放{label}…")
        self._set_play_buttons_enabled(False)
        self.stop_button.setEnabled_(True)

    def stopPlayback_(self, sender):
        self._stop_playback()
        if self.record:
            self.status_label.setStringValue_("已停止播放")

    @objc.python_method
    def _stop_playback(self):
        sound = self.sound
        self.sound = None
        self._playing_button = None
        if sound is not None:
            sound.setDelegate_(None)
            sound.stop()
        self._set_play_buttons_enabled(True)
        if self.stop_button is not None:
            self.stop_button.setEnabled_(False)

    def _set_play_buttons_enabled(self, enabled):
        paths = (
            (self.original_play_button, "original_audio_path"),
            (self.play_button, "audio_path"),
            (self.vad_play_button, "vad_audio_path"),
        )
        for button, field in paths:
            if button is None:
                continue
            audio_path = str((self.record or {}).get(field) or "")
            button.setEnabled_(bool(enabled and audio_path and os.path.isfile(audio_path)))

    def sound_didFinishPlaying_(self, sound, finished):
        if self.sound is None or sound != self.sound:
            return
        self.sound = None
        self._playing_button = None
        self._set_play_buttons_enabled(True)
        self.stop_button.setEnabled_(False)
        self.status_label.setStringValue_("播放完成" if finished else "播放已结束")

    def windowWillClose_(self, notification):
        self._stop_playback()
