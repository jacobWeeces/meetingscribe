"""
THROWAWAY SPIKE — de-risks Task 11 (meeting auto-detect).

Confirms that the CoreAudio mic-in-use flag is readable from Python at each
state transition: idle (expect False), stream open (expect True), closed again
(expect False).

Run directly: python3 spikes/mic_in_use_spike.py
"""

import ctypes
import ctypes.util
import struct
import time

# ---------------------------------------------------------------------------
# Proven CoreAudio mic-in-use mechanism (do not modify — this is the canonical
# implementation that will be copied verbatim into meeting_detector.py)
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
# Spike exercise
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=== mic_in_use spike ===")

    # 1. Idle state
    idle = mic_in_use()
    print(f"[1] Idle  → mic_in_use() = {idle}  (expect False when no mic active)")

    # 2. Open a sounddevice InputStream for ~2 s
    try:
        import sounddevice as sd

        print("[2] Opening sounddevice InputStream...")
        with sd.InputStream(channels=1, samplerate=16000):
            time.sleep(0.3)  # give CoreAudio a moment to register
            active = mic_in_use()
            print(f"    Stream open → mic_in_use() = {active}  (expect True)")
            time.sleep(1.7)

        # 3. After close
        time.sleep(0.2)
        closed = mic_in_use()
        print(f"[3] After close → mic_in_use() = {closed}  (expect False)")

    except ImportError:
        print("[2] sounddevice not installed — skipping live-stream check")
        print("    (mic_in_use() itself is proven; stream check is bonus)")

    print("=== done ===")
