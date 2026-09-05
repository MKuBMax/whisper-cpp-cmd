#!/usr/bin/env python3
"""
配置管理模块
"""

import json
import math
import os
import re
import tempfile
from typing import Optional
from dataclasses import dataclass, asdict

from .paths import (
    config_path as default_config_path,
    default_whisper_cli_path,
    glossary_path as default_glossary_path,
    history_path as default_history_path,
    models_dir as default_models_dir,
)


_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _coerce_bool(value: object, default: bool) -> bool:
    """解析 JSON/旧配置中的布尔值，避免 ``bool('false')`` 误启用功能。"""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # json.load 默认接受 NaN/Infinity；它们不能被当成 truthy 配置。
        try:
            return bool(value) if math.isfinite(value) else default
        except (TypeError, OverflowError):
            return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return default


def _coerce_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        # ``int(1.9)`` is less surprising for an imported JSON value than
        # allowing a float to leak into AppKit/menu or subprocess arguments.
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return min(maximum, max(minimum, result))


def _coerce_float(value: object, default: float, minimum: float, maximum: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(result):
        return default
    return min(maximum, max(minimum, result))


def _coerce_path(value: object, default: str) -> str:
    if not isinstance(value, str) or not value.strip():
        value = default
    return os.path.expanduser(str(value).strip())


@dataclass
class Settings:
    """配置设置"""
    
    # 模型配置
    current_model: str = "large-v3"
    models_dir: str = ""
    
    # 识别配置
    language: str = "zh"
    sample_rate: int = 16000  # Whisper 固定采样率，不要修改
    
    # 后端配置
    whisper_cli_path: str = ""
    n_threads: int = 8
    auto_release_minutes: int = 10
    transcription_timeout: float = 120.0  # whisper-server 单次转录 HTTP 超时（秒），watchdog 据此对齐

    # VAD 配置（whisper-server 端 Silero 静音裁剪）
    use_vad: bool = False  # 启用 VAD；模型缺失时自动下载
    vad_model: str = ""  # 留空则用 models/ggml-silero-v6.2.0.bin
    
    # 录音配置
    min_duration: float = 0.3
    block_size: int = 256
    latency: str = 'low'  # 'low' 或数值 (秒)
    max_recording_seconds: float = 300.0  # 单次录音最大时长，超出截断防止内存无限增长
    hotkey: str = "cmd_r"  # 录音触发键（cmd_r/cmd_l/alt_r/shift_r/ctrl_r/f13/f14）

    # 媒体 ducking：录音期间压低系统输出音量，降低扬声器音乐串入麦克风（用耳机时自动跳过）
    duck_media: bool = True
    duck_volume: int = 10  # ducking 目标音量 0-100；越低压制越彻底=转写越好但音乐越听不清
    duck_when_headphones: bool = False  # 戴耳机时也压低（默认 False=耳机时跳过，耳机不串扰麦克风）

    # 麦克风配置（使用名称避免索引飘移）
    audio_device_name: Optional[str] = None
    
    # 输出配置
    auto_paste: bool = True
    paste_delay: float = 0.03
    chinese_script: str = "simplified"
    dictation_mode: str = "quick"
    transcription_prompt: str = "请使用中文标点符号输出，句子尽量完整自然。数值与数量请用阿拉伯数字表示。"
    glossary_file: str = ""  # 术语表路径；留空则用项目根 glossary.txt（每行一个专有名词/术语）
    update_check_enabled: bool = True
    last_update_check_at: str = ""
    show_overlay: bool = True  # 录音时显示音量电平+时长的浮窗
    overlay_follow_mouse: bool = False  # 浮窗跟随鼠标（默认 False=屏幕底部居中）
    
    # 历史配置
    history_file: str = ""
    history_max_entries: int = 100
    onboarding_completed: bool = False
    show_in_dock: bool = True  # 同时在 Dock 栏显示图标（防刘海屏/菜单栏隐藏工具折叠丢失）
    show_floating_pill: bool = False  # 旧配置兼容，常驻胶囊已移除
    status_bar_show_title: bool = False  # 旧配置兼容，菜单栏固定为图标
    
    experience_version: int = 1

    # 日志配置
    verbose: bool = True
    
    def __post_init__(self):
        """初始化并清洗配置。

        配置文件是用户可编辑的数据，不能假定字段类型永远和 dataclass 默认值
        一致。这里集中做轻量边界校验，保证设置窗口、定时更新检查和运行时
        子进程拿到的值不会被 ``"false"``、NaN 或路径穿越等输入污染。
        """

        if not isinstance(self.current_model, str) or not _MODEL_NAME_RE.fullmatch(
            self.current_model.strip()
        ):
            self.current_model = "large-v3"
        else:
            self.current_model = self.current_model.strip()

        self.models_dir = _coerce_path(self.models_dir, default_models_dir())
        self.history_file = _coerce_path(self.history_file, default_history_path())
        self.glossary_file = _coerce_path(self.glossary_file, default_glossary_path())
        self.whisper_cli_path = _coerce_path(
            self.whisper_cli_path, default_whisper_cli_path()
        )

        self.language = self.language.strip().lower() if isinstance(self.language, str) else "zh"
        if self.language not in {"zh", "en", "ja", "ko", "auto"}:
            self.language = "zh"
        self.chinese_script = (
            self.chinese_script.strip().lower()
            if isinstance(self.chinese_script, str)
            else "simplified"
        )
        if self.chinese_script not in {"simplified", "traditional", "auto"}:
            self.chinese_script = "simplified"
        self.dictation_mode = (
            self.dictation_mode.strip().lower()
            if isinstance(self.dictation_mode, str)
            else "quick"
        )
        if self.dictation_mode not in {"preview", "quick"}:
            self.dictation_mode = "quick"

        for name, default in (
            ("duck_media", True),
            ("duck_when_headphones", False),
            ("auto_paste", True),
            ("update_check_enabled", True),
            ("onboarding_completed", False),
            ("show_overlay", True),
            ("overlay_follow_mouse", False),
            ("verbose", True),
            ("use_vad", False),
            ("show_in_dock", True),
            ("show_floating_pill", False),
            ("status_bar_show_title", False),
        ):
            setattr(self, name, _coerce_bool(getattr(self, name, default), default))

        self.experience_version = _coerce_int(self.experience_version, 1, 0, 10_000)

        # The recorder/pipeline contract is fixed at 16 kHz.  Treat this legacy
        # field as a compatibility value rather than allowing a malformed or
        # hand-edited config to silently change the audio/runtime contract.
        self.sample_rate = 16_000
        self.n_threads = _coerce_int(self.n_threads, 8, 1, 128)
        self.auto_release_minutes = _coerce_int(self.auto_release_minutes, 10, 0, 24 * 60)
        self.duck_volume = _coerce_int(self.duck_volume, 10, 0, 100)
        self.history_max_entries = _coerce_int(self.history_max_entries, 100, 1, 100_000)
        self.block_size = _coerce_int(self.block_size, 256, 1, 65_536)
        self.hotkey = self.hotkey.strip() if isinstance(self.hotkey, str) else "cmd_r"
        if self.hotkey not in {"cmd_r", "cmd_l", "alt_r", "shift_r", "ctrl_r", "f13", "f14"}:
            self.hotkey = "cmd_r"

        self.paste_delay = _coerce_float(self.paste_delay, 0.03, 0.0, 5.0)
        self.min_duration = _coerce_float(self.min_duration, 0.3, 0.05, 60.0)
        self.max_recording_seconds = _coerce_float(
            self.max_recording_seconds, 300.0, self.min_duration, 3_600.0
        )
        self.transcription_timeout = _coerce_float(
            self.transcription_timeout, 120.0, 1.0, 3_600.0
        )
        self.audio_device_name = (
            self.audio_device_name.strip()
            if isinstance(self.audio_device_name, str) and self.audio_device_name.strip()
            else None
        )
        self.vad_model = self.vad_model.strip() if isinstance(self.vad_model, str) else ""
        self.transcription_prompt = (
            self.transcription_prompt if isinstance(self.transcription_prompt, str) else ""
        )
        self.last_update_check_at = (
            self.last_update_check_at if isinstance(self.last_update_check_at, str) else ""
        )

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> 'Settings':
        """从文件加载配置"""
        if config_path is None:
            config_path = default_config_path()
        
        settings = cls()
        
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for key, value in data.items():
                        if hasattr(settings, key):
                            setattr(settings, key, value)
                # 允许配置文件用空字符串表示「使用当前运行环境默认值」，也兼容
                # 旧机器上保存的 Homebrew 路径在分发 App 中不存在的情况。
                try:
                    experience_version = int(data.get("experience_version", 0) or 0)
                except (TypeError, ValueError):
                    experience_version = 0
                if experience_version < 1:
                    settings.dictation_mode = "quick"
                    settings.auto_paste = True
                    settings.show_floating_pill = False
                    settings.status_bar_show_title = False
                    settings.experience_version = 1
                settings.__post_init__()
                if not os.path.exists(settings.whisper_cli_path):
                    settings.whisper_cli_path = default_whisper_cli_path()
            except Exception as e:
                print(f"⚠️  读取配置文件失败：{e}")
        
        return settings
    
    def save(self, config_path: Optional[str] = None):
        """保存配置到文件"""
        if config_path is None:
            config_path = default_config_path()

        config_path = os.path.abspath(os.path.expanduser(config_path))
        parent = os.path.dirname(config_path) or "."
        os.makedirs(parent, mode=0o700, exist_ok=True)
        data = asdict(self)
        # 原子写：临时文件与目标位于同一目录，先 fsync 再 os.replace，保证
        # 写一半崩溃时旧配置仍完整可读，也避免多个线程共用固定 ``.tmp``。
        temp_fd, temp_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(config_path)}.tmp-",
            dir=parent,
        )
        try:
            os.chmod(temp_path, 0o600)
            with os.fdopen(temp_fd, "w", encoding="utf-8") as handle:
                temp_fd = -1
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, config_path)
            try:
                os.chmod(config_path, 0o600)
            except OSError:
                pass
        finally:
            if temp_fd >= 0:
                try:
                    os.close(temp_fd)
                except OSError:
                    pass
            try:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            except OSError:
                pass

    def get_glossary_terms(self) -> list:
        """读取术语表，返回保序去重的术语列表（忽略 # 注释行与空行）。文件缺失或读取失败返回 []。"""
        if not self.glossary_file or not os.path.exists(self.glossary_file):
            return []
        try:
            with open(self.glossary_file, 'r', encoding='utf-8') as f:
                raw = [line.strip() for line in f]
            terms = [t for t in raw if t and not t.startswith('#')]
            return list(dict.fromkeys(terms))  # 保序去重
        except Exception as e:
            print(f"⚠️  读取术语表失败：{e}")
            return []

    def get_transcription_prompt(self) -> str:
        """组合「风格 prompt + 术语表」作为 whisper-server 的 --prompt。术语表为空时只返回风格 prompt。"""
        style = (self.transcription_prompt or '').strip()
        terms = self.get_glossary_terms()
        if not terms:
            return style
        return f"{style}\n专有名词与术语：{'、'.join(terms)}。"
    
    def get_model_path(self, model_name: Optional[str] = None) -> str:
        """获取模型文件路径"""
        if model_name is None:
            model_name = self.current_model
        
        return os.path.join(self.models_dir, f'ggml-{model_name}.bin')
    
    def model_exists(self, model_name: Optional[str] = None) -> bool:
        """检查模型文件是否存在"""
        path = self.get_model_path(model_name)
        try:
            return os.path.isfile(path) and os.path.getsize(path) > 0
        except OSError:
            return False
    
    def list_available_models(self) -> list:
        """列出可用的模型"""
        models = []
        try:
            filenames = os.listdir(self.models_dir)
        except OSError:
            return models
        for filename in filenames:
            if (filename.startswith('ggml-') and filename.endswith('.bin')
                    and not filename.startswith('ggml-silero-')):
                try:
                    if os.path.getsize(os.path.join(self.models_dir, filename)) <= 0:
                        continue
                except OSError:
                    continue
                name = filename[5:-4]
                models.append(name)
        return sorted(models)
    
    def get_audio_device_index(self) -> Optional[int]:
        """根据设备名称获取当前索引（解决设备索引飘移问题）"""
        import sounddevice as sd
        
        if self.audio_device_name:
            for i, dev in enumerate(sd.query_devices()):
                if dev['name'] == self.audio_device_name and dev['max_input_channels'] > 0:
                    return i
            print(f"⚠️  未找到设备：{self.audio_device_name}，使用系统默认")
            return None
        
        return None
