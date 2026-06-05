from unittest import mock
import numpy as np
from meetingscribe.transcriber import Transcriber


def _seg(start, end, text):
    s = mock.MagicMock(); s.start, s.end, s.text = start, end, text
    return s


def test_transcribe_streams_merges_by_timestamp():
    t = Transcriber()
    fake_model = mock.MagicMock()
    fake_model.transcribe.side_effect = [
        ([_seg(0.0, 1.0, "local one")], mock.MagicMock(duration=10)),
        ([_seg(2.0, 3.0, "remote one")], mock.MagicMock(duration=10)),
    ]
    t._model = fake_model
    segs = t.transcribe_streams(np.zeros(16000, "float32"), 16000,
                                np.zeros(16000, "float32"), 16000)
    assert [s["side"] for s in segs] == ["local", "remote"]
    assert [s["text"] for s in segs] == ["local one", "remote one"]


def test_transcribe_streams_reports_weighted_progress():
    t = Transcriber()
    fake_model = mock.MagicMock()
    fake_model.transcribe.side_effect = [
        ([_seg(0.0, 1.0, "l")], mock.MagicMock(duration=1)),
        ([_seg(0.0, 1.0, "r")], mock.MagicMock(duration=1)),
    ]
    t._model = fake_model
    seen = []
    # two equal-length streams (1 s each) -> 0.5 after local, 1.0 after remote
    t.transcribe_streams(np.zeros(16000, "float32"), 16000,
                         np.zeros(16000, "float32"), 16000,
                         on_progress=lambda p: seen.append(round(p, 6)))
    assert seen == [0.5, 1.0]


def test_transcribe_streams_empty_remote_progresses_and_returns_local_only():
    t = Transcriber()
    fake_model = mock.MagicMock()
    fake_model.transcribe.side_effect = [
        ([_seg(0.0, 1.0, "l")], mock.MagicMock(duration=1)),
    ]
    t._model = fake_model
    seen = []
    segs = t.transcribe_streams(np.zeros(16000, "float32"), 16000,
                                np.zeros(0, "float32"), 16000,
                                on_progress=lambda p: seen.append(round(p, 6)))
    assert [s["side"] for s in segs] == ["local"]
    assert seen[-1] == 1.0                        # completes even with an empty remote
    assert fake_model.transcribe.call_count == 1  # the empty stream is not transcribed
