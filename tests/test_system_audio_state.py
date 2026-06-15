"""SystemAudioRecorder.is_capturing reflects real stream state, and AudioRecorder
trusts it so a silent start() failure isn't reported as captured remote audio."""

from unittest import mock

import numpy as np


def test_is_capturing_reflects_stream_state():
    from meetingscribe.system_audio import SystemAudioRecorder
    rec = SystemAudioRecorder()
    assert rec.is_capturing() is False          # nothing started
    rec._stream = object()                       # simulate a live stream
    assert rec.is_capturing() is True
    rec._stream = None
    assert rec.is_capturing() is False


def test_recorder_marks_system_unavailable_when_start_silently_fails(monkeypatch):
    from meetingscribe import recorder
    monkeypatch.setattr(recorder.AudioRecorder, "_choose_mic_device", lambda self: (1, "Mic"))
    monkeypatch.setattr(recorder.sd, "query_devices",
                        lambda *a, **k: {"name": "Mic", "max_input_channels": 1, "default_samplerate": 48000})
    monkeypatch.setattr(recorder.sd, "InputStream", lambda **kw: mock.MagicMock(samplerate=48000))

    fake_sys = mock.MagicMock()
    fake_sys.available.return_value = True       # permission/display exist...
    fake_sys.is_capturing.return_value = False   # ...but start() never produced a stream
    monkeypatch.setattr(recorder, "SystemAudioRecorder", lambda: fake_sys)

    rec = recorder.AudioRecorder()
    rec.start()
    assert rec.system_available() is False       # not falsely reported as capturing


def test_recorder_marks_system_available_when_capturing(monkeypatch):
    from meetingscribe import recorder
    monkeypatch.setattr(recorder.AudioRecorder, "_choose_mic_device", lambda self: (1, "Mic"))
    monkeypatch.setattr(recorder.sd, "query_devices",
                        lambda *a, **k: {"name": "Mic", "max_input_channels": 1, "default_samplerate": 48000})
    monkeypatch.setattr(recorder.sd, "InputStream", lambda **kw: mock.MagicMock(samplerate=48000))

    fake_sys = mock.MagicMock()
    fake_sys.available.return_value = True
    fake_sys.is_capturing.return_value = True
    monkeypatch.setattr(recorder, "SystemAudioRecorder", lambda: fake_sys)

    rec = recorder.AudioRecorder()
    rec.start()
    assert rec.system_available() is True
