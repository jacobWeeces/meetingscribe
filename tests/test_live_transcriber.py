import pytest
import numpy as np
from meetingscribe.live_transcriber import LiveTranscriber, resolve_transcript

SR = 100  # tiny sample rate keeps the math readable


class FakeTranscriber:
    """Returns a scripted list of (start, end, text) per call, ignoring the audio."""
    def __init__(self, scripts):
        self._scripts = list(scripts)
        self.calls = []

    def transcribe_segments(self, source, sample_rate=None):
        self.calls.append(source)
        return self._scripts.pop(0) if self._scripts else []

    def transcribe(self, wav_path, on_progress=None):
        return "FULL-FILE"


def _tail(seconds, sr=SR):
    return np.zeros(int(seconds * sr), dtype="float32")


def test_commits_segments_before_horizon_holds_the_rest():
    # tail = 20 s, guard = 3 -> horizon = 17. Seg A ends at 10 (commit),
    # Seg B ends at 19 (held, inside guard).
    fake = FakeTranscriber([[(0.0, 10.0, "alpha"), (10.0, 19.0, "beta")]])
    lt = LiveTranscriber(fake, sample_rate=SR, guard_sec=3, max_tail_sec=90)
    lt.process_tick(_tail(20))
    assert lt.text() == "alpha"
    assert lt.committed_sample == int(10.0 * SR)
    assert lt.ever_committed is True


def test_short_tail_commits_nothing():
    fake = FakeTranscriber([[(0.0, 1.0, "hi")]])
    lt = LiveTranscriber(fake, sample_rate=SR, guard_sec=3, max_tail_sec=90)
    lt.process_tick(_tail(4))  # < guard + 2
    assert lt.text() == ""
    assert lt.committed_sample == 0
    assert lt.ever_committed is False
    assert fake.calls == []  # didn't even bother transcribing


def test_no_duplicate_text_across_ticks():
    # Tick 1: tail 20 s -> commit "alpha" (ends 10), advance to sample 1000.
    # Tick 2: a fresh 15 s tail (audio from sample 1000 on) -> commit "gamma" (ends 9).
    fake = FakeTranscriber([
        [(0.0, 10.0, "alpha"), (10.0, 19.0, "beta")],
        [(0.0, 9.0, "gamma"), (9.0, 14.0, "delta")],
    ])
    lt = LiveTranscriber(fake, sample_rate=SR, guard_sec=3, max_tail_sec=90)
    lt.process_tick(_tail(20))
    lt.process_tick(_tail(15))
    assert lt.text() == "alpha\ngamma"          # no "beta" duplication
    assert lt.committed_sample == int(10.0 * SR) + int(9.0 * SR)


def test_max_tail_cap_force_commits():
    # tail 95 s (> max 90), single long segment ending at 94 (> horizon 92) -> force commit.
    fake = FakeTranscriber([[(0.0, 94.0, "monologue")]])
    lt = LiveTranscriber(fake, sample_rate=SR, guard_sec=3, max_tail_sec=90)
    lt.process_tick(_tail(95))
    assert lt.text() == "monologue"
    assert lt.committed_sample == int(94.0 * SR)


def test_finalize_flushes_remaining_tail_with_no_guard():
    fake = FakeTranscriber([
        [(0.0, 10.0, "alpha"), (10.0, 19.0, "beta")],   # tick commits alpha
        [(0.0, 4.0, "omega")],                          # finalize commits everything
    ])
    lt = LiveTranscriber(fake, sample_rate=SR, guard_sec=3, max_tail_sec=90)
    lt.process_tick(_tail(20))
    result = lt.finalize(_tail(5))
    assert result == "alpha\nomega"


def test_finalize_with_empty_tail_returns_committed():
    fake = FakeTranscriber([[(0.0, 10.0, "alpha")]])
    lt = LiveTranscriber(fake, sample_rate=SR, guard_sec=3, max_tail_sec=90)
    lt.process_tick(_tail(20))
    assert lt.finalize(np.zeros(0, dtype="float32")) == "alpha"


def test_resolve_uses_live_when_committed():
    fake = FakeTranscriber([])
    lt = LiveTranscriber(fake, sample_rate=SR)
    lt._committed = ["live text"]
    lt._ever_committed = True
    out = resolve_transcript(fake, lt, np.zeros(0, dtype="float32"), "/tmp/x.wav")
    assert out == "live text"


def test_resolve_falls_back_to_whole_file_when_live_none():
    fake = FakeTranscriber([])
    out = resolve_transcript(fake, None, None, "/tmp/x.wav")
    assert out == "FULL-FILE"


def test_resolve_falls_back_when_live_never_committed():
    fake = FakeTranscriber([])
    lt = LiveTranscriber(fake, sample_rate=SR)  # nothing committed
    out = resolve_transcript(fake, lt, None, "/tmp/x.wav")
    assert out == "FULL-FILE"


def test_segment_ending_exactly_at_horizon_commits():
    # horizon = 20 - 3 = 17; a segment ending exactly at 17 must commit (<= boundary).
    fake = FakeTranscriber([[(0.0, 17.0, "edge"), (17.0, 19.0, "after")]])
    lt = LiveTranscriber(fake, sample_rate=SR, guard_sec=3, max_tail_sec=90)
    lt.process_tick(_tail(20))
    assert lt.text() == "edge"
    assert lt.committed_sample == int(17.0 * SR)


def test_all_segments_inside_guard_is_a_noop():
    # every segment is past the horizon (17) -> nothing commits, no advance.
    fake = FakeTranscriber([[(17.5, 18.0, "late"), (18.0, 19.0, "later")]])
    lt = LiveTranscriber(fake, sample_rate=SR, guard_sec=3, max_tail_sec=90)
    lt.process_tick(_tail(20))
    assert lt.text() == ""
    assert lt.committed_sample == 0
    assert lt.ever_committed is False


def test_tick_exception_leaves_state_unchanged():
    class Boom:
        def transcribe_segments(self, source, sample_rate=None):
            raise RuntimeError("whisper blew up")

    lt = LiveTranscriber(Boom(), sample_rate=SR, guard_sec=3, max_tail_sec=90)
    lt.process_tick(_tail(20))  # must not raise
    assert lt.text() == ""
    assert lt.committed_sample == 0
    assert lt.ever_committed is False


def test_resolve_fires_progress_complete_on_live():
    fake = FakeTranscriber([])
    lt = LiveTranscriber(fake, sample_rate=SR)
    lt._committed = ["live text"]
    lt._ever_committed = True
    seen = []
    out = resolve_transcript(
        fake, lt, np.zeros(0, dtype="float32"), "/tmp/x.wav",
        on_progress=lambda p: seen.append(p),
    )
    assert out == "live text"
    assert seen == [1.0]


def test_finalize_propagates_transcriber_error():
    class Boom:
        def transcribe_segments(self, source, sample_rate=None):
            raise RuntimeError("whisper blew up on the tail")

    lt = LiveTranscriber(Boom(), sample_rate=SR, guard_sec=3, max_tail_sec=90)
    with pytest.raises(RuntimeError):
        lt.finalize(_tail(5))


def test_resolve_falls_back_to_whole_file_when_finalize_raises():
    class Boom:
        def transcribe_segments(self, source, sample_rate=None):
            raise RuntimeError("whisper blew up on the tail")

        def transcribe(self, wav_path, on_progress=None):
            return "FULL-FILE"

    boom = Boom()
    lt = LiveTranscriber(boom, sample_rate=SR)
    lt._committed = ["partial live text"]   # simulate one good tick before the failure
    lt._ever_committed = True
    out = resolve_transcript(boom, lt, _tail(5), "/tmp/x.wav")
    assert out == "FULL-FILE"               # fell back, not the partial


def test_committed_segments_have_absolute_times_and_side():
    fake = FakeTranscriber([[(0.0, 10.0, "alpha"), (10.0, 19.0, "beta")]])
    lt = LiveTranscriber(fake, sample_rate=SR, guard_sec=3, max_tail_sec=90, side="remote")
    lt.process_tick(_tail(20))                       # commits "alpha" (ends 10)
    segs = lt.committed_segments()
    assert len(segs) == 1
    assert segs[0]["text"] == "alpha" and segs[0]["side"] == "remote"
    assert segs[0]["start"] == 0.0 and segs[0]["end"] == 10.0


def test_committed_segments_absolute_across_ticks():
    fake = FakeTranscriber([
        [(0.0, 10.0, "alpha"), (10.0, 19.0, "beta")],   # tick1 commits alpha, base -> 10s
        [(0.0, 9.0, "gamma")],                          # tick2 tail starts at 10s -> gamma abs 10..19
    ])
    lt = LiveTranscriber(fake, sample_rate=SR, guard_sec=3, max_tail_sec=90, side="local")
    lt.process_tick(_tail(20)); lt.process_tick(_tail(15))
    segs = lt.committed_segments()
    assert [s["text"] for s in segs] == ["alpha", "gamma"]
    assert segs[1]["start"] == 10.0 and segs[1]["end"] == 19.0


def test_finalize_appends_absolute_segment():
    fake = FakeTranscriber([[(0.0, 4.0, "omega")]])
    lt = LiveTranscriber(fake, sample_rate=SR, guard_sec=3, max_tail_sec=90, side="local")
    lt.finalize(_tail(5))
    segs = lt.committed_segments()
    assert segs[0]["text"] == "omega" and segs[0]["start"] == 0.0 and segs[0]["end"] == 4.0


class FakeStreamTranscriber:
    def __init__(self, streams_result, seg_scripts=None):
        self._streams = streams_result
        self._scripts = list(seg_scripts or [])

    def transcribe_streams(self, *a, **k):
        return self._streams

    def transcribe_segments(self, source, sample_rate=None):
        return self._scripts.pop(0) if self._scripts else []


def test_resolve_segments_uses_live_when_committed():
    from meetingscribe.live_transcriber import resolve_segments
    t = FakeStreamTranscriber(streams_result=[{"start": 9, "end": 9, "text": "FALLBACK", "side": "local", "id": 1}])
    local = LiveTranscriber(t, sample_rate=SR, side="local"); local._ever_committed = True
    local._committed_segments = [{"start": 0.0, "end": 1.0, "text": "L", "side": "local"}]
    remote = LiveTranscriber(t, sample_rate=SR, side="remote"); remote._ever_committed = True
    remote._committed_segments = [{"start": 0.5, "end": 1.5, "text": "R", "side": "remote"}]
    merged = resolve_segments(t, local, remote, _tail(0), _tail(0),
                              {"local": _tail(0), "local_rate": SR, "remote": _tail(0), "remote_rate": SR})
    assert [s["text"] for s in merged] == ["L", "R"]
    assert all("id" in s for s in merged)


def test_resolve_segments_falls_back_when_no_live():
    from meetingscribe.live_transcriber import resolve_segments
    t = FakeStreamTranscriber(streams_result=[{"start": 0, "end": 1, "text": "FB", "side": "local", "id": 1}])
    merged = resolve_segments(t, None, None, None, None,
                              {"local": _tail(1), "local_rate": SR, "remote": _tail(0), "remote_rate": SR})
    assert [s["text"] for s in merged] == ["FB"]
