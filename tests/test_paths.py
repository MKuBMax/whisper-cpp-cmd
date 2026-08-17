"""分发包运行时路径单测。"""

import os

from config import paths
from config.settings import Settings


def _make_standalone_resource_tree(tmp_path):
    contents = tmp_path / "Contents"
    resources = contents / "Resources"
    (resources / "lib").mkdir(parents=True)
    (contents / "Frameworks" / "Python.framework").mkdir(parents=True)
    return resources


def test_settings_paths_follow_user_data_override(monkeypatch, tmp_path):
    data_dir = tmp_path / "WhisperCppCmd"
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(data_dir))

    settings = Settings()

    assert settings.models_dir == str(data_dir / "models")
    assert settings.history_file == str(data_dir / "history.json")
    assert settings.glossary_file == str(data_dir / "glossary.txt")
    assert paths.config_path() == str(data_dir / "config.json")


def test_standalone_bundle_prefers_bundled_whisper_cli(monkeypatch, tmp_path):
    resources = _make_standalone_resource_tree(tmp_path)
    executable = resources.parent / "MacOS" / "WhisperCppCmd"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    cli = resources / "whisper-runtime" / "bin" / "whisper-cli"
    cli.parent.mkdir(parents=True)
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    cli.chmod(0o755)
    monkeypatch.setenv("RESOURCEPATH", str(resources))
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "data"))

    assert paths.is_standalone_bundle() is True
    assert paths.resource_root() == os.path.realpath(resources)
    assert paths.app_executable() == str(executable)
    assert paths.bundled_whisper_cli_path() == str(cli)
    assert paths.default_whisper_cli_path() == str(cli)
