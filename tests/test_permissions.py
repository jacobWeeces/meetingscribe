"""Tests for microphone TCC permission handling.

The app is a background LSUIElement; the implicit PortAudio mic prompt renders
behind the call app and is missed, so the mic records silence. These helpers
let the app explicitly query and request mic access (mirroring the existing
Screen Recording UX). AVFoundation is injected so tests need no ObjC runtime.
"""


class _FakeAV:
    """Stand-in for AVCaptureDevice with a settable authorization status."""

    def __init__(self, status, grant=None):
        self._status = status
        self._grant = grant
        self.requested_media = None

    def authorizationStatusForMediaType_(self, media):
        return self._status

    def requestAccessForMediaType_completionHandler_(self, media, handler):
        self.requested_media = media
        # Real API is async; fakes fire synchronously so pump() is a no-op.
        handler(self._grant)


def test_status_maps_authorized():
    from meetingscribe import permissions
    assert permissions.mic_authorization_status(av=_FakeAV(3)) == "authorized"


def test_status_maps_denied_restricted_not_determined():
    from meetingscribe import permissions
    assert permissions.mic_authorization_status(av=_FakeAV(2)) == "denied"
    assert permissions.mic_authorization_status(av=_FakeAV(1)) == "restricted"
    assert permissions.mic_authorization_status(av=_FakeAV(0)) == "not_determined"


def test_status_unavailable_when_no_avfoundation():
    from meetingscribe import permissions
    assert permissions.mic_authorization_status(av=None) == "unavailable"


def test_request_returns_immediately_when_already_authorized():
    from meetingscribe import permissions
    av = _FakeAV(3)
    assert permissions.request_mic_access(av=av, pump=lambda *_: None) == "authorized"
    assert av.requested_media is None  # never prompts when already granted


def test_request_does_not_reprompt_when_denied():
    from meetingscribe import permissions
    av = _FakeAV(2)
    assert permissions.request_mic_access(av=av, pump=lambda *_: None) == "denied"
    assert av.requested_media is None


def test_request_prompts_when_not_determined_and_grants():
    from meetingscribe import permissions
    av = _FakeAV(0, grant=True)
    assert permissions.request_mic_access(av=av, pump=lambda *_: None) == "authorized"
    assert av.requested_media == permissions.AUDIO_MEDIA_TYPE


def test_request_prompts_when_not_determined_and_denies():
    from meetingscribe import permissions
    av = _FakeAV(0, grant=False)
    assert permissions.request_mic_access(av=av, pump=lambda *_: None) == "denied"


def test_request_unavailable_when_no_avfoundation():
    from meetingscribe import permissions
    assert permissions.request_mic_access(av=None, pump=lambda *_: None) == "unavailable"
