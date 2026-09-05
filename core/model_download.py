"""A single, cancellable model download. Partial files never appear as models."""
from __future__ import annotations

import os
from pathlib import Path
import threading
import ssl
import time
import urllib.request

RECOMMENDED_MODEL = "large-v3-turbo-q5_0"
MODEL_URL = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{RECOMMENDED_MODEL}.bin"


class ModelDownload:
    def __init__(self):
        self.active = False
        self.message = ""
        self.progress = 0.0
        self._cancel = threading.Event()
        self._last_update_at = 0.0

    def cancel(self):
        self._cancel.set()

    def start(self, directory, completed, updated=None):
        if self.active:
            return False
        self.active = True
        self.message = "正在连接模型下载…"
        self.progress = 0.0
        self._cancel.clear()

        def work():
            path = None
            success = False
            try:
                target = Path(directory) / f"ggml-{RECOMMENDED_MODEL}.bin"
                target.parent.mkdir(parents=True, exist_ok=True)
                path = target.with_suffix(".bin.part")
                # Homebrew OpenSSL defaults point outside the standalone bundle.
                # Use the certificate bundle shipped by macOS on a fresh Mac.
                ca_file = "/etc/ssl/cert.pem" if Path("/etc/ssl/cert.pem").is_file() else None
                context = ssl.create_default_context(cafile=ca_file)
                with urllib.request.urlopen(MODEL_URL, timeout=20, context=context) as response, path.open("wb") as out:
                    total = int(response.headers.get("Content-Length", 0))
                    size = 0
                    while True:
                        if self._cancel.is_set():
                            raise InterruptedError("已取消下载，可随时重试")
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
                        size += len(chunk)
                        self.progress = size / total if total else 0
                        self.message = (f"正在下载 · {self.progress:.0%}" if total
                                        else f"正在下载 · {size / 1024**2:.0f} MB")
                        now = time.monotonic()
                        if updated is not None and now - self._last_update_at >= 0.25:
                            self._last_update_at = now
                            try:
                                updated()
                            except Exception:
                                # A closing window must never abort an otherwise
                                # valid model download.
                                pass
                    if size == 0 or (total and size != total):
                        raise OSError("模型下载不完整，请重试")
                if self._cancel.is_set():
                    raise InterruptedError("已取消下载，可随时重试")
                os.replace(path, target)
                success = True
                self.message = "下载完成，正在加载模型…"
            except InterruptedError as exc:
                self.message = str(exc)
            except Exception:
                self.message = "下载失败，请检查网络后重试，或导入本地模型。"
            finally:
                if path is not None:
                    path.unlink(missing_ok=True)
                self.active = False
                try:
                    completed(success)
                except Exception:
                    # Completion callbacks belong to the UI layer.  Keep the
                    # download state settled even if the UI is closing.
                    pass

        threading.Thread(target=work, name="ModelDownload", daemon=True).start()
        return True
