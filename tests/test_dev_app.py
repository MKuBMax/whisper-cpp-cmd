"""RED: DEV bundle 必须是 alias 模式，引用源码而不是打包代码。"""
import os
import plistlib

import pytest

pytestmark = pytest.mark.skipif(
    not os.path.isdir("build/dev"), reason="DEV App 未构建，跳过 alias 断言"
)

DEV_APP = "build/dev/WhisperCppCmdDev.app"


def _plist():
    path = os.path.join(DEV_APP, "Contents", "Info.plist")
    assert os.path.isfile(path), f"DEV App 缺少 Info.plist：{path}"
    with open(path, "rb") as stream:
        return plistlib.load(stream)


def test_dev_bundle_is_alias():
    assert _plist()["PyOptions"]["alias"] is True


def test_dev_bundle_identity():
    plist = _plist()
    assert plist["CFBundleIdentifier"] == "com.mkbm.whispercppcmd.dev"
    assert plist["CFBundleName"] == "WhisperCppCmdDev"


def test_dev_executable_name_matches_bundle():
    executable = os.path.join(DEV_APP, "Contents", "MacOS", "WhisperCppCmdDev")
    assert os.access(executable, os.X_OK), f"DEV 可执行文件缺失：{executable}"
