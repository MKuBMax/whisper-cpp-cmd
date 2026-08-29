#!/usr/bin/env python3
"""运行时路径。

开发时项目仍然把配置、模型和日志放在仓库根目录，方便现有的 alias
模式调试。独立分发的 py2app 则把这些可变数据放到用户的
``~/Library/Application Support/WhisperCppCmd``，避免向 ``.app`` 内写文件。
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Optional


APP_NAME = "WhisperCppCmd"
DATA_DIR_ENV = "WHISPER_CPP_CMD_DATA_DIR"
CLI_PATH_ENV = "WHISPER_CPP_CMD_WHISPER_CLI"


def project_root() -> str:
    """返回源码项目根目录。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bundle_resource_path() -> str:
    """返回 py2app 设置的资源目录，非 App 进程返回空字符串。"""
    candidate = os.environ.get("RESOURCEPATH", "")
    if candidate and os.path.isdir(candidate):
        return os.path.realpath(candidate)
    return ""


def is_standalone_bundle() -> bool:
    """判断当前是否运行在 py2app standalone App 中。

    alias 包没有自己的 ``Resources/lib`` 和 ``Contents/Frameworks``；这两个
    目录是 standalone 包的稳定特征，也避免仅凭 ``sys.frozen`` 误判开发包。
    """
    resource_dir = _bundle_resource_path()
    if not resource_dir:
        return False
    return (
        os.path.isdir(os.path.join(resource_dir, "lib"))
        and os.path.isdir(
            os.path.join(resource_dir, os.pardir, "Frameworks", "Python.framework")
        )
    )


def resource_root() -> str:
    """返回只读资源根目录（源码根目录或 App 的 Contents/Resources）。"""
    if is_standalone_bundle():
        return _bundle_resource_path()
    return project_root()


def runtime_root() -> str:
    """返回可写运行时数据根目录。"""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return os.path.abspath(os.path.expanduser(override))
    if is_standalone_bundle():
        return os.path.expanduser(os.path.join("~/Library/Application Support", APP_NAME))
    return project_root()


def logs_dir() -> str:
    return os.path.join(runtime_root(), "logs")


def models_dir() -> str:
    return os.path.join(runtime_root(), "models")


def config_path() -> str:
    return os.path.join(runtime_root(), "config.json")


def history_path() -> str:
    return os.path.join(runtime_root(), "history.json")


def glossary_path() -> str:
    return os.path.join(runtime_root(), "glossary.txt")


def resource_path(*parts: str) -> str:
    return os.path.join(resource_root(), *parts)


def update_helper_path() -> Optional[str]:
    """查找 standalone 包内或源码目录中的更新替换 helper。"""
    candidates = (
        resource_path("update_app.sh"),
        resource_path("distribution", "update_app.sh"),
    )
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def app_executable() -> Optional[str]:
    """返回当前 py2app App 自身的可执行文件路径。

    alias 和 standalone 都会设置 ``RESOURCEPATH``；区别只在资源布局，
    因此登录启动需要的可执行文件路径可以共用这条解析逻辑。
    """
    resource_dir = _bundle_resource_path()
    if not resource_dir:
        return None
    path = os.path.realpath(
        os.path.join(resource_dir, os.pardir, "MacOS", APP_NAME)
    )
    return path if os.path.isfile(path) and os.access(path, os.X_OK) else None


def bundled_whisper_cli_path() -> Optional[str]:
    """返回 App 内置的 whisper-cli；开发包没有该资源时返回 None。"""
    path = resource_path("whisper-runtime", "bin", "whisper-cli")
    if os.path.isfile(path) and os.access(path, os.X_OK):
        return path
    return None


def default_whisper_cli_path() -> str:
    """按「显式环境变量 → App 内置 → 常见本机安装」顺序找 whisper-cli。"""
    override = os.environ.get(CLI_PATH_ENV)
    if override:
        return os.path.abspath(os.path.expanduser(override))

    bundled = bundled_whisper_cli_path()
    if bundled:
        return bundled

    candidates = [
        "/opt/homebrew/bin/whisper-cli",
        "/usr/local/bin/whisper-cli",
        shutil.which("whisper-cli"),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    # 保留一个清晰、可诊断的默认值；真正加载时会给出缺少二进制的错误。
    return "/opt/homebrew/bin/whisper-cli"


def ensure_runtime_dirs() -> None:
    """创建 App 需要的可写目录。"""
    os.makedirs(logs_dir(), exist_ok=True)
    os.makedirs(models_dir(), exist_ok=True)
