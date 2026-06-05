import threading
import time

import numpy as np
import sounddevice as sd

from meetingscribe.config import SAMPLE_RATE, ensure_dirs
from meetingscribe.system_audio import SystemAudioRecorder


class AudioRecorder:
    def __init__(self):
        self._mic_frames = []
        self._mic_stream = None
        self._sys = None
        self._system_available = False
        self._lock = threading.Lock()
        self.t0 = None

    def _mic_callback(self, indata, frames, time_info, status):
        with self._lock:
            self._mic_frames.append(indata.copy())

    def start(self):
        ensure_dirs()
        self._mic_frames = []
        self.t0 = time.time()

        self._mic_stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=self._mic_callback,
        )
        self._mic_stream.start()

        self._sys = SystemAudioRecorder()
        if self._sys.available():
            self._sys.start()
            self._system_available = True
        else:
            self._system_available = False

    def stop(self) -> dict:
        if self._mic_stream:
            self._mic_stream.stop()
            self._mic_stream.close()
            self._mic_stream = None

        with self._lock:
            frames = list(self._mic_frames)

        if frames:
            local = np.concatenate(frames).reshape(-1).astype("float32")
        else:
            local = np.zeros(0, dtype="float32")

        if self._system_available and self._sys is not None:
            remote, remote_rate = self._sys.stop()
        else:
            remote = np.zeros(0, dtype="float32")
            remote_rate = 48000

        return {
            "local": local,
            "local_rate": SAMPLE_RATE,
            "remote": remote,
            "remote_rate": remote_rate,
            "t0": self.t0,
            "system_available": self._system_available,
        }

    def snapshot_side(self, side: str, start_frame: int = 0) -> np.ndarray:
        """Thread-safe mono audio for one side, from start_frame to now.

        Valid during recording and after stop() (stop() does not clear buffers).
        'local' = mic frames; 'remote' = system stream (empty when mic-only).
        """
        if side == "local":
            with self._lock:
                blocks = list(self._mic_frames)
            if not blocks:
                return np.zeros(0, dtype="float32")
            mono = np.concatenate(blocks).reshape(-1).astype("float32")
            return mono[start_frame:]
        if side == "remote":
            if self._system_available and self._sys is not None:
                return self._sys.snapshot(start_frame).astype("float32")
            return np.zeros(0, dtype="float32")
        raise ValueError(f"unknown side: {side}")

    def system_available(self) -> bool:
        return self._system_available

    def remote_rate(self) -> int:
        return self._sys.rate() if (self._system_available and self._sys is not None) else 48000
