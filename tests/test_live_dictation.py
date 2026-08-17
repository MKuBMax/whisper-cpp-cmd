"""LiveDictationSession 纯逻辑单测：预览合并 / 后缀前缀重叠检测。

只覆盖不依赖音频/模型/剪贴板的纯字符串逻辑，给后续重构兜底。
"""

import pytest

from core.live_dictation import LiveDictationSession, LiveDictationConfig


@pytest.fixture
def session():
    # 三个依赖仅用于其它方法；被测方法只读 self.config
    return LiveDictationSession(None, None, None, LiveDictationConfig())


# ---------------- _find_suffix_prefix_overlap ----------------

class TestFindSuffixPrefixOverlap:
    def test_empty_left(self, session):
        assert session._find_suffix_prefix_overlap("", "abc") == 0

    def test_empty_right(self, session):
        assert session._find_suffix_prefix_overlap("abc", "") == 0

    def test_no_overlap(self, session):
        assert session._find_suffix_prefix_overlap("你好", "世界") == 0

    def test_suffix_is_prefix(self, session):
        # left 后缀 == right 前缀
        assert session._find_suffix_prefix_overlap("今天天气", "天气很好") == 2

    def test_complete_overlap(self, session):
        assert session._find_suffix_prefix_overlap("你好世界", "你好世界") == 4

    def test_max_overlap_chars_is_detection_cap_not_truncation(self, session):
        # 真实重叠为 3（"XYZ"）；max_overlap_chars 是检测上限，不是截断
        left, right = "abcXYZ", "XYZdef"
        # 默认（120）能检出完整重叠 3
        assert session._find_suffix_prefix_overlap(left, right) == 3
        # 上限 >= 真实重叠时检出 3
        session.config.max_overlap_chars = 3
        assert session._find_suffix_prefix_overlap(left, right) == 3
        # 上限 < 真实重叠时，更短长度探测不命中 → 返回 0（不会截断成更小值）
        session.config.max_overlap_chars = 2
        assert session._find_suffix_prefix_overlap(left, right) == 0


# ---------------- _merge_preview_text ----------------

class TestMergePreviewText:
    def test_empty_current_returns_transcript(self, session):
        assert session._merge_preview_text("", "你好") == "你好"

    def test_overlap_dedup(self, session):
        # current 后缀 == transcript 前缀，去重拼接
        assert session._merge_preview_text("今天天气", "天气很好") == "今天天气很好"

    def test_overlap_extends(self, session):
        assert session._merge_preview_text("你好世", "你好世界") == "你好世界"

    def test_no_overlap_short_current_transcript_longer(self, session):
        # current 短（< mutable_tail_chars=80），无重叠，transcript 更长 → 追加
        assert session._merge_preview_text("abc", "defgh") == "abcdefgh"

    def test_no_overlap_short_current_transcript_not_longer(self, session):
        # current 短，无重叠，transcript 不更长 → 保持 current（不退化）
        assert session._merge_preview_text("abc", "def") == "abc"

    def test_long_current_replaces_mutable_tail(self, session):
        # current > mutable_tail_chars，无重叠 → 保留稳定前缀 + 新 transcript
        session.config.mutable_tail_chars = 3
        current = "0123456789"  # len 10
        # stable_len = 10 - 3 = 7 → current[:7] + transcript
        assert session._merge_preview_text(current, "XYZ") == "0123456XYZ"

    def test_overlap_takes_priority_over_mutable_tail(self, session):
        # 有重叠时走重叠分支，不走 mutable_tail 截断
        session.config.mutable_tail_chars = 2
        assert session._merge_preview_text("今天天气", "天气很好") == "今天天气很好"
