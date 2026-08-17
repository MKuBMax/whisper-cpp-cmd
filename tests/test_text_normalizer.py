"""normalize_chinese_script 分支单测。

覆盖 simplified/traditional/auto/None/未知模式 各分支。
opencc 未安装时整体跳过（保持纯逻辑测试的可移植性）。
"""

import pytest

from core.text_normalizer import normalize_chinese_script, OpenCC

pytestmark = pytest.mark.skipif(OpenCC is None, reason="opencc 未安装")

# 选用单字级别的简繁对，避免短语级转换的不确定性
TRADITIONAL = "漢字語言"
SIMPLIFIED = "汉字语言"


class TestNormalizeChineseScript:
    def test_empty_returns_empty(self):
        assert normalize_chinese_script("", "simplified") == ""

    @pytest.mark.parametrize("mode", ["auto", "none", "", None])
    def test_passthrough_modes(self, mode):
        assert normalize_chinese_script(TRADITIONAL, mode) == TRADITIONAL

    def test_simplified_t2s(self):
        assert normalize_chinese_script(TRADITIONAL, "simplified") == SIMPLIFIED

    def test_traditional_s2t(self):
        assert normalize_chinese_script(SIMPLIFIED, "traditional") == TRADITIONAL

    def test_case_insensitive(self):
        assert normalize_chinese_script(TRADITIONAL, "SIMPLIFIED") == SIMPLIFIED

    def test_unknown_mode_passthrough(self):
        assert normalize_chinese_script(TRADITIONAL, "klingon") == TRADITIONAL
