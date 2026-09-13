#!/usr/bin/env python3
"""
剪贴板模块 - 文本粘贴操作
"""

import subprocess
import time
from typing import Optional
from dataclasses import dataclass
import logging

import AppKit
import ApplicationServices
import CoreFoundation
import Quartz

logger = logging.getLogger(__name__)


@dataclass
class ClipboardConfig:
    """剪贴板配置"""
    paste_delay: float = 0.03
    use_applescript: bool = True


class Clipboard:
    """
    剪贴板 - 管理文本复制粘贴
    
    职责:
    - 复制文本到剪贴板
    - 模拟粘贴操作
    - 管理粘贴延迟
    """
    
    def __init__(self, config: Optional[ClipboardConfig] = None):
        self.config = config or ClipboardConfig()
        self.last_delivery = "none"
        self._recording_target = None
        self._recording_snapshot = None
        self._recording_pid = None
        self._recording_app = "-"
        self._recording_bundle = "-"
        self._target_captured = False
        self._private_event_source = Quartz.CGEventSourceCreate(
            Quartz.kCGEventSourceStatePrivate
        )

    def _is_browser_app(self, bundle_id: str) -> bool:
        return bundle_id in {
            "com.apple.Safari",
            "com.google.Chrome",
            "com.google.Chrome.canary",
            "com.microsoft.edgemac",
            "com.operasoftware.Opera",
        }

    def _is_terminal_app(self, bundle_id: str) -> bool:
        return bundle_id in {
            "com.googlecode.iterm2",
            "com.apple.Terminal",
            "com.termius-dmg.mac",
        }

    def _get_frontmost_app_name(self) -> str:
        try:
            app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return "-"
            return app.localizedName() or app.bundleIdentifier() or "-"
        except Exception as e:
            logger.warning("获取前台应用失败：%s", e)
            return "-"

    def _get_frontmost_app_bundle_id(self) -> str:
        try:
            app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return "-"
            return app.bundleIdentifier() or "-"
        except Exception as e:
            logger.warning("获取前台应用 bundle id 失败：%s", e)
            return "-"

    def _ax_copy_attribute_value(self, element, attribute: str):
        status, value = ApplicationServices.AXUIElementCopyAttributeValue(
            element,
            attribute,
            None
        )
        return status, value

    def _ax_is_attribute_settable(self, element, attribute: str) -> tuple[int, bool]:
        status, settable = ApplicationServices.AXUIElementIsAttributeSettable(
            element,
            attribute,
            None
        )
        return status, bool(settable)

    def _get_focused_ui_element(self):
        system_wide = ApplicationServices.AXUIElementCreateSystemWide()
        status, element = self._ax_copy_attribute_value(
            system_wide,
            ApplicationServices.kAXFocusedUIElementAttribute
        )
        if status != ApplicationServices.kAXErrorSuccess:
            return status, None
        return status, element

    def _insert_text_via_ax_selected_text(self, element, text: str) -> bool:
        status, settable = self._ax_is_attribute_settable(
            element,
            ApplicationServices.kAXSelectedTextAttribute
        )
        if status != ApplicationServices.kAXErrorSuccess or not settable:
            return False

        status = ApplicationServices.AXUIElementSetAttributeValue(
            element,
            ApplicationServices.kAXSelectedTextAttribute,
            text
        )
        return status == ApplicationServices.kAXErrorSuccess

    def _insert_text_via_ax_value(self, element, text: str) -> bool:
        value_status, current_value = self._ax_copy_attribute_value(
            element,
            ApplicationServices.kAXValueAttribute
        )
        range_status, selected_range = self._ax_copy_attribute_value(
            element,
            ApplicationServices.kAXSelectedTextRangeAttribute
        )
        settable_status, value_settable = self._ax_is_attribute_settable(
            element,
            ApplicationServices.kAXValueAttribute
        )

        if value_status != ApplicationServices.kAXErrorSuccess:
            return False
        if range_status != ApplicationServices.kAXErrorSuccess:
            return False
        if settable_status != ApplicationServices.kAXErrorSuccess or not value_settable:
            return False
        if not isinstance(current_value, str):
            return False

        ok, selected_range_value = ApplicationServices.AXValueGetValue(
            selected_range,
            ApplicationServices.kAXValueCFRangeType,
            None
        )
        if not ok:
            return False

        location, length = selected_range_value
        new_value = current_value[:location] + text + current_value[location + length:]
        status = ApplicationServices.AXUIElementSetAttributeValue(
            element,
            ApplicationServices.kAXValueAttribute,
            new_value
        )
        if status != ApplicationServices.kAXErrorSuccess:
            return False

        range_settable_status, range_settable = self._ax_is_attribute_settable(
            element,
            ApplicationServices.kAXSelectedTextRangeAttribute
        )
        if range_settable_status == ApplicationServices.kAXErrorSuccess and range_settable:
            new_range = CoreFoundation.CFRange(location + len(text), 0)
            range_value = ApplicationServices.AXValueCreate(
                ApplicationServices.kAXValueCFRangeType,
                new_range
            )
            ApplicationServices.AXUIElementSetAttributeValue(
                element,
                ApplicationServices.kAXSelectedTextRangeAttribute,
                range_value
            )

        return True

    def _insert_text_via_iterm2(self, text: str) -> bool:
        bundle_id = self._get_frontmost_app_bundle_id()
        if bundle_id != "com.googlecode.iterm2":
            return False

        subprocess.run(
            [
                'osascript',
                '-e', 'on run argv',
                '-e', 'set theText to item 1 of argv',
                '-e', 'tell application "iTerm2"',
                '-e', 'tell current session of current window',
                '-e', 'write text theText newline NO',
                '-e', 'end tell',
                '-e', 'end tell',
                '-e', 'end run',
                text,
            ],
            check=True
        )
        return True

    def _type_text_with_cg_event(self, text: str, delay: Optional[float] = None) -> bool:
        if delay is None:
            delay = self.config.paste_delay

        delay = max(delay, 0.03)
        if delay > 0:
            time.sleep(delay)

        for char in text:
            key_down = Quartz.CGEventCreateKeyboardEvent(self._private_event_source, 0, True)
            Quartz.CGEventKeyboardSetUnicodeString(key_down, len(char), char)
            Quartz.CGEventSetFlags(key_down, 0)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, key_down)

            key_up = Quartz.CGEventCreateKeyboardEvent(self._private_event_source, 0, False)
            Quartz.CGEventKeyboardSetUnicodeString(key_up, len(char), char)
            Quartz.CGEventSetFlags(key_up, 0)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, key_up)

            time.sleep(0.002)

        return True

    def _delete_text_with_cg_event(self, count: int, delay: Optional[float] = None) -> bool:
        if count <= 0:
            return True

        if delay is None:
            delay = self.config.paste_delay

        delay = max(delay, 0.03)
        if delay > 0:
            time.sleep(delay)

        delete_keycode = 51
        for _ in range(count):
            key_down = Quartz.CGEventCreateKeyboardEvent(self._private_event_source, delete_keycode, True)
            Quartz.CGEventSetFlags(key_down, 0)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, key_down)

            key_up = Quartz.CGEventCreateKeyboardEvent(self._private_event_source, delete_keycode, False)
            Quartz.CGEventSetFlags(key_up, 0)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, key_up)

            time.sleep(0.002)

        return True

    def replace_typed_text(
        self,
        new_text: str,
        previous_text: str = "",
        delay: Optional[float] = None
    ) -> bool:
        """
        在当前光标处替换上一版已输入文本。

        这是实时听写预览的核心：尽量只删改变化的尾部，避免整段重打。
        """
        new_text = new_text or ""
        previous_text = previous_text or ""

        if new_text == previous_text:
            return True

        if delay is None:
            delay = self.config.paste_delay

        if previous_text and new_text.startswith(previous_text):
            suffix = new_text[len(previous_text):]
            return self._type_text_with_cg_event(suffix, delay)

        if previous_text and previous_text.startswith(new_text):
            delete_count = len(previous_text) - len(new_text)
            return self._delete_text_with_cg_event(delete_count, delay)

        if previous_text:
            if not self._delete_text_with_cg_event(len(previous_text), delay):
                return False

        if new_text:
            return self._type_text_with_cg_event(new_text, delay)

        return True

    def _insert_text_directly(self, text: str) -> bool:
        frontmost_app = self._get_frontmost_app_name()
        bundle_id = self._get_frontmost_app_bundle_id()

        try:
            status, element = self._get_focused_ui_element()
            if status == ApplicationServices.kAXErrorSuccess and element is not None:
                role_status, role = self._ax_copy_attribute_value(
                    element,
                    ApplicationServices.kAXRoleAttribute
                )
                role_text = role if role_status == ApplicationServices.kAXErrorSuccess else "-"

                if self._insert_text_via_ax_selected_text(element, text):
                    logger.info(
                        "直接插入成功：frontmost_app=%s bundle_id=%s role=%s mode=ax_selected_text",
                        frontmost_app,
                        bundle_id,
                        role_text,
                    )
                    return True

                if self._insert_text_via_ax_value(element, text):
                    logger.info(
                        "直接插入成功：frontmost_app=%s bundle_id=%s role=%s mode=ax_value",
                        frontmost_app,
                        bundle_id,
                        role_text,
                    )
                    return True

                logger.info(
                    "直接插入未命中可写控件：frontmost_app=%s bundle_id=%s role=%s",
                    frontmost_app,
                    bundle_id,
                    role_text,
                )
            else:
                logger.warning(
                    "获取聚焦控件失败：frontmost_app=%s bundle_id=%s ax_status=%s",
                    frontmost_app,
                    bundle_id,
                    status,
                )
        except Exception as e:
            logger.warning("Accessibility 直接插入失败：%s", e)

        try:
            if self._insert_text_via_iterm2(text):
                logger.info(
                    "直接插入成功：frontmost_app=%s bundle_id=%s mode=iterm2_write_text",
                    frontmost_app,
                    bundle_id,
                )
                return True
        except Exception as e:
            logger.warning("iTerm2 直写失败：%s", e)

        return False

    def _paste_via_preferred_mode(self, delay: Optional[float] = None) -> bool:
        return self.paste(delay)

    def _paste_via_cgevent_fallback(self, delay: Optional[float] = None) -> bool:
        if delay is None:
            delay = self.config.paste_delay

        delay = max(delay, 0.12)
        if delay > 0:
            time.sleep(delay)

        try:
            frontmost_app = self._get_frontmost_app_name()
            self._paste_with_cg_event()
            logger.info(
                "粘贴成功：frontmost_app=%s delay=%.2fs mode=cgevent_fallback",
                frontmost_app,
                delay,
            )
            return True
        except Exception as e:
            logger.warning("CGEvent 回退粘贴失败：%s", e)
            return False

    def _post_key(self, keycode: int, is_down: bool, flags: int = 0) -> None:
        event = Quartz.CGEventCreateKeyboardEvent(
            self._private_event_source,
            keycode,
            is_down,
        )
        Quartz.CGEventSetFlags(event, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def _paste_with_cg_event(self) -> bool:
        command_keycode = 55
        v_keycode = 9
        command_flag = Quartz.kCGEventFlagMaskCommand

        self._post_key(command_keycode, True, command_flag)
        time.sleep(0.01)
        self._post_key(v_keycode, True, command_flag)
        time.sleep(0.01)
        self._post_key(v_keycode, False, command_flag)
        time.sleep(0.01)
        self._post_key(command_keycode, False, 0)
        return True

    def _paste_with_applescript(self) -> bool:
        if self.config.use_applescript:
            subprocess.run(
                ['osascript', '-e', 'tell application "System Events" to keystroke "v" using command down'],
                check=True
            )
        else:
            subprocess.run(
                ['osascript', '-e', 'tell application "System Events" to key code 9 using command down'],
                check=True
            )
        return True

    def _copy_with_pasteboard(self, text: str) -> bool:
        pasteboard = AppKit.NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        ok = pasteboard.setString_forType_(text, AppKit.NSPasteboardTypeString)
        if not ok:
            return False

        current = pasteboard.stringForType_(AppKit.NSPasteboardTypeString)
        return current == text
    
    def copy(self, text: str) -> bool:
        """
        复制文本到剪贴板
        
        Args:
            text: 要复制的文本
        
        Returns:
            是否成功
        """
        if not text:
            return False

        # NSPasteboard 是 GUI App 内的原生路径：无子进程开销且实测 100% 可靠。
        # pbcopy/pbpaste 子进程方案在 GUI 进程内存在 pasteboard 同步竞态
        # （日志显示约 45% 校验失败），故降为兜底。
        try:
            if self._copy_with_pasteboard(text):
                logger.info("复制到剪贴板成功：len=%s mode=nspasteboard", len(text))
                return True
        except Exception as e:
            logger.warning("NSPasteboard 复制失败，尝试 pbcopy：%s", e)

        # 兜底：pbcopy 子进程（仅 NSPasteboard 异常时尝试）
        try:
            subprocess.run(
                ['pbcopy'],
                input=text.encode('utf-8'),
                check=True
            )
            time.sleep(0.01)
            result = subprocess.run(
                ['pbpaste'],
                capture_output=True,
                check=True
            )
            if result.stdout.decode('utf-8') == text:
                logger.info("复制到剪贴板成功：len=%s mode=pbcopy", len(text))
                return True

            logger.warning("pbcopy 校验失败：len=%s", len(text))
        except Exception as e:
            logger.warning("pbcopy 复制失败：%s", e)

        print("❌ 复制失败")
        logger.warning("复制到剪贴板失败：len=%s", len(text))
        return False
    
    def paste(self, delay: Optional[float] = None) -> bool:
        """
        执行粘贴操作
        
        Args:
            delay: 粘贴前延迟（秒）
        
        Returns:
            是否成功
        """
        if delay is None:
            delay = self.config.paste_delay

        delay = max(delay, 0.12)
        
        if delay > 0:
            time.sleep(delay)
        
        try:
            frontmost_app = self._get_frontmost_app_name()
            if self.config.use_applescript:
                self._paste_with_applescript()
            else:
                self._paste_with_cg_event()
            logger.info("粘贴成功：frontmost_app=%s delay=%.2fs mode=%s", frontmost_app, delay, "applescript" if self.config.use_applescript else "cgevent")
            return True
        except Exception as e:
            print(f"❌ 粘贴失败：{e}")
            logger.warning("粘贴失败：%s", e)
            return False
    
    def _frontmost_app_for_probe(self):
        return AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()

    def _probe_snapshot(self, phase: str):
        """Query focus once and record every field used by the verdict.

        Never log AXValue content or dictation text. Value content is
        privacy sensitive and length alone is enough for diagnosis.
        """
        try:
            app = self._frontmost_app_for_probe()
        except Exception as e:
            logger.info("paste probe phase=%s reason=frontmost_query_failed error=%r", phase, e)
            return None
        if app is None:
            logger.info("paste probe phase=%s reason=no_frontmost_app", phase)
            return None
        try:
            pid = app.processIdentifier()
        except Exception:
            pid = -1
        try:
            app_name = app.localizedName() or "-"
        except Exception:
            app_name = "-"
        try:
            bundle_id = app.bundleIdentifier() or "-"
        except Exception:
            bundle_id = "-"
        snapshot = {
            "phase": phase,
            "frontmost_app": app_name,
            "bundle_id": bundle_id,
            "pid": pid,
            "ax_status": None,
            "has_element": False,
            "role": "-",
            "role_status": None,
            "subrole": "-",
            "subrole_status": None,
            "selected_text_settable": False,
            "selected_text_status": None,
            "value_settable": False,
            "value_status": None,
            "value_is_str": False,
            "has_selected_range": False,
            "selected_range_status": None,
            "verdict": "rejected",
        }
        try:
            status, element = self._get_focused_ui_element()
        except Exception as e:
            logger.info(
                "paste probe phase=%s reason=focused_query_raised frontmost_app=%s bundle_id=%s pid=%s error=%r",
                phase, app_name, bundle_id, pid, e,
            )
            return None
        snapshot["ax_status"] = status
        if status != 0 or element is None:
            logger.info(
                "paste probe phase=%s reason=no_focused_element frontmost_app=%s bundle_id=%s pid=%s ax_status=%s",
                phase, app_name, bundle_id, pid, status,
            )
            return None
        snapshot["has_element"] = True
        role_status, role = self._ax_copy_attribute_value(element, "AXRole")
        subrole_status, subrole = self._ax_copy_attribute_value(element, "AXSubrole")
        snapshot["role"] = role if role_status == 0 and isinstance(role, str) else "-"
        snapshot["role_status"] = role_status
        snapshot["subrole"] = subrole if subrole_status == 0 and isinstance(subrole, str) else "-"
        snapshot["subrole_status"] = subrole_status
        if snapshot["subrole"] == "AXSecureTextField":
            logger.info(
                "paste probe phase=%s reason=secure_field frontmost_app=%s bundle_id=%s pid=%s role=%s",
                phase, app_name, bundle_id, pid, snapshot["role"],
            )
            return None
        selected_status, selected_writable = self._ax_is_attribute_settable(
            element, "AXSelectedText"
        )
        value_status, value_writable = self._ax_is_attribute_settable(element, "AXValue")
        snapshot["selected_text_settable"] = bool(selected_writable)
        snapshot["selected_text_status"] = selected_status
        snapshot["value_settable"] = bool(value_writable)
        snapshot["value_status"] = value_status
        value_attr_status, current_value = self._ax_copy_attribute_value(element, "AXValue")
        snapshot["value_is_str"] = isinstance(current_value, str)
        range_status, selected_range = self._ax_copy_attribute_value(
            element, "AXSelectedTextRange"
        )
        snapshot["has_selected_range"] = selected_range is not None
        snapshot["selected_range_status"] = range_status
        selected_ok = selected_status == 0 and bool(selected_writable)
        value_ok = (
            value_status == 0
            and bool(value_writable)
            and (
                snapshot["role"] in {"AXTextField", "AXTextArea", "AXComboBox"}
                or snapshot["has_selected_range"]
            )
        )
        terminal_ok = (
            snapshot["role"] == "AXTextArea"
            and snapshot["has_selected_range"]
            and self._is_terminal_app(bundle_id)
        )
        if not (selected_ok or value_ok or terminal_ok):
            logger.info(
                "paste probe phase=%s reason=not_editable frontmost_app=%s bundle_id=%s pid=%s role=%s subrole=%s "
                "selected_text_status=%s selected_text_settable=%s value_status=%s value_settable=%s "
                "value_is_str=%s selected_range_status=%s has_selected_range=%s",
                phase, app_name, bundle_id, pid, snapshot["role"], snapshot["subrole"],
                selected_status, bool(selected_writable), value_status, bool(value_writable),
                snapshot["value_is_str"], range_status, snapshot["has_selected_range"],
            )
            return None
        snapshot["verdict"] = "editable"
        snapshot["element"] = element
        logger.info(
            "paste probe phase=%s reason=editable frontmost_app=%s bundle_id=%s pid=%s role=%s subrole=%s "
            "selected_text_status=%s selected_text_settable=%s value_status=%s value_settable=%s "
            "value_is_str=%s selected_range_status=%s has_selected_range=%s",
            phase, app_name, bundle_id, pid, snapshot["role"], snapshot["subrole"],
            selected_status, bool(selected_writable), value_status, bool(value_writable),
            snapshot["value_is_str"], range_status, snapshot["has_selected_range"],
        )
        return snapshot

    def editable_target(self):
        """Return a verified editable focus, never infer a cursor from the app name."""
        snapshot = self._probe_snapshot("editable")
        if snapshot is None or snapshot.get("verdict") != "editable":
            return None
        return (snapshot["pid"], snapshot["element"])

    def capture_target(self):
        self.last_delivery = "none"
        self._recording_snapshot = self._probe_snapshot("capture")
        self._recording_pid = None
        self._recording_app = "-"
        self._recording_bundle = "-"
        try:
            frontmost = self._frontmost_app_for_probe()
            if frontmost is not None:
                try:
                    self._recording_pid = frontmost.processIdentifier()
                except Exception:
                    self._recording_pid = None
                try:
                    self._recording_app = frontmost.localizedName() or "-"
                except Exception:
                    pass
                try:
                    self._recording_bundle = frontmost.bundleIdentifier() or "-"
                except Exception:
                    pass
        except Exception:
            self._recording_pid = None
        if self._recording_snapshot is None:
            logger.info(
                "paste probe phase=capture reason=capture_rejected recording_pid=%s",
                self._recording_pid,
            )
            self._recording_target = None
        else:
            self._recording_target = (
                self._recording_snapshot["pid"],
                self._recording_snapshot.get("element"),
            )
        self._target_captured = True

    @staticmethod
    def _same_accessibility_target(left, right):
        """Compare AX elements without turning a failed comparison into lost text."""
        if left is right:
            return True
        try:
            return bool(CoreFoundation.CFEqual(left, right))
        except Exception:
            logger.debug("无法比较录音前后的 AX 输入目标", exc_info=True)
            return False

    def insert(self, text: str, delay: Optional[float] = None) -> bool:
        """Keep a clipboard copy; paste only into the verified recording target.

        Sending an event is not proof that an application accepted the text.
        last_delivery distinguishes a paste request from clipboard fallback.
        """
        self.last_delivery = "failed"
        if not text:
            logger.info("paste probe phase=insert reason=empty_text")
            return False
        if not self.copy(text):
            logger.info("paste probe phase=insert reason=copy_failed")
            return False
        self.last_delivery = "copied"
        snapshot = self._probe_snapshot("insert")
        try:
            insert_frontmost = self._frontmost_app_for_probe()
            insert_pid = (
                insert_frontmost.processIdentifier()
                if insert_frontmost is not None
                else None
            )
        except Exception:
            insert_pid = None
        if snapshot is None:
            if (
                self._target_captured
                and self._recording_pid is not None
                and insert_pid is not None
                and insert_pid == self._recording_pid
            ):
                try:
                    if delay:
                        time.sleep(delay)
                    self._paste_with_cg_event()
                    self.last_delivery = "sent"
                    logger.info(
                        "paste probe phase=insert reason=pid_match_sent "
                        "capture_app=%s capture_bundle=%s capture_pid=%s "
                        "insert_pid=%s",
                        self._recording_app, self._recording_bundle,
                        self._recording_pid, insert_pid,
                    )
                    return True
                except Exception:
                    logger.warning("粘贴请求失败，文字保留在剪贴板", exc_info=True)
                    return False
            logger.info(
                "paste probe phase=insert reason=insert_rejected target_captured=%s "
                "had_recording_target=%s recording_pid=%s insert_pid=%s",
                self._target_captured, self._recording_target is not None,
                self._recording_pid, insert_pid,
            )
            return False
        target = (snapshot["pid"], snapshot.get("element"))
        if self._target_captured:
            original = self._recording_target
            original_recording = self._recording_snapshot or {}
            if original is not None:
                same_ax = (
                    target[1] is not None
                    and original[1] is not None
                    and self._same_accessibility_target(target[1], original[1])
                )
                pid_changed = target[0] != original[0]
                if not pid_changed and same_ax:
                    pass
                elif target[1] is None or original[1] is None:
                    if (
                        self._recording_pid is not None
                        and insert_pid is not None
                        and insert_pid == self._recording_pid
                        and target[0] == self._recording_pid
                    ):
                        try:
                            if delay:
                                time.sleep(delay)
                            self._paste_with_cg_event()
                            self.last_delivery = "sent"
                            logger.info(
                                "paste probe phase=insert reason=pid_match_sent "
                                "capture_app=%s capture_bundle=%s capture_pid=%s "
                                "insert_pid=%s",
                                self._recording_app, self._recording_bundle,
                                self._recording_pid, insert_pid,
                            )
                            return True
                        except Exception:
                            logger.warning("粘贴请求失败，文字保留在剪贴板", exc_info=True)
                            return False
                    reason = "pid_changed" if pid_changed else "ax_target_changed"
                    logger.info(
                        "paste probe phase=insert reason=%s target_captured=True "
                        "capture_app=%s capture_bundle=%s capture_pid=%s capture_role=%s "
                        "insert_app=%s insert_bundle=%s insert_pid=%s insert_role=%s "
                        "pid_changed=%s same_ax=%s",
                        reason,
                        original_recording.get("frontmost_app", "-"),
                        original_recording.get("bundle_id", "-"),
                        original_recording.get("pid", "-"),
                        original_recording.get("role", "-"),
                        snapshot["frontmost_app"], snapshot["bundle_id"], snapshot["pid"], snapshot["role"],
                        pid_changed, same_ax,
                    )
                    return False
                else:
                    reason = "pid_changed" if pid_changed else "ax_target_changed"
                    logger.info(
                        "paste probe phase=insert reason=%s target_captured=True "
                        "capture_app=%s capture_bundle=%s capture_pid=%s capture_role=%s "
                        "insert_app=%s insert_bundle=%s insert_pid=%s insert_role=%s "
                        "pid_changed=%s same_ax=%s",
                        reason,
                        original_recording.get("frontmost_app", "-"),
                        original_recording.get("bundle_id", "-"),
                        original_recording.get("pid", "-"),
                        original_recording.get("role", "-"),
                        snapshot["frontmost_app"], snapshot["bundle_id"], snapshot["pid"], snapshot["role"],
                        pid_changed, same_ax,
                    )
                    return False
            else:
                if (
                    self._recording_pid is not None
                    and insert_pid is not None
                    and insert_pid == self._recording_pid
                ):
                    try:
                        if delay:
                            time.sleep(delay)
                        self._paste_with_cg_event()
                        self.last_delivery = "sent"
                        logger.info(
                            "paste probe phase=insert reason=pid_match_sent "
                            "capture_app=%s capture_bundle=%s capture_pid=%s "
                            "insert_pid=%s",
                            self._recording_app, self._recording_bundle,
                            self._recording_pid, insert_pid,
                        )
                        return True
                    except Exception:
                        logger.warning("粘贴请求失败，文字保留在剪贴板", exc_info=True)
                        return False
                if (
                    self._recording_pid is not None
                    and insert_pid is not None
                    and insert_pid != self._recording_pid
                ):
                    reason = "pid_changed"
                else:
                    reason = "no_recording_target"
                logger.info(
                    "paste probe phase=insert reason=%s target_captured=True "
                    "capture_app=%s capture_bundle=%s capture_pid=%s capture_role=%s "
                    "insert_app=%s insert_bundle=%s insert_pid=%s insert_role=%s "
                    "pid_changed=True same_ax=False",
                    reason,
                    original_recording.get("frontmost_app", "-"),
                    original_recording.get("bundle_id", "-"),
                    original_recording.get("pid", "-"),
                    original_recording.get("role", "-"),
                    snapshot["frontmost_app"], snapshot["bundle_id"], snapshot["pid"], snapshot["role"],
                )
                return False
        # A single paste avoids per-character interleaving, unicode truncation,
        # and duplicate insertion from speculative fallback chains.
        try:
            if delay:
                time.sleep(delay)
            self._paste_with_cg_event()
            self.last_delivery = "sent"
            logger.info("已向确认的输入框发送粘贴请求")
            return True
        except Exception:
            logger.warning("粘贴请求失败，文字保留在剪贴板", exc_info=True)
            return False
