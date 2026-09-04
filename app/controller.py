#!/usr/bin/env python3
"""
应用控制器 - 协调菜单栏 UI、配置和识别流水线
"""

import sys
import threading
import queue
import time
import logging
import os
import subprocess
import ctypes
from contextlib import contextmanager
from datetime import datetime

from pynput import keyboard
import objc
import AppKit
from Foundation import NSObject, NSURL
from PyObjCTools import AppHelper

try:
    import ApplicationServices
except ImportError:  # pragma: no cover - macOS runtime dependency
    ApplicationServices = None

try:
    import Quartz
except ImportError:  # pragma: no cover - macOS runtime dependency
    Quartz = None

try:
    # CGEventTap 的“输入监控”请求在不同 macOS 版本上的 UI 行为并不一致。
    # IOHIDRequestAccess 是 Apple 为 HID 监听提供的请求入口；项目不依赖
    # PyObjC 的 IOKit 包，因此直接加载系统框架，避免增加运行时依赖。
    _IOKIT = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
    _IOHID_CHECK_ACCESS = _IOKIT.IOHIDCheckAccess
    _IOHID_CHECK_ACCESS.argtypes = [ctypes.c_uint32]
    _IOHID_CHECK_ACCESS.restype = ctypes.c_uint32
    _IOHID_REQUEST_ACCESS = _IOKIT.IOHIDRequestAccess
    _IOHID_REQUEST_ACCESS.argtypes = [ctypes.c_uint32]
    _IOHID_REQUEST_ACCESS.restype = ctypes.c_bool
except (AttributeError, OSError):  # pragma: no cover - macOS runtime dependency
    _IOKIT = None
    _IOHID_CHECK_ACCESS = None
    _IOHID_REQUEST_ACCESS = None

_IOHID_REQUEST_TYPE_LISTEN_EVENT = 1
_IOHID_ACCESS_TYPE_GRANTED = 0
_INPUT_MONITORING_SETTINGS_URLS = (
    # macOS 13+ 的 System Settings 扩展 URL。
    "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_ListenEvent",
    # macOS 12 及更早版本，以及部分系统升级后的兼容 URL。
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
)
_MICROPHONE_SETTINGS_URLS = (
    "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_Microphone",
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
)
_ACCESSIBILITY_SETTINGS_URLS = (
    "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_Accessibility",
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
)

# AVAudioApplication/AVAudioSession use four-character enum values on macOS.
# Keep the numeric 2 fallback for older bindings that expose the enum as 0/1/2.
_AUDIO_RECORD_PERMISSION_GRANTED = int.from_bytes(b"grnt", "big")
_AUDIO_RECORD_PERMISSION_UNDETERMINED = int.from_bytes(b"undt", "big")

# 本项目通过 Objective-C runtime 动态取得 AVFAudio 类，没有安装
# pyobjc-framework-AVFAudio，因此 PyObjC 不会自带这个 block 的元数据。
# requestRecordPermissionWithCompletionHandler: 的回调是 void (^)(BOOL)。
_AUDIO_PERMISSION_REQUEST_METADATA = {
    "arguments": {
        2: {
            "callable": {
                "retval": {"type": b"v"},
                "arguments": {
                    0: {"type": b"^v"},
                    1: {"type": b"Z"},
                },
            },
        },
    },
}


@contextmanager
def _empty_pynput_keycode_context():
    """跳过 Darwin listener 启动时不必要的 Carbon 键盘布局查询。

    pynput 的 macOS listener 在后台线程初始化时会调用
    ``TISCopyCurrentKeyboardInputSource``。macOS 26 要求这条输入源查询在
    主线程/指定队列上执行，后台调用会触发 ``dispatch_assert_queue_fail``
    直接终止整个 App。listener 自己处理事件时使用
    ``CGEventKeyboardGetUnicodeString``，不会读取这个 context，因此空 context
    不影响当前的按键识别。
    """
    yield (None, None)


def _new_keyboard_listener(**callbacks):
    """创建不会触发 macOS 26 Carbon 后台线程断言的键盘 listener。"""
    listener_class = keyboard.Listener
    if sys.platform != "darwin" or listener_class.__module__ != "pynput.keyboard._darwin":
        return listener_class(**callbacks)

    class SafeDarwinKeyboardListener(listener_class):
        def _run(self):
            # ``pynput.keyboard._darwin.Listener._run`` 通过模块级名称查找
            # keycode_context；仅在这个 listener 的线程生命周期内替换它。
            import importlib

            darwin_module = importlib.import_module("pynput.keyboard._darwin")
            original_context = darwin_module.keycode_context
            darwin_module.keycode_context = _empty_pynput_keycode_context
            try:
                return super()._run()
            finally:
                darwin_module.keycode_context = original_context

    return SafeDarwinKeyboardListener(**callbacks)

from config.settings import Settings
from config.paths import (
    app_executable,
    is_standalone_bundle,
    logs_dir,
    runtime_root,
    update_helper_path,
)
from config.version import APP_VERSION, UPDATE_REPOSITORY
from core.dictation_trace import DictationTrace
from core.pipeline import Pipeline, PipelineConfig, AudioConfig
from core.perf_log import append_perf_log
from core.live_dictation import LiveDictationSession, LiveDictationConfig
from core.media_ducker import MediaDucker
from core.stats import format_stats, load_perf_records, summarize_perf_records
from core.update_checker import (
    cleanup_staged_app,
    fetch_latest_release,
    find_macos_asset,
    is_newer,
    launch_update_helper,
    read_code_signature,
    stage_release_app,
)
from core import login_item
from ui.status_bar import StatusBarController
from ui.overlay_window import RecordingOverlay
from ui.settings_window import SettingsWindowController
from ui.stats_window import StatsWindowController
from ui.onboarding_window import OnboardingWindowController
from app import diagnostics

# 模型下载页（ggml 模型正源）；如需国内镜像可改此常量
MODEL_DOWNLOAD_URL = "https://huggingface.co/ggerganov/whisper.cpp/tree/main"
UPDATE_API_URL = f"https://api.github.com/repos/{UPDATE_REPOSITORY}/releases/latest"
_APP_BUNDLE_ID = "com.mkbm.whispercppcmd"

# watchdog 触发阈值 = 转录超时 + 宽限，避免正常长转录被误判卡死
_WATCHDOG_GRACE_SECONDS = 15.0
# 主线程 watchdog：NSTimer 1s tick，心跳停滞超过此秒数判定主线程冻结（runloop 卡在同步 C 调用）。
# 10s 容忍系统短暂卡顿（Dock 动画/WindowServer），Pa_OpenStream 级冻结持续数十秒故延迟可接受。
_MAIN_THREAD_WATCHDOG_THRESHOLD = 10.0
_WATCHDOG_POLL_INTERVAL = 5.0  # watchdog 轮询间隔
_UPDATE_CHECK_INTERVAL_SECONDS = 24 * 60 * 60

# 可配置录音触发键：名称 → 菜单标签（Key 对象由名称 getattr 得到）
_HOTKEY_LABELS = {
    "cmd_r": "右 Command",
    "cmd_l": "左 Command",
    "alt_r": "右 Option",
    "shift_r": "右 Shift",
    "ctrl_r": "右 Control",
    "f13": "F13",
    "f14": "F14",
}
_HOTKEY_KEYS = {name: getattr(keyboard.Key, name) for name in _HOTKEY_LABELS}

# 显式状态机：合法状态 + 允许的转移（from → {to}）。
# 偏离此表的转移在 _set_state 打 WARNING（但仍执行，不改行为），用于早一步暴露
# 状态/线程类 bug。processing → recording 在正确代码里不应发生（worker 串行，
# release 跑完置 idle 才会处理下一个 press）。
_STATES = {"idle", "recording", "processing", "error", "paused"}
_TRANSITIONS = {
    "idle":       {"recording", "processing", "paused", "error", "idle"},
    "recording":  {"processing", "paused", "error", "recording", "idle"},
    "processing": {"idle", "error", "paused", "processing"},
    "error":      {"idle", "paused", "error", "recording", "processing"},
    "paused":     {"idle", "recording", "processing", "error", "paused"},
}


class _SystemSleepWakeObserver(NSObject):
    """NSWorkspace 睡眠/唤醒通知中继，把通知回调到普通 Python 可调用对象。"""

    def initWithHandler_(self, handler):
        self = objc.super(_SystemSleepWakeObserver, self).init()
        if self is None:
            return None
        self._handler = handler
        return self

    def onSleep_(self, _notification):
        try:
            self._handler("sleep")
        except Exception:
            logging.getLogger(__name__).exception("处理系统睡眠通知异常")

    def onWake_(self, _notification):
        try:
            self._handler("wake")
        except Exception:
            logging.getLogger(__name__).exception("处理系统唤醒通知异常")

class _SignalPump(NSObject):
    """空操作定时器目标：让主线程定期回到 Python，以便及时处理待处理信号。

    PyObjC 的 Python 信号处理器在 NSApp.run() 阻塞期间不会主动触发；
    没有 tick 的话，kill 发的 SIGTERM 可能延迟数十秒才被处理。
    """

    def tick_(self, _sender):
        # 更新主线程心跳，供 watchdog 检测 runloop 冻结（主线程卡在同步 C 调用时 NSTimer 不 fire → 心跳停滞）
        app = getattr(self, "_app_ref", None)
        if app is not None:
            try:
                app._main_thread_heartbeat = time.monotonic()
                # 用户可能在启动时打开的系统设置里刚刚完成授权。定时静默检查
                # 可以让菜单状态和全局热键监听在授权后自动恢复，无需再重启 App。
                app.refresh_accessibility_permission_status()
            except Exception:
                pass  # tick_ 是诊断辅助，异常不应影响 runloop


class _TerminationDelegate(NSObject):
    """NSApplication 终止委托：兜住 Cmd+Q / 关机 / 注销 / 强制退出。

    没有 it，这些路径走 NSApplication 默认流程，绕过 shutdown()，导致 whisper-server
    子进程成孤儿（PPID 被 launchd 收养，泄漏 ~3GB/模型）。同步在主线程跑幂等 shutdown，
    含 _stop_server 杀子进程；阻塞数秒可接受，关机被系统强杀的极端情况由启动期防线1兜底。
    """

    def applicationShouldTerminate_(self, sender):
        app = getattr(self, "_app_ref", None)
        if app is not None and app._is_running:
            app.shutdown()
        return AppKit.NSTerminateNow


class VoiceInputApp:
    """语音输入应用控制器"""

    def __init__(self):
        self._logger = logging.getLogger(__name__)
        self.settings = Settings.load()
        self._media_ducker = MediaDucker(
            self.settings.duck_media, self.settings.duck_volume,
            self.settings.duck_when_headphones)
        self.pipeline: Pipeline = None
        self.listener: keyboard.Listener | None = None
        self.status_bar: StatusBarController | None = None
        self._is_running = False
        self._shutdown_lock = threading.Lock()
        self._state = "idle"
        self._last_result = "无"
        self._model_setup_required = False
        self._error_reset_timer: threading.Timer | None = None
        self._paused = False
        self._idle_release_timer: threading.Timer | None = None
        self._countdown_refresh_timer: threading.Timer | None = None
        self._idle_release_deadline: float | None = None
        self._backend_released = False
        self._pipeline_transitioning = False
        self._live_dictation: LiveDictationSession | None = None
        self._backend_warmup_thread: threading.Thread | None = None
        self._backend_warmup_lock = threading.Lock()
        self._current_trace: DictationTrace | None = None
        self._active_trace: DictationTrace | None = None
        self._dictation_queue: queue.Queue | None = None
        self._dictation_worker: threading.Thread | None = None
        self._worker_heartbeat: float = time.monotonic()
        self._worker_busy: bool = False
        self._watchdog_dumped: bool = False
        self._watchdog_thread: threading.Thread | None = None
        self._watchdog_stop = threading.Event()
        # 主线程 watchdog：心跳由信号泵 tick_ 更新，停滞超阈值即 runloop 冻结
        self._main_thread_heartbeat: float | None = None
        self._main_thread_dumped: bool = False
        self._main_thread_healthy_streak: int = 0
        self._sleep_wake_observer = None
        self._signal_pump: _SignalPump | None = None
        # None 表示尚未完成首次检查；后续状态只以当前进程的 AX 查询结果为准。
        self._accessibility_trusted: bool | None = None
        self._input_monitoring_trusted: bool | None = None
        self._permission_repair_alert_key = None
        self._overlay: RecordingOverlay | None = None
        self._settings_window: SettingsWindowController | None = None
        self._stats_window: StatsWindowController | None = None
        self._onboarding_window: OnboardingWindowController | None = None
        self._update_thread: threading.Thread | None = None
        self._perf_log_path = os.path.join(
            logs_dir(), "perf.jsonl",
        )

    def initialize(self) -> bool:
        """初始化应用"""
        self._logger.info(
            "应用初始化：model=%s language=%s backend=%s auto_release=%s",
            self.settings.current_model,
            self.settings.language,
            "whisper-cli",
            self.settings.auto_release_minutes,
        )
        print("\n🎙️  语音输入工具（菜单栏模式）")
        print("=" * 50)

        os.makedirs(self.settings.models_dir, exist_ok=True)
        if not self.settings.model_exists():
            print(f"❌ 模型文件不存在：{self.settings.get_model_path()}")
            self._model_setup_required = True
        elif not self._create_pipeline():
            return False

        ns_app = AppKit.NSApplication.sharedApplication()
        # Set the final activation policy before creating NSStatusItem.  The
        # previous order created the item while the app was still regular and
        # changed to accessory only after all UI was built; macOS 26 can then
        # leave the item's window outside the menu bar after relayout.
        ns_app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        self.status_bar = StatusBarController.alloc().initWithApp_(self)
        try:
            self._overlay = RecordingOverlay.alloc().init()
            self._overlay.setLevelProvider_(self._overlay_rms)
            self._overlay.setFollowMouse_(self.settings.overlay_follow_mouse)
        except Exception:
            self._logger.exception("录音浮窗构建失败，将禁用")
            self._overlay = None
        self._refresh_status_bar_details()
        self._refresh_status_bar_dynamic_details()
        threading.Thread(
            target=self._prewarm_audio_devices,
            name="AudioDeviceWarmup",
            daemon=True,
        ).start()

        self.print_status_to_console()
        return True

    def print_status_to_console(self):
        """打印状态信息"""
        pipeline_status = self.pipeline.get_status() if self.pipeline else {}
        print(f"当前模型：{self.settings.current_model}")
        language_labels = {
            "auto": "自动识别（多语言）",
            "zh": "中文",
            "en": "English",
            "ja": "日本語",
            "ko": "한국어",
        }
        print(f"识别语言：{language_labels.get(self.settings.language, self.settings.language)}")
        print(f"中文脚本：{self.settings.chinese_script}")
        print(f"听写模式：{'预览模式' if self.settings.dictation_mode == 'preview' else '快速模式'}")
        print(f"录音采样率：{self.settings.sample_rate}Hz")
        print("后端：whisper-cli")
        if pipeline_status:
            print(
                f"后端健康：{'正常' if pipeline_status.get('backend_healthy') else '异常'}"
                f" ({pipeline_status.get('backend_detail', '-')})"
            )

        if self.settings.audio_device_name:
            current_index = self.settings.get_audio_device_index()
            if current_index is not None:
                print(f"麦克风：{self.settings.audio_device_name} (索引：{current_index})")
            else:
                print(f"⚠️  未找到设备：{self.settings.audio_device_name}")
        else:
            print("麦克风：系统默认")

        print("\n可用模型:")
        for model in self.settings.list_available_models():
            marker = "●" if model == self.settings.current_model else " "
            print(f"  [{marker}] {model}")

        print("\n操作说明:")
        print("  • 按住 右 Command 键 - 开始录音")
        print("  • 松开 右 Command 键 - 转录并插入")
        print("  • 菜单栏图标 - 显示程序运行状态")
        print("  • Ctrl+C - 退出程序")
        print("=" * 50)

    def _run_on_main_thread(self, func):
        """将函数调度到主线程执行并返回结果，用于 PortAudio 流创建等线程敏感操作。"""
        if threading.current_thread() is threading.main_thread():
            return func()

        result = [None]
        exception = [None]
        event = threading.Event()

        def wrapper():
            try:
                result[0] = func()
            except Exception as e:
                exception[0] = e
            finally:
                event.set()

        AppHelper.callAfter(wrapper)
        if not event.wait(timeout=10.0):
            if not self._watchdog_dumped:  # 主线程 watchdog 已 dump 则跳过（避免重复）
                self._dump_all_thread_stacks("主线程调度超时")
            raise TimeoutError("主线程调度超时")
        if exception[0] is not None:
            raise exception[0]
        return result[0]

    def _dump_all_thread_stacks(self, reason: str) -> None:
        """主线程调度超时等卡死场景：dump 全线程 Python 栈到日志，定位主线程卡在哪个 C 调用。

        主线程冻结时卡在同步 C 调用（如 Pa_OpenStream），Python 栈顶即停留于进入该调用前
        的最后一行，足以坐实卡死位置。faulthandler 为标准库，函数内 import 避免影响启动。
        """
        import faulthandler
        import io
        buf = io.StringIO()
        faulthandler.dump_traceback(file=buf, all_threads=True)
        self._logger.error(
            "==== 全线程栈 dump：%s ====\n%s==== 栈 dump 结束 ====",
            reason,
            buf.getvalue(),
        )

    def _create_pipeline(self) -> bool:
        self._logger.info(
            "创建流水线：backend=%s model=%s language=%s device=%s",
            "whisper-cli",
            self.settings.current_model,
            self.settings.language,
            self.settings.audio_device_name or "default",
        )
        pipeline_config = PipelineConfig(
            audio=AudioConfig(
                sample_rate=self.settings.sample_rate,
                block_size=256,
                latency='low',
                device_name=self.settings.audio_device_name,
                max_recording_seconds=self.settings.max_recording_seconds,
            ),
            model_backend='whisper-cli',
            model_path=self.settings.get_model_path(),
            model_name=self.settings.current_model,
            language=self.settings.language,
            n_threads=self.settings.n_threads,
            cli_path=self.settings.whisper_cli_path,
        )

        pipeline_config.output.history_file = self.settings.history_file
        pipeline_config.output.verbose = self.settings.verbose
        pipeline_config.output.auto_paste = self.settings.auto_paste
        pipeline_config.output.chinese_script = self.settings.chinese_script
        pipeline_config.clipboard.paste_delay = self.settings.paste_delay
        pipeline_config.transcription_prompt = self.settings.get_transcription_prompt()
        pipeline_config.transcription_timeout = self.settings.transcription_timeout
        pipeline_config.use_vad = self.settings.use_vad
        pipeline_config.vad_model = self.settings.vad_model

        self.pipeline = Pipeline(pipeline_config)
        self._live_dictation = LiveDictationSession(
            audio_source=self.pipeline.audio_source,
            model_engine=self.pipeline.model_engine,
            clipboard=self.pipeline.clipboard,
            config=LiveDictationConfig(
                update_interval=0.25,
                window_seconds=4.0,
                min_audio_seconds=0.45,
                silence_rms_threshold=0.008,
                chinese_script=self.settings.chinese_script,
                reconcile_interval=2.0,
                reconcile_min_audio_seconds=1.25,
                mutable_tail_chars=80,
                max_overlap_chars=120,
                full_reconcile_diff_ratio=0.65,
            ),
        )
        self.pipeline.trace = None
        self.pipeline.audio_source.trace = None
        self.pipeline.model_engine.trace = None
        self._live_dictation.trace = None
        return self.pipeline.initialize()

    def open_settings(self) -> None:
        if self._settings_window is None:
            self._settings_window = SettingsWindowController.alloc().initWithApp_(self)
        self._settings_window.show()

    def apply_app_settings(self, values: dict) -> None:
        """保存设置窗口中的应用级选项。"""
        if "update_check_enabled" in values:
            self.settings.update_check_enabled = bool(values["update_check_enabled"])
        self.settings.__post_init__()
        self.settings.save()
        self._refresh_status_bar_details()

    def get_stats_text(self) -> str:
        summary = summarize_perf_records(load_perf_records(self._perf_log_path))
        return f"WhisperCppCmd {APP_VERSION}\n\n{format_stats(summary)}"

    def show_stats(self) -> None:
        if self._stats_window is None:
            self._stats_window = StatsWindowController.alloc().initWithApp_(self)
        self._stats_window.show()

    def open_onboarding(self) -> None:
        """手动打开欢迎与权限向导窗口。"""
        if self._onboarding_window is None:
            self._onboarding_window = OnboardingWindowController.alloc().initWithApp_(self)
        self._onboarding_window.show()

    def show_onboarding_if_needed(self) -> None:
        # 即使用户之前点过完成，只要核心权限后来因替换 App 而失效，也重新
        # 打开同一个常驻向导；不再用一次性的权限 Alert 打断用户。
        self._check_accessibility_permission()
        permissions = self.get_permission_status()
        if self.settings.onboarding_completed and all(
            permissions.get(key, False) for key in ("microphone", "accessibility")
        ):
            return
        # 允许测试/恢复路径使用未完整初始化的轻量控制器对象。
        getattr(self, "_logger", logging.getLogger(__name__)).info(
            "显示常驻欢迎页：permissions=%s", permissions
        )
        if self._onboarding_window is None:
            self._onboarding_window = OnboardingWindowController.alloc().initWithApp_(self)
        self._onboarding_window.show()

    def _check_for_updates_if_due(self) -> None:
        """每天最多自动检查一次；自动检查不打扰用户显示“已最新”。"""
        if not self.settings.update_check_enabled:
            return
        try:
            last_checked = datetime.fromisoformat(self.settings.last_update_check_at).timestamp()
        except (TypeError, ValueError, OverflowError):
            last_checked = 0.0
        if time.time() - last_checked < _UPDATE_CHECK_INTERVAL_SECONDS:
            return
        self.settings.last_update_check_at = datetime.now().isoformat(timespec="seconds")
        self.settings.save()
        self.check_for_updates(silent=True)

    def check_for_updates(self, silent: bool = False) -> None:
        """后台检查 GitHub Release；用户确认后下载并安装。"""
        self._logger.info("开始检查更新：current=%s", APP_VERSION)

        def worker():
            try:
                release = fetch_latest_release(UPDATE_API_URL)
                newer = is_newer(APP_VERSION, release)
                AppHelper.callAfter(self._show_update_result, release, newer, "", silent)
            except Exception as exc:
                self._logger.info("检查更新失败：%s", exc)
                AppHelper.callAfter(self._show_update_result, None, False, str(exc), silent)

        threading.Thread(target=worker, name="UpdateChecker", daemon=True).start()

    def _show_update_result(self, release, newer: bool, error: str, silent: bool = False):
        if error:
            if silent:
                return
            self._show_simple_alert("检查更新失败", f"暂时无法连接 GitHub Releases。\n\n{error}")
            return
        if release is None:
            if silent:
                return
            self._show_simple_alert("检查更新失败", "GitHub 没有返回可用版本信息。")
            return
        if not newer:
            if silent:
                return
            self._show_simple_alert("已是最新版本", f"当前版本：{APP_VERSION}\n最新发布：{release.tag_name}")
            return

        asset = find_macos_asset(release)
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_(f"发现新版本 {release.tag_name}")
        alert.setInformativeText_(f"当前版本：{APP_VERSION}\n{release.name}")
        if asset is None:
            alert.addButtonWithTitle_("打开下载页")
            alert.addButtonWithTitle_("稍后")
            should_open = alert.runModal() == AppKit.NSAlertFirstButtonReturn
        else:
            alert.addButtonWithTitle_("下载并安装")
            alert.addButtonWithTitle_("打开下载页")
            alert.addButtonWithTitle_("稍后")
            response = alert.runModal()
            if response == AppKit.NSAlertFirstButtonReturn:
                self._download_update(release)
                return
            should_open = response == AppKit.NSAlertSecondButtonReturn
        if should_open and release.html_url:
            import webbrowser
            webbrowser.open(release.html_url)

    def _download_update(self, release) -> None:
        if self._update_thread is not None and self._update_thread.is_alive():
            self._show_simple_alert("更新正在下载", "请稍候，当前已经有一个更新任务在进行。")
            return

        if not is_standalone_bundle():
            # alias App 的 executable 指向源码/构建目录；让它走自动替换会
            # 破坏开发环境，也无法保证新旧 bundle 的签名连续性。
            self._show_simple_alert(
                "当前版本不支持自动安装",
                "自动更新仅在 standalone 分发包中可用，请打开正式安装的 App。",
            )
            return

        def worker():
            staged_app = ""
            updates_root = os.path.join(runtime_root(), "updates")
            try:
                current_executable = app_executable()
                if not current_executable:
                    raise RuntimeError("当前运行环境找不到可用的 App executable")
                # .../WhisperCppCmd.app/Contents/MacOS/WhisperCppCmd
                current_app = os.path.abspath(
                    os.path.dirname(os.path.dirname(os.path.dirname(current_executable)))
                )
                if os.path.basename(current_app) != "WhisperCppCmd.app":
                    raise RuntimeError("当前 App bundle 路径无效")

                # 固定更新签名连续性：Developer ID 安装要求同 Team ID；早期
                # ad hoc 包则只接受 ad hoc 更新。helper 退出前会再次验证，防止
                # staged 文件在下载完成后被替换造成 TOCTOU 绕过。
                current_signature = read_code_signature(current_app)
                signature_policy = {
                    "trusted_team_id": current_signature.team_identifier or None,
                    "require_developer_id": bool(current_signature.team_identifier),
                    "require_adhoc": bool(current_signature.adhoc),
                }
                helper = update_helper_path()
                if not helper:
                    raise RuntimeError("当前运行环境找不到可用的更新 helper")
                staged_app = stage_release_app(
                    release,
                    updates_root,
                    **signature_policy,
                )
                AppHelper.callAfter(self._confirm_install_update, current_app, staged_app, helper)
            except Exception as exc:
                if staged_app:
                    cleanup_staged_app(staged_app, updates_root)
                self._logger.info("下载更新失败：%s", exc)
                AppHelper.callAfter(
                    self._show_simple_alert,
                    "下载更新失败",
                    str(exc),
                )

        self._update_thread = threading.Thread(target=worker, name="UpdateDownloader", daemon=True)
        self._update_thread.start()

    def _confirm_install_update(self, current_app: str, staged_app: str, helper: str) -> None:
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("更新包已准备好")
        alert.setInformativeText_("安装更新会关闭并重新打开 WhisperCppCmd。旧版本会保留为 .previous 备份。")
        alert.addButtonWithTitle_("现在安装")
        alert.addButtonWithTitle_("取消")
        if alert.runModal() != AppKit.NSAlertFirstButtonReturn:
            cleanup_staged_app(staged_app, os.path.dirname(staged_app))
            return
        try:
            launch_update_helper(helper, current_app, staged_app)
            self.shutdown()
        except Exception as exc:
            cleanup_staged_app(staged_app, os.path.dirname(staged_app))
            self._logger.exception("启动更新 helper 失败")
            self._show_simple_alert("安装更新失败", str(exc))

    def _show_simple_alert(self, title: str, message: str) -> None:
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(message)
        alert.addButtonWithTitle_("好")
        alert.runModal()

    def _prewarm_audio_devices(self):
        if self.pipeline is None:
            return
        try:
            self._logger.info("预热音频设备列表")
            _ = self.pipeline.audio_source.available_devices
        except Exception as e:
            self._logger.warning("预热音频设备列表失败：%s", e)

    def _start_backend_warmup(self):
        if self.pipeline is None or self.pipeline.model_engine.is_loaded:
            return

        with self._backend_warmup_lock:
            thread = self._backend_warmup_thread
            if thread is not None and thread.is_alive():
                return

            trace = self._current_trace

            def worker():
                try:
                    self._logger.info(
                        "%s 后台唤醒后端开始",
                        trace.prefix("backend_warmup") if trace else "[backend_warmup]",
                    )
                    self._ensure_backend_ready()
                    self._logger.info(
                        "%s 后台唤醒后端完成：loaded=%s recording=%s",
                        trace.prefix("backend_warmup") if trace else "[backend_warmup]",
                        self.pipeline.model_engine.is_loaded if self.pipeline else None,
                        self.pipeline.is_recording if self.pipeline else None,
                    )
                    if (
                        self.settings.dictation_mode == "preview"
                        and self._live_dictation is not None
                        and self.pipeline is not None
                        and self.pipeline.is_recording
                        and self.pipeline.model_engine.is_loaded
                    ):
                        self._live_dictation.start()
                finally:
                    with self._backend_warmup_lock:
                        if self._backend_warmup_thread is threading.current_thread():
                            self._backend_warmup_thread = None

            self._backend_warmup_thread = threading.Thread(
                target=worker,
                name="BackendWarmup",
                daemon=True,
            )
            self._backend_warmup_thread.start()

    def _wait_for_backend_warmup(self, timeout: float = 15.0):
        with self._backend_warmup_lock:
            thread = self._backend_warmup_thread

        if thread is None or not thread.is_alive():
            return

        thread.join(timeout=timeout)

    def _rebuild_pipeline(self) -> bool:
        if self.pipeline and self.pipeline.is_recording:
            print("❌ 录音中无法切换该配置")
            return False

        self._cancel_error_reset_timer()
        self._cancel_idle_release_timer()

        if self._live_dictation is not None:
            self._live_dictation.stop()
        if self.pipeline:
            self.pipeline.shutdown()

        if not self.settings.model_exists():
            print(f"❌ 模型文件不存在：{self.settings.get_model_path()}")
            self._model_setup_required = True
            self._last_result = "错误：模型文件不存在"
            self._set_state("error")
            self._refresh_status_bar_details()
            return False

        if not self._create_pipeline():
            self._last_result = "错误：转写引擎重启失败"
            self._set_state("error")
            self._refresh_status_bar_details()
            return False

        self._backend_released = False
        self._model_setup_required = False
        self._set_state("paused" if self._paused else "idle")
        self._refresh_status_bar_details()
        self._refresh_status_bar_dynamic_details()
        self._schedule_idle_release_timer()
        return True

    def _get_available_microphones(self):
        devices = [{
            "title": "系统默认",
            "value": "__default__",
            "selected": self.settings.audio_device_name is None
        }]
        if self.pipeline is None:
            return devices

        for dev in self.pipeline.audio_source.available_devices:
            name = dev['name']
            devices.append({
                "title": name,
                "value": name,
                "selected": name == self.settings.audio_device_name
            })
        return devices

    def _get_model_options(self):
        current = self.settings.current_model
        return [
            {"title": model, "value": model, "selected": model == current}
            for model in self.settings.list_available_models()
        ]

    def _get_language_options(self):
        labels = {
            "zh": "中文",
            "en": "English",
            "ja": "日本語",
            "ko": "한국어",
            "auto": "自动识别（多语言）",
        }
        return [
            {"title": label, "value": code, "selected": code == self.settings.language}
            for code, label in labels.items()
        ]

    def _get_chinese_script_options(self):
        labels = {
            "simplified": "简体",
            "traditional": "繁体",
            "auto": "自动",
        }
        return [
            {"title": label, "value": code, "selected": code == self.settings.chinese_script}
            for code, label in labels.items()
        ]

    def _get_dictation_mode_options(self):
        labels = {
            "preview": "预览模式",
            "quick": "快速模式",
        }
        return [
            {"title": label, "value": code, "selected": code == self.settings.dictation_mode}
            for code, label in labels.items()
        ]

    def _set_state(self, state: str):
        prev = self._state
        if state not in _STATES:
            self._logger.warning("未知状态被拒绝：%s（当前保持 %s）", state, prev)
            return
        if state not in _TRANSITIONS.get(prev, set()):
            trace_id = self._current_trace.trace_id if self._current_trace else "-"
            self._logger.warning(
                "非法状态转移：%s → %s（不在转移表内）trace=%s",
                prev, state, trace_id,
            )
        self._state = state
        self._logger.info("状态切换：%s → %s", prev, state)
        self._cancel_error_reset_timer()
        if self.status_bar is not None:
            AppHelper.callAfter(self.status_bar.setState_, state)
        if state == "error":
            self._schedule_error_reset()
        if state == "recording":
            self._show_overlay()
        else:
            self._hide_overlay()

    def _refresh_status_bar_details(self):
        if self.status_bar is None:
            return

        if self.pipeline is None:
            backend_text = "等待模型"
        else:
            status = self.pipeline.get_status()
            backend_text = status.get('backend', '-') or '-'
            backend_detail = status.get('backend_detail', '')
            if self._backend_released:
                backend_detail = "已释放"
            if backend_detail:
                backend_text = f"{backend_text} / {backend_detail}"

        AppHelper.callAfter(self.status_bar.setModelName_, self.settings.current_model)
        AppHelper.callAfter(self.status_bar.setBackendStatus_, backend_text)
        AppHelper.callAfter(self.status_bar.setLastResult_, self._truncate_menu_text(self._last_result))
        AppHelper.callAfter(self.status_bar.setPaused_, self._paused)
        AppHelper.callAfter(
            self.status_bar.setReleaseBackendEnabled_,
            bool(self.pipeline is not None and not self._backend_released),
        )
        AppHelper.callAfter(self.status_bar.setAutoReleaseMinutes_, self.settings.auto_release_minutes)
        AppHelper.callAfter(self.status_bar.setAutoReleaseCountdown_, self._get_auto_release_countdown_text())
        AppHelper.callAfter(self.status_bar.setAutoPaste_, self.settings.auto_paste)
        AppHelper.callAfter(self.status_bar.setLoginAtStartup_, login_item.is_enabled())
        AppHelper.callAfter(self.status_bar.setVad_, self.settings.use_vad)
        AppHelper.callAfter(self.status_bar.setOverlay_, self.settings.show_overlay)
        AppHelper.callAfter(self.status_bar.setOverlayFollowMouse_, self.settings.overlay_follow_mouse)
        AppHelper.callAfter(self.status_bar.setDuckMedia_, self.settings.duck_media)
        AppHelper.callAfter(self.status_bar.setDuckHeadphones_, self.settings.duck_when_headphones)
        AppHelper.callAfter(self.status_bar.setChineseScriptOptions_, self._get_chinese_script_options())
        AppHelper.callAfter(self.status_bar.setDictationModeOptions_, self._get_dictation_mode_options())

    def _refresh_status_bar_dynamic_details(self):
        if self.status_bar is None:
            return

        AppHelper.callAfter(self.status_bar.setModelOptions_, self._get_model_options())
        AppHelper.callAfter(self.status_bar.setMicOptions_, self._get_available_microphones())
        AppHelper.callAfter(self.status_bar.setLanguageOptions_, self._get_language_options())
        AppHelper.callAfter(self.status_bar.setHistoryItems_, self.get_recent_history())
        AppHelper.callAfter(self.status_bar.setHotkeyOptions_, self._get_hotkey_options())

    def _refresh_status_bar_countdown(self):
        if self.status_bar is None:
            return
        AppHelper.callAfter(self.status_bar.setAutoReleaseCountdown_, self._get_auto_release_countdown_text())

    def _truncate_menu_text(self, text: str, max_length: int = 28) -> str:
        text = text.strip() if text else "无"
        if len(text) <= max_length:
            return text
        return text[: max_length - 1] + "…"

    def truncate_menu_text(self, text: str, max_length: int = 28) -> str:
        return self._truncate_menu_text(text, max_length)

    def _has_accessibility_permission(self, prompt: bool = False) -> bool:
        if ApplicationServices is None:
            self._logger.warning("macOS 辅助功能 API 不可用，无法确认权限")
            return False

        try:
            if prompt and hasattr(ApplicationServices, "AXIsProcessTrustedWithOptions"):
                return bool(
                    ApplicationServices.AXIsProcessTrustedWithOptions(
                        {ApplicationServices.kAXTrustedCheckOptionPrompt: True}
                    )
                )
            return bool(ApplicationServices.AXIsProcessTrusted())
        except Exception as e:
            self._logger.warning("检查辅助功能权限失败：%s", e)
            return False

    def _set_accessibility_permission_status(self, trusted: bool):
        if self.status_bar is None:
            return
        # 权限检查发生在主线程（启动、菜单回调、NSTimer tick）。这里直接更新，
        # 避免 callAfter 排队造成菜单短暂显示旧的“已允许”状态。
        if threading.current_thread() is threading.main_thread():
            self.status_bar.setAccessibilityPermission_(bool(trusted))
        else:
            AppHelper.callAfter(self.status_bar.setAccessibilityPermission_, bool(trusted))

    def _set_input_monitoring_permission_status(self, trusted: bool):
        if self.status_bar is None:
            return
        if threading.current_thread() is threading.main_thread():
            self.status_bar.setInputMonitoringPermission_(bool(trusted))
        else:
            AppHelper.callAfter(self.status_bar.setInputMonitoringPermission_, bool(trusted))

    def _has_input_monitoring_permission(self, prompt: bool = False) -> bool:
        """检查/请求单独的「输入监控」权限状态。

        macOS 将后台监听键盘与辅助功能拆成两项隐私权限。当前 pynput 的
        listen-only CGEventTap 在辅助功能已授权时可以工作，因此这里的输入
        监控结果用于准确展示和引导用户，不作为监听器启动的硬门槛。
        """
        try:
            if prompt:
                # IOHIDRequestAccess 在部分 macOS/无 TTY 启动路径会同步等待
                # 系统授权 UI，若在 NSRunLoop 启动前调用会把整个 App 卡住。
                # 这里的“请求”统一交给调用方打开系统设置，保持主线程可用；
                # 静默状态仍由 IOHIDCheckAccess/Quartz 查询。
                return False
            if not prompt and _IOHID_CHECK_ACCESS is not None:
                return int(
                    _IOHID_CHECK_ACCESS(_IOHID_REQUEST_TYPE_LISTEN_EVENT)
                ) == _IOHID_ACCESS_TYPE_GRANTED

            if Quartz is None:
                self._logger.warning("Quartz 输入监控 API 不可用，无法确认权限")
                return False
            check = (
                getattr(Quartz, "CGRequestListenEventAccess", None)
                if prompt
                else getattr(Quartz, "CGPreflightListenEventAccess", None)
            )
            if check is None:
                self._logger.warning("当前 macOS 未提供输入监控权限 API")
                return False
            return bool(check())
        except Exception as exc:
            self._logger.warning("检查输入监控权限失败：%s", exc)
            return False

    def _audio_permission_class(self):
        """返回可读取麦克风授权状态的 AVAudio 类。"""
        for class_name in ("AVAudioApplication", "AVAudioSession"):
            try:
                audio_class = objc.lookUpClass(class_name)
                shared = getattr(audio_class, "sharedInstance", None)
                if shared is not None:
                    return class_name, audio_class
            except Exception:
                continue
        return None

    def _audio_permission_object(self):
        """返回可读取麦克风授权状态的 AVAudio 对象。"""
        audio_info = self._audio_permission_class()
        if audio_info is None:
            return None
        _, audio_class = audio_info
        try:
            return audio_class.sharedInstance()
        except Exception:
            return None

    def _microphone_permission_value(self):
        audio_object = self._audio_permission_object()
        if audio_object is None:
            self._logger.warning("AVAudio 权限 API 不可用，无法确认麦克风权限")
            return None
        try:
            record_permission = getattr(audio_object, "recordPermission", None)
            value = record_permission() if callable(record_permission) else record_permission
            return int(value)
        except Exception as exc:
            self._logger.warning("检查麦克风权限失败：%s", exc)
            return None

    def _has_microphone_permission(self) -> bool:
        """静默检查当前 App 的麦克风权限。"""
        value = self._microphone_permission_value()
        return value in {_AUDIO_RECORD_PERMISSION_GRANTED, 2}

    def _request_microphone_permission(self) -> bool:
        """请求麦克风权限；页面保持打开，由回调触发状态刷新。"""
        value = self._microphone_permission_value()
        if value in {_AUDIO_RECORD_PERMISSION_GRANTED, 2}:
            return True

        audio_info = self._audio_permission_class()
        audio_class = audio_info[1] if audio_info is not None else None
        if audio_info is not None and audio_info[0] == "AVAudioApplication":
            # 动态 runtime 类没有 AVFAudio 的 PyObjC metadata；补上 block
            # 签名，否则会在真实 App 中抛出 “block, but no signature available”。
            try:
                objc.registerMetaDataForSelector(
                    b"AVAudioApplication",
                    b"requestRecordPermissionWithCompletionHandler:",
                    _AUDIO_PERMISSION_REQUEST_METADATA,
                )
            except Exception:
                self._logger.exception("注册 AVAudio 麦克风授权回调签名失败")

        request = getattr(audio_class, "requestRecordPermissionWithCompletionHandler_", None)
        if audio_class is not None and request is not None and value in {
            _AUDIO_RECORD_PERMISSION_UNDETERMINED,
            0,
        }:
            def completed(_granted):
                self._microphone_permission_callback = None
                AppHelper.callAfter(self.refresh_accessibility_permission_status)

            try:
                # 这是 AVAudioApplication 的类方法，不能从 sharedInstance
                # 返回的实例上调用。
                self._microphone_permission_callback = completed
                request(completed)
                self._logger.info("请求 macOS 麦克风授权")
                return False
            except Exception:
                self._logger.exception("请求 macOS 麦克风授权失败")

        self._open_microphone_settings()
        return False

    def _open_privacy_settings(self, urls, label: str) -> bool:
        """打开指定的系统隐私页面，作为系统请求无 UI 时的兜底。"""
        workspace = AppKit.NSWorkspace.sharedWorkspace()
        for raw_url in urls:
            try:
                url = NSURL.URLWithString_(raw_url)
                if url is not None and workspace.openURL_(url):
                    self._logger.info("已打开 macOS %s 设置：%s", label, raw_url)
                    return True
            except Exception:
                self._logger.exception("打开 macOS %s 设置失败：%s", label, raw_url)
        self._logger.warning("无法打开 macOS %s 设置，请在系统设置 → 隐私与安全性中手动处理", label)
        return False

    def _open_input_monitoring_settings(self) -> bool:
        """打开系统设置的“输入监控”页面。"""
        return self._open_privacy_settings(_INPUT_MONITORING_SETTINGS_URLS, "输入监控")

    def _open_microphone_settings(self) -> bool:
        """打开系统设置的“麦克风”页面。"""
        return self._open_privacy_settings(_MICROPHONE_SETTINGS_URLS, "麦克风")

    def _open_accessibility_settings(self) -> bool:
        """打开系统设置的“辅助功能”页面。"""
        return self._open_privacy_settings(_ACCESSIBILITY_SETTINGS_URLS, "辅助功能")

    def get_permission_status(self) -> dict[str, bool]:
        """返回欢迎页使用的实时权限状态。"""
        return {
            "microphone": self._has_microphone_permission(),
            "accessibility": bool(getattr(self, "_accessibility_trusted", False)),
            "input_monitoring": bool(getattr(self, "_input_monitoring_trusted", False)),
        }

    def request_permission(self, permission: str) -> bool:
        """从欢迎页的单个权限行发起请求，不弹出一次性 App Alert。"""
        permission = str(permission or "")
        if permission == "microphone":
            return self._request_microphone_permission()
        if permission == "accessibility":
            self._check_accessibility_permission(request_prompt=True)
            if not getattr(self, "_accessibility_trusted", False):
                self._open_accessibility_settings()
            return bool(getattr(self, "_accessibility_trusted", False))
        if permission == "input_monitoring":
            return self.check_input_monitoring_permission()
        return False

    def check_input_monitoring_permission(self):
        """由菜单栏“输入监控权限”项触发请求，并在必要时打开系统设置。"""
        trusted = self._has_input_monitoring_permission(prompt=False)
        if not trusted:
            self._logger.info("请求 macOS 输入监控授权")
            trusted = self._has_input_monitoring_permission(prompt=True)

        self._input_monitoring_trusted = bool(trusted)
        self._set_input_monitoring_permission_status(self._input_monitoring_trusted)
        if not self._input_monitoring_trusted:
            self._open_input_monitoring_settings()
        else:
            # 让辅助功能状态和监听器状态也同步一次；已有辅助功能权限时，
            # pynput 的 event tap 不需要因输入监控状态变化而重建。
            self._check_accessibility_permission()
        return self._input_monitoring_trusted

    def _keyboard_listener_is_healthy(self) -> bool:
        """判断 pynput 监听器是否真的建立了 event tap，而不只是线程存活。"""
        listener = getattr(self, "listener", None)
        if listener is None:
            return False
        try:
            if not listener.is_alive():
                return False
        except Exception:
            return False
        if getattr(listener, "IS_TRUSTED", True) is False:
            return False
        # Fake listener/test double 不一定有 _loop；真实 pynput listener 有该字段。
        if hasattr(listener, "_loop") and getattr(listener, "_loop", None) is None:
            return False
        return True

    def _current_app_bundle_path(self) -> str:
        executable = app_executable()
        if executable:
            # .../WhisperCppCmd.app/Contents/MacOS/WhisperCppCmd
            return os.path.dirname(os.path.dirname(os.path.dirname(executable)))
        return "当前正在运行的 WhisperCppCmd.app"

    def _show_permission_repair_guidance(self) -> None:
        """记录权限异常，由常驻欢迎页展示，不再弹出一次性对话框。"""
        if not getattr(self, "_is_running", False):
            return

        missing = []
        accessibility_trusted = getattr(self, "_accessibility_trusted", False)
        input_monitoring_trusted = getattr(self, "_input_monitoring_trusted", False)
        listener_exists = getattr(self, "listener", None) is not None
        listener_healthy = self._keyboard_listener_is_healthy() if listener_exists else True

        if not accessibility_trusted:
            missing.append("辅助功能权限")
        # 当前 macOS 在辅助功能已授权时可以让 pynput 的 event tap 工作；
        # 单独的输入监控未授权只有在它同时造成监听器异常时才需要弹出修复指引。
        if not input_monitoring_trusted and (not accessibility_trusted or not listener_healthy):
            missing.append("输入监控权限")
        if accessibility_trusted and listener_exists and not listener_healthy:
            missing.append("全局键盘监听器")

        if not missing:
            self._permission_repair_alert_key = None
            return

        alert_key = tuple(missing)
        if alert_key == self._permission_repair_alert_key:
            return
        self._permission_repair_alert_key = alert_key
        self._logger.info(
            "权限状态待处理：%s；由常驻欢迎页显示，当前不弹出一次性对话框",
            "、".join(missing),
        )
        onboarding = getattr(self, "_onboarding_window", None)
        if onboarding is not None:
            onboarding.update_permission_status(self.get_permission_status())

    def _stop_keyboard_listener(self):
        previous = self.listener
        self.listener = None
        if previous is None:
            return
        try:
            previous.stop()
            previous.join(timeout=1.0)
        except Exception:
            self._logger.exception("停止全局热键监听器失败")

    def _restart_keyboard_listener(self):
        """重建 pynput 监听器。

        pynput 在启动时没有辅助功能权限时会创建一个无法收到事件的 event tap；
        用户稍后在系统设置里授权后，旧 tap 不会可靠地恢复，必须新建监听器。
        输入监控状态单独展示和请求；在当前 macOS 上，辅助功能授权已经足以
        让这个 listen-only event tap 工作。
        """
        if not getattr(self, "_accessibility_trusted", False):
            self._logger.info(
                "全局热键监听器暂不启动：accessibility=%s input_monitoring=%s",
                getattr(self, "_accessibility_trusted", None),
                getattr(self, "_input_monitoring_trusted", None),
            )
            return
        if not getattr(self, "_input_monitoring_trusted", False):
            self._logger.info(
                "输入监控尚未单独授权：当前监听由辅助功能权限提供"
            )

        self._stop_keyboard_listener()

        self.listener = _new_keyboard_listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self.listener.start()
        try:
            # 等待 pynput 完成 CGEventTap 初始化，再记录真实状态；仅看
            # Thread.is_alive() 只能说明线程存在，不能说明 event tap 已建立。
            self.listener.wait()
        except Exception:
            self._logger.exception("等待全局热键监听器就绪失败")
        self._logger.info(
            "全局热键监听器已启动：target=%s trusted=%s listener_trusted=%s running=%s alive=%s event_tap=%s",
            getattr(getattr(self, "settings", None), "hotkey", "?"),
            self._accessibility_trusted,
            getattr(self.listener, "IS_TRUSTED", None),
            getattr(self.listener, "running", None),
            self.listener.is_alive(),
            getattr(self.listener, "_loop", None) is not None,
        )

    def _check_accessibility_permission(self, *, request_prompt: bool = False, trusted=None) -> bool:
        """检查当前进程权限，并同步菜单栏状态。

        ``AXIsProcessTrustedWithOptions`` 的 prompt 是异步提示，返回值仍代表
        调用当下的状态，所以这里允许复用已经完成的静默检查结果，避免一次
        启动检查触发多次系统调用。
        """
        if trusted is None:
            trusted = self._has_accessibility_permission(prompt=False)
        accessibility_trusted = bool(trusted)
        input_monitoring_trusted = self._has_input_monitoring_permission(prompt=False)
        previous_accessibility_trusted = getattr(self, "_accessibility_trusted", None) is True

        if not accessibility_trusted and request_prompt:
            self._logger.info("请求 macOS 辅助功能授权引导")
            accessibility_trusted = bool(self._has_accessibility_permission(prompt=True))
        if not input_monitoring_trusted and request_prompt:
            self._logger.info("请求 macOS 输入监控授权引导")
            input_monitoring_trusted = bool(self._has_input_monitoring_permission(prompt=True))

        self._accessibility_trusted = accessibility_trusted
        self._input_monitoring_trusted = input_monitoring_trusted
        self._set_accessibility_permission_status(accessibility_trusted)
        self._set_input_monitoring_permission_status(input_monitoring_trusted)
        trusted = accessibility_trusted and input_monitoring_trusted

        # 授权发生在 App 已经运行之后：重新创建 event tap，避免旧监听器仍停留
        # 在“未受信任”状态。启动阶段 listener 尚未创建，不会重复启动。
        if (
            accessibility_trusted
            and getattr(self, "_is_running", False)
            and (
                getattr(self, "listener", None) is None
                or not previous_accessibility_trusted
                or not self._keyboard_listener_is_healthy()
            )
        ):
            self._restart_keyboard_listener()
        elif not accessibility_trusted and getattr(self, "listener", None) is not None:
            self._stop_keyboard_listener()

        if getattr(self, "_is_running", False) and getattr(self, "listener", None) is not None:
            self._show_permission_repair_guidance()

        return trusted

    def _print_permission_guidance_if_needed(self):
        # 每次启动都做静默检查。权限引导由常驻欢迎页负责，不再额外弹出
        # 一次性 App Alert 或触发“前往后结束”的临时流程。
        trusted = self._check_accessibility_permission()
        if trusted:
            return

        if not self._accessibility_trusted:
            self._logger.warning("缺少辅助功能权限：右 Command 热键监听将不可用")
        if not self._input_monitoring_trusted:
            self._logger.info("输入监控尚未单独授权：当前右 Command 监听仍由辅助功能权限提供")

        if not self._accessibility_trusted:
            print("⚠️  当前 App 未获得辅助功能权限，右 Command 热键不会生效")
        if not self._accessibility_trusted:
            print("   请在「系统设置 → 隐私与安全性 → 辅助功能」允许 WhisperCppCmd.app")
        if not self._input_monitoring_trusted:
            print("   可在「系统设置 → 隐私与安全性 → 输入监控」单独允许 WhisperCppCmd.app")
        print("   请在欢迎页点击对应权限行的“打开设置”；授权后页面会自动刷新")

    def check_accessibility_permission(self):
        """由菜单栏状态项触发：重新检查并打开系统授权页面。"""
        return self.request_permission("accessibility")

    def request_permissions(self):
        """兼容旧调用：请求全部权限，页面本身保持打开并持续刷新。"""
        self.request_permission("microphone")
        self.request_permission("accessibility")
        self.request_permission("input_monitoring")
        self.refresh_accessibility_permission_status()

    def refresh_accessibility_permission_status(self):
        """菜单打开时静默刷新权限状态，不主动弹出系统授权引导。"""
        self._check_accessibility_permission()
        onboarding = getattr(self, "_onboarding_window", None)
        if onboarding is not None:
            onboarding.update_permission_status(self.get_permission_status())

    def _schedule_error_reset(self):
        self._error_reset_timer = threading.Timer(1.5, self._reset_error_state_if_needed)
        self._error_reset_timer.daemon = True
        self._error_reset_timer.start()

    def _cancel_error_reset_timer(self):
        if self._error_reset_timer is not None:
            self._error_reset_timer.cancel()
            self._error_reset_timer = None

    def _schedule_idle_release_timer(self):
        self._cancel_idle_release_timer()
        if self.settings.auto_release_minutes <= 0:
            return
        if self.pipeline is None or self.pipeline.is_recording or self._backend_released:
            return

        delay_seconds = self.settings.auto_release_minutes * 60
        self._idle_release_deadline = time.time() + delay_seconds

        self._idle_release_timer = threading.Timer(
            delay_seconds,
            self._auto_release_backend
        )
        self._idle_release_timer.daemon = True
        self._idle_release_timer.start()
        self._schedule_countdown_refresh()
        self._refresh_status_bar_details()

    def _cancel_idle_release_timer(self):
        if self._idle_release_timer is not None:
            self._idle_release_timer.cancel()
            self._idle_release_timer = None
        self._idle_release_deadline = None
        self._cancel_countdown_refresh()

    def _schedule_countdown_refresh(self):
        self._cancel_countdown_refresh()
        if self._idle_release_deadline is None:
            return
        self._countdown_refresh_timer = threading.Timer(1.0, self._refresh_countdown)
        self._countdown_refresh_timer.daemon = True
        self._countdown_refresh_timer.start()

    def _cancel_countdown_refresh(self):
        if self._countdown_refresh_timer is not None:
            self._countdown_refresh_timer.cancel()
            self._countdown_refresh_timer = None

    def _refresh_countdown(self):
        self._countdown_refresh_timer = None
        self._refresh_status_bar_countdown()
        if self._idle_release_deadline is not None and not self._backend_released:
            self._schedule_countdown_refresh()

    def _get_auto_release_countdown_text(self) -> str:
        if self.settings.auto_release_minutes <= 0:
            return "关闭"
        if self._backend_released:
            return "已释放"
        if self.pipeline is not None and self.pipeline.is_recording:
            return "录音中"
        if self._idle_release_deadline is None:
            return "待机"

        remaining = max(0, int(self._idle_release_deadline - time.time()))
        minutes, seconds = divmod(remaining, 60)
        if minutes > 0:
            return f"{minutes:02d}:{seconds:02d}"
        return f"{seconds} 秒"

    def _auto_release_backend(self):
        self._idle_release_timer = None
        self._idle_release_deadline = None
        if self._pipeline_transitioning:
            self._logger.info("自动释放被忽略：后端重建中")
            return
        if self.pipeline is None or self.pipeline.is_recording:
            return
        self._logger.info("自动释放触发")
        self.release_backend_resources(manual=False)

    def _reset_error_state_if_needed(self):
        self._error_reset_timer = None
        if self._state == "error" and self.pipeline is not None and not self.pipeline.is_recording:
            self._set_state("paused" if self._paused else "idle")
            self._refresh_status_bar_details()
            self._schedule_idle_release_timer()

    def toggle_pause(self):
        self._paused = not self._paused
        next_state = "paused" if self._paused else "idle"
        if self.pipeline and self.pipeline.is_recording:
            next_state = "recording"
        self._set_state(next_state)
        self._refresh_status_bar_details()
        self._schedule_idle_release_timer()
        self._logger.info("暂停状态切换：%s", self._paused)
        print("⏸️ 已暂停监听" if self._paused else "▶️ 已恢复监听")

    def toggle_auto_paste(self):
        self.settings.auto_paste = not self.settings.auto_paste
        self.settings.save()
        if self.pipeline is not None:
            self.pipeline.config.output.auto_paste = self.settings.auto_paste
        self._refresh_status_bar_details()
        self._logger.info("自动粘贴切换：%s", self.settings.auto_paste)
        print("📌 已开启自动粘贴" if self.settings.auto_paste else "📌 已关闭自动粘贴")

    def toggle_login_at_startup(self):
        """切换用户登录启动项；不影响当前已运行的 App。"""
        try:
            if login_item.is_enabled():
                login_item.disable()
            else:
                login_item.enable()
        except Exception as exc:
            self._logger.exception("开机启动切换失败")
            print(f"❌ 开机启动切换失败：{exc}")
            self._refresh_status_bar_details()
            return

        enabled = login_item.is_enabled()
        self._refresh_status_bar_details()
        self._logger.info("开机启动切换：%s", enabled)
        print(f"🚀 开机启动已{'开启' if enabled else '关闭'}（下次登录生效）")

    def toggle_vad(self):
        if self.pipeline is not None and self.pipeline.is_recording:
            print("❌ 录音中无法切换 VAD")
            return
        self.settings.use_vad = not self.settings.use_vad
        self.settings.save()
        self._logger.info("VAD 切换：%s", self.settings.use_vad)
        # --vad 是 whisper-server 启动参数，需重建流水线生效
        self._pipeline_transitioning = True
        self._set_state("processing")
        self._refresh_status_bar_details()
        try:
            if self._rebuild_pipeline():
                print(f"🔇 VAD 已{'开启' if self.settings.use_vad else '关闭'}")
        finally:
            self._pipeline_transitioning = False

    def _overlay_rms(self) -> float:
        """供浮窗读取当前麦克风电平（RMS）。保持 50ms 窗口以稳定单帧电平；
        浮窗本身由 60Hz 定时器刷新，采样窗口不限制界面刷新率。"""
        if self.pipeline is None:
            return 0.0
        try:
            return self.pipeline.audio_source.get_recent_rms(0.05)
        except Exception:
            return 0.0

    def _show_overlay(self):
        if self._overlay is None or not self.settings.show_overlay:
            return
        AppHelper.callAfter(self._overlay.show)

    def _hide_overlay(self):
        if self._overlay is None:
            return
        AppHelper.callAfter(self._overlay.hide)

    def toggle_overlay(self):
        self.settings.show_overlay = not self.settings.show_overlay
        self.settings.save()
        self._refresh_status_bar_details()
        if not self.settings.show_overlay:
            self._hide_overlay()
        self._logger.info("录音浮窗切换：%s", self.settings.show_overlay)
        print(f"🪧 录音浮窗已{'开启' if self.settings.show_overlay else '关闭'}")

    def toggle_overlay_follow_mouse(self):
        self.settings.overlay_follow_mouse = not self.settings.overlay_follow_mouse
        self.settings.save()
        # 直接驱动浮窗：_refresh_status_bar_details 只刷新菜单勾选态、不碰浮窗本身，
        # 故需单独驱动 setFollowMouse_ 让浮窗即时生效（含录音中途切换的 snap）
        if self._overlay is not None:
            AppHelper.callAfter(self._overlay.setFollowMouse_, self.settings.overlay_follow_mouse)
        self._refresh_status_bar_details()
        self._logger.info("浮窗跟随鼠标切换：%s", self.settings.overlay_follow_mouse)
        print(f"🖱️ 浮窗跟随鼠标已{'开启' if self.settings.overlay_follow_mouse else '关闭'}")

    def toggle_duck_media(self):
        self.settings.duck_media = not self.settings.duck_media
        self.settings.save()
        # 即时生效：把开关同步给已构造的 MediaDucker（无需重建对象）
        self._media_ducker.set_enabled(self.settings.duck_media)
        # 关闭瞬间回弹音量（若当前正 duck）；restore 幂等且不读 _enabled，
        # 非录音态（_pre_duck_volume=None）亦安全空操作。
        if not self.settings.duck_media:
            self._media_ducker.restore()
        self._refresh_status_bar_details()
        self._logger.info("录音压低音量切换：%s", self.settings.duck_media)
        print(f"🔉 录音压低音量已{'开启' if self.settings.duck_media else '关闭'}")

    def toggle_duck_headphones(self):
        self.settings.duck_when_headphones = not self.settings.duck_when_headphones
        self.settings.save()
        self._media_ducker.set_duck_when_headphones(self.settings.duck_when_headphones)
        self._refresh_status_bar_details()
        self._logger.info("戴耳机时也压低切换：%s", self.settings.duck_when_headphones)
        print(f"🎧 戴耳机时也压低已{'开启' if self.settings.duck_when_headphones else '关闭'}")

    def copy_text(self, text: str):
        if not text or self.pipeline is None:
            return False
        ok = self.pipeline.clipboard.copy(text)
        if ok:
            print(f"📋 已复制：{self._truncate_menu_text(text, 60)}")
        return ok

    def copy_last_result(self):
        if self._last_result == "无":
            return False
        return self.copy_text(self._last_result)

    def get_recent_history(self):
        if self.pipeline is None:
            return []
        history = self.pipeline.output_handler.get_history(20)
        return list(reversed(history))

    def release_backend_resources(self, manual: bool):
        if (
            self.pipeline is None
            or self.pipeline.is_recording
            or self._backend_released
            or self._pipeline_transitioning
        ):
            return
        self._cancel_idle_release_timer()
        self._logger.info("释放后端资源：manual=%s", manual)
        self.pipeline.release_backend_resources()
        self._backend_released = True
        self._refresh_status_bar_details()
        reason = "手动" if manual else "自动"
        print(f"🧹 已{reason}释放后端资源")

    def _ensure_backend_ready(self) -> bool:
        if self.pipeline is None:
            return False
        if self._pipeline_transitioning:
            self._logger.info("后端重建中，跳过后端就绪检查")
            return False
        self._cancel_idle_release_timer()
        self._logger.info("确保后端就绪")
        if self.pipeline.ensure_backend_ready():
            self._backend_released = False
            self._refresh_status_bar_details()
            return True
        return False

    def set_auto_release_minutes(self, minutes: int):
        self.settings.auto_release_minutes = max(0, int(minutes))
        self.settings.save()
        self._refresh_status_bar_details()
        self._schedule_idle_release_timer()
        self._logger.info("自动释放分钟数：%s", self.settings.auto_release_minutes)
        if self.settings.auto_release_minutes <= 0:
            print("🕒 已关闭自动释放")
        else:
            print(f"🕒 自动释放已设置为 {self.settings.auto_release_minutes} 分钟")

    def select_model(self, model_name: str):
        if model_name == self.settings.current_model:
            return
        self._pipeline_transitioning = True
        self._set_state("processing")
        self._refresh_status_bar_details()
        try:
            self.settings.current_model = model_name
            self.settings.save()
            self._logger.info("切换模型：%s", model_name)
            if self._rebuild_pipeline():
                self._last_result = f"已切换模型：{model_name}"
                print(f"🧠 已切换模型：{model_name}")
        finally:
            self._pipeline_transitioning = False

    def reload_glossary(self):
        """重载术语表：重新读取 glossary.txt 并重启 whisper-server 让新 --prompt 生效。"""
        self._pipeline_transitioning = True
        self._set_state("processing")
        self._refresh_status_bar_details()
        try:
            terms = self.settings.get_glossary_terms()
            self._logger.info("重载术语表：%d 条", len(terms))
            if self._rebuild_pipeline():
                self._last_result = f"已重载术语表（{len(terms)} 条）"
                print(f"📖 已重载术语表（{len(terms)} 条），后端已重启")
        finally:
            self._pipeline_transitioning = False

    def edit_glossary(self):
        """用默认文本编辑器打开术语表；首次打开时写入格式说明头。"""
        path = self.settings.glossary_file
        try:
            if not os.path.exists(path):
                with open(path, 'w', encoding='utf-8') as f:
                    f.write("# 术语表：每行一个专有名词/术语（如 WhisperCppCmd、PyObjC、Karpathy）。\n")
                    f.write("# 以 # 开头的行与空行会被忽略；改完保存后点菜单「重载术语表」生效。\n")
                    f.write("# 提示：whisper.cpp 的 prompt 上限约 224 token，放几十个高频词为宜。\n")
            subprocess.Popen(['open', '-t', path])
            self._logger.info("打开术语表：%s", path)
        except Exception as e:
            self._logger.warning("打开术语表失败：%s", e)
            print(f"⚠️  打开术语表失败：{e}")

    def select_microphone(self, device_name: str | None):
        if device_name == self.settings.audio_device_name:
            return
        self.settings.audio_device_name = device_name
        self.settings.save()
        self._logger.info("切换麦克风：%s", device_name or "default")
        if self.pipeline is not None:
            self.pipeline.config.audio.device_name = device_name
            self.pipeline.audio_source.close()
        self._refresh_status_bar_details()
        print(f"🎙️ 已切换麦克风：{device_name or '系统默认'}")

    def select_language(self, language: str):
        if language == self.settings.language:
            return
        self._pipeline_transitioning = True
        self._set_state("processing")
        self._refresh_status_bar_details()
        try:
            self.settings.language = language
            self.settings.save()
            self._logger.info("切换识别语言：%s", language)
            if self._rebuild_pipeline():
                self._last_result = f"已切换语言：{language}"
                print(f"🌐 已切换识别语言：{language}")
        finally:
            self._pipeline_transitioning = False

    def select_chinese_script(self, script: str):
        if script == self.settings.chinese_script:
            return
        self.settings.chinese_script = script
        self.settings.save()
        if self.pipeline is not None:
            self.pipeline.config.output.chinese_script = script
        if self._live_dictation is not None:
            self._live_dictation.config.chinese_script = script
        self._logger.info("切换中文脚本：%s", script)
        self._refresh_status_bar_details()
        print(f"🈶 已切换中文脚本：{script}")

    def select_dictation_mode(self, mode: str):
        if mode == self.settings.dictation_mode:
            return
        if mode not in {"preview", "quick"}:
            self._logger.warning("忽略未知听写模式：%s", mode)
            return
        if self.pipeline is not None and self.pipeline.is_recording:
            print("❌ 录音中无法切换听写模式")
            return

        self.settings.dictation_mode = mode
        self.settings.save()
        self._logger.info("切换听写模式：%s", mode)
        self._refresh_status_bar_details()
        print(f"🎚️ 已切换听写模式：{'预览模式' if mode == 'preview' else '快速模式'}")

    def _get_hotkey_options(self):
        return [
            {"title": label, "value": name, "selected": name == self.settings.hotkey}
            for name, label in _HOTKEY_LABELS.items()
        ]

    def select_hotkey(self, name: str):
        if name not in _HOTKEY_KEYS or name == self.settings.hotkey:
            return
        self.settings.hotkey = name
        self.settings.save()
        self._logger.info("热键切换：%s", name)
        self._refresh_status_bar_dynamic_details()
        print(f"⌨️ 热键已切换为 {_HOTKEY_LABELS.get(name, name)}（即时生效）")

    def _hotkey_target(self):
        """当前配置的录音触发键（pynput Key 对象）；未知值回退右 Command。"""
        return _HOTKEY_KEYS.get(self.settings.hotkey, keyboard.Key.cmd_r)

    def _on_press(self, key):
        """按键按下事件（pynput 监听线程）：只做轻量检查并投递事件，重操作交给 DictationWorker。"""
        if self._paused:
            return
        if self.pipeline is None:
            return
        if key == self._hotkey_target():
            if self._pipeline_transitioning:
                self._logger.info("按键按下忽略：后端重建中")
                return
            trace = DictationTrace.create()
            self._active_trace = trace
            self._logger.info("%s 按键按下：右Command", trace.prefix("press") if trace else "[press]")
            if self._dictation_queue is not None:
                self._dictation_queue.put(("press", trace))

    def _on_release(self, key):
        """按键释放事件（pynput 监听线程）：投递事件后立即返回，不等待转录。"""
        if key == self._hotkey_target():
            trace = self._active_trace
            if trace is None:
                return
            self._active_trace = None
            self._logger.info("%s 按键释放：右Command", trace.prefix("release") if trace else "[release]")
            if self._dictation_queue is not None:
                self._dictation_queue.put(("release", trace))

    def _dictation_worker_loop(self):
        """DictationWorker 线程：串行消费按键事件并执行重操作，保证不阻塞监听线程。"""
        while True:
            task = self._dictation_queue.get()
            if task is None:
                break
            kind, trace = task
            # 心跳：每个任务开始时刷新，供 watchdog 判断 worker 是否卡死
            self._worker_heartbeat = time.monotonic()
            self._watchdog_dumped = False
            self._worker_busy = True
            try:
                if self._pipeline_transitioning:
                    self._logger.info("%s worker 跳过：后端重建中", trace.prefix(kind) if trace else f"[{kind}]")
                    continue
                if kind == "press":
                    self._handle_press(trace)
                elif kind == "release":
                    self._handle_release(trace)
            except Exception:
                self._logger.exception("dictation worker 处理 %s 异常", kind)
            finally:
                self._worker_busy = False

    def _handle_press(self, trace):
        """worker：处理按键按下（开始录音等重操作）。"""
        self._current_trace = trace
        if self.pipeline is None:
            self._current_trace = None
            self._last_result = "请先在模型目录放入模型，并从菜单重新加载模型"
            AppHelper.callAfter(self._refresh_status_bar_details)
            return
        self.pipeline.trace = trace
        self.pipeline.audio_source.trace = trace
        self.pipeline.model_engine.trace = trace
        if self._live_dictation is not None:
            self._live_dictation.trace = trace
        self._logger.info(
            "%s 按键按下上下文：paused=%s state=%s recording=%s pipeline_init=%s backend_released=%s live_dictation=%s dictation_mode=%s",
            trace.prefix("press") if trace else "[press]",
            self._paused,
            self._state,
            self.pipeline.is_recording if self.pipeline else None,
            self.pipeline.is_initialized if self.pipeline else None,
            self._backend_released,
            self._live_dictation is not None,
            self.settings.dictation_mode,
        )
        self._logger.info("%s 按键按下：右Command（worker 处理）", trace.prefix("press") if trace else "[press]")
        if not self.pipeline.is_recording:
            if self.pipeline.start_recording():
                self._media_ducker.begin()  # ducking：尽早压低系统音量，减少扬声器音乐串扰
                self._backend_released = False
                self._set_state("recording")
                self._refresh_status_bar_details()
                print("\n🎤 录音中...")
                if self.pipeline.audio_source.fell_back_to_default:
                    print("   ⚠️ 指定麦克风不可用，已回退到系统默认设备")
                self._logger.info("%s 录音开始成功", trace.prefix("recording_start") if trace else "[recording_start]")
                if self.settings.dictation_mode == "preview" and self._live_dictation is not None:
                    if self.pipeline.model_engine.is_loaded:
                        self._live_dictation.start()
                    else:
                        self._start_backend_warmup()
                elif not self.pipeline.model_engine.is_loaded:
                    self._start_backend_warmup()
            else:
                if self.pipeline.audio_source.virtual_device_suspect:
                    self._last_result = "音频卡死，疑似虚拟声卡(向日葵等)；建议退出虚拟声卡或重启 coreaudiod"
                    print("\n❌ 录音启动失败（疑似虚拟声卡，详见日志）")
                else:
                    self._last_result = "错误：录音启动失败"
                    print("\n❌ 录音启动失败")
                self._set_state("error")
                self._refresh_status_bar_details()

    def _handle_release(self, trace):
        """worker：处理按键释放（停止录音、转录、粘贴等重操作）。"""
        self._current_trace = trace
        if self.pipeline is None:
            self._current_trace = None
            return
        self._logger.info(
            "%s 按键释放上下文：paused=%s state=%s recording=%s pipeline_init=%s backend_released=%s live_dictation=%s dictation_mode=%s",
            trace.prefix("release") if trace else "[release]",
            self._paused,
            self._state,
            self.pipeline.is_recording if self.pipeline else None,
            self.pipeline.is_initialized if self.pipeline else None,
            self._backend_released,
            self._live_dictation is not None,
            self.settings.dictation_mode,
        )
        self._logger.info("%s 按键释放：右Command（worker 处理）", trace.prefix("release") if trace else "[release]")
        if self.pipeline.is_recording:
            self._logger.info("%s 开始等待后台唤醒", trace.prefix("release") if trace else "[release]")
            self._wait_for_backend_warmup()
            if self.settings.dictation_mode == "preview" and self._live_dictation is not None:
                self._live_dictation.stop()
            self._set_state("processing")
            print("⏳ 转录中...")
            self._logger.info("%s 开始 stop_recording", trace.prefix("stop_recording") if trace else "[stop_recording]")

            try:
                paste_output = self.settings.dictation_mode == "quick"
                result = self.pipeline.stop_recording(paste_output=paste_output)
            except Exception as e:
                self._logger.exception("stop_recording 异常")
                self._last_result = f"错误：{e}"
                self._media_ducker.restore()
                self._set_state("error")
                self._refresh_status_bar_details()
                self._schedule_idle_release_timer()
                self._current_trace = None
                return

            self._log_perf(result, trace)
            overflow = self.pipeline.audio_source.overflow
            if result.success:
                no_speech = bool(getattr(result, "no_speech", False))
                if self.settings.dictation_mode == "preview" and self._live_dictation is not None:
                    # 预览收尾使用与 quick 模式相同的最终识别文本。
                    self._live_dictation.finalize(result.text)
                self._logger.info(
                    "%s stop_recording 完成：no_speech=%s recording_duration=%.2fs processing_time=%.2fs rtf=%.2fx text_len=%s",
                    trace.prefix("stop_recording") if trace else "[stop_recording]",
                    no_speech,
                    result.recording_duration,
                    result.processing_time,
                    result.rtf,
                    len(result.text or ""),
                )
                if no_speech:
                    print(f"⏱️  录音：{result.recording_duration:.2f}秒 → ⚪ 未检测到语音")
                    self._last_result = "未检测到语音"
                else:
                    print(f"⏱️  录音：{result.recording_duration:.2f}秒 → ✅ {result.processing_time:.2f}秒 (RTF: {result.rtf:.2f}x)")
                    print(f"   「{result.text}」")
                    self._last_result = result.text
                if overflow:
                    print(f"   ⚠️ 已达最大录音时长 {self.settings.max_recording_seconds:.0f}s，超出部分已截断")
                    self._logger.warning(
                        "%s 录音达到最大时长上限已截断：max=%ss",
                        trace.prefix("stop_recording") if trace else "[stop_recording]",
                        self.settings.max_recording_seconds,
                    )
                self._set_state("idle")
            else:
                self._logger.info(
                    "%s stop_recording 失败：error=%s processing_time=%.2fs",
                    trace.prefix("stop_recording") if trace else "[stop_recording]",
                    result.error,
                    result.processing_time,
                )
                print(f"❌ {result.error}")
                self._last_result = f"错误：{result.error}"
                self._set_state("error")
            self._media_ducker.restore()
            self._refresh_status_bar_details()
            self._schedule_idle_release_timer()
            self._logger.info("%s 本次听写流程结束", trace.prefix("complete") if trace else "[complete]")
        self._current_trace = None
        if self.pipeline is not None:
            self.pipeline.trace = None
            self.pipeline.audio_source.trace = None
            self.pipeline.model_engine.trace = None
        if self._live_dictation is not None:
            self._live_dictation.trace = None

    def export_diagnostic_report(self, reason: str = "manual"):
        """导出诊断报告（手动触发，主线程执行，worker 卡死时也能跑）。"""
        try:
            path = diagnostics.dump_report(self, reason)
            self._logger.info("诊断报告已导出：reason=%s path=%s", reason, path)
            print(f"\n📋 诊断报告已导出：{path}")
        except Exception as e:
            self._logger.exception("导出诊断报告失败")
            print(f"\n❌ 导出诊断报告失败：{e}")

    def open_models_folder(self):
        """在 Finder 中打开模型文件夹。"""
        import os
        import subprocess
        models_dir = self.settings.models_dir
        try:
            os.makedirs(models_dir, exist_ok=True)
            subprocess.run(["open", models_dir], check=False)
            self._logger.info("打开模型文件夹：%s", models_dir)
        except Exception as e:
            self._logger.warning("打开模型文件夹失败：%s", e)
            print(f"❌ 打开模型文件夹失败：{e}")

    def open_model_download_page(self):
        """打开浏览器跳转到模型下载页。"""
        import webbrowser
        try:
            webbrowser.open(MODEL_DOWNLOAD_URL)
            self._logger.info("打开模型下载页：%s", MODEL_DOWNLOAD_URL)
        except Exception as e:
            self._logger.warning("打开下载页失败：%s", e)
            print(f"❌ 打开下载页失败：{e}")

    def reload_model(self):
        """在首次放入模型后，从菜单栏加载当前选中的模型。"""
        if self.pipeline is not None and self.pipeline.is_recording:
            self._show_simple_alert("暂时无法加载模型", "请先结束当前录音。")
            return

        self._pipeline_transitioning = True
        self._set_state("processing")
        self._refresh_status_bar_details()
        try:
            if self._rebuild_pipeline():
                self._last_result = f"已加载模型：{self.settings.current_model}"
                print(f"🧠 已加载模型：{self.settings.current_model}")
            else:
                self._show_simple_alert(
                    "模型尚未加载",
                    f"找不到：{self.settings.get_model_path()}\n\n"
                    "请先把 GGML 模型放入模型目录，再重试。",
                )
        finally:
            self._pipeline_transitioning = False
            self._refresh_status_bar_details()

    def _first_char_ms(self):
        """预览模式下首字延迟（ms）；非预览/未产生返回 None。"""
        if self._live_dictation is None:
            return None
        return self._live_dictation.first_char_latency_ms

    def _log_perf(self, result, trace):
        """把本次听写的性能数据追加到 perf.jsonl（延迟/RTF 度量基线）。"""
        try:
            record = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "trace_id": trace.trace_id if trace else None,
                "model": self.settings.current_model,
                "language": self.settings.language,
                "mode": self.settings.dictation_mode,
                "use_vad": self.settings.use_vad,
                "duration_s": round(result.recording_duration, 3),
                "processing_s": round(result.processing_time, 3),
                "rtf": round(result.rtf, 3),
                "text_len": len(result.text or ""),
                "no_speech": bool(getattr(result, "no_speech", False)),
                "first_char_ms": self._first_char_ms(),
                "success": result.success,
            }
            append_perf_log(self._perf_log_path, record)
        except Exception:
            self._logger.debug("写 perf 日志失败", exc_info=True)

    def _register_system_sleep_wake(self):
        """注册系统睡眠/唤醒通知，醒来后失效设备缓存避免录音失灵。"""
        try:
            observer = _SystemSleepWakeObserver.alloc().initWithHandler_(self._on_system_sleep_wake)
            nc = AppKit.NSWorkspace.sharedWorkspace().notificationCenter()
            nc.addObserver_selector_name_object_(
                observer, "onSleep:", AppKit.NSWorkspaceWillSleepNotification, None
            )
            nc.addObserver_selector_name_object_(
                observer, "onWake:", AppKit.NSWorkspaceDidWakeNotification, None
            )
            self._sleep_wake_observer = observer
            self._logger.info("已注册系统睡眠/唤醒通知")
        except Exception:
            self._logger.exception("注册系统睡眠/唤醒通知失败")

    def _on_system_sleep_wake(self, event: str):
        """系统睡眠/唤醒处理（NSWorkspace 通知回调，主线程）。

        唤醒后只失效设备缓存并回到 idle：死流由下次录音经音频设备容错(A7)重建，
        卡死的后端由 watchdog 自愈(A5)处理，避免在这里做有风险的主动重建。
        """
        if self.pipeline is None:
            return
        try:
            if event == "sleep":
                self._logger.info("系统即将睡眠")
                self.pipeline.audio_source.invalidate_devices()
                # 录音中不主动释放后端，避免丢失进行中的听写；空闲则释放节省资源
                if not self.pipeline.is_recording:
                    self.release_backend_resources(manual=False)
            elif event == "wake":
                self._logger.info("系统已唤醒，失效设备缓存")
                self.pipeline.audio_source.invalidate_devices()
                self._set_state("paused" if self._paused else "idle")
                self._refresh_status_bar_details()
        except Exception:
            self._logger.exception("处理系统睡眠/唤醒事件异常")

    def _start_signal_pump(self):
        """启动 NSTimer 让主线程每秒回到 Python，及时处理 SIGTERM 等待处理信号。"""
        pump = _SignalPump.alloc().init()
        pump._app_ref = self  # 让 tick_ 更新主线程心跳，供 watchdog 检测冻结
        AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, pump, "tick:", None, True
        )
        self._signal_pump = pump
        self._logger.info("信号泵已启动（1s tick）")

    def _watchdog_loop(self):
        """后台监控：worker 转录卡死则 dump+自愈；主线程冻结则主动 dump 栈诊断。"""
        threshold = self.settings.transcription_timeout + _WATCHDOG_GRACE_SECONDS
        while not self._watchdog_stop.wait(_WATCHDOG_POLL_INTERVAL):
            try:
                now = time.monotonic()
                # worker 心跳检测（转录卡死）：dump + 自愈打断后端
                if (
                    self._worker_busy
                    and not self._watchdog_dumped
                    and now - self._worker_heartbeat > threshold
                ):
                    self._watchdog_dumped = True
                    self._logger.warning(
                        "watchdog：worker 无响应超过 %.0fs（转录超时+宽限），自动导出诊断报告",
                        threshold,
                    )
                    diagnostics.dump_report(self, f"watchdog: worker 无响应超过 {threshold:.0f}s")
                    # 自愈：强制停止 whisper-server，打断 worker 阻塞的 HTTP 调用，
                    # 触发其异常路径与重试/被动重启恢复（commit 56a34c9 防卡死闭环）
                    if self.pipeline is not None:
                        self._logger.warning("watchdog：强制停止后端以打断卡死的转录")
                        self.pipeline.model_engine.interrupt_backend()
                # 主线程心跳检测（runloop 冻结）：仅诊断，无法自愈
                self._evaluate_main_thread_watchdog(now)
            except Exception:
                self._logger.exception("watchdog 异常")

    def _evaluate_main_thread_watchdog(self, now: float) -> None:
        """主线程心跳检测：runloop 冻结（NSTimer 不 fire）则 dump 栈诊断。

        主线程冻结时卡在同步 C 调用（如 Pa_OpenStream），Python 层无法自愈
        （runloop 本身卡死），故仅 dump。设 _watchdog_dumped=True 抑制 worker
        watchdog 与 _run_on_main_thread 的重复 dump（主线程冻结时它们必然也卡）。
        连续 2 轮健康才重置 _main_thread_dumped，防 ping-pong 抖动日志洪水。
        """
        hb = self._main_thread_heartbeat
        if hb is None:  # runEventLoop 尚未开始 tick，启动宽限
            return
        elapsed = now - hb
        if elapsed > _MAIN_THREAD_WATCHDOG_THRESHOLD:
            if not self._main_thread_dumped:
                self._main_thread_dumped = True
                self._watchdog_dumped = True  # 抑制 worker watchdog / _run_on_main_thread 重复 dump
                self._main_thread_healthy_streak = 0
                self._logger.warning(
                    "watchdog：主线程冻结，心跳停滞 %.1fs，导出诊断报告（仅诊断，无法自愈）",
                    elapsed,
                )
                diagnostics.dump_report(self, f"watchdog: 主线程冻结，心跳停滞 {elapsed:.1f}s")
        elif elapsed < _WATCHDOG_POLL_INTERVAL:
            self._main_thread_healthy_streak += 1
            if self._main_thread_healthy_streak >= 2 and self._main_thread_dumped:
                self._main_thread_dumped = False
                self._main_thread_healthy_streak = 0
                self._logger.info("主线程心跳恢复，重置 watchdog dump 标记")
        else:
            self._main_thread_healthy_streak = 0  # 灰色区间（poll~阈值），既不冻结也不健康

    def run(self):
        """运行应用"""
        if not self.initialize():
            sys.exit(1)

        self._is_running = True
        self._set_state("paused" if self._paused else "idle")
        self._refresh_status_bar_details()
        self._schedule_idle_release_timer()
        # DictationWorker：串行执行录音启停等重操作，避免阻塞 pynput 按键监听线程
        self._dictation_queue = queue.Queue()
        self._dictation_worker = threading.Thread(
            target=self._dictation_worker_loop,
            name="DictationWorker",
            daemon=True,
        )
        self._dictation_worker.start()

        # DictationWatchdog：监控 worker 心跳，卡死时自动导出诊断报告
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="DictationWatchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

        self._restart_keyboard_listener()

        self._register_system_sleep_wake()
        self._start_signal_pump()

        # 首次启动向导在事件循环准备好后弹出，避免阻塞初始化和热键监听。
        AppHelper.callAfter(self.show_onboarding_if_needed)
        # 权限 API 可能触发系统授权 UI，不能在进入 NSRunLoop 前同步调用；否则
        # 首次启动会卡在权限请求，向导和菜单栏都无法出现。
        AppHelper.callAfter(self._print_permission_guidance_if_needed)
        AppHelper.callAfter(self._check_for_updates_if_due)

        app = AppKit.NSApplication.sharedApplication()
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        # Reassert after the event loop is fully wired and record diagnostics.
        if self.status_bar is not None:
            self.refresh_status_bar_health()

        # 防线2b：注册终止委托，兜住 Cmd+Q / 关机 / 注销（否则绕过 shutdown 留下孤儿 server）
        self._term_delegate = _TerminationDelegate.alloc().init()
        self._term_delegate._app_ref = self  # 强引用持有，防 PyObjC 弱引用回收
        app.setDelegate_(self._term_delegate)

        self._logger.info("应用已启动，进入事件循环")
        if self._model_setup_required:
            print("\n⚠️  App 已启动，但尚未加载模型；请先放入模型并从菜单栏重新加载")
        else:
            print("\n✅ 就绪，菜单栏图标已显示，按住右 Command 开始录音")
        print("   使用菜单栏图标或 Ctrl+C 退出\n")

        AppHelper.runEventLoop()

    def refresh_status_bar_health(self):
        """Keep the menu item configured and record its actual screen health."""
        status_bar = getattr(self, "status_bar", None)
        if status_bar is None:
            return None
        return status_bar.ensure_visible()

    def shutdown(self):
        """关闭应用（atexit / signal / NSApplicationDelegate 三入口，幂等 + 锁防并发重入）"""
        with self._shutdown_lock:
            if not self._is_running:
                try:
                    AppHelper.stopEventLoop()
                except Exception:
                    pass
                return
            self._is_running = False

        # 锁外执行耗时清理（worker join / _stop_server wait），避免与 watchdog 互相死锁
        self._logger.info("开始关闭应用")
        if self._sleep_wake_observer is not None:
            try:
                AppKit.NSWorkspace.sharedWorkspace().notificationCenter().removeObserver_(self._sleep_wake_observer)
            except Exception:
                self._logger.warning("注销系统睡眠/唤醒通知失败", exc_info=True)
            self._sleep_wake_observer = None
        self._set_state("idle")
        self._cancel_error_reset_timer()
        self._cancel_idle_release_timer()

        if self._live_dictation is not None:
            self._live_dictation.stop()

        if self.listener:
            self.listener.stop()
            self.listener = None

        # 停止 watchdog（先于 worker，避免 worker 退出期间被误判卡死）
        self._watchdog_stop.set()
        if self._watchdog_thread is not None:
            self._watchdog_thread.join(timeout=1.0)
            self._watchdog_thread = None

        # 停止 dictation worker：投递哨兵并限时等待，避免与 pipeline.shutdown() 竞争
        if self._dictation_queue is not None:
            self._dictation_queue.put(None)
        if self._dictation_worker is not None:
            self._dictation_worker.join(timeout=2.0)
            self._dictation_worker = None
            self._dictation_queue = None

        if self.pipeline:
            self.pipeline.shutdown()

        self._refresh_status_bar_details()

        AppHelper.stopEventLoop()
        self._logger.info("应用关闭完成")
