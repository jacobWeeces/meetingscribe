import numpy as np
from scipy.signal import resample_poly

TARGET_RATE = 16000


class GrowableMonoBuffer:
    """Append-only mono float32 buffer with amortized-O(1) appends.

    Lets callers fold each new audio block in exactly once and read a bounded
    tail, instead of re-concatenating the entire capture history on every read
    (which was O(n^2) over a long meeting's live ticks). Capacity doubles as
    needed; `view(start)` returns a copy of [start:len].
    """

    def __init__(self, initial_capacity: int = 48000):
        self._buf = np.zeros(max(1, int(initial_capacity)), dtype="float32")
        self._len = 0

    def __len__(self) -> int:
        return self._len

    def append(self, samples: np.ndarray) -> None:
        samples = np.asarray(samples, dtype="float32").reshape(-1)
        n = samples.shape[0]
        if n == 0:
            return
        need = self._len + n
        cap = self._buf.shape[0]
        if need > cap:
            while cap < need:
                cap *= 2
            grown = np.zeros(cap, dtype="float32")
            grown[: self._len] = self._buf[: self._len]
            self._buf = grown
        self._buf[self._len:need] = samples
        self._len = need

    def view(self, start: int = 0) -> np.ndarray:
        start = max(0, int(start))
        if start >= self._len:
            return np.zeros(0, dtype="float32")
        return self._buf[start:self._len].astype("float32", copy=True)


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


class PlanarChunkAccumulator:
    """Incrementally fold a growing list of planar Float32 PCM chunks into mono.

    Equivalent to planar_chunks_to_mono(all_chunks, channels) but folds only the
    new tail on each update() (amortized O(new)), so repeated snapshots during a
    long capture aren't O(n^2). Single-consumer (no internal locking).
    """

    def __init__(self):
        self._buf = GrowableMonoBuffer()
        self._folded = 0

    def update(self, chunks: list, channels: int) -> "PlanarChunkAccumulator":
        total = len(chunks)
        if total < self._folded:        # list was reset/replaced — rebuild from scratch
            self._buf = GrowableMonoBuffer()
            self._folded = 0
        for c in chunks[self._folded:total]:
            self._buf.append(planar_float32_to_mono(c, channels))
        self._folded = total
        return self

    def view(self, start: int = 0) -> np.ndarray:
        return self._buf.view(start)


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
