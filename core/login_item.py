#!/usr/bin/env python3
"""用户级 macOS 登录启动项。

macOS 13+ 优先使用 ``SMAppService.mainApp``，让主 App 注册到系统的
“登录时打开”列表；旧系统或 Service Management 不可用时才回退到
``~/Library/LaunchAgents``。这里的“开机启动”准确来说是用户登录后启动。

启用/取消不主动启动第二个当前 App，也不关闭当前正在运行的 App。
"""

from __future__ import annotations

import os
import plistlib
import sys
import tempfile
from typing import Any, Dict

from config.paths import app_executable, ensure_runtime_dirs, logs_dir


LAUNCH_AGENT_LABEL = "com.mkbm.whispercppcmd"
_LAUNCH_AGENT_FILENAME = f"{LAUNCH_AGENT_LABEL}.plist"

# SMAppServiceStatus 的公开枚举值（macOS SDK）：
# NotRegistered=0, Enabled=1, RequiresApproval=2, NotFound=3。
_SM_STATUS_NOT_REGISTERED = 0
_SM_STATUS_ENABLED = 1
_SM_STATUS_REQUIRES_APPROVAL = 2
_SM_STATUS_NOT_FOUND = 3

_sm_service_load_attempted = False
_sm_app_service_class = None


def launch_agent_path() -> str:
    """返回旧版回退 LaunchAgent plist 路径。"""
    return os.path.expanduser(os.path.join("~/Library/LaunchAgents", _LAUNCH_AGENT_FILENAME))


def _legacy_is_enabled() -> bool:
    """判断旧版 LaunchAgent plist 是否存在。"""
    path = launch_agent_path()
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "rb") as stream:
            payload = plistlib.load(stream)
        return (
            payload.get("Label") == LAUNCH_AGENT_LABEL
            and bool(payload.get("ProgramArguments"))
        )
    except (OSError, ValueError, plistlib.InvalidFileException):
        return False


def _main_app_service():
    """动态加载 ServiceManagement，兼容没有 Python wrapper 的 PyObjC 版本。"""
    global _sm_service_load_attempted, _sm_app_service_class
    if _sm_service_load_attempted:
        if _sm_app_service_class is None:
            return None
        return _sm_app_service_class.mainAppService()

    _sm_service_load_attempted = True
    if sys.platform != "darwin":
        return None

    try:
        import objc

        objc.loadBundle(
            "ServiceManagement",
            globals(),
            "/System/Library/Frameworks/ServiceManagement.framework",
        )
        _sm_app_service_class = globals().get("SMAppService")
        if _sm_app_service_class is None:
            return None
        return _sm_app_service_class.mainAppService()
    except Exception:
        # 旧 macOS、旧 PyObjC 或非 App 进程都可能无法加载；交给 plist 回退。
        _sm_app_service_class = None
        return None


def _native_status():
    service = _main_app_service()
    if service is None:
        return None
    try:
        return int(service.status())
    except Exception:
        return None


def is_enabled() -> bool:
    """判断原生登录项或旧版 LaunchAgent 是否启用。"""
    status = _native_status()
    if status in (_SM_STATUS_ENABLED, _SM_STATUS_REQUIRES_APPROVAL):
        return True
    if status in (_SM_STATUS_NOT_REGISTERED, _SM_STATUS_NOT_FOUND):
        # 兼容用户在升级前已经勾选过旧版 LaunchAgent 的情况。
        return _legacy_is_enabled()
    return _legacy_is_enabled()


def _build_legacy_payload(executable: str) -> Dict[str, Any]:
    log_dir = logs_dir()
    return {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [executable],
        "RunAtLoad": True,
        "ProcessType": "Interactive",
        "ThrottleInterval": 10,
        "StandardOutPath": os.path.join(log_dir, "launch-agent.stdout.log"),
        "StandardErrorPath": os.path.join(log_dir, "launch-agent.stderr.log"),
    }


def _enable_legacy(executable: str) -> None:
    """旧系统回退：写入 LaunchAgent plist。"""
    ensure_runtime_dirs()
    path = launch_agent_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = _build_legacy_payload(executable)

    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{_LAUNCH_AGENT_FILENAME}.",
            dir=os.path.dirname(path),
            delete=False,
        ) as stream:
            temp_path = stream.name
            plistlib.dump(payload, stream, fmt=plistlib.FMT_XML, sort_keys=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def _remove_legacy() -> None:
    try:
        os.remove(launch_agent_path())
    except FileNotFoundError:
        pass


def _error_text(error) -> str:
    if error is None:
        return "Service Management 未接受该登录项"
    try:
        return str(error.localizedDescription())
    except Exception:
        return str(error)


def _enable_native(service) -> None:
    if _native_status() in (_SM_STATUS_ENABLED, _SM_STATUS_REQUIRES_APPROVAL):
        return

    result = service.registerAndReturnError_(None)
    if isinstance(result, tuple):
        ok = bool(result[0])
        error = result[1] if len(result) > 1 else None
    else:
        ok = bool(result)
        error = None
    if not ok:
        raise RuntimeError(_error_text(error))


def _disable_native(service) -> None:
    status = _native_status()
    if status in (_SM_STATUS_NOT_REGISTERED, _SM_STATUS_NOT_FOUND):
        return

    result = service.unregisterAndReturnError_(None)
    if isinstance(result, tuple):
        ok = bool(result[0])
        error = result[1] if len(result) > 1 else None
    else:
        ok = bool(result)
        error = None
    if not ok:
        raise RuntimeError(_error_text(error))


def enable() -> str:
    """注册登录启动项，返回实际使用的 App 可执行文件路径。"""
    executable = app_executable()
    if not executable:
        raise RuntimeError("请从 WhisperCppCmd.app 启动后再设置开机启动")

    service = _main_app_service()
    if service is None:
        _enable_legacy(executable)
    else:
        _enable_native(service)
        # 注册成功后清理本项目旧版本留下的 plist，避免出现两套启动项。
        _remove_legacy()
    return executable


def disable() -> None:
    """取消原生登录项，并清理旧版 LaunchAgent。"""
    service = _main_app_service()
    if service is not None:
        _disable_native(service)
    _remove_legacy()
