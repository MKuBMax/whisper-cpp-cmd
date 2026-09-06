"""sysref 耳机跳过测试：耳机输出时不启动 ScreenCaptureKit。

TDD RED：core/sysref.py 尚无 is_headphone_output，测试应失败。
"""

from core.sysref import is_headphone_output


def test_airpods_is_headphone():
    assert is_headphone_output("AirPods Pro 2#") is True


def test_builtin_speaker_is_not_headphone():
    assert is_headphone_output("MacBook Pro扬声器") is False
    assert is_headphone_output("MacBook Pro Speakers") is False


def test_empty_or_none_is_not_headphone():
    assert is_headphone_output("") is False
    assert is_headphone_output(None) is False


def test_wired_headphone_names():
    assert is_headphone_output("外置耳机") is True
    assert is_headphone_output("USB Headphones") is True
