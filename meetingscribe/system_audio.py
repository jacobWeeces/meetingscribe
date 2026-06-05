"""SystemAudioRecorder — capture macOS system audio via ScreenCaptureKit.

Replaces the BlackHole virtual device approach.  Requires macOS Screen
Recording permission (System Settings → Privacy & Security → Screen Recording).

Usage::

    rec = SystemAudioRecorder()
    if rec.available():
        rec.start()
        # … your capture window …
        audio, rate = rec.stop()
"""

import threading
import time
import traceback

import numpy as np

# ---------------------------------------------------------------------------
# Optional imports — degrade gracefully on non-macOS or missing deps
# ---------------------------------------------------------------------------
try:
    import objc
    from Foundation import NSObject, NSRunLoop, NSDate, NSDefaultRunLoopMode
    import ScreenCaptureKit as SCK
    import CoreMedia

    _SCK_AVAILABLE = True
    _AUDIO_TYPE = getattr(SCK, "SCStreamOutputTypeAudio", 1)
except Exception:  # noqa: BLE001
    _SCK_AVAILABLE = False
    _AUDIO_TYPE = 1

# Seconds to wait for async SCK permission / start / stop handlers
_PERM_TIMEOUT = 12.0
_OP_TIMEOUT = 6.0


# ---------------------------------------------------------------------------
# Run-loop helper (verbatim pattern from sck_audio_spike.py)
# ---------------------------------------------------------------------------

def _pump(run_loop, seconds: float, predicate=None) -> bool:
    """Pump *run_loop* for up to *seconds*, optionally early-exiting."""
    end = time.time() + seconds
    while time.time() < end:
        if predicate is not None and predicate():
            return True
        run_loop.runMode_beforeDate_(
            NSDefaultRunLoopMode,
            NSDate.dateWithTimeIntervalSinceNow_(0.05),
        )
    return predicate() if predicate else False


# ---------------------------------------------------------------------------
# ObjC delegate (only defined when imports succeeded)
# ---------------------------------------------------------------------------

if _SCK_AVAILABLE:
    _protocols: list = []
    for _pname in ("SCStreamOutput", "SCStreamDelegate"):
        try:
            _protocols.append(objc.protocolNamed(_pname))
        except Exception:  # noqa: BLE001
            pass

    class _AudioDelegate(NSObject, protocols=_protocols):
        """Receives audio sample-buffer callbacks from SCStream."""

        def init(self):
            self = objc.super(_AudioDelegate, self).init()
            if self is None:
                return None
            self._chunks: list[bytes] = []
            self._count: int = 0
            self._rate: float = 48000.0
            self._channels: int = 2
            self._fmt_done: bool = False
            self._diagnosed: bool = False
            self._lock = threading.Lock()
            return self

        # --- SCStreamOutput ---
        def stream_didOutputSampleBuffer_ofType_(self, stream, sbuf, mtype):
            try:
                if int(mtype) != int(_AUDIO_TYPE):
                    return
                if not CoreMedia.CMSampleBufferIsValid(sbuf):
                    return
                self._count += 1

                if not self._fmt_done:
                    self._fmt_done = True
                    try:
                        fmtdesc = CoreMedia.CMSampleBufferGetFormatDescription(sbuf)
                        asbd = CoreMedia.CMAudioFormatDescriptionGetStreamBasicDescription(fmtdesc)
                        # ASBD tuple: [0]=sampleRate, [2]=flags, [6]=channelCount
                        self._rate = float(asbd[0])
                        self._channels = int(asbd[6])
                    except Exception:  # noqa: BLE001
                        pass  # keep defaults on parse failure

                data = self._extract(sbuf)
                if data:
                    with self._lock:
                        self._chunks.append(data)
            except Exception:  # noqa: BLE001
                pass  # never propagate into ObjC runtime

        @objc.python_method
        def _extract(self, sbuf) -> bytes | None:
            bb = CoreMedia.CMSampleBufferGetDataBuffer(sbuf)
            if bb is None:
                if not self._diagnosed:
                    self._diagnosed = True
                return None
            total = CoreMedia.CMBlockBufferGetDataLength(bb)
            if not total:
                return None
            buf = bytearray(total)
            res = CoreMedia.CMBlockBufferCopyDataBytes(bb, 0, total, buf)
            status = res[0] if isinstance(res, tuple) else res
            if status != 0:
                return None
            return bytes(buf)

        @objc.python_method
        def snapshot_mono(self, start_frame: int) -> np.ndarray:
            """Return mono float32 from start_frame to latest (thread-safe)."""
            from meetingscribe.audio_format import planar_chunks_to_mono
            with self._lock:
                chunks = list(self._chunks)        # cheap ref copy; convert outside the lock
                channels = int(self._channels)
            mono = planar_chunks_to_mono(chunks, channels)
            return mono[start_frame:]

        # --- SCStreamDelegate ---
        def stream_didStopWithError_(self, stream, error):
            pass  # production code — silent


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------

class SystemAudioRecorder:
    """Capture system audio (remote side of calls) via ScreenCaptureKit.

    Screen Recording permission must be granted in System Preferences.
    No BlackHole or virtual device required.
    """

    def __init__(self) -> None:
        self._stream = None
        self._delegate: "_AudioDelegate | None" = None
        self._keep: list = []  # strong refs so ObjC objects aren't GC'd
        self._stopped = False

    # ------------------------------------------------------------------

    def available(self) -> bool:
        """Return True if Screen Recording permission is granted and a display exists.

        Pumps the run loop briefly (up to ~12 s on first call) to await the
        async SCShareableContent handler — identical to the spike approach.
        """
        if not _SCK_AVAILABLE:
            return False

        state: dict = {}
        done = threading.Event()

        def _handler(content, error):
            state["content"] = content
            state["error"] = error
            done.set()

        try:
            SCK.SCShareableContent.getShareableContentWithCompletionHandler_(_handler)
        except Exception:  # noqa: BLE001
            return False

        rl = NSRunLoop.currentRunLoop()
        _pump(rl, _PERM_TIMEOUT, done.is_set)

        if not done.is_set():
            return False
        if state.get("error") is not None:
            return False
        content = state.get("content")
        if content is None:
            return False
        try:
            displays = content.displays()
        except Exception:  # noqa: BLE001
            return False
        return bool(displays)

    # ------------------------------------------------------------------

    def start(self) -> None:
        """Begin capturing system audio.

        Builds ``SCContentFilter`` + ``SCStreamConfiguration``, creates the
        stream, wires the audio output, and starts capture.  Returns as soon
        as the async start handler fires (pumps the run loop briefly).

        Audio callbacks will be delivered to the delegate via the host
        run-loop (the rumps event loop in the main app) — we do *not* block
        here during the capture window.
        """
        if not _SCK_AVAILABLE:
            return

        # --- obtain display list ----------------------------------------
        state: dict = {}
        done = threading.Event()

        def _content_handler(content, error):
            state["content"] = content
            state["error"] = error
            done.set()

        SCK.SCShareableContent.getShareableContentWithCompletionHandler_(_content_handler)
        rl = NSRunLoop.currentRunLoop()
        _pump(rl, _PERM_TIMEOUT, done.is_set)

        if not done.is_set() or state.get("error") is not None:
            return
        content = state.get("content")
        if content is None:
            return
        displays = content.displays()
        if not displays:
            return
        display = displays[0]

        # --- build filter + config --------------------------------------
        filt = SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(display, [])
        cfg = SCK.SCStreamConfiguration.alloc().init()
        cfg.setCapturesAudio_(True)
        try:
            cfg.setExcludesCurrentProcessAudio_(True)
        except Exception:  # noqa: BLE001
            pass
        cfg.setSampleRate_(48000)
        cfg.setChannelCount_(2)
        cfg.setWidth_(2)
        cfg.setHeight_(2)

        # --- create delegate + stream ------------------------------------
        delegate = _AudioDelegate.alloc().init()
        stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(filt, cfg, delegate)

        # Keep strong refs — essential to prevent ObjC GC during capture
        self._keep = [delegate, stream, filt, cfg]
        self._delegate = delegate
        self._stream = stream
        self._stopped = False

        ok, _err = stream.addStreamOutput_type_sampleHandlerQueue_error_(
            delegate, _AUDIO_TYPE, None, None
        )
        if not ok:
            self._stream = None
            self._keep = []
            return

        # --- start capture ----------------------------------------------
        start_state: dict = {}
        start_done = threading.Event()

        def _start_handler(error):
            start_state["error"] = error
            start_done.set()

        stream.startCaptureWithCompletionHandler_(_start_handler)
        _pump(rl, _OP_TIMEOUT, start_done.is_set)

        if start_state.get("error") is not None:
            self._stream = None
            self._keep = []

    # ------------------------------------------------------------------

    def stop(self) -> tuple:
        """Stop capture and return ``(mono_float32_array, sample_rate)``.

        Blocks briefly to await the async stop handler, then assembles
        accumulated PCM chunks via per-chunk mono conversion (preserves
        planar boundaries across buffer edges).  The delegate is kept alive
        so ``snapshot()`` remains readable after Stop.

        Returns ``(np.zeros(0, dtype='float32'), 48000)`` if nothing was
        captured (e.g. *start()* failed or no audio arrived).
        """
        empty = (np.zeros(0, dtype="float32"), 48000)

        stream = self._stream
        delegate = self._delegate

        if stream is None or delegate is None:
            return empty

        # --- stop stream ------------------------------------------------
        stop_done = threading.Event()
        stream.stopCaptureWithCompletionHandler_(lambda _err: stop_done.set())
        rl = NSRunLoop.currentRunLoop()
        _pump(rl, _OP_TIMEOUT, stop_done.is_set)

        self._stream = None
        # NOTE: intentionally keep self._delegate and self._keep alive so
        # snapshot() remains readable after stop.
        self._stopped = True

        # --- assemble PCM via per-chunk mono ----------------------------
        rate = int(delegate._rate)
        return (delegate.snapshot_mono(0), rate)

    # ------------------------------------------------------------------

    def snapshot(self, start_frame: int) -> np.ndarray:
        """Mono system audio from start_frame to now (valid during and after capture)."""
        d = self._delegate
        if d is None:
            return np.zeros(0, dtype="float32")
        return d.snapshot_mono(start_frame)

    def rate(self) -> int:
        """Return the detected sample rate (defaults to 48000 before first buffer)."""
        return int(self._delegate._rate) if self._delegate is not None else 48000

    def release(self) -> None:
        """Drop ObjC refs once the recording is fully processed."""
        self._stream = None
        self._delegate = None
        self._keep = []
