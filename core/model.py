#!/usr/bin/env python3
"""
模型引擎模块 - 语音识别推理
"""

import numpy as np
from typing import Optional, Protocol
from dataclasses import dataclass
import time
import os
import json
import socket
import subprocess
import tempfile
import uuid
import http.client
import mimetypes
import logging

from .dictation_trace import DictationTrace
from config.paths import logs_dir


logger = logging.getLogger(__name__)

# Silero VAD 模型（whisper-server --vad 用）；缺失时自动从这里下载
_SILERO_VAD_MODEL_URL = "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v6.2.0.bin"
_DEFAULT_VAD_MODEL_NAME = "ggml-silero-v6.2.0.bin"


@dataclass
class TranscriptionResult:
    """转录结果"""
    text: str
    duration: float
    processing_time: float
    model_name: str
    success: bool
    error: Optional[str] = None
    
    @property
    def rtf(self) -> float:
        """实时率 (Real Time Factor)"""
        if self.duration > 0:
            return self.processing_time / self.duration
        return 0.0


class ModelBackend(Protocol):
    """模型后端接口"""
    
    def load(self, model_path: str, **kwargs) -> bool:
        """加载模型"""
        ...
    
    def unload(self) -> None:
        """卸载模型"""
        ...
    
    def transcribe(self, audio: np.ndarray, **kwargs) -> str:
        """转录音频"""
        ...
    
    @property
    def is_loaded(self) -> bool:
        """模型是否已加载"""
        ...

    def get_health_status(self) -> dict:
        """获取后端健康状态"""
        ...

    def release_resources(self) -> None:
        """释放后端资源但保留必要配置"""
        ...

    def ensure_ready(self) -> bool:
        """确认后端可用；必要时执行轻量健康检查"""
        ...


class WhisperCliBackend:
    """whisper-cli 后端"""
    
    def __init__(self, cli_path: str, language: str = 'zh', n_threads: int = 8, transcription_timeout: float = 120.0, use_vad: bool = False, vad_model: str = ''):
        self.cli_path = cli_path
        self.language = language
        self.n_threads = n_threads
        self._transcription_timeout = transcription_timeout
        self._use_vad = use_vad
        self._vad_model = vad_model
        self._model_path: Optional[str] = None
        self._server_process: Optional[subprocess.Popen] = None
        self._server_log_path: Optional[str] = None
        self._server_bind_host = '0.0.0.0'
        self._server_client_host = '127.0.0.1'
        self._server_port: Optional[int] = None
        self._server_start_timeout = 45.0
        self._server_path = self._derive_server_path(cli_path)
        self._health_error: str = ""
        self._initial_prompt: str = ""
    
    @property
    def is_loaded(self) -> bool:
        return self._server_process is not None and self._server_process.poll() is None

    def get_health_status(self) -> dict:
        process_alive = self._server_process is not None and self._server_process.poll() is None
        detail = "运行中" if process_alive else (self._health_error or "未运行")
        return {
            'backend': 'whisper-server',
            'healthy': process_alive,
            'detail': detail,
            'port': self._server_port
        }
    
    def load(self, model_path: str, **kwargs) -> bool:
        if not os.path.exists(model_path):
            return False

        self._model_path = model_path
        self._server_bind_host = kwargs.get('server_host', '0.0.0.0')
        self._server_client_host = kwargs.get('server_client_host', '127.0.0.1')
        self._server_start_timeout = kwargs.get('server_start_timeout', 45.0)
        self._initial_prompt = kwargs.get('initial_prompt', '') or ''
        trace = kwargs.get('trace')

        if not self._server_path or not os.path.exists(self._server_path):
            self._health_error = f"未找到 whisper-server：{self._server_path}"
            print(f"❌ {self._health_error}")
            logger.error(self._health_error)
            self._model_path = None
            return False

        try:
            if isinstance(trace, DictationTrace):
                logger.info("%s backend.load begin path=%s", trace.prefix("backend"), model_path)
            self._start_server()
            if isinstance(trace, DictationTrace):
                logger.info("%s backend.load done port=%s", trace.prefix("backend"), self._server_port)
            return True
        except Exception as e:
            detail = self._read_server_log_tail()
            if detail:
                print(f"❌ whisper-server 启动失败：{e}\n{detail}")
            else:
                print(f"❌ whisper-server 启动失败：{e}")
            logger.exception("whisper-server 启动失败：%s", e)
            self._health_error = str(e)
            self._stop_server()
            self._model_path = None
            return False
    
    def unload(self) -> None:
        self._stop_server()
        self._model_path = None

    def release_resources(self) -> None:
        self._stop_server()
        self._health_error = "已释放"

    def ensure_ready(self) -> bool:
        if self._server_process is None or self._server_process.poll() is not None:
            self._health_error = self._health_error or "未运行"
            return False

        if self._server_port is None:
            self._health_error = "未分配端口"
            return False

        conn = None
        try:
            conn = http.client.HTTPConnection(
                self._server_client_host,
                self._server_port,
                timeout=1.0
            )
            conn.request('GET', '/')
            response = conn.getresponse()
            response.read()
            self._health_error = ""
            return True
        except Exception as e:
            self._health_error = f"whisper-server 健康检查失败：{e}"
            logger.warning(self._health_error)
            return False
        finally:
            if conn is not None:
                conn.close()
    
    def transcribe(self, audio: np.ndarray, **kwargs) -> str:
        import wave
        
        if self._model_path is None:
            raise RuntimeError("模型未加载")
        
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            audio_path = f.name
        
        try:
            with wave.open(audio_path, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                audio_int16 = (audio * 32767).astype(np.int16)
                wf.writeframes(audio_int16.tobytes())

            if self._server_process is None:
                raise RuntimeError("whisper-server 未运行")

            trace = kwargs.get('trace')
            if isinstance(trace, DictationTrace):
                logger.info("%s backend.transcribe begin samples=%s", trace.prefix("backend_transcribe"), len(audio))
            return self._transcribe_with_retry(audio_path, trace=trace)
            
        finally:
            if os.path.exists(audio_path):
                os.remove(audio_path)
    
    def _derive_server_path(self, cli_path: str) -> Optional[str]:
        if not cli_path:
            return None
        
        cli_name = os.path.basename(cli_path)
        if cli_name == 'whisper-cli':
            return os.path.join(os.path.dirname(cli_path), 'whisper-server')
        
        return None
    
    def _pick_free_port(self) -> int:
        # 某些 macOS 环境下，直接对 127.0.0.1 做 bind 会失败；
        # 这里改成先用通配地址申请空闲端口，再把端口交给 whisper-server 使用。
        for bind_host in ('0.0.0.0', ''):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.bind((bind_host, 0))
                    return sock.getsockname()[1]
            except OSError:
                continue

        raise RuntimeError("无法分配 whisper-server 端口")
    
    def _build_server_cmd(self) -> list:
        """构建 whisper-server 启动命令。"""
        cmd = [
            self._server_path,
            '-m', self._model_path,
            '--host', self._server_bind_host,
            '--port', str(self._server_port),
            '-l', self.language,
            '-t', str(self.n_threads),
            '-p', '1',
            '-nt',
            '-sns',  # suppress-nst：抑制静音/噪音段的非语音 token 幻觉（如「。」、「谢谢观看」），听写更干净；如觉误伤可删此 flag 回退
        ]
        if self._initial_prompt:
            cmd.extend(['--prompt', self._initial_prompt])
        if self._use_vad:
            vad_path = self._resolve_vad_model()
            if vad_path:
                cmd.extend(['--vad', '--vad-model', vad_path])
                logger.info("VAD 已启用：model=%s", vad_path)
            else:
                logger.warning("VAD 已启用但模型不可用，本次跳过 VAD")
        return cmd

    def _resolve_vad_model(self) -> Optional[str]:
        """返回 VAD 模型路径：已有则直接用，缺失则自动下载；不可用返回 None。"""
        path = self._vad_model
        if not path:
            models_dir = os.path.dirname(self._model_path) if self._model_path else ''
            if not models_dir:
                return None
            path = os.path.join(models_dir, _DEFAULT_VAD_MODEL_NAME)
        if os.path.exists(path):
            return path
        if self._download_vad_model(path):
            return path
        return None

    def _download_vad_model(self, dest_path: str) -> bool:
        """下载 Silero VAD 模型到 dest_path（原子写）。成功返回 True。"""
        import urllib.request
        import shutil
        tmp_path = dest_path + '.tmp'
        try:
            logger.info("下载 Silero VAD 模型：%s -> %s", _SILERO_VAD_MODEL_URL, dest_path)
            os.makedirs(os.path.dirname(dest_path) or '.', exist_ok=True)
            with urllib.request.urlopen(_SILERO_VAD_MODEL_URL, timeout=30) as resp, open(tmp_path, 'wb') as f:
                shutil.copyfileobj(resp, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, dest_path)
            logger.info("Silero VAD 模型下载完成：%s", dest_path)
            return True
        except Exception as e:
            logger.warning("下载 Silero VAD 模型失败：%s（本次禁用 VAD）", e)
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            return False

    def _start_server(self) -> None:
        if self._server_process is not None:
            return

        self._server_port = self._pick_free_port()
        log_dir = logs_dir()
        os.makedirs(log_dir, exist_ok=True)
        self._server_log_path = os.path.join(
            log_dir,
            f"whisper-server-{int(time.time())}.log"
        )
        log_file = open(self._server_log_path, 'a', encoding='utf-8')
        cmd = self._build_server_cmd()
        self._server_process = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=log_file,
            text=True
        )
        log_file.close()
        self._health_error = ""
        logger.info("启动 whisper-server：port=%s model=%s", self._server_port, self._model_path)

        deadline = time.time() + self._server_start_timeout
        while time.time() < deadline:
            if self._server_process.poll() is not None:
                raise RuntimeError(self._build_server_failure_message("进程提前退出"))
            
            try:
                conn = http.client.HTTPConnection(
                    self._server_client_host,
                    self._server_port,
                    timeout=0.5
                )
                conn.request('GET', '/')
                conn.getresponse()
                conn.close()
                self._health_error = ""
                logger.info("whisper-server 已就绪：port=%s", self._server_port)
                return
            except OSError:
                time.sleep(0.2)
        
        self._stop_server()
        raise RuntimeError(self._build_server_failure_message("启动超时"))
    
    def _stop_server(self) -> None:
        if self._server_process is None:
            self._server_port = None
            self._cleanup_server_log()
            return

        try:
            logger.info("停止 whisper-server：port=%s", self._server_port)
            self._server_process.terminate()
            self._server_process.wait(timeout=3)
        except Exception:
            self._server_process.kill()
            self._server_process.wait(timeout=3)
        finally:
            self._server_process = None
            self._server_port = None
            self._cleanup_server_log()
    
    def _transcribe_with_retry(self, audio_path: str, trace: Optional[DictationTrace] = None) -> str:
        try:
            logger.info("whisper-server 开始转录：audio=%s", os.path.basename(audio_path))
            if isinstance(trace, DictationTrace):
                logger.info("%s backend.transcribe request begin audio=%s", trace.prefix("backend_transcribe"), os.path.basename(audio_path))
            return self._transcribe_via_server(audio_path)
        except RuntimeError as first_error:
            self._health_error = str(first_error)
            logger.warning("whisper-server 首次转录失败，准备重启：%s", first_error)
            self._restart_server()
            try:
                return self._transcribe_via_server(audio_path)
            except RuntimeError as second_error:
                self._health_error = str(second_error)
                logger.exception("whisper-server 重试后仍失败：%s", second_error)
                raise RuntimeError(
                    f"首次请求失败：{first_error}\n重启后仍失败：{second_error}"
                ) from second_error
    
    def _restart_server(self) -> None:
        self._stop_server()
        self._start_server()
    
    def _transcribe_via_server(self, audio_path: str) -> str:
        boundary = f'----CodexWhisperBoundary{uuid.uuid4().hex}'
        mime_type = mimetypes.guess_type(audio_path)[0] or 'audio/wav'
        
        with open(audio_path, 'rb') as f:
            audio_bytes = f.read()
        
        body = bytearray()
        body.extend(self._multipart_field(boundary, 'file', os.path.basename(audio_path), mime_type, audio_bytes))
        body.extend(self._multipart_field(boundary, 'response_format', None, 'text/plain', b'json'))
        body.extend(f'--{boundary}--\r\n'.encode('utf-8'))
        
        try:
            conn = http.client.HTTPConnection(
                self._server_client_host,
                self._server_port,
                timeout=self._transcription_timeout
            )
            logger.info(
                "发送转录请求：port=%s bytes=%s",
                self._server_port,
                len(body)
            )
            conn.request(
                'POST',
                '/inference',
                body=bytes(body),
                headers={
                    'Content-Type': f'multipart/form-data; boundary={boundary}',
                    'Content-Length': str(len(body))
                }
            )
            response = conn.getresponse()
            payload = response.read().decode('utf-8', errors='replace')
            conn.close()
        except OSError as e:
            self._health_error = f"whisper-server 请求失败：{e}"
            self._stop_server()
            logger.exception("whisper-server 请求失败")
            raise RuntimeError(f"whisper-server 请求失败：{e}") from e

        if response.status >= 400:
            self._health_error = payload
            logger.warning("whisper-server 返回错误：status=%s payload=%s", response.status, payload[:500])
            return f"错误：{payload}"
        
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return payload.strip()
        
        if isinstance(data, dict):
            if data.get('error'):
                self._health_error = str(data['error'])
                logger.warning("whisper-server 业务错误：%s", data['error'])
                return f"错误：{data['error']}"
            if data.get('text') is not None:
                self._health_error = ""
                text = str(data['text']).strip()
                logger.info("whisper-server 转录完成：text_len=%s", len(text))
                return text

        self._health_error = ""
        text = payload.strip()
        logger.info("whisper-server 转录完成：text_len=%s", len(text))
        return text
    
    def _multipart_field(
        self,
        boundary: str,
        field_name: str,
        filename: Optional[str],
        content_type: str,
        payload: bytes
    ) -> bytes:
        parts = [f'--{boundary}\r\n']
        
        disposition = f'Content-Disposition: form-data; name="{field_name}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        parts.append(disposition + '\r\n')
        parts.append(f'Content-Type: {content_type}\r\n\r\n')
        
        header = ''.join(parts).encode('utf-8')
        return header + payload + b'\r\n'
    
    def _build_server_failure_message(self, reason: str) -> str:
        detail = self._read_server_log_tail()
        if detail:
            return f"{reason}\n{detail}"
        return reason
    
    def _read_server_log_tail(self, max_lines: int = 20) -> str:
        if not self._server_log_path or not os.path.exists(self._server_log_path):
            return ""
        
        try:
            with open(self._server_log_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
        except OSError:
            return ""
        
        tail = ''.join(lines[-max_lines:]).strip()
        if not tail:
            return ""
        
        return f"whisper-server 日志：\n{tail}"

    def _cleanup_server_log(self) -> None:
        if not self._server_log_path:
            return

        self._server_log_path = None
    
class ModelEngine:
    """
    模型引擎 - 语音识别推理引擎
    
    职责:
    - 管理模型生命周期
    - 执行推理
    - 支持多后端
    """
    
    def __init__(self, backend: Optional[ModelBackend] = None):
        self._backend: Optional[ModelBackend] = backend
        self._model_name: str = ""
        self._load_config: dict = {}
        self.trace: Optional[DictationTrace] = None
    
    @property
    def is_loaded(self) -> bool:
        return self._backend is not None and self._backend.is_loaded
    
    @property
    def model_name(self) -> str:
        return self._model_name

    def get_backend_status(self) -> dict:
        if self._backend is None:
            return {
                'backend': '',
                'healthy': False,
                'detail': '未初始化'
            }

        status_getter = getattr(self._backend, 'get_health_status', None)
        if callable(status_getter):
            return status_getter()

        return {
            'backend': type(self._backend).__name__,
            'healthy': self.is_loaded,
            'detail': '已加载' if self.is_loaded else '未加载'
        }
    
    def load(self, model_path: str, model_name: str, backend: str = 'whisper-cli', **kwargs) -> bool:
        """
        加载模型
        
        Args:
            model_path: 模型文件路径
            model_name: 模型名称
            backend: 后端类型 ('whisper-cli')
            **kwargs: 后端特定参数
        
        Returns:
            是否成功加载
        """
        logger.info(
            "模型引擎加载：backend=%s model=%s path=%s",
            backend,
            model_name,
            model_path,
        )
        self._load_config = {
            'model_path': model_path,
            'model_name': model_name,
            'backend': backend,
            **kwargs
        }

        if backend == 'whisper-cli':
            self._backend = WhisperCliBackend(
                cli_path=kwargs.get('cli_path', '/opt/homebrew/bin/whisper-cli'),
                language=kwargs.get('language', 'zh'),
                n_threads=kwargs.get('n_threads', 8),
                transcription_timeout=kwargs.get('transcription_timeout', 120.0),
                use_vad=kwargs.get('use_vad', False),
                vad_model=kwargs.get('vad_model', ''),
            )
        else:
            print(f"❌ 未知后端：{backend}")
            return False
        
        if self._backend.load(model_path, **kwargs):
            self._model_name = model_name
            return True
        
        return False
    
    def unload(self) -> None:
        """卸载模型"""
        if self._backend:
            logger.info("模型引擎卸载")
            self._backend.unload()
            self._model_name = ""

    def release_resources(self) -> None:
        """释放后端资源，保留重载配置"""
        if self._backend:
            logger.info("模型引擎释放资源")
            releaser = getattr(self._backend, 'release_resources', None)
            if callable(releaser):
                releaser()
            else:
                self._backend.unload()

    def interrupt_backend(self) -> None:
        """强制停止后端进程，供 watchdog 打断卡死的转录调用。

        杀掉 whisper-server 让 worker 阻塞在 conn.getresponse() 的请求抛异常，
        随后由 _transcribe_with_retry 的重试 / ensure_loaded 的被动重启恢复。
        不设置「已释放」状态，避免与 release_resources 语义混淆。
        """
        if self._backend is None:
            return
        stop = getattr(self._backend, '_stop_server', None)
        if callable(stop):
            logger.warning("模型引擎：强制停止后端进程（watchdog 自愈）")
            stop()

    def ensure_loaded(self) -> bool:
        """确保后端资源已加载"""
        if self.is_loaded:
            readiness_check = getattr(self._backend, 'ensure_ready', None)
            if not callable(readiness_check) or readiness_check():
                return True

            logger.warning("后端健康检查失败，准备重载")
            self.unload()

        if not self._load_config:
            return False

        model_path = self._load_config.get('model_path')
        model_name = self._load_config.get('model_name', self._model_name)
        backend = self._load_config.get('backend', 'whisper-cli')
        kwargs = {
            key: value
            for key, value in self._load_config.items()
            if key not in {'model_path', 'model_name', 'backend'}
        }
        return self.load(model_path=model_path, model_name=model_name, backend=backend, **kwargs)
    
    def transcribe(self, audio: np.ndarray, **kwargs) -> TranscriptionResult:
        """
        转录音频
        
        Args:
            audio: 音频数据
        
        Returns:
            转录结果
        """
        if not self.is_loaded:
            return TranscriptionResult(
                text="",
                duration=0.0,
                processing_time=0.0,
                model_name="",
                success=False,
                error="模型未加载"
            )

        start_time = time.time()
        logger.info("开始转录：samples=%s", len(audio))
        trace = kwargs.get('trace', self.trace)
        if isinstance(trace, DictationTrace):
            logger.info("%s model.transcribe begin samples=%s", trace.prefix("model"), len(audio))
        
        try:
            text = self._backend.transcribe(audio, trace=trace)
            processing_time = time.time() - start_time
            logger.info("转录结束：success=%s elapsed=%.2fs", not text.startswith("错误"), processing_time)
            if isinstance(trace, DictationTrace):
                logger.info("%s model.transcribe done success=%s elapsed=%.2fs", trace.prefix("model"), not text.startswith("错误"), processing_time)
            
            if text.startswith("错误"):
                return TranscriptionResult(
                    text="",
                    duration=0.0,
                    processing_time=processing_time,
                    model_name=self._model_name,
                    success=False,
                    error=text
                )
            
            return TranscriptionResult(
                text=text,
                duration=len(audio) / 16000,
                processing_time=processing_time,
                model_name=self._model_name,
                success=True
            )
            
        except Exception as e:
            logger.exception("转录异常")
            return TranscriptionResult(
                text="",
                duration=0.0,
                processing_time=time.time() - start_time,
                model_name=self._model_name,
                success=False,
                error=str(e)
            )
