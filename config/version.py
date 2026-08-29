"""应用版本信息。

``VERSION`` 是唯一的仓库内版本来源；构建发布包时可以用
``WHISPER_CPP_CMD_VERSION`` 临时覆盖它。运行时不读取 Git 历史，因而 alias、
standalone 和安装到 ``/Applications`` 后都能得到同一个版本值。
"""

from __future__ import annotations

import os
import re
from pathlib import Path


_VERSION_RE = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)"
    r"\.(?P<minor>0|[1-9]\d*)"
    r"\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


def _read_repository_version() -> str:
    candidates = [Path(__file__).resolve().parent.parent / "VERSION"]
    # standalone py2app 把 VERSION 作为 Resources/VERSION 复制；源码文件的
    # ``__file__`` 位于 Resources/lib/.../config 下，不能靠固定 parent 层级
    # 找回它，优先读取 py2app 的标准 RESOURCEPATH。
    resource_root = os.environ.get("RESOURCEPATH", "")
    if resource_root:
        candidates.insert(0, Path(resource_root) / "VERSION")
    for path in candidates:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return "0.1.0-beta.1"


def _normalize_version(value: object) -> str:
    """返回可用于 Release tag 比较的严格三段 SemVer。

    环境变量来自构建机，错误值不应悄悄写进 Info.plist；回退到仓库版本让
    alias/本地开发仍可启动，正式打包脚本会在调用 setup.py 前再次拒绝错误值。
    """

    candidate = str(value or "").strip()
    if _VERSION_RE.fullmatch(candidate):
        return candidate[1:] if candidate.startswith("v") else candidate
    fallback = _read_repository_version()
    if _VERSION_RE.fullmatch(fallback):
        return fallback[1:] if fallback.startswith("v") else fallback
    return "0.1.0-beta.1"


APP_VERSION = _normalize_version(
    os.environ.get("WHISPER_CPP_CMD_VERSION") or _read_repository_version()
)

# CFBundleVersion 只能使用数字版本段；预发布标签仍由 APP_VERSION 用于
# GitHub Release 比较，bundle 内采用稳定的三段数字版本。
_VERSION_MATCH = _VERSION_RE.fullmatch(APP_VERSION)
if _VERSION_MATCH:
    APP_BUNDLE_VERSION = ".".join(
        _VERSION_MATCH.group(name) for name in ("major", "minor", "patch")
    )
else:  # pragma: no cover - _normalize_version guarantees this branch is unreachable
    APP_BUNDLE_VERSION = "0.0.0"
UPDATE_REPOSITORY = "MKuBMax/whisper-cpp-cmd"
