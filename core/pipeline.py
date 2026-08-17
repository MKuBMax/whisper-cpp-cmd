#!/usr/bin/env python3
"""
流水线模块 - 协调各组件的管道
"""

import time
import logging
from typing import Optional, Callable
from dataclasses import dataclass
from datetime import datetime

from .audio_source import AudioSource, AudioConfig
from .recorder import Recorder, RecordingSession
from .processor import Processor, ProcessorConfig
from .model import ModelEngine, TranscriptionResult
from .output import OutputHandler, OutputConfig, TextOutput
from .clipboard import Clipboard, ClipboardConfig
from .dictation_trace import DictationTrace


logger = logging.getLogger(__name__)


@dataclass
class PipelineConfig:
    """流水线配置"""
    audio: AudioConfig = None
    processor: ProcessorConfig = None
    output: OutputConfig = None
    clipboard: ClipboardConfig = None
    model_backend: str = 'whisper-cli'
    model_path: str = ''
    model_name: str = ''
    language: str = 'zh'
    n_threads: int = 8
    cli_path: str = '/opt/homebrew/bin/whisper-cli'
    transcription_timeout: float = 120.0
    use_vad: bool = False
    vad_model: str = ''
    transcription_prompt: str = ''
    
    def __post_init__(self):
        if self.audio is None:
            self.audio = AudioConfig()
        if self.processor is None:
            self.processor = ProcessorConfig()
        if self.output is None:
            self.output = OutputConfig()
        if self.clipboard is None:
            self.clipboard = ClipboardConfig()


@dataclass
class PipelineResult:
    """流水线执行结果"""
    success: bool
    text: str = ""
    error: Optional[str] = None
    recording_duration: float = 0.0
    processing_time: float = 0.0
    rtf: float = 0.0


class Pipeline:
    """
    语音识别流水线
    
    流程:
    1. AudioSource - 音频流管理
    2. Recorder - 录音控制
    3. Processor - 音频预处理
    4. ModelEngine - 语音识别
    5. OutputHandler - 输出处理
    6. Clipboard - 文本粘贴
    
    各组件完全解耦，可独立替换和优化
    """
    
    def __init__(self, config: Optional[PipelineConfig] = None):
        self.config = config or PipelineConfig()
        
        self.audio_source = AudioSource(self.config.audio)
        self.recorder = Recorder(
            sample_rate=self.config.audio.sample_rate,
            min_duration=0.3
        )
        self.processor = Processor(self.config.processor)
        self.model_engine = ModelEngine()
        self.output_handler = OutputHandler(self.config.output)
        self.clipboard = Clipboard(self.config.clipboard)
        
        self._is_initialized = False
        self._on_complete_callback: Optional[Callable] = None
        self.trace: Optional[DictationTrace] = None
    
    @property
    def is_initialized(self) -> bool:
        """是否已初始化"""
        return self._is_initialized
    
    @property
    def is_recording(self) -> bool:
        """是否正在录音"""
        return self.audio_source.is_recording
    
    def initialize(self) -> bool:
        """
        初始化流水线
        
        Returns:
            是否成功
        """
        if self._is_initialized:
            return True
        
        print(f"⏳ 加载模型：{self.config.model_name}...")
        logger.info(
            "流水线初始化：model=%s backend=%s language=%s",
            self.config.model_name,
            self.config.model_backend,
            self.config.language,
        )
        start = time.time()
        
        success = self.model_engine.load(
            model_path=self.config.model_path,
            model_name=self.config.model_name,
            backend=self.config.model_backend,
            language=self.config.language,
            n_threads=self.config.n_threads,
            cli_path=self.config.cli_path,
            transcription_timeout=self.config.transcription_timeout,
            use_vad=self.config.use_vad,
            vad_model=self.config.vad_model,
            initial_prompt=self.config.transcription_prompt,
            transcription_prompt=self.config.transcription_prompt,
            trace=self.trace,
        )
        
        if not success:
            print(f"❌ 模型加载失败")
            logger.error("流水线初始化失败：模型加载失败")
            return False
        
        load_time = time.time() - start
        print(f"✅ 模型加载完成 ({load_time:.2f}秒)")
        logger.info("流水线初始化完成：load_time=%.2fs", load_time)
        
        self._is_initialized = True
        return True
    
    def shutdown(self):
        """关闭流水线"""
        logger.info("流水线关闭")
        self.audio_source.close()
        self.model_engine.unload()
        self._is_initialized = False

    def ensure_backend_ready(self) -> bool:
        """确保识别后端已就绪"""
        if not self._is_initialized:
            return False
        logger.info("确保后端可用")
        return self.model_engine.ensure_loaded()

    def release_backend_resources(self) -> None:
        """释放识别后端资源"""
        if not self._is_initialized:
            return
        logger.info("释放后端资源")
        self.model_engine.release_resources()
    
    def _audio_callback(self, audio_chunk):
        """音频流数据回调 - 始终收集到音频源缓冲"""
        pass
    
    def start_recording(self) -> bool:
        """开始录音"""
        if not self._is_initialized:
            return False
        
        trace = self.trace
        if isinstance(trace, DictationTrace):
            logger.info("%s pipeline.start_recording begin", trace.prefix("pipeline"))
        if not self.audio_source.start(self.config.audio.device_name):
            return False
        
        logger.info("开始录音")
        self.audio_source.start_recording()
        self.recorder.start()
        if isinstance(trace, DictationTrace):
            logger.info("%s pipeline.start_recording done", trace.prefix("pipeline"))
        return True
    
    def stop_recording(self, paste_output: bool = True) -> PipelineResult:
        """
        停止录音并执行完整流程
        
        Returns:
            流水线结果
        """
        if not self.recorder.is_recording:
            return PipelineResult(
                success=False,
                error="未录音"
            )
        
        start_time = time.time()
        trace = self.trace
        if isinstance(trace, DictationTrace):
            logger.info("%s pipeline.stop_recording begin", trace.prefix("pipeline"))
        logger.info("停止录音并开始处理")
        
        try:
            self.recorder.stop()
            audio_data = self.audio_source.stop_recording()
            
            if audio_data is None or len(audio_data) == 0:
                logger.warning("停止录音后没有音频数据")
                return PipelineResult(
                    success=False,
                    error="没有录音数据"
                )

            duration = len(audio_data) / self.config.audio.sample_rate
            
            if duration < 0.3:
                logger.warning("录音太短：%.2fs", duration)
                return PipelineResult(
                    success=False,
                    error=f"录音太短 ({duration:.2f}秒)"
                )
            
            logger.info("开始预处理：duration=%.2fs samples=%s", duration, len(audio_data))
            if isinstance(trace, DictationTrace):
                logger.info("%s pre_process begin duration=%.2fs samples=%s", trace.prefix("pre_process"), duration, len(audio_data))
            process_start = time.time()
            processed_audio = self.processor.process(audio_data, self.config.audio.sample_rate)
            logger.info("预处理完成：elapsed=%.2fs", time.time() - process_start)
            if isinstance(trace, DictationTrace):
                logger.info("%s pre_process done elapsed=%.2fs", trace.prefix("pre_process"), time.time() - process_start)
            
            transcribe_start = time.time()
            result = self.model_engine.transcribe(processed_audio, trace=trace)
            logger.info("模型转录完成：elapsed=%.2fs success=%s", time.time() - transcribe_start, result.success)
            if isinstance(trace, DictationTrace):
                logger.info("%s transcribe done elapsed=%.2fs success=%s", trace.prefix("transcribe"), time.time() - transcribe_start, result.success)
            
            output_start = time.time()
            output = self.output_handler.process(
                text=result.text,
                model=result.model_name,
                language=self.config.language,
                success=result.success,
                error=result.error
            )
            logger.info("输出处理完成：elapsed=%.2fs", time.time() - output_start)
            if isinstance(trace, DictationTrace):
                logger.info("%s output done elapsed=%.2fs", trace.prefix("output"), time.time() - output_start)
            
            if result.success and paste_output and self.config.output.auto_paste:
                paste_start = time.time()
                paste_ok = self.clipboard.insert(output.text)
                logger.info("自动粘贴完成：ok=%s elapsed=%.2fs", paste_ok, time.time() - paste_start)
                if isinstance(trace, DictationTrace):
                    logger.info("%s paste done ok=%s elapsed=%.2fs", trace.prefix("paste"), paste_ok, time.time() - paste_start)
            
            total_time = time.time() - start_time
            
            pipeline_result = PipelineResult(
                success=result.success,
                text=output.text if result.success else "",
                error=result.error if not result.success else None,
                recording_duration=duration,
                processing_time=total_time,
                rtf=result.rtf
            )
            
            if self._on_complete_callback:
                self._on_complete_callback(pipeline_result)
            if isinstance(trace, DictationTrace):
                logger.info("%s pipeline.stop_recording done total=%.2fs success=%s", trace.prefix("pipeline"), total_time, result.success)
            
            return pipeline_result
        except Exception as e:
            logger.exception("stop_recording 处理异常")
            if isinstance(trace, DictationTrace):
                logger.exception("%s pipeline.stop_recording exception", trace.prefix("pipeline"))
            return PipelineResult(
                success=False,
                error=str(e),
                processing_time=time.time() - start_time
            )
        finally:
            # ffmpeg 音频流由 AudioSource 自己管理空闲释放，
            # 这里只确保录音状态被正确停止。
            if self.audio_source.is_recording:
                logger.info("finally: 录音状态未正确停止，强制停止收集")
                self.audio_source.stop_recording()
    
    def set_on_complete(self, callback: Callable):
        """设置完成回调"""
        self._on_complete_callback = callback
    
    def get_status(self) -> dict:
        """获取流水线状态"""
        backend_status = self.model_engine.get_backend_status()
        return {
            'initialized': self._is_initialized,
            'recording': self.audio_source.is_recording,
            'model_loaded': self.model_engine.is_loaded,
            'model_name': self.model_engine.model_name,
            'sample_rate': self.config.audio.sample_rate,
            'language': self.config.language,
            'backend': backend_status.get('backend', ''),
            'backend_healthy': backend_status.get('healthy', False),
            'backend_detail': backend_status.get('detail', '')
        }
