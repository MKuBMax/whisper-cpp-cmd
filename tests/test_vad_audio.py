"""Pure logic tests for the whisper.cpp VAD archive adapter."""

from core.vad_audio import VADAudioExtractor


def test_merge_ranges_removes_overlap_padding_duplicates():
    assert VADAudioExtractor._merge_ranges(
        [(100, 200), (190, 300), (450, 500), (500, 600)]
    ) == ((100, 300), (450, 600))


def test_merge_ranges_handles_empty_input():
    assert VADAudioExtractor._merge_ranges([]) == ()
