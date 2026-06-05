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
