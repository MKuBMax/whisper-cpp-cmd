"""最近送入识别引擎的音频归档。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import logging
import os
import tempfile
import uuid
import wave

import numpy as np

from config.paths import audio_archive_dir


logger = logging.getLogger(__name__)
MAX_AUDIO_RECORDINGS = 10


@dataclass(frozen=True)
class AudioArchiveEntry:
    recording_id: str
    audio_path: str
    metadata_path: str
    original_audio_path: str | None = None
    vad_audio_path: str | None = None


class AudioArchive:
    """以 WAV + JSON 保存最近十次实际识别请求，文件只对当前用户可读。"""

    def __init__(self, directory: str | None = None, max_records: int = MAX_AUDIO_RECORDINGS):
        self.directory = directory or audio_archive_dir()
        self.max_records = max(1, int(max_records))

    def save_pending(
        self,
        audio: np.ndarray,
        sample_rate: int,
        metadata: dict,
        *,
        original_audio: np.ndarray | None = None,
        vad_audio: np.ndarray | None = None,
        vad_segments: list[tuple[float, float]] | tuple[tuple[float, float], ...] | None = None,
    ) -> AudioArchiveEntry:
        """保存原始、送入引擎和可选 VAD 后的 WAV，并写入待完成元数据。

        ``audio`` 保持为送入 whisper-server 的 WAV（也就是服务端 VAD 之前
        的输入），这样现有的 ``audio_path`` 语义和旧记录完全兼容。
        """
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        os.chmod(self.directory, 0o700)

        now = datetime.now().astimezone()
        recording_id = f"{now.strftime('%Y%m%d-%H%M%S-%f')}-{uuid.uuid4().hex[:8]}"
        audio_path = os.path.join(self.directory, f"{recording_id}.wav")
        original_audio_path = os.path.join(self.directory, f"{recording_id}.original.wav")
        vad_audio_path = (
            os.path.join(self.directory, f"{recording_id}.vad.wav")
            if vad_audio is not None
            else None
        )
        metadata_path = os.path.join(self.directory, f"{recording_id}.json")
        audio_array = np.asarray(audio, dtype=np.float32)
        original_array = np.asarray(
            audio if original_audio is None else original_audio,
            dtype=np.float32,
        )
        vad_array = np.asarray(vad_audio, dtype=np.float32) if vad_audio is not None else None
        sample_rate = int(sample_rate)
        if sample_rate <= 0:
            raise ValueError("sample_rate 必须大于 0")

        paths = [audio_path, original_audio_path]
        if vad_audio_path is not None:
            paths.append(vad_audio_path)
        try:
            self._write_wav_atomic(audio_path, audio_array, sample_rate)
            self._write_wav_atomic(original_audio_path, original_array, sample_rate)
            if vad_audio_path is not None and vad_array is not None:
                self._write_wav_atomic(vad_audio_path, vad_array, sample_rate)
            record = {
                **metadata,
                "schema_version": 2,
                "recording_id": recording_id,
                "created_at": now.isoformat(timespec="milliseconds"),
                "status": "pending",
                "audio_file": os.path.basename(audio_path),
                "original_audio_file": os.path.basename(original_audio_path),
                "vad_audio_file": (
                    os.path.basename(vad_audio_path) if vad_audio_path is not None else None
                ),
                "sample_rate": sample_rate,
                "sample_count": int(audio_array.size),
                "duration_seconds": round(audio_array.size / sample_rate, 3),
                "original_sample_count": int(original_array.size),
                "original_duration_seconds": round(original_array.size / sample_rate, 3),
                "vad_sample_count": int(vad_array.size) if vad_array is not None else None,
                "vad_duration_seconds": (
                    round(vad_array.size / sample_rate, 3) if vad_array is not None else None
                ),
                "vad_segment_count": len(vad_segments or ()),
                "vad_segments": [
                    [round(float(start), 3), round(float(end), 3)]
                    for start, end in (vad_segments or ())
                ],
            }
            self._write_json_atomic(metadata_path, record)
        except Exception:
            for path in (*paths, metadata_path):
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
            raise

        try:
            self._prune_old_recordings()
        except OSError:
            logger.warning("清理旧语音记录失败", exc_info=True)
        logger.info(
            "已归档识别音频：id=%s duration=%.2fs original=%.2fs vad=%s",
            recording_id,
            record["duration_seconds"],
            record["original_duration_seconds"],
            record["vad_duration_seconds"],
        )
        return AudioArchiveEntry(
            recording_id,
            audio_path,
            metadata_path,
            original_audio_path,
            vad_audio_path,
        )

    def finish(self, entry: AudioArchiveEntry, metadata: dict) -> None:
        """补全识别状态、文本和耗时。"""
        with open(entry.metadata_path, "r", encoding="utf-8") as stream:
            record = json.load(stream)
        record.update(metadata)
        record["completed_at"] = datetime.now().astimezone().isoformat(timespec="milliseconds")
        self._write_json_atomic(entry.metadata_path, record)

    def list_recent(self) -> list[dict]:
        """返回最近记录及所有可用音频路径，兼容旧的 WAV/JSON 对。"""
        try:
            names = os.listdir(self.directory)
        except FileNotFoundError:
            return []

        records = []
        for name in names:
            if not name.endswith(".json"):
                continue
            metadata_path = os.path.join(self.directory, name)
            stem = os.path.splitext(name)[0]
            try:
                with open(metadata_path, "r", encoding="utf-8") as stream:
                    record = json.load(stream)
                if not isinstance(record, dict):
                    continue
                audio_path = self._record_audio_path(
                    record.get("audio_file"), f"{stem}.wav"
                )
                if not audio_path or not os.path.isfile(audio_path):
                    continue
                original_audio_path = self._record_audio_path(
                    record.get("original_audio_file"), f"{stem}.original.wav"
                )
                vad_audio_path = self._record_audio_path(
                    record.get("vad_audio_file"),
                    None if "vad_audio_file" in record else f"{stem}.vad.wav",
                )
                record["audio_path"] = audio_path
                record["processed_audio_path"] = audio_path
                if original_audio_path and os.path.isfile(original_audio_path):
                    record["original_audio_path"] = original_audio_path
                else:
                    record.pop("original_audio_path", None)
                if vad_audio_path and os.path.isfile(vad_audio_path):
                    record["vad_audio_path"] = vad_audio_path
                else:
                    record.pop("vad_audio_path", None)
                record["metadata_path"] = metadata_path
                # Keep the original recording order; ``finish`` rewrites JSON
                # later and must not make a long-running older recording look
                # newer than the WAV that was captured afterward.
                record["_modified_at"] = os.path.getmtime(audio_path)
                records.append(record)
            except (OSError, UnicodeError, json.JSONDecodeError):
                logger.warning("读取语音记录元数据失败：%s", metadata_path, exc_info=True)

        records.sort(key=lambda record: record["_modified_at"], reverse=True)
        for record in records:
            record.pop("_modified_at", None)
        return records[:self.max_records]

    def _record_audio_path(self, filename: object, fallback: str | None) -> str | None:
        """Resolve metadata file names inside the archive directory only."""
        candidate = filename if isinstance(filename, str) and filename.strip() else fallback
        if not candidate:
            return None
        candidate = os.path.basename(candidate)
        path = os.path.join(self.directory, candidate)
        return path if os.path.isfile(path) else None

    def get_recording(self, recording_id: str) -> dict | None:
        """按归档 ID 读取一条仍在保留期内的记录。"""
        for record in self.list_recent():
            if record.get("recording_id") == recording_id:
                return record
        return None

    def _write_wav_atomic(self, destination: str, audio: np.ndarray, sample_rate: int) -> None:
        fd, temporary_path = tempfile.mkstemp(
            prefix=".audio-", suffix=".wav.tmp", dir=self.directory
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                with wave.open(stream, "wb") as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2)
                    wav_file.setframerate(int(sample_rate))
                    audio_int16 = (audio * 32767).astype(np.int16)
                    wav_file.writeframes(audio_int16.tobytes())
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, destination)
        except Exception:
            try:
                os.remove(temporary_path)
            except FileNotFoundError:
                pass
            raise

    def _write_json_atomic(self, destination: str, record: dict) -> None:
        fd, temporary_path = tempfile.mkstemp(
            prefix=".metadata-", suffix=".json.tmp", dir=self.directory
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, destination)
        except Exception:
            try:
                os.remove(temporary_path)
            except FileNotFoundError:
                pass
            raise

    def _prune_old_recordings(self) -> None:
        records = []
        for name in os.listdir(self.directory):
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.directory, name)
            if os.path.isfile(path):
                records.append((os.path.getmtime(path), name))
        records.sort()
        for _modified_at, metadata_name in records[:-self.max_records]:
            metadata_path = os.path.join(self.directory, metadata_name)
            stem = os.path.splitext(metadata_name)[0]
            files = {
                metadata_name,
                f"{stem}.wav",
                f"{stem}.original.wav",
                f"{stem}.vad.wav",
            }
            try:
                with open(metadata_path, "r", encoding="utf-8") as stream:
                    record = json.load(stream)
                if isinstance(record, dict):
                    for field in ("audio_file", "original_audio_file", "vad_audio_file"):
                        filename = record.get(field)
                        if isinstance(filename, str) and filename.strip():
                            files.add(os.path.basename(filename))
            except (OSError, UnicodeError, json.JSONDecodeError):
                logger.warning("读取待清理语音记录失败：%s", metadata_path, exc_info=True)

            for filename in files:
                try:
                    os.remove(os.path.join(self.directory, filename))
                except FileNotFoundError:
                    pass
                except OSError:
                    logger.warning(
                        "清理旧语音记录失败：%s",
                        os.path.join(self.directory, filename),
                        exc_info=True,
                    )
