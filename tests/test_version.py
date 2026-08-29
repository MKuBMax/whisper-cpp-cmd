"""版本来源和 Bundle 版本格式测试。"""

import os
from pathlib import Path

from config.version import APP_BUNDLE_VERSION, APP_VERSION, _normalize_version


def test_repository_version_is_strict_semver_and_bundle_version_is_numeric():
    repository_version = (Path(__file__).parents[1] / "VERSION").read_text(
        encoding="utf-8"
    ).strip()

    expected = _normalize_version(os.environ.get("WHISPER_CPP_CMD_VERSION") or repository_version)
    assert APP_VERSION == expected
    assert _normalize_version("v1.2.3-beta.1") == "1.2.3-beta.1"
    assert _normalize_version("not-a-version") == repository_version
    assert APP_BUNDLE_VERSION == APP_VERSION.split("-", 1)[0]


def test_normalize_version_rejects_non_semver_segments():
    assert _normalize_version("1.2") != "1.2"
    assert _normalize_version("01.2.3") != "01.2.3"
