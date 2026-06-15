"""Equivalence guard for the incremental local-audio buffering refactor.

snapshot_side('local', start) and stop()['local'] must return EXACTLY what a naive
full concatenation of the mic blocks would, across interleaved appends + snapshots
(the refactor only changes performance: amortized O(1) folding instead of O(n^2)
re-concatenation on every live tick).
"""

import threading
from unittest import mock

import numpy as np


def _bare_recorder():
    from meetingscribe import recorder
    r = object.__new__(recorder.AudioRecorder)
    r._mic_frames = []
    r._mic_stream = None
    r._mic_rate = 24000
    r._mic_failed = False
    r._sys = None
    r._system_available = False
    r._lock = threading.Lock()
    r.t0 = None
    # new incremental-buffer state (lazy-init tolerant, but set explicitly here)
    r._local_accum = None
    r._local_cached_blocks = 0
    return r


def test_local_snapshot_matches_naive_concat_across_interleaved_appends():
    r = _bare_recorder()
    rng_blocks = [
        np.array([0.0, 0.1, 0.2], dtype="float32").reshape(-1, 1),
        np.array([0.3], dtype="float32").reshape(-1, 1),
        np.array([0.4, 0.5, 0.6, 0.7], dtype="float32").reshape(-1, 1),
        np.array([0.8, 0.9], dtype="float32").reshape(-1, 1),
    ]
    appended = []
    for block in rng_blocks:
        r._mic_frames.append(block)
        appended.append(block)
        full = np.concatenate(appended).reshape(-1).astype("float32")
        # snapshot from several start frames must match the naive concat+slice
        for start in (0, 1, 3, full.size, full.size + 5):
            got = r.snapshot_side("local", start)
            exp = full[start:]
            assert got.dtype == np.float32
            assert np.array_equal(got, exp), f"mismatch start={start} after {len(appended)} blocks"


def test_stop_local_matches_naive_concat(monkeypatch):
    from meetingscribe import recorder
    r = _bare_recorder()
    blocks = [np.full((1000, 1), 0.2, dtype="float32"),
              np.full((500, 1), -0.1, dtype="float32")]
    r._mic_frames = list(blocks)
    out = r.stop()
    expected = np.concatenate(blocks).reshape(-1).astype("float32")
    assert np.array_equal(out["local"], expected)
    assert out["local_rate"] == 24000
