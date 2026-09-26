"""Recent audio archive keeps logical records, not individual WAV files."""

import json
import os

import numpy as np

from core.audio_archive import AudioArchive


def test_save_pending_writes_original_model_input_and_vad_audio(tmp_path):
    archive = AudioArchive(str(tmp_path), max_records=10)
    model_input = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    original = np.array([0.4, 0.5, 0.6, 0.7], dtype=np.float32)
    vad = np.array([0.4, 0.5], dtype=np.float32)

    entry = archive.save_pending(
        model_input,
        16_000,
        {"use_vad": True},
        original_audio=original,
        vad_audio=vad,
        vad_segments=((0.0, 0.5),),
    )

    assert os.path.isfile(entry.audio_path)
    assert os.path.isfile(entry.original_audio_path)
    assert os.path.isfile(entry.vad_audio_path)
    record = archive.get_recording(entry.recording_id)
    assert record is not None
    assert record["audio_path"] == entry.audio_path
    assert record["original_audio_path"] == entry.original_audio_path
    assert record["vad_audio_path"] == entry.vad_audio_path
    assert record["sample_count"] == 3
    assert record["original_sample_count"] == 4
    assert record["vad_sample_count"] == 2
    assert record["vad_segments"] == [[0.0, 0.5]]


def test_prune_keeps_ten_logical_records_and_all_track_files(tmp_path):
    archive = AudioArchive(str(tmp_path), max_records=2)
    entries = []
    for index in range(3):
        entries.append(
            archive.save_pending(
                np.full(4, index / 10, dtype=np.float32),
                16_000,
                {},
                original_audio=np.full(5, index / 10, dtype=np.float32),
                vad_audio=np.full(2, index / 10, dtype=np.float32),
            )
        )

    records = archive.list_recent()
    assert len(records) == 2
    assert not os.path.exists(entries[0].metadata_path)
    assert not os.path.exists(entries[0].audio_path)
    assert not os.path.exists(entries[0].original_audio_path)
    assert not os.path.exists(entries[0].vad_audio_path)
    for entry in entries[1:]:
        assert os.path.exists(entry.metadata_path)
        assert os.path.exists(entry.audio_path)
        assert os.path.exists(entry.original_audio_path)
        assert os.path.exists(entry.vad_audio_path)


def test_list_recent_keeps_legacy_single_wav_record(tmp_path):
    archive = AudioArchive(str(tmp_path), max_records=10)
    audio_path = tmp_path / "legacy.wav"
    metadata_path = tmp_path / "legacy.json"
    archive._write_wav_atomic(str(audio_path), np.ones(16, dtype=np.float32), 16_000)
    metadata_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "recording_id": "legacy",
                "audio_file": "legacy.wav",
                "status": "success",
            }
        ),
        encoding="utf-8",
    )

    records = archive.list_recent()
    assert len(records) == 1
    assert records[0]["audio_path"] == str(audio_path)
    assert "original_audio_path" not in records[0]
    assert "vad_audio_path" not in records[0]
