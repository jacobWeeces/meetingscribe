import difflib
import re
from pathlib import Path

import numpy as np
import pytest
from scipy.io import wavfile

from meetingscribe.transcriber import Transcriber
from meetingscribe.live_transcriber import LiveTranscriber

SAMPLE_WAV = Path(__file__).resolve().parent.parent / "spikes" / "sck_capture.wav"

# Live transcription is never byte-identical to a whole-file pass (Whisper's context
# differs across windows), so we compare a similarity ratio after normalizing away benign
# surface-form noise (case, punctuation, digit-vs-word). The threshold reflects the
# measured chunked-vs-whole parity on the tiled sample.
PARITY_THRESHOLD = 0.95

_NUM_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
}


def _normalize(text):
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(_NUM_WORDS.get(w, w) for w in text.split())


def _read_clip(path):
    sr, data = wavfile.read(str(path))
    audio = data.astype("float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return sr, (audio / 32768.0).astype("float32")  # int16 -> [-1, 1], no peak-norm


def _tile_with_silence(clip, sr, target_sec=50, gap_sec=1.0):
    # Repeat the clip with short silence gaps so VAD has clean boundaries and the audio is
    # long enough for the live path to cross 2 of its 25 s chunk boundaries.
    #
    # ~52 s (4 reps) is deliberate: at ~6 reps (75 s) Whisper's repetition-suppression
    # diverges between the long whole-file pass and the short chunks (an artifact of the
    # repeated phrase, NOT a chunking bug), which tanks the similarity ratio. If
    # sck_capture.wav is ever replaced with longer/varied audio, revisit target_sec and
    # PARITY_THRESHOLD together.
    gap = np.zeros(int(gap_sec * sr), dtype="float32")
    piece = np.concatenate([clip, gap])
    reps = int(np.ceil(target_sec * sr / len(piece)))
    return np.tile(piece, reps)


@pytest.mark.slow
def test_live_pipeline_matches_whole_file():
    """Supplementary guard: chunked live transcription agrees with a single whole-audio
    pass, and chunking actually fired.

    Both passes go through the SAME array->temp-WAV decode (transcribe_segments), so the
    only variable is chunking. The short sample is tiled (with silence gaps) to ~52 s so
    the live path crosses 2 of its 25 s chunk boundaries; we assert committed_sample
    advanced (chunking is not silently inert) and that the normalized transcripts match.

    This is a coarse real-audio agreement check ON TOP OF the unit tests in
    test_live_transcriber.py, which are the primary guard on commit/hold/advance logic.
    It is NOT a correctness proof on natural speech: the tiled repetition is an artifact
    of only having a short sample clip.
    """
    if not SAMPLE_WAV.exists():
        pytest.skip(f"sample audio not present: {SAMPLE_WAV}")

    sr, clip = _read_clip(SAMPLE_WAV)
    audio = _tile_with_silence(clip, sr)

    model = Transcriber()  # one shared model instance for both passes (sequential; safe)

    reference = "\n".join(text for _, _, text in model.transcribe_segments(audio))

    lt = LiveTranscriber(model, sample_rate=sr)
    step = int(25 * sr)
    pos = step
    while pos < len(audio):
        lt.process_tick(audio[lt.committed_sample:pos])
        pos += step
    live = lt.finalize(audio[lt.committed_sample:])

    # Prove the live path actually chunked (not silently inert): without this, a
    # process_tick that committed nothing would leave committed_sample at 0, hand the
    # whole array to finalize, and score ~1.0 — passing while chunking is dead.
    assert lt.ever_committed, "live chunking never committed anything"
    assert lt.committed_sample > int(15 * sr), (
        f"live chunking barely advanced ({lt.committed_sample / sr:.1f}s) — "
        "process_tick may be inert"
    )

    ratio = difflib.SequenceMatcher(None, _normalize(reference), _normalize(live)).ratio()
    assert ratio >= PARITY_THRESHOLD, (
        f"live transcript diverged from whole-file (ratio={ratio:.3f})\n"
        f"REF : {reference!r}\nLIVE: {live!r}"
    )
