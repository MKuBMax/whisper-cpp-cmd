"""Settings load/save 往返与健壮性单测（顺带覆盖 A2 原子写）。"""

import os

from config.settings import Settings


def test_defaults_when_file_missing(tmp_path):
    s = Settings.load(str(tmp_path / "nope.json"))
    assert s.current_model == "large-v3"
    assert s.language == "zh"


def test_round_trip(tmp_path):
    path = str(tmp_path / "config.json")
    s = Settings()
    s.current_model = "large-v3-turbo"
    s.language = "en"
    s.auto_paste = False
    s.audio_device_name = "外接麦克风"
    s.save(path)

    # 原子写：无 .tmp 残留
    assert not os.path.exists(path + ".tmp")

    loaded = Settings.load(path)
    assert loaded.current_model == "large-v3-turbo"
    assert loaded.language == "en"
    assert loaded.auto_paste is False
    assert loaded.audio_device_name == "外接麦克风"


def test_invalid_json_falls_back_to_defaults(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    s = Settings.load(str(bad))
    # 异常被吞掉并回退默认值
    assert s.current_model == "large-v3"


def test_glossary_terms_reads_filters_and_dedups(tmp_path):
    g = tmp_path / "glossary.txt"
    g.write_text(
        "# 这是注释\n"
        "WhisperCppCmd\n"
        "\n"
        "PyObjC\n"
        "# 另一条注释\n"
        "Karpathy\n"
        "PyObjC\n",  # 重复项应被去重
        encoding="utf-8",
    )
    s = Settings()
    s.glossary_file = str(g)
    assert s.get_glossary_terms() == ["WhisperCppCmd", "PyObjC", "Karpathy"]


def test_glossary_terms_missing_file_returns_empty(tmp_path):
    s = Settings()
    s.glossary_file = str(tmp_path / "nope.txt")
    assert s.get_glossary_terms() == []


def test_transcription_prompt_combines_style_and_glossary(tmp_path):
    g = tmp_path / "glossary.txt"
    g.write_text("WhisperCppCmd\nPyObjC\n", encoding="utf-8")
    s = Settings()
    s.glossary_file = str(g)
    prompt = s.get_transcription_prompt()
    assert "请使用中文标点符号输出" in prompt
    assert "WhisperCppCmd、PyObjC" in prompt


def test_transcription_prompt_style_only_when_no_glossary(tmp_path):
    s = Settings()
    s.glossary_file = str(tmp_path / "nope.txt")
    # 无术语表时只返回风格 prompt（与既有行为一致）
    assert s.get_transcription_prompt() == s.transcription_prompt.strip()


def test_load_coerces_unsafe_boolean_and_numeric_values(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        "{"
        '"update_check_enabled": "false", '
        '"onboarding_completed": "false", '
        '"n_threads": "not-a-number", '
        '"auto_release_minutes": -4, '
        '"current_model": "../outside"'
        "}",
        encoding="utf-8",
    )

    loaded = Settings.load(str(path))

    assert loaded.update_check_enabled is False
    assert loaded.onboarding_completed is False
    assert loaded.n_threads == 8
    assert loaded.auto_release_minutes == 0
    assert loaded.current_model == "large-v3"
    assert loaded.sample_rate == 16_000


def test_sample_rate_remains_fixed_for_legacy_config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"sample_rate": 48000}', encoding="utf-8")

    assert Settings.load(str(path)).sample_rate == 16_000


def test_save_creates_private_parent_and_uses_atomic_temp_files(tmp_path):
    path = tmp_path / "nested" / "config.json"
    Settings().save(str(path))

    assert path.exists()
    assert not list(path.parent.glob(".*.tmp-*"))
    assert path.stat().st_mode & 0o777 == 0o600


def test_show_in_dock_setting_default_and_roundtrip(tmp_path):
    s = Settings()
    assert s.show_in_dock is True
    assert s.show_floating_pill is False
    assert s.status_bar_show_title is False

    path = tmp_path / "dock_config.json"
    s.show_in_dock = False
    s.show_floating_pill = False
    s.status_bar_show_title = False
    s.save(str(path))

    loaded = Settings.load(str(path))
    assert loaded.show_in_dock is False
    assert loaded.show_floating_pill is False
    assert loaded.status_bar_show_title is False

