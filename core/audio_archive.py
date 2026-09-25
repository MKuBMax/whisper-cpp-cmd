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


class AudioArchive:
    """以 WAV + JSON 保存最近十次实际识别请求，文件只对当前用户可读。"""

    def __init__(self, directory: str | None = None, max_records: int = MAX_AUDIO_RECORDINGS):
        self.directory = directory or audio_archive_dir()
        self.max_records = max(1, int(max_records))

    def save_pending(self, audio: np.ndarray, sample_rate: int, metadata: dict) -> AudioArchiveEntry:
        """先持久化将要发送的 WAV，并写入待完成元数据。"""
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        os.chmod(self.directory, 0o700)

        now = datetime.now().astimezone()
        recording_id = f"{now.strftime('%Y%m%d-%H%M%S-%f')}-{uuid.uuid4().hex[:8]}"
        audio_path = os.path.join(self.directory, f"{recording_id}.wav")
        metadata_path = os.path.join(self.directory, f"{recording_id}.json")
        audio_array = np.asarray(audio, dtype=np.float32)
        sample_rate = int(sample_rate)
        if sample_rate <= 0:
            raise ValueError("sample_rate 必须大于 0")

        try:
            self._write_wav_atomic(audio_path, audio_array, sample_rate)
            record = {
                **metadata,
                "schema_version": 1,
                "recording_id": recording_id,
                "created_at": now.isoformat(timespec="milliseconds"),
                "status": "pending",
                "audio_file": os.path.basename(audio_path),
                "sample_rate": sample_rate,
                "sample_count": int(audio_array.size),
                "duration_seconds": round(audio_array.size / sample_rate, 3),
            }
            self._write_json_atomic(metadata_path, record)
        except Exception:
            for path in (audio_path, metadata_path):
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
            raise

        try:
            self._prune_old_recordings()
        except OSError:
            logger.warning("清理旧语音记录失败", exc_info=True)
        logger.info("已归档识别音频：id=%s duration=%.2fs", recording_id, record["duration_seconds"])
        return AudioArchiveEntry(recording_id, audio_path, metadata_path)

    def finish(self, entry: AudioArchiveEntry, metadata: dict) -> None:
        """补全识别状态、文本和耗时。"""
        with open(entry.metadata_path, "r", encoding="utf-8") as stream:
            record = json.load(stream)
        record.update(metadata)
        record["completed_at"] = datetime.now().astimezone().isoformat(timespec="milliseconds")
        self._write_json_atomic(entry.metadata_path, record)

    def list_recent(self) -> list[dict]:
        """返回最近记录及对应 WAV 路径，跳过缺少配对文件的记录。"""
        try:
            names = os.listdir(self.directory)
        except FileNotFoundError:
            return []

        records = []
        for name in names:
            if not name.endswith(".wav"):
                continue
            audio_path = os.path.join(self.directory, name)
            metadata_path = os.path.splitext(audio_path)[0] + ".json"
            if not os.path.isfile(metadata_path):
                continue
            try:
                with open(metadata_path, "r", encoding="utf-8") as stream:
                    record = json.load(stream)
                if not isinstance(record, dict):
                    continue
                record["audio_path"] = audio_path
                record["metadata_path"] = metadata_path
                record["_modified_at"] = os.path.getmtime(audio_path)
                records.append(record)
            except (OSError, UnicodeError, json.JSONDecodeError):
                logger.warning("读取语音记录元数据失败：%s", metadata_path, exc_info=True)

        records.sort(key=lambda record: record["_modified_at"], reverse=True)
        for record in records:
            record.pop("_modified_at", None)
        return records[:self.max_records]

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
        wav_names = [
            name for name in os.listdir(self.directory)
            if name.endswith(".wav") and os.path.isfile(os.path.join(self.directory, name))
        ]
        wav_names.sort(key=lambda name: os.path.getmtime(os.path.join(self.directory, name)))
        for old_name in wav_names[:-self.max_records]:
            stem = os.path.splitext(old_name)[0]
            for suffix in (".wav", ".json"):
                try:
                    os.remove(os.path.join(self.directory, stem + suffix))
                except FileNotFoundError:
                    pass
                except OSError:
                    logger.warning("清理旧语音记录失败：%s", stem + suffix, exc_info=True)
