#!/usr/bin/env python3
"""
输出处理模块 - 文本后处理和格式化
"""

from typing import Optional, Callable
from dataclasses import dataclass
from datetime import datetime
import json
import os
import logging

from .text_normalizer import normalize_chinese_script
from .dictation_trace import DictationTrace


logger = logging.getLogger(__name__)


@dataclass
class OutputConfig:
    """输出配置"""
    auto_paste: bool = True
    save_history: bool = True
    history_file: str = ""
    history_max_entries: int = 100
    verbose: bool = True
    chinese_script: str = "simplified"


@dataclass
class TextOutput:
    """文本输出"""
    text: str
    model: str
    language: str
    timestamp: datetime
    success: bool
    error: Optional[str] = None


class OutputHandler:
    """
    输出处理器 - 处理转录结果
    
    职责:
    - 文本格式化
    - 历史记录保存
    - 输出回调
    - 日志记录
    """
    
    def __init__(self, config: Optional[OutputConfig] = None):
        self.config = config or OutputConfig()
        self._on_output_callback: Optional[Callable] = None
        self.trace: Optional[DictationTrace] = None
    
    def process(self, text: str, model: str, language: str, success: bool, error: Optional[str] = None) -> TextOutput:
        """
        处理输出
        
        Args:
            text: 转录文本
            model: 使用的模型
            language: 识别语言
            success: 是否成功
            error: 错误信息
        
        Returns:
            文本输出对象
        """
        normalized_text = normalize_chinese_script(text, self.config.chinese_script)

        output = TextOutput(
            text=normalized_text,
            model=model,
            language=language,
            timestamp=datetime.now(),
            success=success,
            error=error
        )
        if isinstance(self.trace, DictationTrace):
            logger.info(
                "%s output.process begin success=%s text_len=%s",
                self.trace.prefix("output"),
                success,
                len(normalized_text),
            )
        
        if self.config.verbose:
            self._log_output(output)
        
        if success and self.config.save_history:
            self._save_to_history(output)
        
        if self._on_output_callback:
            self._on_output_callback(output)
        if isinstance(self.trace, DictationTrace):
            logger.info(
                "%s output.process done success=%s text_len=%s",
                self.trace.prefix("output"),
                success,
                len(normalized_text),
            )
        
        return output
    
    def _log_output(self, output: TextOutput):
        """日志输出"""
        if output.success:
            print(f"✅ 「{output.text}」")
        else:
            print(f"❌ {output.error}")
    
    def _save_to_history(self, output: TextOutput):
        """保存到历史记录"""
        if not self.config.history_file:
            return
        
        history = []
        if os.path.exists(self.config.history_file):
            try:
                with open(self.config.history_file, 'r', encoding='utf-8') as f:
                    history = json.load(f)
            except Exception as e:
                logger.warning("读取历史文件失败：%s", e)
                history = []
        
        history.append({
            "timestamp": output.timestamp.isoformat(),
            "text": output.text,
            "model": output.model,
            "language": output.language
        })
        
        history = history[-self.config.history_max_entries:]

        os.makedirs(os.path.dirname(self.config.history_file), exist_ok=True)
        # 原子写：先写 .tmp 再 fsync 后 os.replace，保证写一半崩溃时旧文件仍完整可读
        tmp_path = self.config.history_file + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, self.config.history_file)
        logger.info("写入历史记录：count=%s file=%s", len(history), self.config.history_file)
    
    def set_on_output(self, callback: Callable):
        """设置输出回调"""
        self._on_output_callback = callback
    
    def get_history(self, count: int = 10) -> list:
        """获取历史记录"""
        if not os.path.exists(self.config.history_file):
            return []
        
        try:
            with open(self.config.history_file, 'r', encoding='utf-8') as f:
                history = json.load(f)
            return history[-count:]
        except Exception as e:
            logger.warning("读取历史记录失败：%s", e)
            return []
