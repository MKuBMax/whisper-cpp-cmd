#!/usr/bin/env python3
"""
录音器模块 - 控制录音状态和音频捕获
"""

import numpy as np
from typing import Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
import time


@dataclass
class RecordingSession:
    """录音会话数据"""
    audio_data: np.ndarray
    start_time: datetime
    end_time: datetime
    duration: float
    sample_rate: int
    
    @property
    def is_valid(self) -> bool:
        """检查录音是否有效"""
        return self.audio_data is not None and len(self.audio_data) > 0


class Recorder:
    """
    录音器 - 控制录音过程
    
    职责:
    - 控制录音开始/停止
    - 收集音频数据
    - 创建录音会话
    - 验证录音质量
    """
    
    def __init__(self, sample_rate: int = 16000, min_duration: float = 0.3):
        self.sample_rate = sample_rate
        self.min_duration = min_duration
        self._is_recording: bool = False
        self._buffer: list = []
        self._start_time: Optional[datetime] = None
        self._on_start_callback: Optional[Callable] = None
        self._on_stop_callback: Optional[Callable] = None
    
    @property
    def is_recording(self) -> bool:
        """是否正在录音"""
        return self._is_recording
    
    @property
    def recording_duration(self) -> float:
        """当前录音时长（秒）"""
        if not self._start_time:
            return 0.0
        return (datetime.now() - self._start_time).total_seconds()
    
    def start(self) -> bool:
        """
        开始录音
        
        Returns:
            是否成功开始
        """
        if self._is_recording:
            return False
        
        self._buffer = []
        self._start_time = datetime.now()
        self._is_recording = True
        
        if self._on_start_callback:
            self._on_start_callback()
        
        return True
    
    def stop(self) -> Optional[RecordingSession]:
        """
        停止录音并创建会话
        
        Returns:
            录音会话，如果无效则返回 None
        """
        if not self._is_recording:
            return None
        
        self._is_recording = False
        end_time = datetime.now()
        duration = (end_time - self._start_time).total_seconds()
        
        if self._on_stop_callback:
            self._on_stop_callback()
        
        if not self._buffer:
            return None
        
        audio_data = np.concatenate(self._buffer, axis=0)
        
        session = RecordingSession(
            audio_data=audio_data,
            start_time=self._start_time,
            end_time=end_time,
            duration=duration,
            sample_rate=self.sample_rate
        )
        
        self._buffer = []
        self._start_time = None
        
        return session
    
    def add_audio(self, audio_chunk: np.ndarray):
        """添加音频数据块 - 只在录音时收集"""
        if self._is_recording:
            self._buffer.append(audio_chunk)
    
    def clear_buffer(self):
        """清空缓冲区"""
        self._buffer = []
    
    def validate_session(self, session: RecordingSession) -> tuple[bool, str]:
        """
        验证录音会话是否有效
        
        Returns:
            (是否有效，原因)
        """
        if session.audio_data is None or len(session.audio_data) == 0:
            return False, "没有录音数据"
        
        if session.duration < self.min_duration:
            return False, f"录音太短 ({session.duration:.2f}秒 < {self.min_duration}秒)"
        
        return True, "验证通过"
    
    def set_on_start(self, callback: Callable):
        """设置开始录音回调"""
        self._on_start_callback = callback
    
    def set_on_stop(self, callback: Callable):
        """设置停止录音回调"""
        self._on_stop_callback = callback
