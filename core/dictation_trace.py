#!/usr/bin/env python3
"""
听写链路追踪上下文。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import time
import uuid


@dataclass
class DictationTrace:
    trace_id: str
    started_at: float
    started_wall: datetime

    @classmethod
    def create(cls) -> "DictationTrace":
        return cls(
            trace_id=uuid.uuid4().hex[:8],
            started_at=time.perf_counter(),
            started_wall=datetime.now(),
        )

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started_at) * 1000.0

    def prefix(self, stage: str | None = None) -> str:
        stage_part = f" {stage}" if stage else ""
        return f"[dictation:{self.trace_id}{stage_part} +{self.elapsed_ms():.1f}ms]"
