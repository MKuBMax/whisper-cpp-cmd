"""常驻 sysref 切段：预滚进入本段，end 后采集状态可继续。"""

import numpy as np

from core.sysref import SysRefCapture, _PREROLL_SAMPLES


def _pcm_bytes(n, value=0.1):
    return (np.full(n, value, dtype=np.float32)).tobytes()


def test_segment_includes_preroll_and_body():
    cap = SysRefCapture()
    cap._handle_frame(0, _pcm_bytes(_PREROLL_SAMPLES, 0.1))
    cap.begin_segment()
    cap._handle_frame(0, _pcm_bytes(16000, 0.2))
    out = cap.end_segment()
    assert out is not None
    assert out.size >= 16000 + _PREROLL_SAMPLES // 2
    assert np.isclose(out[0], 0.1)
    assert np.isclose(out[-1], 0.2)


def test_second_segment_does_not_keep_first_body():
    cap = SysRefCapture()
    cap._handle_frame(0, _pcm_bytes(8000, 0.3))
    cap.begin_segment()
    cap._handle_frame(0, _pcm_bytes(4000, 0.4))
    first = cap.end_segment()
    cap._handle_frame(0, _pcm_bytes(8000, 0.5))
    cap.begin_segment()
    cap._handle_frame(0, _pcm_bytes(2000, 0.6))
    second = cap.end_segment()
    assert first is not None and second is not None
    assert np.any(np.isclose(first, 0.4))
    assert np.any(np.isclose(second, 0.6))
    assert not np.any(np.isclose(second, 0.4))


def test_end_without_begin_returns_none():
    cap = SysRefCapture()
    cap._handle_frame(0, _pcm_bytes(1000, 0.2))
    assert cap.end_segment() is None
