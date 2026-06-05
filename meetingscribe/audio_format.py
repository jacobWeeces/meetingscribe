import numpy as np
from scipy.signal import resample_poly

TARGET_RATE = 16000


def planar_float32_to_mono(raw: bytes, channels: int) -> np.ndarray:
    """Convert raw non-interleaved (planar) Float32 PCM to mono float32.

    SCK delivers planar blocks: all channel-0 frames, then all channel-1 frames.
    """
    flat = np.frombuffer(raw, dtype="<f4")
    if channels <= 1 or flat.size == 0:
        return flat.astype("float32", copy=True)
    per_ch = flat.size // channels
    flat = flat[: per_ch * channels]
    planar = flat.reshape(channels, per_ch)  # rows = channels
    return planar.mean(axis=0).astype("float32")


def resample_to_16k(samples: np.ndarray, src_rate: int) -> np.ndarray:
    """Resample mono float32 to 16 kHz for Whisper."""
    if src_rate == TARGET_RATE or samples.size == 0:
        return samples.astype("float32", copy=False)
    g = np.gcd(int(src_rate), TARGET_RATE)
    up, down = TARGET_RATE // g, int(src_rate) // g
    return resample_poly(samples, up, down).astype("float32")


def planar_chunks_to_mono(chunks: list[bytes], channels: int) -> np.ndarray:
    """Convert a list of independent planar Float32 PCM chunks to one mono array.

    SCK delivers each sample buffer as its own planar block ([all L, all R]).
    Concatenating raw bytes first and de-interleaving once would mix channels
    across chunk boundaries — so convert each chunk independently, then join.
    """
    if not chunks:
        return np.zeros(0, dtype="float32")
    return np.concatenate([planar_float32_to_mono(c, channels) for c in chunks])
