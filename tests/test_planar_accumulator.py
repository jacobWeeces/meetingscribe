"""PlanarChunkAccumulator must be exactly equivalent to planar_chunks_to_mono over
all chunks — it only changes the system-audio snapshot from O(n^2) to O(new)."""

import numpy as np


def _planar_chunk(left, right):
    """Build a planar Float32 chunk: all left samples then all right samples."""
    return np.array(list(left) + list(right), dtype="<f4").tobytes()


def test_accumulator_matches_planar_chunks_to_mono_incrementally():
    from meetingscribe.audio_format import PlanarChunkAccumulator, planar_chunks_to_mono
    chunks = [
        _planar_chunk([1, 2, 3], [10, 20, 30]),
        _planar_chunk([4], [40]),
        _planar_chunk([5, 6], [50, 60]),
    ]
    acc = PlanarChunkAccumulator()
    for i in range(1, len(chunks) + 1):
        partial = chunks[:i]
        expected = planar_chunks_to_mono(partial, 2)
        got = acc.update(partial, 2).view(0)
        assert np.allclose(got, expected), f"mismatch after {i} chunks"
        assert got.dtype == np.float32


def test_accumulator_view_start_frame():
    from meetingscribe.audio_format import PlanarChunkAccumulator, planar_chunks_to_mono
    chunks = [_planar_chunk([1, 2, 3], [10, 20, 30]), _planar_chunk([4, 5], [40, 50])]
    acc = PlanarChunkAccumulator()
    full = planar_chunks_to_mono(chunks, 2)
    acc.update(chunks, 2)
    assert np.allclose(acc.view(2), full[2:])
    assert acc.view(999).size == 0


def test_accumulator_rebuilds_if_list_shrinks():
    from meetingscribe.audio_format import PlanarChunkAccumulator, planar_chunks_to_mono
    acc = PlanarChunkAccumulator()
    acc.update([_planar_chunk([1, 2], [3, 4]), _planar_chunk([5], [6])], 2)
    smaller = [_planar_chunk([9, 9], [9, 9])]
    got = acc.update(smaller, 2).view(0)
    assert np.allclose(got, planar_chunks_to_mono(smaller, 2))
