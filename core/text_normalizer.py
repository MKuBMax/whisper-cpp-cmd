#!/usr/bin/env python3
"""
文本规范化工具 - 处理中文简繁转换
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional


logger = logging.getLogger(__name__)

try:
    from opencc import OpenCC
except ImportError:  # pragma: no cover - optional dependency
    OpenCC = None


@lru_cache(maxsize=4)
def _get_converter(config_name: str):
    if OpenCC is None:
        return None

    try:
        return OpenCC(config_name)
    except Exception as e:
        logger.warning("初始化 OpenCC 失败：%s (%s)", config_name, e)
        return None


def normalize_chinese_script(text: str, script: Optional[str]) -> str:
    """
    规范中文脚本输出。

    Args:
        text: 原始文本
        script: simplified/traditional/auto/None

    Returns:
        规范后的文本
    """
    if not text:
        return text

    mode = (script or "auto").strip().lower()
    if mode in {"auto", "none", ""}:
        return text

    if OpenCC is None:
        logger.warning("未安装 opencc，无法执行中文脚本转换")
        return text

    config_name = "t2s" if mode == "simplified" else "s2t" if mode == "traditional" else None
    if config_name is None:
        logger.warning("未知中文脚本模式：%s", script)
        return text

    converter = _get_converter(config_name)
    if converter is None:
        return text

    try:
        converted = converter.convert(text)
        logger.info("中文脚本转换：%s -> %s，len=%s", mode, config_name, len(text))
        return converted
    except Exception as e:
        logger.warning("中文脚本转换失败：%s", e)
        return text
