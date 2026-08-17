#!/usr/bin/env python3
"""
音频处理器模块 - 音频预处理
"""

import numpy as np
from typing import Optional
from dataclasses import dataclass


@dataclass
class ProcessorConfig:
    """处理器配置"""
    normalize: bool = True
    remove_silence: bool = False
    target_db: float = -20.0
    silence_threshold: float = 0.01


class Processor:
    """
    音频处理器 - 预处理音频数据
    
    职责:
    - 音频归一化
    - 静音检测
    - 降噪处理
    - 格式转换
    """
    
    def __init__(self, config: Optional[ProcessorConfig] = None):
        self.config = config or ProcessorConfig()
    
    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """
        处理音频数据
        
        Args:
            audio: 原始音频数据
            sample_rate: 采样率
        
        Returns:
            处理后的音频数据
        """
        result = audio
        
        if self.config.normalize:
            result = self._normalize(result)
        
        if self.config.remove_silence:
            result = self._remove_silence(result)
        
        return result
    
    def _normalize(self, audio: np.ndarray) -> np.ndarray:
        """音频归一化"""
        max_val = np.max(np.abs(audio))
        if max_val > 0:
            audio = audio / max_val
        return audio.astype(np.float32)
    
    def _remove_silence(self, audio: np.ndarray) -> np.ndarray:
        """移除静音部分"""
        threshold = self.config.silence_threshold
        mask = np.abs(audio) > threshold
        
        if not np.any(mask):
            return audio
        
        indices = np.where(mask)[0]
        start = indices[0]
        end = indices[-1] + 1
        
        return audio[start:end]
    
    def to_wav_bytes(self, audio: np.ndarray, sample_rate: int) -> bytes:
        """
        转换为 WAV 格式字节
        
        Args:
            audio: 音频数据
            sample_rate: 采样率
        
        Returns:
            WAV 格式字节
        """
        import wave
        import io
        
        audio_int16 = (audio * 32767).astype(np.int16)
        
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(audio_int16.tobytes())
        
        return buffer.getvalue()
    
    def get_duration(self, audio: np.ndarray, sample_rate: int) -> float:
        """获取音频时长（秒）"""
        return len(audio) / sample_rate
