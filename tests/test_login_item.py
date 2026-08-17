"""macOS 用户级登录启动项单测。"""

import plistlib

import pytest

from core import login_item


def _patch_launch_agent_environment(monkeypatch, tmp_path):
    plist_path = tmp_path / "LaunchAgents" / "com.mkbm.whispercppcmd.plist"
    log_dir = tmp_path / "logs"
    executable = tmp_path / "WhisperCppCmd"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    monkeypatch.setattr(login_item, "launch_agent_path", lambda: str(plist_path))
    monkeypatch.setattr(login_item, "logs_dir", lambda: str(log_dir))
    monkeypatch.setattr(login_item, "ensure_runtime_dirs", lambda: log_dir.mkdir(exist_ok=True))
    monkeypatch.setattr(login_item, "app_executable", lambda: str(executable))
    monkeypatch.setattr(login_item, "_main_app_service", lambda: None)
    return plist_path, executable, log_dir


def test_enable_writes_launch_agent(monkeypatch, tmp_path):
    plist_path, executable, log_dir = _patch_launch_agent_environment(monkeypatch, tmp_path)

    assert login_item.enable() == str(executable)
    assert login_item.is_enabled() is True
    assert log_dir.is_dir()

    with plist_path.open("rb") as stream:
        payload = plistlib.load(stream)

    assert payload["Label"] == login_item.LAUNCH_AGENT_LABEL
    assert payload["ProgramArguments"] == [str(executable)]
    assert payload["RunAtLoad"] is True
    assert payload["ProcessType"] == "Interactive"
    assert payload["StandardOutPath"] == str(log_dir / "launch-agent.stdout.log")


def test_disable_removes_launch_agent(monkeypatch, tmp_path):
    plist_path, _executable, _log_dir = _patch_launch_agent_environment(monkeypatch, tmp_path)
    login_item.enable()

    login_item.disable()

    assert not plist_path.exists()
    assert login_item.is_enabled() is False


def test_invalid_launch_agent_is_not_enabled(monkeypatch, tmp_path):
    plist_path, _executable, _log_dir = _patch_launch_agent_environment(monkeypatch, tmp_path)
    plist_path.parent.mkdir(parents=True)
    plist_path.write_bytes(b"not a plist")

    assert login_item.is_enabled() is False


def test_enable_requires_app_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(login_item, "app_executable", lambda: None)
    monkeypatch.setattr(login_item, "launch_agent_path", lambda: str(tmp_path / "item.plist"))

    with pytest.raises(RuntimeError, match="WhisperCppCmd.app"):
        login_item.enable()


class _FakeNativeService:
    def __init__(self, status=0):
        self._status = status
        self.register_calls = 0
        self.unregister_calls = 0

    def status(self):
        return self._status

    def registerAndReturnError_(self, _error):
        self.register_calls += 1
        self._status = 1
        return True, None

    def unregisterAndReturnError_(self, _error):
        self.unregister_calls += 1
        self._status = 0
        return True, None


def test_native_service_registration_is_used(monkeypatch, tmp_path):
    _plist_path, executable, _log_dir = _patch_launch_agent_environment(monkeypatch, tmp_path)
    service = _FakeNativeService()
    monkeypatch.setattr(login_item, "_main_app_service", lambda: service)

    assert login_item.enable() == str(executable)
    assert service.register_calls == 1
    assert login_item.is_enabled() is True

    login_item.disable()
    assert service.unregister_calls == 1
    assert login_item.is_enabled() is False


def test_native_requires_approval_counts_as_enabled(monkeypatch, tmp_path):
    _plist_path, _executable, _log_dir = _patch_launch_agent_environment(monkeypatch, tmp_path)
    service = _FakeNativeService(status=2)
    monkeypatch.setattr(login_item, "_main_app_service", lambda: service)

    assert login_item.is_enabled() is True
