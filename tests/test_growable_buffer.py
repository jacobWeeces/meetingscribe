"""GrowableMonoBuffer: amortized-O(1) append + bounded-tail views, used to stop the
O(n^2) full re-concatenation that recorder/system_audio did on every live tick."""

import numpy as np


def test_append_and_full_view_matches_concatenation():
    from meetingscribe.audio_format import GrowableMonoBuffer
    blocks = [np.arange(0, 5, dtype="float32"),
              np.arange(5, 7, dtype="float32"),
              np.arange(7, 13, dtype="float32")]
    buf = GrowableMonoBuffer()
    for b in blocks:
        buf.append(b)
    expected = np.concatenate(blocks)
    assert np.array_equal(buf.view(0), expected)
    assert buf.view(0).dtype == np.float32


def test_view_from_start_frame_returns_tail():
    from meetingscribe.audio_format import GrowableMonoBuffer
    buf = GrowableMonoBuffer()
    buf.append(np.arange(0, 10, dtype="float32"))
    assert np.array_equal(buf.view(4), np.arange(4, 10, dtype="float32"))
    assert buf.view(0).dtype == np.float32


def test_view_past_end_is_empty_float32():
    from meetingscribe.audio_format import GrowableMonoBuffer
    buf = GrowableMonoBuffer()
    buf.append(np.arange(0, 3, dtype="float32"))
    out = buf.view(10)
    assert out.size == 0 and out.dtype == np.float32


def test_view_is_a_copy_not_a_reference():
    from meetingscribe.audio_format import GrowableMonoBuffer
    buf = GrowableMonoBuffer()
    buf.append(np.arange(0, 4, dtype="float32"))
    v = buf.view(0)
    v[0] = 999.0
    assert buf.view(0)[0] == 0.0          # mutating the view must not corrupt the buffer


def test_many_small_appends_grow_correctly():
    from meetingscribe.audio_format import GrowableMonoBuffer
    buf = GrowableMonoBuffer(initial_capacity=4)   # force several doublings
    expected = []
    for i in range(50):
        block = np.full(3, float(i), dtype="float32")
        buf.append(block)
        expected.append(block)
    assert np.array_equal(buf.view(0), np.concatenate(expected))
    assert len(buf) == 150


def test_empty_buffer_view_is_empty():
    from meetingscribe.audio_format import GrowableMonoBuffer
    buf = GrowableMonoBuffer()
    assert buf.view(0).size == 0
    assert len(buf) == 0
