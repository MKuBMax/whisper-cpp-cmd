"""Regressions for the simple dictation contract and first-run model setup."""
import io
import threading
from types import SimpleNamespace

from config.settings import Settings
from core.clipboard import Clipboard
from core.model_download import ModelDownload, RECOMMENDED_MODEL


def test_no_cursor_copies_without_sending_keys(monkeypatch):
    clipboard = Clipboard()
    copied = []
    monkeypatch.setattr(clipboard, "copy", lambda text: copied.append(text) or True)
    monkeypatch.setattr(clipboard, "editable_target", lambda: None)
    monkeypatch.setattr(clipboard, "_paste_with_cg_event", lambda: (_ for _ in ()).throw(AssertionError("no cursor")))
    clipboard.capture_target()
    assert not clipboard.insert("你好")
    assert copied == ["你好"]
    assert clipboard.last_delivery == "copied"


def test_changed_focus_never_pastes_into_new_app(monkeypatch):
    clipboard = Clipboard()
    monkeypatch.setattr(clipboard, "copy", lambda text: True)
    monkeypatch.setattr(clipboard, "editable_target", lambda: (12, "original"))
    clipboard.capture_target()
    monkeypatch.setattr(clipboard, "editable_target", lambda: (13, "other"))
    monkeypatch.setattr(clipboard, "_paste_with_cg_event", lambda: (_ for _ in ()).throw(AssertionError("focus changed")))
    assert not clipboard.insert("你好")
    assert clipboard.last_delivery == "copied"


def test_verified_cursor_gets_one_paste(monkeypatch):
    clipboard = Clipboard()
    sent = []
    monkeypatch.setattr(clipboard, "copy", lambda text: True)
    monkeypatch.setattr(clipboard, "editable_target", lambda: (12, "original"))
    monkeypatch.setattr("core.clipboard.CoreFoundation.CFEqual", lambda a, b: a == b)
    monkeypatch.setattr(clipboard, "_paste_with_cg_event", lambda: sent.append(True))
    clipboard.capture_target()
    assert clipboard.insert("你好 👋")
    assert sent == [True]
    assert clipboard.last_delivery == "sent"


def test_legacy_settings_migrate_to_release_to_transcribe(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"dictation_mode":"preview","show_floating_pill":true,"status_bar_show_title":true}')
    settings = Settings.load(str(path))
    assert settings.dictation_mode == "quick"
    assert not settings.show_floating_pill
    assert not settings.status_bar_show_title


def test_malformed_experience_version_still_uses_safe_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"experience_version":"broken","dictation_mode":"preview"}')
    settings = Settings.load(str(path))
    assert settings.experience_version == 1
    assert settings.dictation_mode == "quick"


def test_vad_and_partial_downloads_are_not_speech_models(tmp_path):
    for name in ("ggml-silero-v6.2.0.bin", "ggml-large.bin.part", "ggml-small.bin"):
        (tmp_path / name).write_bytes(b"model")
    assert Settings(models_dir=str(tmp_path)).list_available_models() == ["small"]


def test_empty_model_is_not_ready(tmp_path):
    (tmp_path / "ggml-large-v3.bin").write_bytes(b"")
    settings = Settings(models_dir=str(tmp_path))
    assert not settings.model_exists("large-v3")
    assert settings.list_available_models() == []


def test_truncated_download_never_installs_model(tmp_path, monkeypatch):
    class Response(io.BytesIO):
        headers = {"Content-Length": "100"}
    monkeypatch.setattr("core.model_download.urllib.request.urlopen", lambda *a, **k: Response(b"short"))
    downloader = ModelDownload()
    done = threading.Event()
    results = []
    downloader.start(tmp_path, lambda success: (results.append(success), done.set()))
    assert done.wait(3)
    assert results == [False]
    assert list(tmp_path.iterdir()) == []
    assert not downloader.active


def test_model_download_installs_atomically(tmp_path, monkeypatch):
    class Response(io.BytesIO):
        headers = {"Content-Length": "5"}
    monkeypatch.setattr("core.model_download.urllib.request.urlopen", lambda *a, **k: Response(b"model"))
    downloader = ModelDownload()
    done = threading.Event()
    results = []
    downloader.start(tmp_path, lambda success: (results.append(success), done.set()))
    assert done.wait(3)
    assert results == [True]
    assert (tmp_path / f"ggml-{RECOMMENDED_MODEL}.bin").read_bytes() == b"model"
    assert not list(tmp_path.glob("*.part"))


def test_cancelled_download_removes_partial_and_can_retry(tmp_path, monkeypatch):
    downloader = ModelDownload()
    class Response(io.BytesIO):
        headers = {"Content-Length": "5"}
        def read(self, size):
            downloader.cancel()
            return super().read(size)
    monkeypatch.setattr("core.model_download.urllib.request.urlopen", lambda *a, **k: Response(b"model"))
    done = threading.Event()
    results = []
    downloader.start(tmp_path, lambda success: (results.append(success), done.set()))
    assert done.wait(3)
    assert results == [False]
    assert not downloader.active
    assert list(tmp_path.iterdir()) == []


def test_chinese_script_menu_action_reaches_app():
    from ui.status_bar import StatusBarController
    selected = []
    controller = StatusBarController.alloc().init()
    controller.app = SimpleNamespace(select_chinese_script=selected.append)
    assert controller.respondsToSelector_("selectChineseScript:")
    controller.selectChineseScript_(SimpleNamespace(representedObject=lambda: "traditional"))
    assert selected == ["traditional"]
