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
    
    def editable_target(self):
        """Return a verified editable focus, never infer a cursor from the app name."""
        try:
            app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return None
            status, element = self._get_focused_ui_element()
            if status != 0 or element is None:
                return None
            _, role = self._ax_copy_attribute_value(element, "AXRole")
            _, subrole = self._ax_copy_attribute_value(element, "AXSubrole")
            if subrole == "AXSecureTextField":
                return None
            status, writable = self._ax_is_attribute_settable(element, "AXSelectedText")
            value_status, value_writable = self._ax_is_attribute_settable(element, "AXValue")
            _, selected_range = self._ax_copy_attribute_value(element, "AXSelectedTextRange")
            if ((status == 0 and writable)
                    or (value_status == 0 and value_writable
                        and (role in {"AXTextField", "AXTextArea", "AXComboBox"}
                             or selected_range is not None))
                    or (role == "AXTextArea" and selected_range is not None
                        and self._is_terminal_app(app.bundleIdentifier() or ""))):
                return (app.processIdentifier(), element)
        except Exception:
            logger.debug("无法确认可编辑光标", exc_info=True)
        return None

    def capture_target(self):
        self.last_delivery = "none"
        self._recording_target = self.editable_target()
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
        if not text or not self.copy(text):
            return False
        self.last_delivery = "copied"
        target = self.editable_target()
        if target is None:
            return False
        if self._target_captured:
            original = self._recording_target
            if (original is None or target[0] != original[0]
                    or not self._same_accessibility_target(target[1], original[1])):
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
