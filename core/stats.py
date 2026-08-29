"""从本地 perf.jsonl 汇总听写统计。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import math
import os
from typing import Iterable


@dataclass(frozen=True)
class StatsSummary:
    total: int = 0
    successful: int = 0
    failed: int = 0
    total_audio_seconds: float = 0.0
    average_processing_seconds: float = 0.0
    average_rtf: float = 0.0
    average_first_char_ms: float = 0.0
    total_text_chars: int = 0
    last_7_days: tuple[tuple[str, int], ...] = ()

    @property
    def success_rate(self) -> float:
        return self.successful / self.total if self.total else 0.0


def load_perf_records(path: str) -> list[dict]:
    if not path or not os.path.exists(path):
        return []
    records: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if isinstance(value, dict):
                    records.append(value)
    except OSError:
        return []
    return records


def _coerce_success(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return math.isfinite(float(value)) and bool(value)
        except (TypeError, ValueError, OverflowError):
            return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def summarize_perf_records(records: Iterable[dict], now: datetime | None = None) -> StatsSummary:
    rows = [row for row in records if isinstance(row, dict)]
    total = len(rows)
    successful = sum(1 for row in rows if _coerce_success(row.get("success")))
    failed = total - successful

    def number(name: str) -> list[float]:
        values: list[float] = []
        for row in rows:
            try:
                value = float(row.get(name, 0.0))
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and value >= 0:
                values.append(value)
        return values

    processing = number("processing_s")
    rtf = number("rtf")
    first_char = [value for value in number("first_char_ms") if value > 0]
    text_chars = number("text_len")

    reference = now or datetime.now()
    reference_timezone = reference.tzinfo
    if reference_timezone is not None:
        reference = reference.astimezone(reference_timezone).replace(tzinfo=None)
    cutoff = reference - timedelta(days=6)
    days = Counter()
    for row in rows:
        raw_ts = row.get("ts")
        try:
            timestamp = datetime.fromisoformat(str(raw_ts))
        except (TypeError, ValueError):
            continue
        if timestamp.tzinfo is not None:
            # perf.jsonl historically used local naive timestamps; accept newer
            # aware records too, normalizing them to the reference local clock
            # before comparing so a mixed file cannot crash the stats window.
            timestamp = timestamp.astimezone(reference_timezone).replace(tzinfo=None)
        if timestamp >= cutoff:
            days[timestamp.date().isoformat()] += 1
    day_values = tuple(
        (
            (reference.date() - timedelta(days=offset)).isoformat(),
            days.get((reference.date() - timedelta(days=offset)).isoformat(), 0),
        )
        for offset in range(6, -1, -1)
    )

    return StatsSummary(
        total=total,
        successful=successful,
        failed=failed,
        total_audio_seconds=sum(number("duration_s")),
        average_processing_seconds=sum(processing) / len(processing) if processing else 0.0,
        average_rtf=sum(rtf) / len(rtf) if rtf else 0.0,
        average_first_char_ms=sum(first_char) / len(first_char) if first_char else 0.0,
        total_text_chars=int(sum(text_chars)),
        last_7_days=day_values,
    )


def format_stats(summary: StatsSummary) -> str:
    days = "  ".join(f"{day[5:]}: {count}" for day, count in summary.last_7_days)
    return (
        f"听写次数：{summary.total}\n"
        f"成功率：{summary.success_rate:.1%}（成功 {summary.successful}，失败 {summary.failed}）\n"
        f"累计录音：{summary.total_audio_seconds / 60:.1f} 分钟\n"
        f"累计输出：{summary.total_text_chars} 字符\n"
        f"平均处理耗时：{summary.average_processing_seconds:.2f} 秒\n"
        f"平均 RTF：{summary.average_rtf:.2f}x\n"
        f"平均首字延迟：{summary.average_first_char_ms:.0f} ms\n\n"
        f"最近 7 天：\n{days}"
    )
