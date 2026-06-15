"""Robust microphone-stream opening (the "my own voice was never recorded" bug).

Field logs showed `sd.InputStream` raising PortAudioError -9986 (paInternalError)
when Start was pressed *during* a live meeting (the call app already held the
Bluetooth mic). The mic was force-opened at a hardcoded 44100 Hz regardless of
the device's native rate, and a failure aborted the ENTIRE recording while the
user got no feedback.

The recorder must instead:
  * open the mic at the device's *native* sample rate first, then fall back
    across other rates / the default device on PortAudio errors;
  * report the rate it actually opened at (so transcription isn't pitch-shifted);
  * degrade to system-audio-only (never raise, never silently no-op) when the
    mic genuinely cannot be opened.
"""

from unittest import mock

import numpy as np


def _patch_choose(monkeypatch, recorder, idx=1, name="AirPods"):
    monkeypatch.setattr(recorder.AudioRecorder, "_choose_mic_device", lambda self: (idx, name))


def _patch_native_rate(monkeypatch, recorder, rate):
    """Fake sd.query_devices supporting both the list form and the (index) form."""
    dev = {"name": "AirPods", "max_input_channels": 1, "default_samplerate": rate}

    def qd(idx=None):
        return dev if idx is not None else [dev]

    monkeypatch.setattr(recorder.sd, "query_devices", qd)


def _patch_sys(monkeypatch, recorder, available):
    fake = mock.MagicMock()
    fake.available.return_value = available
    fake.stop.return_value = (np.zeros(0, dtype="float32"), 48000)
    monkeypatch.setattr(recorder, "SystemAudioRecorder", lambda: fake)
    return fake


class _FakeStream:
    def __init__(self, **kw):
        self.kw = kw
        self.samplerate = kw.get("samplerate")
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        pass

    def close(self):
        pass


def test_mic_opens_at_device_native_rate(monkeypatch):
    from meetingscribe import recorder
    _patch_choose(monkeypatch, recorder, idx=1)
    _patch_native_rate(monkeypatch, recorder, 24000)   # AirPods native rate
    _patch_sys(monkeypatch, recorder, available=False)
    opened = {}
    monkeypatch.setattr(recorder.sd, "InputStream",
                        lambda **kw: opened.update(kw) or _FakeStream(**kw))

    rec = recorder.AudioRecorder()
    rec.start()

    assert opened["samplerate"] == 24000           # NOT the hardcoded 44100
    assert opened["device"] == 1
    assert rec.local_rate() == 24000
    assert rec.mic_failed() is False


def test_mic_retries_other_rates_then_succeeds(monkeypatch):
    from meetingscribe import recorder
    _patch_choose(monkeypatch, recorder, idx=1)
    _patch_native_rate(monkeypatch, recorder, 24000)
    _patch_sys(monkeypatch, recorder, available=False)
    attempts = []

    def maybe(**kw):
        attempts.append(kw.get("samplerate"))
        if kw.get("samplerate") == 24000:
            raise recorder.sd.PortAudioError(
                "Error opening InputStream: Internal PortAudio error [PaErrorCode -9986]")
        return _FakeStream(**kw)

    monkeypatch.setattr(recorder.sd, "InputStream", maybe)

    rec = recorder.AudioRecorder()
    rec.start()                                    # must not raise

    assert attempts[0] == 24000                    # tried native first
    assert rec.mic_failed() is False
    assert rec._mic_stream is not None
    assert rec.local_rate() != 24000               # fell back to a working rate


def test_mic_failure_degrades_to_system_only(monkeypatch):
    from meetingscribe import recorder
    _patch_choose(monkeypatch, recorder, idx=1)
    _patch_native_rate(monkeypatch, recorder, 24000)
    fake_sys = _patch_sys(monkeypatch, recorder, available=True)

    def always_fail(**kw):
        raise recorder.sd.PortAudioError(
            "Error opening InputStream: Internal PortAudio error [PaErrorCode -9986]")

    monkeypatch.setattr(recorder.sd, "InputStream", always_fail)

    rec = recorder.AudioRecorder()
    rec.start()                                    # must NOT raise (was: aborted everything)

    assert rec.mic_failed() is True
    assert rec._mic_stream is None
    fake_sys.start.assert_called_once()            # system audio still captured
    assert rec.system_available() is True


def test_stop_reports_actual_mic_rate(monkeypatch):
    from meetingscribe import recorder
    _patch_choose(monkeypatch, recorder, idx=1)
    _patch_native_rate(monkeypatch, recorder, 24000)
    _patch_sys(monkeypatch, recorder, available=False)
    monkeypatch.setattr(recorder.sd, "InputStream", lambda **kw: _FakeStream(**kw))

    rec = recorder.AudioRecorder()
    rec.start()
    rec._mic_frames = [np.zeros((100, 1), dtype="float32")]
    result = rec.stop()

    assert result["local_rate"] == 24000           # not hardcoded SAMPLE_RATE
