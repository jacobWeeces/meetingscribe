"""Microphone (TCC) permission helpers.

MeetingScribe is a background ``LSUIElement`` app. When it opens its
``sounddevice`` input stream, the implicit macOS microphone prompt renders
*behind* the active call app (Zoom/Teams) and is easily missed — so the mic
stream silently records zeros while system audio (a separate ScreenCaptureKit
grant) works fine, producing a transcript with only the remote speaker.

These helpers let the app explicitly query and request microphone access up
front, mirroring the existing Screen Recording permission UX. AVFoundation is
injectable so the logic is unit-testable without the ObjC runtime.
"""

import logging
import threading
import time

log = logging.getLogger("meetingscribe")

# AVMediaTypeAudio is the FourCC 'soun'.
AUDIO_MEDIA_TYPE = "soun"

# AVAuthorizationStatus raw values.
_STATUS_NAMES = {0: "not_determined", 1: "restricted", 2: "denied", 3: "authorized"}

_DEFAULT = object()  # sentinel: "use the module's AVCaptureDevice"

# Optional import — degrade gracefully off-macOS / in test/headless envs.
try:  # pragma: no cover - import side effect depends on platform
    from AVFoundation import AVCaptureDevice as _AVCaptureDevice
except Exception:  # noqa: BLE001
    _AVCaptureDevice = None


def _resolve(av):
    return _AVCaptureDevice if av is _DEFAULT else av


def mic_authorization_status(av=_DEFAULT) -> str:
    """Return the current mic authorization as a human-readable string.

    One of: 'authorized', 'denied', 'restricted', 'not_determined',
    'unavailable' (AVFoundation missing) or 'unknown' (unmapped value).
    """
    av = _resolve(av)
    if av is None:
        return "unavailable"
    try:
        raw = int(av.authorizationStatusForMediaType_(AUDIO_MEDIA_TYPE))
    except Exception:  # noqa: BLE001
        return "unavailable"
    return _STATUS_NAMES.get(raw, "unknown")


def _default_pump(done: threading.Event, timeout: float) -> None:  # pragma: no cover
    """Pump the main run loop so the async completion handler can fire.

    Mirrors system_audio._pump so the mic prompt resolves on the main thread.
    """
    try:
        from Foundation import NSRunLoop, NSDate, NSDefaultRunLoopMode
        rl = NSRunLoop.currentRunLoop()
        end = time.time() + timeout
        while time.time() < end and not done.is_set():
            rl.runMode_beforeDate_(
                NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.05)
            )
    except Exception:  # noqa: BLE001
        done.wait(timeout)


def request_mic_access(av=_DEFAULT, pump=None, timeout: float = 12.0) -> str:
    """Ensure microphone access, prompting once if undetermined.

    Returns the resulting status string. Only prompts when status is
    'not_determined' (macOS never re-prompts a denied app). Returns
    'unavailable' when AVFoundation is missing.
    """
    av = _resolve(av)
    if av is None:
        return "unavailable"

    status = mic_authorization_status(av=av)
    if status != "not_determined":
        return status

    done = threading.Event()
    result: dict = {}

    def _handler(granted):
        result["granted"] = bool(granted)
        done.set()

    av.requestAccessForMediaType_completionHandler_(AUDIO_MEDIA_TYPE, _handler)
    (pump or _default_pump)(done, timeout)

    if not done.is_set():
        return "not_determined"
    return "authorized" if result.get("granted") else "denied"
