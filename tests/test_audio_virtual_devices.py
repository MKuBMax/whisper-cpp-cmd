"""P3：虚拟/远控声卡识别单测。

向日葵 OrayVirtualAudioDevice、Virtual Desktop 等虚拟声卡 CoreAudio 驱动常不稳，
是 Pa_StopStream 卡死的诱因。枚举时标注 is_virtual，配置命中时警告。

注：_is_virtual_device 纯函数留在 core.audio_source；available_devices 枚举与
_resolve_device_index 警告在 core.audio_worker._AudioCore 内核（采集已子进程化）。
"""

import core.audio_source as asmod_src       # _is_virtual_device 在此
import core.audio_worker as asmod_worker    # _AudioCore + sd 在此
from core.audio_source import AudioConfig
from core.audio_worker import _AudioCore


# ---------------- _is_virtual_device（纯函数）----------------

def test_is_virtual_device_identifies_known_virtuals():
    assert asmod_src._is_virtual_device("OrayVirtualAudioDevice")
    assert asmod_src._is_virtual_device("Virtual Desktop Mic")
    assert asmod_src._is_virtual_device("BlackHole 2ch")
    assert asmod_src._is_virtual_device("Soundflower (2ch)")


def test_is_virtual_device_excludes_physical_devices():
    assert not asmod_src._is_virtual_device("MacBook Pro麦克风")
    assert not asmod_src._is_virtual_device("MKuBMax's iPhone 的麦克风")
    assert not asmod_src._is_virtual_device("")


# ---------------- available_devices 标注（_AudioCore 内核）----------------

def _devices_with_virtual():
    return [
        {"name": "MacBook Pro麦克风", "max_input_channels": 1, "default_samplerate": 48000.0},
        {"name": "OrayVirtualAudioDevice", "max_input_channels": 2, "default_samplerate": 48000.0},
        {"name": "Virtual Desktop Mic", "max_input_channels": 2, "default_samplerate": 48000.0},
    ]


def test_available_devices_marks_virtual(monkeypatch):
    monkeypatch.setattr(asmod_worker.sd, "query_devices", _devices_with_virtual)
    core = _AudioCore(AudioConfig())
    core.invalidate_devices()
    by_name = {d["name"]: d for d in core.available_devices}
    assert by_name["MacBook Pro麦克风"]["is_virtual"] is False
    assert by_name["OrayVirtualAudioDevice"]["is_virtual"] is True
    assert by_name["Virtual Desktop Mic"]["is_virtual"] is True


# ---------------- 配置命中虚拟设备警告（_AudioCore._resolve_device_index）----------------

def test_resolve_virtual_device_warns(monkeypatch, caplog):
    monkeypatch.setattr(asmod_worker.sd, "query_devices", _devices_with_virtual)
    core = _AudioCore(AudioConfig())
    core.invalidate_devices()

    with caplog.at_level("WARNING", logger="core.audio_worker"):
        idx = core._resolve_device_index("OrayVirtualAudioDevice")

    assert idx == 1  # 仍返回 index（不强制回退，尊重用户配置）
    assert any("虚拟/远控声卡" in r.getMessage() for r in caplog.records)


def test_resolve_physical_device_no_warn(monkeypatch, caplog):
    monkeypatch.setattr(asmod_worker.sd, "query_devices", _devices_with_virtual)
    core = _AudioCore(AudioConfig())
    core.invalidate_devices()

    with caplog.at_level("WARNING", logger="core.audio_worker"):
        idx = core._resolve_device_index("MacBook Pro麦克风")

    assert idx == 0
    assert not any("虚拟/远控声卡" in r.getMessage() for r in caplog.records)
