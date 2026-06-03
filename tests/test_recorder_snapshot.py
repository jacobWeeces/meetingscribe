import numpy as np
from meetingscribe.recorder import AudioRecorder


def _frames(values):
    # frames are stored as (N, 1) float32 blocks, like the sounddevice callback
    return [np.array(values, dtype="float32").reshape(-1, 1)]


def test_mic_only_returns_mic(monkeypatch):
    monkeypatch.setattr(AudioRecorder, "__init__", lambda self: None)
    r = AudioRecorder()
    r._lock = __import__("threading").Lock()
    r._mic_frames = _frames([0.1, 0.2, 0.3, 0.4])
    r._sys_frames = []
    out = r.snapshot_mono(0)
    assert np.allclose(out, [0.1, 0.2, 0.3, 0.4])


def test_mic_plus_sys_is_averaged_and_clipped(monkeypatch):
    monkeypatch.setattr(AudioRecorder, "__init__", lambda self: None)
    r = AudioRecorder()
    r._lock = __import__("threading").Lock()
    r._mic_frames = _frames([1.0, 1.0, 1.0])
    r._sys_frames = _frames([0.0, 0.0])  # shorter -> clip to len 2
    out = r.snapshot_mono(0)
    assert np.allclose(out, [0.5, 0.5])


def test_start_sample_slices(monkeypatch):
    monkeypatch.setattr(AudioRecorder, "__init__", lambda self: None)
    r = AudioRecorder()
    r._lock = __import__("threading").Lock()
    r._mic_frames = _frames([0.0, 0.1, 0.2, 0.3])
    r._sys_frames = []
    out = r.snapshot_mono(2)
    assert np.allclose(out, [0.2, 0.3])


def test_empty_mic_returns_empty_float32(monkeypatch):
    # first tick before any audio callback has fired
    monkeypatch.setattr(AudioRecorder, "__init__", lambda self: None)
    r = AudioRecorder()
    r._lock = __import__("threading").Lock()
    r._mic_frames = []
    r._sys_frames = []
    out = r.snapshot_mono(0)
    assert len(out) == 0
    assert out.dtype == np.float32


def test_start_sample_beyond_end_returns_empty(monkeypatch):
    # the worker asks for audio past committed_sample when nothing new has arrived
    monkeypatch.setattr(AudioRecorder, "__init__", lambda self: None)
    r = AudioRecorder()
    r._lock = __import__("threading").Lock()
    r._mic_frames = _frames([0.0, 0.1, 0.2])
    r._sys_frames = []
    out = r.snapshot_mono(99)
    assert len(out) == 0
