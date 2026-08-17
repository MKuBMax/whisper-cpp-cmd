#!/usr/bin/env python3
"""
媒体 ducking：录音期间压低系统输出音量，降低扬声器音乐串入麦克风。

原理：扬声器播放的音乐经空气串入内置麦克风，污染转写。录音那几秒把系统输出
音量压低（ducking），从声源降低音乐能量——比事后 FFT 降噪确定有效（谱降噪对
调性音乐无效，已实测 RMS 纹丝不动）。用耳机时不触发（耳机不串扰）。

机制：判定默认输出设备 transport 是否 built-in（system_profiler，实时查 CoreAudio，
蓝牙/USB 耳机/虚拟声卡一律跳过），osascript 读原音量 → 压低 → 录音结束恢复原值。
所有 osascript / system_profiler 调用带 timeout + 失败静默，绝不影响录音主流程。

线程模型：begin/restore 均异步提交到单线程 executor，调用方（DictationWorker）
零阻塞——osascript 不再挡住「录音中」UI 反馈。单线程串行保证
begin1→restore1→begin2→restore2 的顺序语义，_pre_duck_volume 仅在 executor
线程读写无竞态。
"""

import json
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

logger = logging.getLogger(__name__)

# osascript 调用超时：set/get volume 本是毫秒级，2s 足够且兜底防卡死
_OSA_TIMEOUT = 2.0
# system_profiler 调用超时：SPAudioDataType 实测 ~0.3s，2s 兜底防卡死
_SP_TIMEOUT = 2.0

# 内置扬声器名称特征（duck 目标）
_BUILTIN_SPEAKER_MARKERS = ("扬声器", "speaker", "built-in", "macbook")
# 耳机名称特征（跳过 ducking——耳机不串扰）
_HEADPHONE_MARKERS = (
    "airpods", "headphone", "headphones", "earphone", "earbuds", "buds",
    "耳机", "蓝牙", "bluetooth",
)
# 虚拟/远控声卡（跳过——ducking 改系统主音量会干扰虚拟声卡路由）
# 与 core/audio_source.py:_VIRTUAL_DEVICE_MARKERS 保持一致
_VIRTUAL_DEVICE_MARKERS = (
    "virtual", "oray", "blackhole", "soundflower", "loopback",
    "audio hijack", "vb-cable", "aggregate",
)


def _default_output_device_name() -> str:
    """返回当前默认输出设备名；查询失败返回空串。"""
    try:
        import sounddevice as sd
        idx = sd.default.device[1]  # [0]=input, [1]=output
        if idx is None:
            return ""
        return str(sd.query_devices(idx).get("name", "") or "")
    except Exception as e:
        logger.debug("查询默认输出设备失败：%s", e)
        return ""


def _is_builtin_speaker(name: str) -> bool:
    """默认输出设备是否内置扬声器（应 duck）。耳机/虚拟设备/未知→False。"""
    lowered = (name or "").lower()
    if not lowered:
        return False
    if any(m in lowered for m in _VIRTUAL_DEVICE_MARKERS):
        return False
    if any(m in lowered for m in _HEADPHONE_MARKERS):
        return False
    return any(m in lowered for m in _BUILTIN_SPEAKER_MARKERS)


def _default_output_is_builtin() -> Optional[bool]:
    """当前默认输出设备是否内置扬声器（应 duck）。

    基于 CoreAudio transport type 判定（system_profiler SPAudioDataType），
    比设备名匹配可靠：蓝牙耳机/USB DAC/虚拟声卡一律 transport != builtin 而跳过，
    不受设备名本地化/品牌名/PortAudio 初始化时机影响。每次实时查 CoreAudio，
    长驻进程热插拔耳机后也能正确判别。

    返回 True/False；system_profiler 不可用/超时/解析不到默认输出设备→None，
    调用方退回 _is_builtin_speaker 设备名兜底（永不比现状差）。
    """
    try:
        out = subprocess.run(
            ["system_profiler", "SPAudioDataType", "-json"],
            capture_output=True, timeout=_SP_TIMEOUT,
            # 显式 utf-8：GUI 进程（LaunchServices 启动、无 TTY）下 text=True 会退化为
            # ascii 解码，遇 system_profiler 输出里的中文设备名（0xe2 等 UTF-8 字节）即
            # UnicodeDecodeError，整个判定被 except 静默吞掉、退回 sounddevice 兜底。
            # errors=replace 双保险，保证子进程输出永远解得出 str。
            encoding="utf-8", errors="replace",
        )
    except Exception as e:
        logger.debug("system_profiler 查询失败：%s", e)
        return None
    if out.returncode != 0:
        logger.debug("system_profiler 非零退出：%s", out.stderr.strip())
        return None
    try:
        items = json.loads(out.stdout).get("SPAudioDataType", [])
    except Exception:
        return None
    for it in items:
        for dev in it.get("_items", []):
            if dev.get("coreaudio_default_audio_output_device") == "spaudio_yes":
                return dev.get("coreaudio_device_transport") == "coreaudio_device_type_builtin"
    return None


def _set_volume(vol: int) -> bool:
    """设系统输出音量（0-100）；返回是否成功。"""
    vol = max(0, min(100, int(vol)))
    try:
        out = subprocess.run(
            ["osascript", "-e", "set volume output volume {}".format(vol)],
            capture_output=True, text=True, timeout=_OSA_TIMEOUT,
        )
    except Exception as e:
        logger.debug("set volume %s 失败：%s", vol, e)
        return False
    return out.returncode == 0


def _duck_once(target_vol: int) -> tuple:
    """一次 osascript 完成 duck 全流程：读原值 → 判定 → 压低 → 读回验证。

    把原来 3 次串行 subprocess（实测 ~600ms）压成 1 次（~250ms），省掉重复的
    osascript 进程启动 + AppleScript 编译开销。

    返回 (原音量, 状态)：
      ok    = 已压低并验证通过，调用方须在结束时 restore 回原音量
      skip  = 原音量已 <= 目标，未改动系统音量
      fail  = set 后读回验证未通过（外接 DAC/HDMI 静默失败），脚本内已回滚原值
      error = osascript 调用失败/超时/返回格式异常，未改动系统音量
    """
    script = (
        "set targetVol to {t}\n"
        "set orig to output volume of (get volume settings)\n"
        "if orig <= targetVol then\n"
        "  return (orig as text) & \"|skip\"\n"
        "end if\n"
        "set volume output volume targetVol\n"
        "set actual to output volume of (get volume settings)\n"
        "if actual > targetVol + 5 then\n"
        "  set volume output volume orig\n"
        "  return (orig as text) & \"|fail\"\n"
        "end if\n"
        "return (orig as text) & \"|ok\""
    ).format(t=target_vol)
    try:
        out = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=_OSA_TIMEOUT,
        )
    except Exception as e:
        logger.debug("duck_once 失败：%s", e)
        return None, "error"
    if out.returncode != 0:
        logger.debug("duck_once 非零退出：%s", out.stderr.strip())
        return None, "error"
    body = out.stdout.strip()
    if "|" not in body:
        return None, "error"
    orig_s, status = body.rsplit("|", 1)
    try:
        return int(orig_s), status
    except ValueError:
        return None, "error"


class MediaDucker:
    """录音期间压低系统输出音量；耳机/虚拟声卡时不触发。restore 幂等。

    begin/restore 异步提交到单线程 executor：调用方零阻塞；单线程串行保证
    begin→restore→begin 的顺序语义，_pre_duck_volume 仅在 executor 线程读写无竞态。
    """

    def __init__(self, enabled: bool, duck_volume: int = 10,
                 duck_when_headphones: bool = False):
        self._enabled = enabled
        self._duck_volume = max(0, min(100, int(duck_volume)))
        self._duck_when_headphones = bool(duck_when_headphones)
        self._pre_duck_volume: Optional[int] = None  # 录音前原音量；None=本次未 duck
        # 单线程 executor：串行所有 duck 操作，避免 begin/restore 并发竞态
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="MediaDucker")

    def set_enabled(self, enabled: bool) -> None:
        """运行时开关 ducking（菜单栏切换）。立即生效，影响后续 begin()。

        restore() 不读 _enabled，故录音中途关闭不会卡音量——_restore_impl 只看
        _pre_duck_volume，当前录音结束仍照常恢复原值。线程安全：GIL 下单 bool
        读写原子；set_enabled 在主线程、begin/restore 在 worker+executor 线程，
        最坏只读到一拍旧值，均被 restore 幂等兜底。
        """
        self._enabled = bool(enabled)

    def set_duck_when_headphones(self, enabled: bool) -> None:
        """运行时开关「戴耳机时也压低」（菜单栏切换）。立即生效，影响后续 begin()。

        True 时跳过设备判定，对所有输出设备 duck（含耳机）；False（默认）时仅
        内置扬声器 duck，耳机/外接/虚拟跳过。线程安全同 set_enabled（GIL 单 bool）。
        """
        self._duck_when_headphones = bool(enabled)

    def begin(self) -> None:
        """录音开始时调用。异步：立即返回不阻塞调用方；失败不改音量、不影响录音。"""
        if not self._enabled:
            return
        self._executor.submit(self._begin_impl)

    def _begin_impl(self) -> None:
        """executor 单线程内执行 duck；_pre_duck_volume 仅在此与 _restore_impl 读写。"""
        if self._duck_when_headphones:
            # 配置「戴耳机时也压低」：跳过设备判定，对所有输出设备 duck
            builtin = True
        else:
            builtin = _default_output_is_builtin()
            if builtin is None:
                # 兜底：system_profiler 不可用，退回设备名匹配（次优，但永不比现状差）
                builtin = _is_builtin_speaker(_default_output_device_name())
                logger.info("system_profiler 不可用，退回设备名兜底：builtin=%s", builtin)
        if not builtin:
            logger.info("media duck 跳过：当前默认输出非内置扬声器（耳机/外接/虚拟/未知）")
            self._pre_duck_volume = None
            return
        orig, status = _duck_once(self._duck_volume)
        if status == "ok":
            self._pre_duck_volume = orig
            logger.info("media duck 生效：%s → %s", orig, self._duck_volume)
        elif status == "skip":
            self._pre_duck_volume = None
            logger.info("media duck 跳过：当前音量 %s 已 <= 目标 %s", orig, self._duck_volume)
        elif status == "fail":
            self._pre_duck_volume = None
            logger.warning("media duck 未生效：set 后验证失败，脚本内已恢复原值 %s", orig)
        else:  # error
            self._pre_duck_volume = None
            logger.info("media duck 跳过：osascript 调用失败")

    def restore(self) -> None:
        """录音结束时调用，恢复原音量。异步提交；幂等：未 duck 或已恢复则空操作。"""
        self._executor.submit(self._restore_impl)

    def _restore_impl(self) -> None:
        """executor 单线程内执行恢复。与 _begin_impl 经同一线程串行，无竞态。"""
        target = self._pre_duck_volume
        self._pre_duck_volume = None
        if target is None:
            return
        if not _set_volume(target):
            logger.warning("media duck 恢复失败：无法 set volume %s（系统音量可能停在低值，请手动检查）", target)
            return
        logger.info("media duck 恢复：%s", target)
