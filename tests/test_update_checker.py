"""更新检查的版本逻辑测试。"""

import io
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import zipfile

import pytest

from core import update_checker
from core.update_checker import (
    CodeSignature,
    ReleaseAsset,
    ReleaseInfo,
    cleanup_staged_app,
    fetch_latest_release,
    find_macos_asset,
    is_newer,
    parse_version,
    verify_app_signature,
)


def test_parse_version_accepts_common_tags():
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("1.4") == (1, 4, 0)
    assert parse_version("invalid") == (0, 0, 0)


def test_is_newer():
    release = ReleaseInfo("v1.2.0", (1, 2, 0), "", "")

    assert is_newer("1.1.9", release)
    assert not is_newer("1.2.0", release)
    assert not is_newer("1.9.0", release)


def test_stable_release_is_newer_than_same_version_prerelease():
    stable = ReleaseInfo("v1.2.0", (1, 2, 0), "", "")
    beta_two = ReleaseInfo("v1.2.0-beta.2", (1, 2, 0), "", "")

    assert is_newer("1.2.0-beta.1", stable)
    assert not is_newer("1.2.0", beta_two)
    assert is_newer("1.2.0-beta.1", beta_two)


def test_find_macos_asset():
    release = ReleaseInfo(
        "v1.2.0",
        (1, 2, 0),
        "",
        "",
        assets=(
            ReleaseAsset("WhisperCppCmd-macOS-x86_64.zip", "https://github.com/a/b"),
            ReleaseAsset("WhisperCppCmd-macOS-arm64.zip", "https://github.com/a/c"),
        ),
    )

    assert find_macos_asset(release).name.endswith("arm64.zip")


def test_github_download_url_rejects_credentials_and_non_github_hosts():
    with pytest.raises(ValueError):
        update_checker._validate_release_download_url("https://github.com:444/a/b.zip")
    with pytest.raises(ValueError):
        update_checker._validate_release_download_url("https://github.com@evil.example/a.zip")
    with pytest.raises(ValueError):
        update_checker._validate_release_download_url("http://github.com/a/b.zip")

    update_checker._validate_release_download_url(
        "https://github.com/MKuBMax/whisper-cpp-cmd/releases/download/v1.2.3/app.zip"
    )


def test_parse_code_signature_supports_developer_id_and_adhoc():
    developer = update_checker._parse_code_signature(
        "Authority=Developer ID Application: Example (TEAM123)\n"
        "Identifier=com.example.app\nTeamIdentifier=TEAM123\n"
    )
    assert developer.team_identifier == "TEAM123"
    assert developer.authority.startswith("Developer ID Application")
    assert developer.adhoc is False

    adhoc = update_checker._parse_code_signature("Signature=adhoc\nIdentifier=com.example.app\n")
    assert adhoc.adhoc is True


def test_verify_app_signature_requires_developer_id_authority(monkeypatch, tmp_path):
    app = tmp_path / "WhisperCppCmd.app"
    executable = app / "Contents" / "MacOS" / "WhisperCppCmd"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    calls = []

    def fake_run(args, **kwargs):
        calls.append(kwargs)
        if "--verify" in args:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="Authority=Apple Development: Example (TEAM123)\n"
            "Identifier=com.mkbm.whispercppcmd\n"
            "TeamIdentifier=TEAM123\n",
            stderr="",
        )

    monkeypatch.setattr(update_checker.subprocess, "run", fake_run)

    with pytest.raises(ValueError, match="Developer ID"):
        verify_app_signature(
            str(app),
            trusted_team_id="TEAM123",
            require_developer_id=True,
        )
    assert calls
    assert all(call.get("encoding") == "utf-8" for call in calls)
    assert all(call.get("errors") == "replace" for call in calls)


def test_fetch_latest_release_rejects_oversize_metadata(monkeypatch):
    response = _DownloadResponse(b"{}", "https://api.github.com/repos/a/b/releases/latest")
    response.headers["Content-Length"] = str(update_checker._MAX_RELEASE_METADATA_BYTES + 1)
    monkeypatch.setattr(update_checker, "urlopen", lambda *_args, **_kwargs: response)

    with pytest.raises(ValueError, match="响应超过大小限制"):
        fetch_latest_release("https://api.github.com/repos/a/b/releases/latest")


def _release_zip(
    *,
    member_name: str = "WhisperCppCmd-macOS-arm64/WhisperCppCmd.app/Contents/Info.plist",
    symlink_name: str = "",
    symlink_target: str = "",
):
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr(
            member_name,
            plistlib.dumps(
                {
                    "CFBundleIdentifier": "com.mkbm.whispercppcmd",
                    "CFBundleExecutable": "WhisperCppCmd",
                }
            ),
        )
        executable = zipfile.ZipInfo(
            "WhisperCppCmd-macOS-arm64/WhisperCppCmd.app/Contents/MacOS/WhisperCppCmd"
        )
        executable.external_attr = (stat.S_IFREG | 0o755) << 16
        archive.writestr(executable, b"#!/bin/sh\n")
        if symlink_name:
            link = zipfile.ZipInfo(symlink_name)
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, symlink_target.encode("utf-8"))
    return raw.getvalue()


class _DownloadResponse:
    def __init__(self, payload: bytes, final_url: str):
        self._stream = io.BytesIO(payload)
        self.headers = {"Content-Length": str(len(payload))}
        self._final_url = final_url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self._final_url

    def read(self, size=-1):
        return self._stream.read(size)


def test_stage_release_app_extracts_only_safe_members_and_preserves_executable_bit(monkeypatch, tmp_path):
    payload = _release_zip()
    release = ReleaseInfo(
        "v1.2.3",
        (1, 2, 3),
        "",
        "",
        assets=(ReleaseAsset("WhisperCppCmd-macOS-arm64.zip", "https://github.com/a/b.zip"),),
    )
    monkeypatch.setattr(
        update_checker,
        "urlopen",
        lambda *_args, **_kwargs: _DownloadResponse(payload, "https://github.com/a/b.zip"),
    )
    monkeypatch.setattr(
        update_checker,
        "verify_app_signature",
        lambda *_args, **_kwargs: CodeSignature(adhoc=True),
    )

    staged = update_checker.stage_release_app(release, str(tmp_path / "updates"))

    assert staged.endswith("WhisperCppCmd-v1.2.3-staged.app")
    assert os.access(os.path.join(staged, "Contents/MacOS/WhisperCppCmd"), os.X_OK)


def test_stage_release_app_preserves_safe_relative_symlinks(monkeypatch, tmp_path):
    symlink_name = "WhisperCppCmd-macOS-arm64/WhisperCppCmd.app/Contents/MacOS/link"
    payload = _release_zip(symlink_name=symlink_name, symlink_target="WhisperCppCmd")
    release = ReleaseInfo(
        "v1.2.3",
        (1, 2, 3),
        "",
        "",
        assets=(ReleaseAsset("WhisperCppCmd-macOS-arm64.zip", "https://github.com/a/b.zip"),),
    )
    monkeypatch.setattr(
        update_checker,
        "urlopen",
        lambda *_args, **_kwargs: _DownloadResponse(payload, "https://github.com/a/b.zip"),
    )
    monkeypatch.setattr(
        update_checker,
        "verify_app_signature",
        lambda *_args, **_kwargs: CodeSignature(adhoc=True),
    )

    staged = update_checker.stage_release_app(release, str(tmp_path / "updates"))

    link_path = os.path.join(staged, "Contents", "MacOS", "link")
    assert os.path.islink(link_path)
    assert os.readlink(link_path) == "WhisperCppCmd"


def test_stage_release_app_ignores_ditto_appledouble_metadata(monkeypatch, tmp_path):
    raw = io.BytesIO(_release_zip())
    with zipfile.ZipFile(raw, "a") as archive:
        archive.writestr(
            "__MACOSX/WhisperCppCmd-macOS-arm64/WhisperCppCmd.app/Contents/Info.plist",
            plistlib.dumps({"CFBundleIdentifier": "not-the-app"}),
        )
        archive.writestr(
            "__MACOSX/WhisperCppCmd-macOS-arm64/WhisperCppCmd.app/Contents/MacOS/WhisperCppCmd",
            b"not an executable",
        )
    payload = raw.getvalue()
    release = ReleaseInfo(
        "v1.2.3",
        (1, 2, 3),
        "",
        "",
        assets=(ReleaseAsset("WhisperCppCmd-macOS-arm64.zip", "https://github.com/a/b.zip"),),
    )
    monkeypatch.setattr(
        update_checker,
        "urlopen",
        lambda *_args, **_kwargs: _DownloadResponse(payload, "https://github.com/a/b.zip"),
    )
    monkeypatch.setattr(
        update_checker,
        "verify_app_signature",
        lambda *_args, **_kwargs: CodeSignature(adhoc=True),
    )

    staged = update_checker.stage_release_app(release, str(tmp_path / "updates"))

    assert staged.endswith("WhisperCppCmd-v1.2.3-staged.app")
    assert not os.path.exists(os.path.join(os.path.dirname(staged), "__MACOSX"))


def test_stage_release_app_rejects_redirect_outside_trusted_hosts(monkeypatch, tmp_path):
    release = ReleaseInfo(
        "v1.2.3",
        (1, 2, 3),
        "",
        "",
        assets=(ReleaseAsset("WhisperCppCmd-macOS-arm64.zip", "https://github.com/a/b.zip"),),
    )
    monkeypatch.setattr(
        update_checker,
        "urlopen",
        lambda *_args, **_kwargs: _DownloadResponse(b"bad", "https://evil.example/b.zip"),
    )

    with pytest.raises(ValueError, match="重定向"):
        update_checker.stage_release_app(release, str(tmp_path / "updates"))


@pytest.mark.parametrize(
    "member_name",
    ["../escape", "\\\\escape", "/absolute", "WhisperCppCmd-macOS-arm64/../escape"],
)
def test_stage_release_app_rejects_unsafe_zip_paths(monkeypatch, tmp_path, member_name):
    payload = _release_zip(member_name=member_name)
    release = ReleaseInfo(
        "v1.2.3",
        (1, 2, 3),
        "",
        "",
        assets=(ReleaseAsset("WhisperCppCmd-macOS-arm64.zip", "https://github.com/a/b.zip"),),
    )
    monkeypatch.setattr(
        update_checker,
        "urlopen",
        lambda *_args, **_kwargs: _DownloadResponse(payload, "https://github.com/a/b.zip"),
    )

    with pytest.raises(ValueError, match="不安全"):
        update_checker.stage_release_app(release, str(tmp_path / "updates"))


def test_stage_release_app_rejects_unsafe_zip_symlink_targets(monkeypatch, tmp_path):
    payload = _release_zip(
        symlink_name="WhisperCppCmd-macOS-arm64/WhisperCppCmd.app/Contents/MacOS/link",
        symlink_target="/tmp/escape",
    )
    release = ReleaseInfo(
        "v1.2.3",
        (1, 2, 3),
        "",
        "",
        assets=(ReleaseAsset("WhisperCppCmd-macOS-arm64.zip", "https://github.com/a/b.zip"),),
    )
    monkeypatch.setattr(
        update_checker,
        "urlopen",
        lambda *_args, **_kwargs: _DownloadResponse(payload, "https://github.com/a/b.zip"),
    )

    with pytest.raises(ValueError, match="符号链接"):
        update_checker.stage_release_app(release, str(tmp_path / "updates"))


def test_cleanup_staged_app_is_scoped_to_updater_directory(tmp_path):
    updates = tmp_path / "updates"
    staged = updates / "WhisperCppCmd-v1.2.3-staged.app"
    staged.mkdir(parents=True)
    (staged / "marker").write_text("x", encoding="utf-8")

    assert cleanup_staged_app(str(staged), str(updates)) is True
    assert not staged.exists()
    assert cleanup_staged_app(str(tmp_path / "other.app"), str(updates)) is False
