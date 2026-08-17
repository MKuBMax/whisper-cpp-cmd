"""C4: VAD 单测。

覆盖：模型自动下载（成功/失败）、路径解析（已有/下载/无路径）、
_build_server_cmd 在 use_vad 时正确追加 --vad / 缺失时跳过。
全部用 mock，不触网络、不启服务。
"""

import io
import os
import urllib.request

from core.model import WhisperCliBackend


def _make_backend(use_vad=False, vad_model=""):
    b = WhisperCliBackend("/fake/whisper-cli", use_vad=use_vad, vad_model=vad_model)
    b._model_path = "/fake/models/ggml-large-v3.bin"
    b._server_port = 9999
    return b


# ---------------- _download_vad_model ----------------

def test_download_success(monkeypatch, tmp_path):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"fake-silero"))
    b = _make_backend()
    dest = str(tmp_path / "silero.bin")
    assert b._download_vad_model(dest) is True
    assert open(dest, "rb").read() == b"fake-silero"
    assert not os.path.exists(dest + ".tmp")  # 原子写：无残留


def test_download_failure_cleans_tmp(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    b = _make_backend()
    dest = str(tmp_path / "silero.bin")
    assert b._download_vad_model(dest) is False
    assert not os.path.exists(dest)
    assert not os.path.exists(dest + ".tmp")


# ---------------- _resolve_vad_model ----------------

def test_resolve_uses_existing(tmp_path):
    dest = tmp_path / "silero.bin"
    dest.write_bytes(b"x")
    b = _make_backend(vad_model=str(dest))
    assert b._resolve_vad_model() == str(dest)


def test_resolve_downloads_when_missing(monkeypatch, tmp_path):
    dest = tmp_path / "silero.bin"
    b = _make_backend(vad_model=str(dest))
    monkeypatch.setattr(b, "_download_vad_model", lambda d: (open(d, "wb").write(b"x"), True)[1])
    assert b._resolve_vad_model() == str(dest)


def test_resolve_returns_none_when_download_fails(monkeypatch, tmp_path):
    b = _make_backend(vad_model=str(tmp_path / "silero.bin"))
    monkeypatch.setattr(b, "_download_vad_model", lambda d: False)
    assert b._resolve_vad_model() is None


def test_resolve_returns_none_when_no_model_path():
    b = WhisperCliBackend("/fake/whisper-cli")  # _model_path=None, vad_model=''
    assert b._resolve_vad_model() is None


# ---------------- _build_server_cmd ----------------

def test_build_cmd_includes_vad_flags(monkeypatch):
    b = _make_backend(use_vad=True)
    monkeypatch.setattr(b, "_resolve_vad_model", lambda: "/fake/silero.bin")
    cmd = b._build_server_cmd()
    assert "--vad" in cmd
    assert "--vad-model" in cmd
    assert cmd[cmd.index("--vad-model") + 1] == "/fake/silero.bin"


def test_build_cmd_skips_vad_when_model_unavailable(monkeypatch):
    b = _make_backend(use_vad=True)
    monkeypatch.setattr(b, "_resolve_vad_model", lambda: None)
    cmd = b._build_server_cmd()
    assert "--vad" not in cmd


def test_build_cmd_no_vad_when_disabled():
    b = _make_backend(use_vad=False)
    cmd = b._build_server_cmd()
    assert "--vad" not in cmd
