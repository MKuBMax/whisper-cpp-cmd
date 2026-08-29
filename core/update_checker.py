"""GitHub Releases 更新检查。

检查由用户从菜单栏触发，网络请求在后台线程执行；本模块负责获取和比较
版本、准备签名更新包，实际替换由独立 helper 在当前 App 退出后完成。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import plistlib
import posixpath
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import PurePosixPath
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int = 0


@dataclass(frozen=True)
class ReleaseInfo:
    tag_name: str
    version: tuple[int, int, int]
    name: str
    html_url: str
    published_at: str = ""
    assets: tuple[ReleaseAsset, ...] = ()


@dataclass(frozen=True)
class CodeSignature:
    """codesign 的最小可比较信息。

    Developer ID 更新要求新旧 App 使用同一 Team ID；内部 ad hoc 构建则要求
    更新仍为 ad hoc。这里不把完整 ``codesign`` 输出写进日志，避免泄露构建
    机路径或证书详情。
    """

    team_identifier: str = ""
    authority: str = ""
    identifier: str = ""
    adhoc: bool = False


_GITHUB_DOWNLOAD_HOSTS = frozenset(
    {
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
        "github-releases.githubusercontent.com",
    }
)
_MAX_ZIP_ENTRIES = 100_000
_MAX_ZIP_MEMBER_BYTES = 2 * 1024 * 1024 * 1024
_MAX_SYMLINK_TARGET_BYTES = 4 * 1024
_MAX_RELEASE_METADATA_BYTES = 2 * 1024 * 1024
_STAGED_APP_RE = re.compile(r"^WhisperCppCmd-[A-Za-z0-9._-]+-staged\.app$")


def parse_version(value: str) -> tuple[int, int, int]:
    match = re.search(r"(?:v)?(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(value or ""))
    if not match:
        return (0, 0, 0)
    return tuple(int(part or 0) for part in match.groups())


def _prerelease_parts(value: str) -> tuple[tuple[int, object], ...]:
    """返回可比较的预发布标识，稳定版用空 tuple 表示。"""
    match = re.search(r"(?:v)?\d+(?:\.\d+){0,2}(?:-([0-9A-Za-z.-]+))?", str(value or ""))
    if not match or not match.group(1):
        return ()
    parts = []
    for part in match.group(1).split("."):
        if part.isdigit():
            parts.append((0, int(part)))
        else:
            parts.append((1, part.lower()))
    return tuple(parts)


def _read_response_limited(response, max_bytes: int) -> bytes:
    """读取 HTTP response，并在 JSON/zip 尚未解析前限制内存和磁盘写入。"""

    try:
        limit = int(max_bytes)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("响应大小限制无效")
    if limit <= 0:
        raise ValueError("响应大小限制无效")

    headers = getattr(response, "headers", {}) or {}
    content_length = headers.get("Content-Length") if hasattr(headers, "get") else None
    try:
        declared_size = int(content_length) if content_length else 0
    except (TypeError, ValueError, OverflowError):
        declared_size = 0
    if declared_size > limit:
        raise ValueError("响应超过大小限制")

    chunks: list[bytes] = []
    total = 0
    while True:
        try:
            chunk = response.read(min(1024 * 1024, limit - total + 1))
        except TypeError:
            # 兼容极简测试 double；真实 urllib response 支持 read(amt)，仍
            # 会在每个 chunk 后执行同一上限检查。
            chunk = response.read()
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise ValueError("响应超过大小限制")
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_latest_release(api_url: str, timeout: float = 5.0) -> ReleaseInfo:
    request = Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "WhisperCppCmd-update-checker",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(
            _read_response_limited(response, _MAX_RELEASE_METADATA_BYTES).decode(
                "utf-8", errors="replace"
            )
        )
    if not isinstance(payload, dict) or not payload.get("tag_name"):
        raise ValueError("GitHub Releases 返回缺少 tag_name")
    tag_name = str(payload["tag_name"])
    assets = []
    for raw_asset in payload.get("assets", []) if isinstance(payload.get("assets", []), list) else []:
        if not isinstance(raw_asset, dict):
            continue
        name = str(raw_asset.get("name") or "")
        download_url = str(raw_asset.get("browser_download_url") or "")
        if name and download_url:
            try:
                size = max(0, int(raw_asset.get("size", 0)))
            except (TypeError, ValueError):
                size = 0
            assets.append(ReleaseAsset(name=name, download_url=download_url, size=size))
    return ReleaseInfo(
        tag_name=tag_name,
        version=parse_version(tag_name),
        name=str(payload.get("name") or tag_name),
        html_url=str(payload.get("html_url") or ""),
        published_at=str(payload.get("published_at") or ""),
        assets=tuple(assets),
    )


def is_newer(current: str, release: ReleaseInfo) -> bool:
    current_version = parse_version(current)
    if release.version != current_version:
        return release.version > current_version

    current_pre = _prerelease_parts(current)
    release_pre = _prerelease_parts(release.tag_name)
    if not release_pre:
        # 同一基础版本的稳定版高于任意预发布版。
        return bool(current_pre)
    if not current_pre:
        return False
    return release_pre > current_pre


def find_macos_asset(release: ReleaseInfo) -> ReleaseAsset | None:
    """选择 Apple Silicon standalone zip。"""
    for asset in release.assets:
        name = asset.name.lower()
        if name.endswith(".zip") and "macos-arm64" in name:
            return asset
    return None


def _safe_zip_member(name: str) -> bool:
    """判断 zip 成员是否只能落在目标目录内。

    除了常见的 ``../``，也拒绝反斜杠、NUL、空成员和隐藏的绝对路径；这让
    同一份包在不同解压器/平台上的路径解释保持一致。
    """

    if not isinstance(name, str) or not name or "\x00" in name or "\\" in name:
        return False
    path = PurePosixPath(name)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and all(part not in {"", "."} for part in path.parts)
    )


def _is_supported_github_url(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 4_096:
        return False
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and host in _GITHUB_DOWNLOAD_HOSTS
        and parsed.netloc
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
        and bool(parsed.path)
    )


def _validate_release_download_url(value: str) -> None:
    """只允许 GitHub 的 HTTPS release URL 作为下载起点。"""

    if not _is_supported_github_url(value):
        raise ValueError("更新资产不是受支持的 GitHub HTTPS 地址")


def _zip_info_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (int(info.external_attr) >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def _is_macos_metadata_member(name: str) -> bool:
    """忽略 ditto 生成的 AppleDouble 资源元数据，不把它当成 App 内容。"""

    parts = PurePosixPath(name).parts
    return "__MACOSX" in parts or any(part.startswith("._") for part in parts)


def _read_symlink_target(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    """读取 zip 中的 macOS 符号链接目标，并限制为 UTF-8 相对路径。"""

    if info.file_size <= 0 or info.file_size > _MAX_SYMLINK_TARGET_BYTES:
        raise ValueError("更新包中的符号链接目标过长或为空")
    raw_target = archive.read(info)
    try:
        target = raw_target.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("更新包中的符号链接目标不是 UTF-8") from exc
    if (
        not target
        or "\x00" in target
        or "\\" in target
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in target)
    ):
        raise ValueError("更新包中的符号链接目标包含不安全字符")
    return target


def _validate_symlink_target(member_name: str, link_target: str) -> None:
    """确保符号链接保持在同一个 App bundle 内。"""

    member_path = PurePosixPath(member_name)
    parts = member_path.parts
    try:
        app_index = parts.index("WhisperCppCmd.app")
    except ValueError as exc:
        raise ValueError("更新包中的符号链接不在 App bundle 内") from exc
    bundle_root = PurePosixPath(*parts[: app_index + 1])
    target_path = PurePosixPath(link_target)
    if target_path.is_absolute():
        raise ValueError("更新包中的符号链接目标必须是相对路径")
    resolved = PurePosixPath(
        posixpath.normpath(str(member_path.parent / target_path))
    )
    try:
        resolved.relative_to(bundle_root)
    except ValueError as exc:
        raise ValueError("更新包中的符号链接目标越出 App bundle") from exc
    if resolved == member_path:
        raise ValueError("更新包中的符号链接不能指向自身")


def _validate_zip_infos(
    infos: list[zipfile.ZipInfo],
    *,
    max_bytes: int,
) -> None:
    if not infos:
        raise ValueError("更新包为空")
    if len(infos) > _MAX_ZIP_ENTRIES:
        raise ValueError("更新包包含过多文件")

    seen: set[str] = set()
    total_size = 0
    for info in infos:
        name = info.filename
        if not _safe_zip_member(name):
            raise ValueError("更新包包含不安全的文件路径")
        if name in seen:
            raise ValueError("更新包包含重复的文件路径")
        seen.add(name)
        if _zip_info_is_symlink(info) and info.file_size > _MAX_SYMLINK_TARGET_BYTES:
            raise ValueError("更新包中的符号链接目标过长")
        # ZipInfo.file_size 是无符号字段，但显式检查仍可防止 fake ZipInfo
        # 或异常归档对象绕过总大小限制。
        if info.file_size < 0 or info.file_size > max_bytes:
            raise ValueError("更新包中的文件超过大小限制")
        total_size += info.file_size
        if total_size > max_bytes:
            raise ValueError("更新包解压后超过大小限制")


def _extract_zip_safely(
    archive: zipfile.ZipFile,
    destination: str,
    *,
    max_bytes: int,
) -> None:
    """逐项解压并恢复 executable bit，避免 extractall 的 symlink/覆盖风险。"""

    infos = archive.infolist()
    _validate_zip_infos(infos, max_bytes=max_bytes)
    root = os.path.realpath(destination)
    os.makedirs(root, mode=0o700, exist_ok=True)

    # 先写普通目录和文件，再创建符号链接，避免归档中的链接影响后续
    # 文件落点；安全的相对链接则由 macOS bundle 原样保留。
    effective_infos = [info for info in infos if not _is_macos_metadata_member(info.filename)]
    regular_infos = [info for info in effective_infos if not _zip_info_is_symlink(info)]
    symlink_infos = [info for info in effective_infos if _zip_info_is_symlink(info)]

    for info in regular_infos:
        relative = PurePosixPath(info.filename)
        target = os.path.realpath(os.path.join(root, *relative.parts))
        try:
            if os.path.commonpath((root, target)) != root:
                raise ValueError("更新包包含越界文件路径")
        except ValueError as exc:
            raise ValueError("更新包包含越界文件路径") from exc

        if info.is_dir() or info.filename.endswith("/"):
            os.makedirs(target, mode=0o700, exist_ok=True)
            continue

        parent = os.path.dirname(target)
        os.makedirs(parent, mode=0o700, exist_ok=True)
        # ``xb`` 防止意外覆盖，即便未来调用方绕过重复名检查也不会静默
        # 让归档中的后一个成员替换前一个成员。
        with archive.open(info, "r") as source, open(target, "xb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)

        # Preserve executable/read bits needed by the App, but never restore
        # setuid/setgid/sticky bits supplied by an untrusted archive member.
        mode = (int(info.external_attr) >> 16) & 0o777
        if mode:
            os.chmod(target, mode)

    for info in symlink_infos:
        relative = PurePosixPath(info.filename)
        link_path = os.path.abspath(os.path.join(root, *relative.parts))
        try:
            if os.path.commonpath((root, link_path)) != root:
                raise ValueError("更新包包含越界符号链接路径")
        except ValueError as exc:
            raise ValueError("更新包包含越界符号链接路径") from exc
        link_target = _read_symlink_target(archive, info)
        _validate_symlink_target(info.filename, link_target)
        parent = os.path.dirname(link_path)
        os.makedirs(parent, mode=0o700, exist_ok=True)
        if os.path.lexists(link_path):
            raise ValueError("更新包包含重复的符号链接路径")
        os.symlink(link_target, link_path)

    # 再检查一次真实解析结果，覆盖符号链接链和未来对提取顺序的改动。
    for info in symlink_infos:
        link_path = os.path.abspath(os.path.join(root, *PurePosixPath(info.filename).parts))
        resolved = os.path.realpath(link_path)
        try:
            if os.path.commonpath((root, resolved)) != root:
                raise ValueError("更新包中的符号链接解析到目录外")
        except ValueError as exc:
            raise ValueError("更新包中的符号链接解析到目录外") from exc


def _validate_app_bundle_structure(app_path: str) -> None:
    """确认更新包确实是本项目 App，而非同名的其它 bundle。"""

    if not os.path.isdir(app_path) or os.path.islink(app_path):
        raise ValueError("更新包中的 App bundle 路径无效")
    info_path = os.path.join(app_path, "Contents", "Info.plist")
    executable = os.path.join(app_path, "Contents", "MacOS", "WhisperCppCmd")
    if not os.path.isfile(executable) or not os.access(executable, os.X_OK):
        raise ValueError("更新包中的 App executable 缺失或不可执行")
    if not os.path.isfile(info_path):
        raise ValueError("更新包中的 Info.plist 缺失")
    try:
        with open(info_path, "rb") as handle:
            payload = plistlib.load(handle)
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        raise ValueError("更新包中的 Info.plist 无效") from exc
    if not isinstance(payload, dict):
        raise ValueError("更新包中的 Info.plist 结构无效")
    if payload.get("CFBundleIdentifier") != "com.mkbm.whispercppcmd":
        raise ValueError("更新包的 bundle identifier 不匹配")
    if payload.get("CFBundleExecutable") not in {None, "WhisperCppCmd"}:
        raise ValueError("更新包的 bundle executable 不匹配")


def _parse_code_signature(output: str) -> CodeSignature:
    team_identifier = ""
    authority = ""
    identifier = ""
    adhoc = False
    for line in (output or "").splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            continue
        value = value.strip()
        if key == "TeamIdentifier":
            team_identifier = "" if value.lower() in {"not set", "not_set"} else value
        elif key == "Authority" and not authority:
            authority = value
        elif key == "Identifier":
            identifier = value
        elif key == "Signature" and value.lower() == "adhoc":
            adhoc = True
    return CodeSignature(
        team_identifier=team_identifier,
        authority=authority,
        identifier=identifier,
        adhoc=adhoc,
    )


def read_code_signature(app_path: str) -> CodeSignature:
    """读取并解析 App 的签名元数据；失败时抛出可展示的 ValueError。"""

    try:
        result = subprocess.run(
            ["codesign", "--display", "--verbose=4", app_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise ValueError("当前系统找不到 codesign，无法验证更新包") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ValueError(f"读取 App 签名失败：{detail}")
    signature = _parse_code_signature(
        "\n".join(part for part in (result.stdout, result.stderr) if part)
    )
    if not signature.adhoc and not signature.team_identifier:
        raise ValueError("App 没有可识别的 Developer ID 或 ad hoc 签名")
    return signature


def verify_app_signature(
    app_path: str,
    *,
    trusted_team_id: str | None = None,
    require_developer_id: bool = False,
    require_adhoc: bool = False,
) -> CodeSignature:
    """验证 bundle 封装完整性，并可选固定签名团队/类型。

    ``codesign --verify`` 只验证封装本身；更新流程额外把新包和当前运行
    App 的 Team ID（或 ad hoc 模式）进行比较，避免把一个“签名有效但来源
    不同”的包替换进已安装的 Developer ID App。
    """

    if not os.path.isdir(app_path) or os.path.islink(app_path):
        raise ValueError("待验证的更新 App 路径无效")
    try:
        result = subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", "--verbose=2", app_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise ValueError("当前系统找不到 codesign，无法验证更新包") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ValueError(f"更新包签名校验失败：{detail}")

    signature = read_code_signature(app_path)
    expected_team = (trusted_team_id or "").strip()
    if require_developer_id and (
        signature.adhoc
        or not signature.team_identifier
        or not signature.authority.startswith("Developer ID Application:")
    ):
        raise ValueError("更新包必须使用 Developer ID 签名")
    if require_adhoc and not signature.adhoc:
        raise ValueError("更新包签名类型与当前 App 不一致")
    if expected_team and signature.team_identifier != expected_team:
        raise ValueError("更新包签名 Team ID 与当前 App 不一致")
    return signature


def stage_release_app(
    release: ReleaseInfo,
    destination_root: str,
    timeout: float = 120.0,
    max_bytes: int = 2 * 1024 * 1024 * 1024,
    *,
    trusted_team_id: str | None = None,
    require_developer_id: bool = False,
    require_adhoc: bool = False,
) -> str:
    """下载并解压一个 release，返回待安装 App 路径。

    只接受 GitHub Release 的 HTTPS 下载链接和安全的 zip 成员路径；函数不
    修改当前 App，替换由独立 helper 在当前进程退出后完成。
    """
    asset = find_macos_asset(release)
    if asset is None:
        raise ValueError("该版本没有 Apple Silicon standalone zip")
    _validate_release_download_url(asset.download_url)

    try:
        max_bytes = int(max_bytes)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("更新包大小限制无效")
    if max_bytes <= 0:
        raise ValueError("更新包大小限制无效")
    if asset.size and asset.size > max_bytes:
        raise ValueError("更新包超过大小限制")
    destination_root = os.path.abspath(os.path.expanduser(destination_root))
    if os.path.islink(destination_root):
        raise ValueError("更新目录不允许是符号链接")
    os.makedirs(destination_root, mode=0o700, exist_ok=True)
    # 更新包中包含可执行文件；即使目录是旧版本创建的，也不让同机其它
    # 用户在“确认安装”和 helper 启动之间替换 staged bundle。
    os.chmod(destination_root, 0o700)
    work_dir = tempfile.mkdtemp(prefix="download-", dir=destination_root)
    zip_path = os.path.join(work_dir, "release.zip")
    extract_dir = os.path.join(work_dir, "extract")
    staged_app = ""
    try:
        request = Request(
            asset.download_url,
            headers={"Accept": "application/octet-stream", "User-Agent": "WhisperCppCmd-updater"},
        )
        with urlopen(request, timeout=timeout) as response, open(zip_path, "wb") as handle:
            get_url = getattr(response, "geturl", None)
            final_url = get_url() if callable(get_url) else asset.download_url
            if final_url and not _is_supported_github_url(final_url):
                raise ValueError("更新下载被重定向到不受信任的地址")
            headers = getattr(response, "headers", {}) or {}
            content_length = headers.get("Content-Length") if hasattr(headers, "get") else None
            try:
                declared_size = int(content_length) if content_length else 0
            except (TypeError, ValueError):
                declared_size = 0
            if declared_size > max_bytes:
                raise ValueError("更新包超过大小限制")
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("更新包超过大小限制")
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())

        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path) as archive:
            _extract_zip_safely(archive, extract_dir, max_bytes=max_bytes)

        candidates = []
        for root, dirs, _files in os.walk(extract_dir):
            for directory in dirs:
                if directory == "WhisperCppCmd.app":
                    candidates.append(os.path.join(root, directory))
        if len(candidates) != 1:
            raise ValueError("更新包中没有唯一的 WhisperCppCmd.app")
        candidate = candidates[0]
        _validate_app_bundle_structure(candidate)

        safe_tag = re.sub(r"[^A-Za-z0-9._-]+", "_", release.tag_name).strip("._") or "release"
        staged_app = os.path.join(destination_root, f"WhisperCppCmd-{safe_tag}-staged.app")
        if os.path.lexists(staged_app):
            if os.path.islink(staged_app) or not os.path.isdir(staged_app):
                os.unlink(staged_app)
            else:
                shutil.rmtree(staged_app)
        # 保留 Python.framework/dylib 等合法链接；解引用会增大包并可能破坏
        # framework 的结构，也会让由 package_app.sh 生成的包无法原样更新。
        shutil.copytree(candidate, staged_app, symlinks=True)

        # standalone 打包脚本会生成 ad hoc 或 Developer ID 签名；安装前要求
        # bundle 本身通过严格校验，并在控制器提供策略时固定签名团队/类型。
        verify_app_signature(
            staged_app,
            trusted_team_id=trusted_team_id,
            require_developer_id=require_developer_id,
            require_adhoc=require_adhoc,
        )
        return staged_app
    except Exception:
        if staged_app:
            if os.path.islink(staged_app) or os.path.isfile(staged_app):
                try:
                    os.unlink(staged_app)
                except OSError:
                    pass
            elif os.path.isdir(staged_app):
                shutil.rmtree(staged_app, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def cleanup_staged_app(staged_app: str, destination_root: str | None = None) -> bool:
    """删除取消安装时留下的 staged bundle，且只接受本 updater 的路径形状。"""

    if not isinstance(staged_app, str) or not _STAGED_APP_RE.fullmatch(
        os.path.basename(staged_app)
    ):
        return False
    staged_app = os.path.abspath(os.path.expanduser(staged_app))
    if destination_root is not None:
        destination_root = os.path.realpath(os.path.abspath(os.path.expanduser(destination_root)))
        if os.path.dirname(staged_app) != destination_root:
            return False
    if os.path.islink(staged_app) or not os.path.isdir(staged_app):
        return False
    shutil.rmtree(staged_app)
    return True


def launch_update_helper(
    helper_path: str,
    current_app: str,
    staged_app: str,
) -> None:
    """启动独立替换程序，并返回给调用方关闭当前 App。"""
    current_app = os.path.abspath(os.path.expanduser(current_app))
    staged_app = os.path.abspath(os.path.expanduser(staged_app))
    helper_path = os.path.abspath(os.path.expanduser(helper_path))
    if os.path.basename(current_app) != "WhisperCppCmd.app" or os.path.islink(current_app):
        raise ValueError("当前 App 路径不是 WhisperCppCmd.app")
    if not _STAGED_APP_RE.fullmatch(os.path.basename(staged_app)) or os.path.islink(staged_app):
        raise ValueError("更新 App 路径无效")
    if not os.path.isdir(current_app) or not os.path.isdir(staged_app):
        raise ValueError("当前或更新 App 不存在")
    if not os.path.isfile(helper_path) or os.path.islink(helper_path) or not os.access(helper_path, os.X_OK):
        raise FileNotFoundError(f"找不到更新 helper：{helper_path}")
    subprocess.Popen(
        [helper_path, str(os.getpid()), current_app, staged_app],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
