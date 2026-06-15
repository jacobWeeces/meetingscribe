import logging
import threading
import time

import numpy as np
import sounddevice as sd

from meetingscribe.config import SAMPLE_RATE, ensure_dirs
from meetingscribe.system_audio import SystemAudioRecorder
from meetingscribe.permissions import mic_authorization_status
from meetingscribe.audio_format import GrowableMonoBuffer

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
        self._mic_rate = SAMPLE_RATE   # rate the mic stream actually opened at
        self._mic_failed = False       # True when no input stream could be opened
        self._sys = None
        self._system_available = False
        self._lock = threading.Lock()
        self.t0 = None
        # Incremental mono accumulator for the local channel: folds each mic block
        # in exactly once so repeated live-tick snapshots don't re-concatenate the
        # whole history (was O(n^2) over a long meeting).
        self._local_accum = None
        self._local_cached_blocks = 0

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

    def _device_default_rate(self, idx):
        """Best-effort native sample rate (Hz) of the chosen input device, else None.

        Opening a constrained device (e.g. a Bluetooth mic in HFP mode during a
        call) at an unrelated rate like 44100 can fail with PortAudio
        paInternalError (-9986) or capture silence; its native rate is the most
        compatible choice.
        """
        try:
            if idx is None:
                idx = sd.default.device[0]
                if not isinstance(idx, int) or idx < 0:
                    return None
        except Exception:
            return None
        info = None
        try:
            info = sd.query_devices(idx)
        except Exception:
            # Some backends / test doubles only support the no-arg list form.
            try:
                devs = sd.query_devices()
                if isinstance(idx, int) and 0 <= idx < len(devs):
                    info = devs[idx]
            except Exception:
                return None
        try:
            rate = int((info or {}).get("default_samplerate") or 0)
            return rate or None
        except Exception:
            return None

    def _open_mic_stream(self, mic_idx):
        """Open the mic InputStream robustly: (actual_rate, stream) or (SAMPLE_RATE, None).

        Tries the device's native rate first, then a few common rates, then the
        PortAudio default device — so a single rate/device hiccup (the field
        PaErrorCode -9986 seen when a call app already holds the mic) doesn't
        kill capture of the user's own voice.
        """
        native = self._device_default_rate(mic_idx)
        rates = []
        for r in (native, SAMPLE_RATE, 48000, 16000):
            if r and r not in rates:
                rates.append(r)
        attempts = [(mic_idx, r) for r in rates]
        attempts.append((None, None))  # last resort: PortAudio's own default device+rate

        last_err = None
        for dev, rate in attempts:
            try:
                kwargs = dict(channels=1, dtype="float32", device=dev,
                              callback=self._mic_callback)
                if rate is not None:
                    kwargs["samplerate"] = rate
                stream = sd.InputStream(**kwargs)
                stream.start()
                try:
                    actual = int(stream.samplerate)
                except (TypeError, ValueError, AttributeError):
                    actual = int(rate) if rate else SAMPLE_RATE
                log.info("mic: opened device=%s at %d Hz",
                         "default" if dev is None else dev, actual)
                return actual, stream
            except Exception as e:  # noqa: BLE001
                last_err = e
                log.warning("mic: InputStream open failed (device=%s rate=%s): %s",
                            dev, rate, e)
        log.error("mic: all input-stream open attempts failed: %s", last_err)
        return SAMPLE_RATE, None

    def _materialized_local(self) -> GrowableMonoBuffer:
        """Fold any not-yet-cached mic blocks into the growable buffer, once each.

        Single-consumer by design: the live worker calls this during recording and
        the Stop thread calls it after the worker is joined, so the cache needs no
        lock of its own — only the brief _mic_frames slice does.
        """
        accum = getattr(self, "_local_accum", None)
        cached = getattr(self, "_local_cached_blocks", 0)
        with self._lock:
            total = len(self._mic_frames)
            if accum is None or total < cached:
                new = list(self._mic_frames)   # first build, or list was reset — rebuild
                rebuild = True
            else:
                new = self._mic_frames[cached:total]
                rebuild = False
        if accum is None or rebuild:
            accum = GrowableMonoBuffer()
            self._local_accum = accum
        for block in new:
            accum.append(np.asarray(block, dtype="float32").reshape(-1))
        self._local_cached_blocks = total
        return accum

    def start(self):
        ensure_dirs()
        self._mic_frames = []
        self._mic_failed = False
        self._local_accum = None
        self._local_cached_blocks = 0
        self.t0 = time.time()

        mic_idx, mic_name = self._choose_mic_device()
        log.info("mic: input device=%s (%s) auth=%s",
                 mic_idx, mic_name or "system default", mic_authorization_status())

        # A mic open failure must NOT abort the whole recording: degrade to
        # system-audio-only so other participants are still captured, and surface
        # the failure (mic_failed) so the caller can warn the user.
        self._mic_rate, self._mic_stream = self._open_mic_stream(mic_idx)
        if self._mic_stream is None:
            self._mic_failed = True
            log.error("mic: could not open any input stream (device in use?); "
                      "recording system audio only")

        # start() is self-sufficient (it fetches shareable content and returns early
        # without a stream if permission/displays are missing), so we call it directly
        # rather than probing available() first — that probe did a second, redundant
        # ScreenCaptureKit run-loop pump on the main thread (~12s worst case) every
        # time recording started. Trust the resulting stream state for availability.
        self._sys = SystemAudioRecorder()
        self._sys.start()
        self._system_available = bool(self._sys.is_capturing())

    def stop(self) -> dict:
        if self._mic_stream:
            self._mic_stream.stop()
            self._mic_stream.close()
            self._mic_stream = None

        local = self._materialized_local().view(0)

        if self._system_available and self._sys is not None:
            remote, remote_rate = self._sys.stop()
        else:
            remote = np.zeros(0, dtype="float32")
            remote_rate = 48000

        local_rate = self._mic_rate or SAMPLE_RATE
        l_rms, l_peak = rms_peak(local)
        r_rms, r_peak = rms_peak(remote)
        log.info("capture levels: local rms=%.5f peak=%.5f (%.1fs) | "
                 "remote rms=%.5f peak=%.5f (%.1fs)",
                 l_rms, l_peak, local.size / local_rate if local.size else 0.0,
                 r_rms, r_peak, remote.size / remote_rate if remote.size and remote_rate else 0.0)

        return {
            "local": local,
            "local_rate": local_rate,
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
            return self._materialized_local().view(start_frame)
        if side == "remote":
            if self._system_available and self._sys is not None:
                return self._sys.snapshot(start_frame).astype("float32")
            return np.zeros(0, dtype="float32")
        raise ValueError(f"unknown side: {side}")

    def system_available(self) -> bool:
        return self._system_available

    def local_rate(self) -> int:
        """Sample rate the mic stream actually opened at (native rate when available)."""
        return self._mic_rate or SAMPLE_RATE

    def mic_failed(self) -> bool:
        """True when no microphone input stream could be opened for this recording."""
        return self._mic_failed

    def remote_rate(self) -> int:
        return self._sys.rate() if (self._system_available and self._sys is not None) else 48000
