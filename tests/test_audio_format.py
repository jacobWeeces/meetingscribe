import numpy as np
from meetingscribe.audio_format import planar_float32_to_mono, resample_to_16k, planar_chunks_to_mono


def test_planar_float32_to_mono_averages_channels():
    # Non-interleaved stereo: [L0,L1,L2, R0,R1,R2]
    left = np.array([1.0, 0.0, -1.0], dtype="<f4")
    right = np.array([0.0, 0.0, 1.0], dtype="<f4")
    raw = left.tobytes() + right.tobytes()
    mono = planar_float32_to_mono(raw, channels=2)
    np.testing.assert_allclose(mono, [0.5, 0.0, 0.0], atol=1e-6)


def test_planar_mono_passthrough():
    samples = np.array([0.1, 0.2, 0.3], dtype="<f4")
    mono = planar_float32_to_mono(samples.tobytes(), channels=1)
    np.testing.assert_allclose(mono, [0.1, 0.2, 0.3], atol=1e-6)


def test_resample_to_16k_changes_length_proportionally():
    sr = 48000
    x = np.sin(np.linspace(0, 2 * np.pi * 100, sr)).astype("float32")  # 1s @ 48k
    y = resample_to_16k(x, sr)
    assert abs(len(y) - 16000) <= 2  # ~1s @ 16k


def test_planar_chunks_to_mono_converts_each_chunk_independently():
    # Each chunk is its own planar [L..., R...] block.
    # c1: L=[1,0] R=[0,1] -> mono [0.5, 0.5];  c2: L=[2,0] R=[0,2] -> mono [1.0, 1.0]
    c1 = np.array([1.0, 0.0,  0.0, 1.0], dtype="<f4").tobytes()
    c2 = np.array([2.0, 0.0,  0.0, 2.0], dtype="<f4").tobytes()
    mono = planar_chunks_to_mono([c1, c2], channels=2)
    np.testing.assert_allclose(mono, [0.5, 0.5, 1.0, 1.0], atol=1e-6)
    # Guard: the wrong "join bytes then de-interleave once" approach would give
    # [1.5, 0.0, 0.0, 1.5] for this data, so this test now distinguishes the two.


def test_planar_chunks_to_mono_empty_is_empty_float32():
    out = planar_chunks_to_mono([], channels=2)
    assert out.dtype == np.float32 and out.size == 0
