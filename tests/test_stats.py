"""perf.jsonl 汇总测试。"""

from datetime import datetime

from core.stats import format_stats, summarize_perf_records


def test_summarize_perf_records():
    summary = summarize_perf_records(
        [
            {
                "ts": "2026-08-28T12:00:00",
                "success": True,
                "duration_s": 4,
                "processing_s": 1,
                "rtf": 0.25,
                "first_char_ms": 500,
                "text_len": 10,
            },
            {
                "ts": "2026-08-27T12:00:00",
                "success": False,
                "duration_s": 2,
                "processing_s": 2,
                "rtf": 1.0,
                "first_char_ms": None,
                "text_len": 0,
            },
        ],
        now=datetime(2026, 8, 28, 13, 0),
    )

    assert summary.total == 2
    assert summary.successful == 1
    assert summary.failed == 1
    assert summary.total_audio_seconds == 6
    assert summary.average_processing_seconds == 1.5
    assert summary.average_rtf == 0.625
    assert summary.average_first_char_ms == 500
    assert summary.total_text_chars == 10
    assert summary.last_7_days[-1] == ("2026-08-28", 1)
    assert summary.last_7_days[-2] == ("2026-08-27", 1)


def test_format_stats_is_readable():
    text = format_stats(summarize_perf_records([]))

    assert "听写次数：0" in text
    assert "最近 7 天" in text


def test_summarize_ignores_non_finite_metrics_and_string_false():
    summary = summarize_perf_records(
        [
            {
                "success": "false",
                "processing_s": "nan",
                "rtf": "inf",
                "duration_s": "-inf",
                "first_char_ms": "nan",
                "text_len": "nan",
            },
            {"success": "true", "processing_s": 2, "rtf": 0.5, "duration_s": 1},
            "not-a-record",
        ],
        now=datetime(2026, 8, 28, 13, 0),
    )

    assert summary.total == 2
    assert summary.successful == 1
    assert summary.failed == 1
    assert summary.average_processing_seconds == 2
    assert summary.average_rtf == 0.5


def test_summarize_accepts_timezone_aware_timestamps():
    summary = summarize_perf_records(
        [{"ts": "2026-08-28T12:00:00+08:00", "success": True}],
        now=datetime.fromisoformat("2026-08-28T13:00:00+08:00"),
    )

    assert summary.last_7_days[-1] == ("2026-08-28", 1)
