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

    def _is_terminal_app(self, bundle_id: str) -> bool:
        return bundle_id in {
            "com.googlecode.iterm2",
            "com.apple.Terminal",
            "com.termius-dmg.mac",
        }

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
