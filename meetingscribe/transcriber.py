import logging
import os
import tempfile

import numpy as np
from scipy.io import wavfile
from faster_whisper import WhisperModel

from meetingscribe.config import WHISPER_COMPUTE_TYPE, whisper_model_path, SAMPLE_RATE
from meetingscribe.audio_format import resample_to_16k
from meetingscribe.segments import merge_segments

log = logging.getLogger("meetingscribe")


class Transcriber:
    def __init__(self):
        self._model = None

    def _load_model(self):
        if self._model is None:
            model_path = whisper_model_path()
            log.info("Loading Whisper model from: %s", model_path)
            if os.path.isdir(model_path):
                log.info("Model directory contents: %s", os.listdir(model_path))
            os.environ["HF_HUB_OFFLINE"] = "1"
            self._model = WhisperModel(
                model_path,
                device="cpu",
                compute_type=WHISPER_COMPUTE_TYPE,
                local_files_only=True,
            )
            log.info("Whisper model loaded successfully")

    def transcribe(self, wav_path: str, on_progress=None) -> str:
        self._load_model()
        segments, info = self._model.transcribe(
            str(wav_path),
            beam_size=5,
            vad_filter=True,
        )
        duration = info.duration
        log.info("Processing audio with duration %.1fs", duration)
        lines = []
        for segment in segments:
            lines.append(segment.text.strip())
            if on_progress and duration > 0:
                pct = min(segment.end / duration, 1.0)
                on_progress(pct)
        return "\n".join(lines)

    def transcribe_segments(self, source):
        """Return a materialized list of (start, end, text) tuples.

        `source` may be a path (str/Path) or a float32 ndarray at SAMPLE_RATE. An
        ndarray is written to a temp WAV at SAMPLE_RATE so faster-whisper's decoder
        resamples it to 16 kHz exactly as it does for the on-disk recording — keeping
        live chunks on the same decode path as the end-of-meeting file.
        """
        self._load_model()
        if isinstance(source, np.ndarray):
            # Same int16 scaling as recorder.stop() so the live temp WAV decodes
            # identically to the on-disk recording.
            int_audio = np.clip(source * 32767, -32768, 32767).astype(np.int16)
            fd, tmp = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            try:
                wavfile.write(tmp, SAMPLE_RATE, int_audio)
                segments, _ = self._model.transcribe(tmp, beam_size=5, vad_filter=True)
                return [(s.start, s.end, s.text) for s in segments]
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        segments, _ = self._model.transcribe(str(source), beam_size=5, vad_filter=True)
        return [(s.start, s.end, s.text) for s in segments]

    def transcribe_streams(self, local, local_rate, remote, remote_rate, on_progress=None) -> list[dict]:
        """Transcribe local + remote streams separately, merge into one
        side-attributed, timestamp-ordered segment list ({start,end,text,side,id})."""
        self._load_model()
        streams = [("local", local, local_rate), ("remote", remote, remote_rate)]
        def _dur(arr, rate):
            return arr.shape[0] / rate if rate > 0 and arr.size > 0 else 0.0
        total_dur = sum(_dur(a, r) for _, a, r in streams)
        elapsed, local_segs, remote_segs = 0.0, [], []
        for side, arr, rate in streams:
            if arr.size == 0:
                if on_progress and total_dur > 0:
                    on_progress(min(elapsed / total_dur, 1.0))
                continue
            arr16 = resample_to_16k(arr, rate)
            raw, _info = self._model.transcribe(arr16, beam_size=5, vad_filter=True)
            segs = [{"start": s.start, "end": s.end, "text": s.text.strip(), "side": side} for s in raw]
            if side == "local":
                local_segs = segs
            else:
                remote_segs = segs
            elapsed += _dur(arr, rate)
            if on_progress and total_dur > 0:
                on_progress(min(elapsed / total_dur, 1.0))
        return merge_segments(local_segs, remote_segs)
