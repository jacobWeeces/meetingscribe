import numpy as np
from unittest import mock


def test_stop_returns_two_streams_and_t0(monkeypatch):
    from meetingscribe import recorder
    monkeypatch.setattr(recorder.sd, "InputStream", mock.MagicMock())
    fake_sys = mock.MagicMock()
    fake_sys.available.return_value = True
    fake_sys.stop.return_value = (np.zeros(16000, dtype="float32"), 48000)
    monkeypatch.setattr(recorder, "SystemAudioRecorder", lambda: fake_sys)

    rec = recorder.AudioRecorder()
    rec.start()
    rec._mic_frames = [np.zeros((1000, 1), dtype="float32")]
    result = rec.stop()
    assert set(result) >= {"local", "remote", "t0", "local_rate", "remote_rate"}
    assert isinstance(result["local"], np.ndarray)
    assert result["remote_rate"] == 48000


def _mk_recorder(monkeypatch):
    from meetingscribe import recorder
    monkeypatch.setattr(recorder.AudioRecorder, "__init__", lambda self: None)
    r = recorder.AudioRecorder()
    r._lock = __import__("threading").Lock()
    r._mic_frames = []
    r._sys = None
    r._system_available = False
    return r


def test_snapshot_side_local_concats_and_slices(monkeypatch):
    r = _mk_recorder(monkeypatch)
    r._mic_frames = [np.array([0.0, 0.1, 0.2, 0.3], dtype="float32").reshape(-1, 1)]
    out = r.snapshot_side("local", 2)
    assert np.allclose(out, [0.2, 0.3])
    assert out.dtype == np.float32


def test_snapshot_side_local_empty_is_float32(monkeypatch):
    r = _mk_recorder(monkeypatch)
    out = r.snapshot_side("local", 0)
    assert out.size == 0 and out.dtype == np.float32


def test_snapshot_side_remote_delegates_to_system(monkeypatch):
    r = _mk_recorder(monkeypatch)
    fake_sys = mock.MagicMock()
    fake_sys.snapshot.return_value = np.array([0.5, 0.5], dtype="float32")
    r._sys = fake_sys
    r._system_available = True
    out = r.snapshot_side("remote", 7)
    fake_sys.snapshot.assert_called_once_with(7)
    assert np.allclose(out, [0.5, 0.5])


def test_snapshot_side_remote_mic_only_is_empty(monkeypatch):
    r = _mk_recorder(monkeypatch)
    out = r.snapshot_side("remote", 0)   # no system stream
    assert out.size == 0 and out.dtype == np.float32


def test_system_available_reflects_flag(monkeypatch):
    r = _mk_recorder(monkeypatch)
    assert r.system_available() is False
    r._system_available = True
    assert r.system_available() is True


def test_remote_rate_from_sys_or_default(monkeypatch):
    r = _mk_recorder(monkeypatch)
    assert r.remote_rate() == 48000          # mic-only default
    fake = mock.MagicMock(); fake.rate.return_value = 44100
    r._sys = fake; r._system_available = True
    assert r.remote_rate() == 44100
