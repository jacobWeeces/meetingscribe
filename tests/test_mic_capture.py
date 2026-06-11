"""Tests for the silent-mic capture fix: per-channel level diagnostics and
robust input-device selection (avoid a virtual/loopback default input that
silently records nothing)."""

import types
from unittest import mock

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# rms_peak — per-channel signal diagnostics
# ---------------------------------------------------------------------------

def test_rms_peak_empty_is_zero():
    from meetingscribe.recorder import rms_peak
    assert rms_peak(np.zeros(0, dtype="float32")) == (0.0, 0.0)


def test_rms_peak_silence_is_zero():
    from meetingscribe.recorder import rms_peak
    rms, peak = rms_peak(np.zeros(1000, dtype="float32"))
    assert rms == 0.0 and peak == 0.0


def test_rms_peak_known_values():
    from meetingscribe.recorder import rms_peak
    rms, peak = rms_peak(np.array([0.0, 0.5, -1.0], dtype="float32"))
    assert peak == pytest.approx(1.0)
    assert rms == pytest.approx(np.sqrt((0.0 + 0.25 + 1.0) / 3), rel=1e-5)


def test_rms_peak_returns_python_floats():
    from meetingscribe.recorder import rms_peak
    rms, peak = rms_peak(np.array([0.2, -0.2], dtype="float32"))
    assert type(rms) is float and type(peak) is float


# ---------------------------------------------------------------------------
# select_input_device — pick a real mic, skip virtual/loopback defaults
# ---------------------------------------------------------------------------

def _dev(name, max_in):
    return {"name": name, "max_input_channels": max_in}


def test_keeps_real_default_input():
    from meetingscribe.recorder import select_input_device
    devices = [_dev("MacBook Pro Microphone", 1), _dev("MacBook Pro Speakers", 0)]
    assert select_input_device(devices, 0) == (0, "MacBook Pro Microphone")


def test_skips_virtual_default_when_real_input_exists():
    # Leftover BlackHole left as the system default input records pure silence —
    # fall back to a real physical microphone instead.
    from meetingscribe.recorder import select_input_device
    devices = [_dev("BlackHole 2ch", 2), _dev("MacBook Pro Microphone", 1)]
    assert select_input_device(devices, 0) == (1, "MacBook Pro Microphone")


def test_keeps_virtual_default_when_no_real_alternative():
    from meetingscribe.recorder import select_input_device
    devices = [_dev("BlackHole 2ch", 2), _dev("Some Speakers", 0)]
    assert select_input_device(devices, 0) == (0, "BlackHole 2ch")


def test_picks_first_real_input_when_default_index_is_none():
    from meetingscribe.recorder import select_input_device
    devices = [_dev("Aggregate Device", 2), _dev("USB Mic", 1)]
    assert select_input_device(devices, None) == (1, "USB Mic")


def test_returns_none_when_no_input_devices_at_all():
    from meetingscribe.recorder import select_input_device
    devices = [_dev("Speakers", 0)]
    assert select_input_device(devices, None) == (None, None)


# ---------------------------------------------------------------------------
# AudioRecorder.start — wires the selected device into the input stream
# ---------------------------------------------------------------------------

def _patch_sys_unavailable(monkeypatch, recorder):
    fake_sys = mock.MagicMock()
    fake_sys.available.return_value = False
    monkeypatch.setattr(recorder, "SystemAudioRecorder", lambda: fake_sys)


def test_start_skips_virtual_default_and_uses_real_mic(monkeypatch):
    from meetingscribe import recorder
    captured = {}
    monkeypatch.setattr(recorder.sd, "InputStream",
                        lambda **kw: captured.update(kw) or mock.MagicMock())
    monkeypatch.setattr(recorder.sd, "query_devices", lambda: [
        _dev("BlackHole 2ch", 2), _dev("MacBook Pro Microphone", 1),
    ])
    monkeypatch.setattr(recorder.sd, "default", types.SimpleNamespace(device=[0, 5]))
    _patch_sys_unavailable(monkeypatch, recorder)

    recorder.AudioRecorder().start()
    assert captured["device"] == 1          # fell back off BlackHole to the mic
    assert captured["samplerate"] == recorder.SAMPLE_RATE
    assert captured["channels"] == 1


def test_start_falls_back_to_default_on_device_query_error(monkeypatch):
    from meetingscribe import recorder

    def boom():
        raise RuntimeError("no audio backend")

    captured = {}
    monkeypatch.setattr(recorder.sd, "InputStream",
                        lambda **kw: captured.update(kw) or mock.MagicMock())
    monkeypatch.setattr(recorder.sd, "query_devices", boom)
    _patch_sys_unavailable(monkeypatch, recorder)

    recorder.AudioRecorder().start()        # must not raise
    assert captured["device"] is None       # graceful fallback to system default


def test_stop_logs_per_channel_levels(monkeypatch, caplog):
    # The silent-mic symptom must be visible in the field log: stop() reports
    # per-channel rms so a dead local channel (rms ~ 0) is obvious.
    from meetingscribe import recorder
    monkeypatch.setattr(recorder.sd, "InputStream", lambda **kw: mock.MagicMock())
    monkeypatch.setattr(recorder.sd, "query_devices", lambda: [_dev("Mic", 1)])
    monkeypatch.setattr(recorder.sd, "default", types.SimpleNamespace(device=[0, 1]))
    fake_sys = mock.MagicMock()
    fake_sys.available.return_value = True
    fake_sys.stop.return_value = (np.full(1000, 0.3, dtype="float32"), 48000)
    monkeypatch.setattr(recorder, "SystemAudioRecorder", lambda: fake_sys)

    rec = recorder.AudioRecorder()
    rec.start()
    rec._mic_frames = [np.zeros((1000, 1), dtype="float32")]  # silent local channel

    with caplog.at_level("INFO", logger="meetingscribe"):
        rec.stop()

    msgs = " ".join(r.getMessage() for r in caplog.records).lower()
    assert "level" in msgs
    assert "local" in msgs and "remote" in msgs


# ---------------------------------------------------------------------------
# local_silent_with_remote_signal — drives the user-facing "mic was silent" hint
# ---------------------------------------------------------------------------

def test_flags_silent_mic_with_active_remote():
    from meetingscribe.recorder import local_silent_with_remote_signal
    local = np.zeros(1000, dtype="float32")            # dead mic
    remote = np.full(1000, 0.3, dtype="float32")       # remote has speech
    assert local_silent_with_remote_signal(local, remote) is True


def test_not_flagged_when_local_has_signal():
    from meetingscribe.recorder import local_silent_with_remote_signal
    local = np.full(1000, 0.2, dtype="float32")
    remote = np.full(1000, 0.3, dtype="float32")
    assert local_silent_with_remote_signal(local, remote) is False


def test_not_flagged_when_remote_also_silent():
    # Both silent -> a real "no speech" case, not a mic-routing problem.
    from meetingscribe.recorder import local_silent_with_remote_signal
    local = np.zeros(1000, dtype="float32")
    remote = np.zeros(1000, dtype="float32")
    assert local_silent_with_remote_signal(local, remote) is False
