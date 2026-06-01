import logging
import os

from faster_whisper import WhisperModel

from meetingscribe.config import WHISPER_COMPUTE_TYPE, whisper_model_path

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
