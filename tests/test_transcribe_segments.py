import os

import numpy as np
from meetingscribe.transcriber import Transcriber


class _Seg:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


class _FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, source, **kwargs):
        self.calls.append(source)
        info = type("Info", (), {"duration": 2.0})()
        return iter([_Seg(0.0, 1.0, "hello"), _Seg(1.0, 2.0, "world")]), info


def test_segments_from_path(monkeypatch):
    t = Transcriber()
    t._model = _FakeModel()
    monkeypatch.setattr(t, "_load_model", lambda: None)
    segs = t.transcribe_segments("/tmp/x.wav")
    assert segs == [(0.0, 1.0, "hello"), (1.0, 2.0, "world")]
    assert t._model.calls == ["/tmp/x.wav"]


def test_segments_from_ndarray_writes_tempwav(monkeypatch):
    t = Transcriber()
    t._model = _FakeModel()
    monkeypatch.setattr(t, "_load_model", lambda: None)
    audio = np.zeros(44100, dtype="float32")  # 1 s of silence
    segs = t.transcribe_segments(audio)
    assert segs == [(0.0, 1.0, "hello"), (1.0, 2.0, "world")]
    # the model was handed a real temp file path (str), not the array
    assert isinstance(t._model.calls[0], str) and t._model.calls[0].endswith(".wav")
    # the temp WAV is cleaned up — no leak across the ~25s live ticks
    assert not os.path.exists(t._model.calls[0])


def test_transcribe_segments_writes_temp_wav_at_given_rate(monkeypatch):
    import numpy as np
    import meetingscribe.transcriber as tr
    t = tr.Transcriber()
    t._model = __import__("unittest").mock.MagicMock()
    t._model.transcribe.return_value = ([], None)
    captured = {}
    monkeypatch.setattr(tr.wavfile, "write", lambda path, rate, data: captured.__setitem__("rate", rate))
    t.transcribe_segments(np.zeros(100, dtype="float32"), sample_rate=48000)
    assert captured["rate"] == 48000
