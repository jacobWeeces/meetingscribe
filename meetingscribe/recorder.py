import logging
import threading
import time

import numpy as np
import sounddevice as sd

from meetingscribe.config import SAMPLE_RATE, ensure_dirs
from meetingscribe.system_audio import SystemAudioRecorder
from meetingscribe.permissions import mic_authorization_status

log = logging.getLogger("meetingscribe")

# Substrings (case-insensitive) identifying virtual/loopback input devices that
# capture no live microphone signal. A leftover one left as the *default* input
# (e.g. BlackHole from this project's pre-ScreenCaptureKit days) makes the local
# channel record pure silence — so we skip it when a real mic is available.
_VIRTUAL_INPUT_HINTS = (
    "blackhole", "soundflower", "aggregate", "loopback",
    "multi-output", "meetingscribe",
)


def rms_peak(arr) -> tuple[float, float]:
    """Return (rms, peak) of a float audio array; (0.0, 0.0) for empty/None.

    Used to log per-channel signal level so a silent capture (rms ~ 0) is
    visible in the field log instead of only surfacing as Whisper VAD output.
    """
    if arr is None or getattr(arr, "size", 0) == 0:
        return (0.0, 0.0)
    a = arr.astype("float64", copy=False)
    return (float(np.sqrt(np.mean(a * a))), float(np.max(np.abs(a))))


# RMS below this is treated as "no signal" (true silence sits near 1e-5).
SILENCE_RMS = 1e-3


def local_silent_with_remote_signal(local, remote, threshold: float = SILENCE_RMS) -> bool:
    """True when the mic (local) captured silence but system audio (remote) did not.

    This is the silent-mic symptom — only the remote participant gets transcribed.
    Both-silent (a genuine no-speech recording) returns False.
    """
    return rms_peak(local)[0] < threshold <= rms_peak(remote)[0]


def _is_real_input(dev: dict) -> bool:
    name = str(dev.get("name", "")).lower()
    return dev.get("max_input_channels", 0) > 0 and not any(
        hint in name for hint in _VIRTUAL_INPUT_HINTS
    )


def select_input_device(devices, default_index):
    """Choose the input device to record from: (index, name).

    Keeps the system default input when it is a real microphone. If the default
    is a virtual/loopback device (or unset), falls back to the first real input
    so we never silently record a dead channel. Returns (None, None) only when
    no input-capable device exists at all.
    """
    if (default_index is not None and 0 <= default_index < len(devices)
            and _is_real_input(devices[default_index])):
        return default_index, devices[default_index]["name"]
    for i, dev in enumerate(devices):
        if _is_real_input(dev):
            return i, dev["name"]
    if default_index is not None and 0 <= default_index < len(devices):
        return default_index, devices[default_index]["name"]
    return (None, None)


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

    def _choose_mic_device(self):
        """Return (index, name) of the input device to record from.

        Skips a virtual/loopback system default (e.g. a leftover BlackHole) that
        would record silence. Returns (None, _) to fall back to sounddevice's
        own default on any query error.
        """
        try:
            devices = list(sd.query_devices())
            default_in = sd.default.device[0]
            if not isinstance(default_in, int) or default_in < 0:
                default_in = None
            return select_input_device(devices, default_in)
        except Exception:
            log.exception("mic: input-device selection failed; using system default")
            return None, None

    def start(self):
        ensure_dirs()
        self._mic_frames = []
        self.t0 = time.time()

        mic_idx, mic_name = self._choose_mic_device()
        log.info("mic: input device=%s (%s) auth=%s",
                 mic_idx, mic_name or "system default", mic_authorization_status())

        self._mic_stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            device=mic_idx,
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

        l_rms, l_peak = rms_peak(local)
        r_rms, r_peak = rms_peak(remote)
        log.info("capture levels: local rms=%.5f peak=%.5f (%.1fs) | "
                 "remote rms=%.5f peak=%.5f (%.1fs)",
                 l_rms, l_peak, local.size / SAMPLE_RATE if local.size else 0.0,
                 r_rms, r_peak, remote.size / remote_rate if remote.size and remote_rate else 0.0)

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
