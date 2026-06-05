import numpy as np
from meetingscribe.segments import merge_segments
from meetingscribe.live_transcriber import LiveTranscriber, resolve_segments


class _T:
    def __init__(self, streams): self._streams = streams
    def transcribe_streams(self, *a, **k): return self._streams
    def transcribe_segments(self, source, sample_rate=None): return []


def test_live_and_stream_paths_produce_same_merged_order():
    local_segs = [{"start": 0.0, "end": 1.0, "text": "L1", "side": "local"},
                  {"start": 4.0, "end": 5.0, "text": "L2", "side": "local"}]
    remote_segs = [{"start": 2.0, "end": 3.0, "text": "R1", "side": "remote"}]
    streams_merged = merge_segments([dict(s) for s in local_segs], [dict(s) for s in remote_segs])
    t = _T(streams_merged)
    ll = LiveTranscriber(t, sample_rate=100, side="local"); ll._ever_committed = True
    ll._committed_segments = [dict(s) for s in local_segs]
    lr = LiveTranscriber(t, sample_rate=100, side="remote"); lr._ever_committed = True
    lr._committed_segments = [dict(s) for s in remote_segs]
    live_merged = resolve_segments(t, ll, lr, np.zeros(0, "float32"), np.zeros(0, "float32"),
                                   {"local": np.zeros(0, "float32"), "local_rate": 100,
                                    "remote": np.zeros(0, "float32"), "remote_rate": 100})
    assert [(s["text"], s["side"]) for s in live_merged] == [(s["text"], s["side"]) for s in streams_merged]
