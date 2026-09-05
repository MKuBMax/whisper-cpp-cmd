"""DEV 数据目录回归：alias DEV 强制使用正式版数据目录。

DEV 与正式版共用同一份 config.json、日志、历史。模型和历史路径由
config.json 内的绝对路径决定，不受本测试约束。
"""
import os

from config import paths


def test_dev_alias_uses_formal_data_dir(monkeypatch, tmp_path):
    resources = tmp_path / "WhisperCppCmdDev.app" / "Contents" / "Resources"
    resources.mkdir(parents=True)
    (resources / "__boot__.py").write_text("# dev alias\n", encoding="utf-8")
    monkeypatch.setenv("RESOURCEPATH", str(resources))
    monkeypatch.delenv(paths.DATA_DIR_ENV, raising=False)

    assert paths.is_standalone_bundle() is False
    assert paths.is_dev_bundle() is True
    formal = os.path.expanduser("~/Library/Application Support/WhisperCppCmd")
    assert paths.runtime_root() == formal
    assert paths.logs_dir() == os.path.join(formal, "logs")
    assert paths.config_path() == os.path.join(formal, "config.json")


def test_formal_standalone_data_dir_unchanged(monkeypatch, tmp_path):
    contents = tmp_path / "WhisperCppCmd.app" / "Contents"
    resources = contents / "Resources"
    (resources / "lib").mkdir(parents=True)
    (contents / "Frameworks" / "Python.framework").mkdir(parents=True)
    monkeypatch.setenv("RESOURCEPATH", str(resources))
    monkeypatch.delenv(paths.DATA_DIR_ENV, raising=False)

    assert paths.is_standalone_bundle() is True
    assert paths.is_dev_bundle() is False
    formal = os.path.expanduser("~/Library/Application Support/WhisperCppCmd")
    assert paths.runtime_root() == formal


def test_bare_source_run_still_uses_project_root(monkeypatch, tmp_path):
    monkeypatch.delenv("RESOURCEPATH", raising=False)
    monkeypatch.delenv(paths.DATA_DIR_ENV, raising=False)

    assert paths.is_standalone_bundle() is False
    assert paths.is_dev_bundle() is False
    assert paths.runtime_root() == paths.project_root()
