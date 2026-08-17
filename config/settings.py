#!/usr/bin/env python3
"""
配置管理模块
"""

import json
import os
from typing import Any, Optional
from dataclasses import dataclass, field, asdict
from typing import Optional

from .paths import (
    config_path as default_config_path,
    default_whisper_cli_path,
    glossary_path as default_glossary_path,
    history_path as default_history_path,
    models_dir as default_models_dir,
)


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
    dictation_mode: str = "preview"
    transcription_prompt: str = "请使用中文标点符号输出，句子尽量完整自然。数值与数量请用阿拉伯数字表示。"
    glossary_file: str = ""  # 术语表路径；留空则用项目根 glossary.txt（每行一个专有名词/术语）
    show_overlay: bool = True  # 录音时显示音量电平+时长的浮窗
    overlay_follow_mouse: bool = False  # 浮窗跟随鼠标（默认 False=屏幕底部居中）
    
    # 历史配置
    history_file: str = ""
    history_max_entries: int = 100
    
    # 日志配置
    verbose: bool = True
    
    def __post_init__(self):
        """初始化后处理"""
        if not self.models_dir:
            self.models_dir = default_models_dir()
        
        if not self.history_file:
            self.history_file = default_history_path()

        if not self.glossary_file:
            self.glossary_file = default_glossary_path()

        if not self.whisper_cli_path:
            self.whisper_cli_path = default_whisper_cli_path()
    
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
        
        data = asdict(self)
        # 原子写：先写 .tmp 再 fsync 后 os.replace，保证写一半崩溃时旧配置仍完整可读
        tmp_path = config_path + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, config_path)

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
        return os.path.exists(self.get_model_path(model_name))
    
    def list_available_models(self) -> list:
        """列出可用的模型"""
        models = []
        if os.path.exists(self.models_dir):
            for filename in os.listdir(self.models_dir):
                if filename.startswith('ggml-') and filename.endswith('.bin'):
                    name = filename[5:-4]
                    models.append(name)
        return models
    
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
