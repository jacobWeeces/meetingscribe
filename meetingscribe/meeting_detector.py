"""
Meeting auto-detection: polls the default mic input state and running apps to
decide when to prompt the user to start recording.
"""

import ctypes
import ctypes.util
import struct
import threading

# ---------------------------------------------------------------------------
# CoreAudio mic-in-use mechanism (proven in spikes/mic_in_use_spike.py)
# ---------------------------------------------------------------------------
_ca = ctypes.CDLL(ctypes.util.find_library("CoreAudio"))
_SYS = 1


def _fourcc(s):
    return struct.unpack(">I", s.encode("ascii"))[0]


_SEL_DEF_IN = _fourcc("dIn ")
_SEL_RUN = _fourcc("gone")
_SCOPE = _fourcc("glob")
_ELEM = 0


class _Addr(ctypes.Structure):
    _fields_ = [
        ("sel", ctypes.c_uint32),
        ("scope", ctypes.c_uint32),
        ("elem", ctypes.c_uint32),
    ]


_ca.AudioObjectGetPropertyData.restype = ctypes.c_int32
_ca.AudioObjectGetPropertyData.argtypes = [
    ctypes.c_uint32,
    ctypes.POINTER(_Addr),
    ctypes.c_uint32,
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.c_void_p,
]


def _get_u32(oid, sel):
    a = _Addr(sel, _SCOPE, _ELEM)
    out = ctypes.c_uint32(0)
    sz = ctypes.c_uint32(4)
    st = _ca.AudioObjectGetPropertyData(
        oid, ctypes.byref(a), 0, None, ctypes.byref(sz), ctypes.byref(out)
    )
    return st, out.value


def mic_in_use() -> bool:
    """True if any process is using the default input device. False on any error (safe default)."""
    st, dev = _get_u32(_SYS, _SEL_DEF_IN)
    if st != 0 or dev == 0:
        return False
    st2, val = _get_u32(dev, _SEL_RUN)
    return bool(val) if st2 == 0 else False


# ---------------------------------------------------------------------------
# Meeting app detection
# ---------------------------------------------------------------------------
MEETING_BUNDLE_IDS = {"us.zoom.xos", "com.microsoft.teams2", "com.microsoft.teams"}


def _meeting_app_running() -> bool:
    """Return True if a known meeting app is currently running."""
    try:
        import AppKit
        running = AppKit.NSWorkspace.sharedWorkspace().runningApplications()
        for app in running:
            bid = app.bundleIdentifier()
            if bid and bid in MEETING_BUNDLE_IDS:
                return True
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Pure decision logic (easily unit-testable with no I/O)
# ---------------------------------------------------------------------------
def should_prompt(mic_in_use, meeting_app, recording, already_prompted) -> bool:
    """Return True only when all conditions for showing a record-prompt are met."""
    return bool(mic_in_use and meeting_app and not recording and not already_prompted)


# ---------------------------------------------------------------------------
# Detector: background polling loop
# ---------------------------------------------------------------------------
class MeetingDetector:
    """Polls mic state and running apps on a background thread.

    on_detect   -- callable, invoked (from the polling thread) when a meeting
                   is detected and conditions pass should_prompt(); caller is
                   responsible for marshalling to the main thread.
    is_recording -- zero-argument callable returning bool; queries current
                    recording state from the app.
    poll_interval -- seconds between polls (default 1.5).
    """

    def __init__(self, on_detect, is_recording, poll_interval=1.5):
        self._on_detect = on_detect
        self._is_recording = is_recording
        self._poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread = None
        self._prompted = False

    def start(self):
        """Start the polling thread (idempotent — no-op if already running)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._prompted = False
        self._thread = threading.Thread(target=self._loop, daemon=True, name="MeetingDetector")
        self._thread.start()

    def stop(self):
        """Stop the polling thread and wait briefly for it to exit."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._poll_interval + 1.0)
            self._thread = None

    def _loop(self):
        while not self._stop_event.wait(timeout=self._poll_interval):
            try:
                mic = mic_in_use()
                app = _meeting_app_running()

                if should_prompt(mic, app, self._is_recording(), self._prompted):
                    self._prompted = True
                    self._on_detect()

                # Reset prompted flag when mic goes idle so next meeting triggers again
                if not mic:
                    self._prompted = False
            except Exception:
                pass  # never crash the detector thread
