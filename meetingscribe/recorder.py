import threading
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd
from scipy.io import wavfile

from meetingscribe.config import (
    BLACKHOLE_DEVICE_NAME,
    RECORDINGS_DIR,
    SAMPLE_RATE,
    ensure_dirs,
)


def find_blackhole_device():
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        if BLACKHOLE_DEVICE_NAME in d["name"] and d["max_input_channels"] > 0:
            return i
    return None


class AudioRecorder:
    def __init__(self):
        self._mic_frames = []
        self._sys_frames = []
        self._mic_stream = None
        self._sys_stream = None
        self._recording = False
        self._lock = threading.Lock()
        self._blackhole_id = find_blackhole_device()

    @property
    def has_system_audio(self):
        return self._blackhole_id is not None

    def _mic_callback(self, indata, frames, time_info, status):
        with self._lock:
            self._mic_frames.append(indata.copy())

    def _sys_callback(self, indata, frames, time_info, status):
        with self._lock:
            self._sys_frames.append(indata.copy())

    def snapshot_mono(self, start_sample: int = 0) -> np.ndarray:
        """Mono mix (mic + sys)/2 of everything captured so far, from start_sample on.

        Mic-only when there's no system stream. Clipped to the shorter of the two
        streams, matching how stop() aligns them. Safe to call while recording.
        """
        # Copy the frame-list references under the lock (cheap O(n) pointer copy), then
        # concatenate OUTSIDE the lock. The callbacks only ever append new blocks, so the
        # copied references stay valid; this avoids stalling the capture callbacks (xruns)
        # behind a multi-millisecond concatenate on a long meeting.
        with self._lock:
            mic_blocks = list(self._mic_frames)
            sys_blocks = list(self._sys_frames) if self._sys_frames else None

        mic = np.concatenate(mic_blocks) if mic_blocks else np.zeros((0, 1), dtype="float32")
        sys = np.concatenate(sys_blocks) if sys_blocks else None

        mic = mic[:, 0] if mic.ndim > 1 else mic
        if sys is not None and len(sys) > 0:
            sys = sys[:, 0] if sys.ndim > 1 else sys
            n = min(len(mic), len(sys))
            mono = (mic[:n] + sys[:n]) / 2.0
        else:
            mono = mic
        return mono[start_sample:].astype("float32")

    def start(self):
        ensure_dirs()
        self._mic_frames = []
        self._sys_frames = []
        self._recording = True

        self._mic_stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=self._mic_callback,
        )
        self._mic_stream.start()

        if self._blackhole_id is not None:
            self._sys_stream = sd.InputStream(
                device=self._blackhole_id,
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                callback=self._sys_callback,
            )
            self._sys_stream.start()

    def stop(self) -> Path:
        self._recording = False

        if self._mic_stream:
            self._mic_stream.stop()
            self._mic_stream.close()
            self._mic_stream = None

        if self._sys_stream:
            self._sys_stream.stop()
            self._sys_stream.close()
            self._sys_stream = None

        mic_audio = np.concatenate(self._mic_frames) if self._mic_frames else np.zeros((0, 1), dtype="float32")
        sys_audio = np.concatenate(self._sys_frames) if self._sys_frames else None

        if sys_audio is not None:
            min_len = min(len(mic_audio), len(sys_audio))
            mic_audio = mic_audio[:min_len]
            sys_audio = sys_audio[:min_len]
            stereo = np.hstack([mic_audio, sys_audio])
        else:
            stereo = mic_audio

        int_audio = np.clip(stereo * 32767, -32768, 32767).astype(np.int16)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = RECORDINGS_DIR / f"recording_{timestamp}.wav"
        wavfile.write(str(path), SAMPLE_RATE, int_audio)

        return path
