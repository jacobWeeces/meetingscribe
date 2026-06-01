#!/usr/bin/env python3
"""THROWAWAY SPIKE — capture macOS system audio via ScreenCaptureKit from Python.

Purpose: de-risk replacing BlackHole + Audio MIDI Setup. Proves we can
  (1) obtain Screen Recording permission,
  (2) receive system-audio sample buffers in pure Python (PyObjC),
  (3) extract PCM and write a WAV.

NOT production code. Safe to delete. Run:  python3 spikes/sck_audio_spike.py
A `say` command plays a test sentence during capture so the WAV is non-silent.
"""
import os
import sys
import time
import threading
import wave
import subprocess
import traceback

OUT_WAV = os.path.join(os.path.dirname(__file__), "sck_capture.wav")
CAPTURE_SECS = 6
PERM_WAIT = 12

try:
    import numpy as np
    import objc
    from Foundation import NSObject, NSRunLoop, NSDate, NSDefaultRunLoopMode
    import ScreenCaptureKit as SCK
    import CoreMedia
except Exception as e:
    print("IMPORT FAILED:", repr(e))
    traceback.print_exc()
    sys.exit(2)

AUDIO_TYPE = getattr(SCK, "SCStreamOutputTypeAudio", 1)
KEEP = []  # keep ObjC objects alive

_protocols = []
for _name in ("SCStreamOutput", "SCStreamDelegate"):
    try:
        _protocols.append(objc.protocolNamed(_name))
    except Exception as e:
        print("protocol unavailable:", _name, repr(e))


class Grabber(NSObject, protocols=_protocols):
    def init(self):
        self = objc.super(Grabber, self).init()
        if self is None:
            return None
        self.chunks = []
        self.count = 0
        self.fmt = {}
        self._fmt_tried = False
        self._diagnosed = False
        return self

    # --- SCStreamOutput ---
    def stream_didOutputSampleBuffer_ofType_(self, stream, sbuf, mtype):
        try:
            if int(mtype) != int(AUDIO_TYPE):
                return
            if not CoreMedia.CMSampleBufferIsValid(sbuf):
                return
            self.count += 1

            if not self._fmt_tried:
                self._fmt_tried = True
                fmtdesc = CoreMedia.CMSampleBufferGetFormatDescription(sbuf)
                asbd = CoreMedia.CMAudioFormatDescriptionGetStreamBasicDescription(fmtdesc)
                print("raw ASBD:", asbd)
                try:
                    # AudioStreamBasicDescription field order:
                    # 0=rate 1=formatID 2=flags 3=bytesPerPacket 4=framesPerPacket
                    # 5=bytesPerFrame 6=channels 7=bitsPerChannel 8=reserved
                    self.fmt = dict(rate=float(asbd[0]), flags=int(asbd[2]),
                                    bpf=int(asbd[5]), ch=int(asbd[6]), bits=int(asbd[7]))
                    print("ASBD parsed:", self.fmt)
                except Exception as e:
                    print("ASBD parse error:", repr(e))

            data = self._extract(sbuf)
            if data:
                self.chunks.append(data)
        except Exception as e:
            print("handler error:", repr(e))
            traceback.print_exc()

    @objc.python_method
    def _extract(self, sbuf):
        bb = CoreMedia.CMSampleBufferGetDataBuffer(sbuf)
        if bb is None:
            if not self._diagnosed:
                self._diagnosed = True
                print("CMSampleBufferGetDataBuffer returned None")
            return None
        total = CoreMedia.CMBlockBufferGetDataLength(bb)
        if not total:
            return None
        buf = bytearray(total)
        res = CoreMedia.CMBlockBufferCopyDataBytes(bb, 0, total, buf)
        status = res[0] if isinstance(res, tuple) else res
        if status != 0:
            if not self._diagnosed:
                self._diagnosed = True
                print("CMBlockBufferCopyDataBytes status:", status, "type:", type(res).__name__)
            return None
        return bytes(buf)

    # --- SCStreamDelegate ---
    def stream_didStopWithError_(self, stream, error):
        print("stream didStopWithError:", error)


def pump(rl, seconds, predicate=None):
    end = time.time() + seconds
    while time.time() < end:
        if predicate is not None and predicate():
            return True
        rl.runMode_beforeDate_(NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.05))
    return predicate() if predicate else False


def main():
    print("ScreenCaptureKit:", getattr(SCK, "__file__", "?"))
    rl = NSRunLoop.currentRunLoop()

    state = {}
    perm_done = threading.Event()

    def perm_handler(content, error):
        state["content"] = content
        state["error"] = error
        perm_done.set()

    SCK.SCShareableContent.getShareableContentWithCompletionHandler_(perm_handler)
    pump(rl, PERM_WAIT, perm_done.is_set)
    if not perm_done.is_set():
        print("RESULT: TIMEOUT — Screen Recording permission prompt likely pending.")
        return 3
    if state.get("error") is not None:
        print("RESULT: shareable-content error (Screen Recording likely DENIED):", state["error"])
        return 4

    displays = state["content"].displays()
    if not displays:
        print("RESULT: no displays")
        return 5
    display = displays[0]

    filt = SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(display, [])
    cfg = SCK.SCStreamConfiguration.alloc().init()
    cfg.setCapturesAudio_(True)
    try:
        cfg.setExcludesCurrentProcessAudio_(True)
    except Exception:
        pass
    cfg.setSampleRate_(48000)
    cfg.setChannelCount_(2)
    cfg.setWidth_(2)
    cfg.setHeight_(2)

    grabber = Grabber.alloc().init()
    stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(filt, cfg, grabber)
    KEEP.extend([grabber, stream, filt, cfg])

    ok, err = stream.addStreamOutput_type_sampleHandlerQueue_error_(grabber, AUDIO_TYPE, None, None)
    if not ok:
        print("RESULT: failed to add audio output:", err)
        return 6

    start_done = threading.Event()
    start_state = {}

    def start_handler(error):
        start_state["error"] = error
        start_done.set()

    stream.startCaptureWithCompletionHandler_(start_handler)
    pump(rl, 6, start_done.is_set)
    if start_state.get("error") is not None:
        print("RESULT: startCapture error:", start_state["error"])
        return 7
    print("capture started; playing test audio...")

    try:
        subprocess.Popen(["say", "-r", "175",
                          "Testing one two three. Screen capture kit audio spike. Four five six."])
    except Exception as e:
        print("(could not launch `say`:", repr(e), ")")

    pump(rl, CAPTURE_SECS)

    stop_done = threading.Event()
    stream.stopCaptureWithCompletionHandler_(lambda error: stop_done.set())
    pump(rl, 5, stop_done.is_set)

    raw = b"".join(grabber.chunks)
    rate = int(grabber.fmt.get("rate", 48000))
    ch = int(grabber.fmt.get("ch", 1))
    flags = int(grabber.fmt.get("flags", 0))
    non_interleaved = bool(flags & (1 << 5))

    print("=" * 56)
    print("audio sample-buffer callbacks:", grabber.count)
    print("total PCM bytes captured     :", len(raw))
    print("format: rate=%d ch=%d %s" % (rate, ch, "non-interleaved" if non_interleaved else "interleaved"))

    peak = rms = 0.0
    if raw:
        n = (len(raw) // 4) * 4
        f = np.frombuffer(raw[:n], dtype="<f4")
        if f.size:
            peak = float(np.max(np.abs(f)))
            rms = float(np.sqrt(np.mean(np.square(f))))
            if ch == 2 and not non_interleaved:
                mono = f.reshape(-1, 2).mean(axis=1)
            else:
                mono = f  # planar/mono: best-effort for the spike
            i16 = np.clip(mono * 32767.0, -32768, 32767).astype("<i2")
            with wave.open(OUT_WAV, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(i16.tobytes())
            print("wrote WAV:", OUT_WAV, "(%.1f s mono @ %d Hz)" % (len(i16) / float(rate), rate))
    print("amplitude  peak=%.4f  rms=%.4f" % (peak, rms))
    print("=" * 56)

    if grabber.count > 0 and len(raw) > 0:
        v = "SUCCESS — received audio buffers AND extracted PCM."
        v += " NON-SILENT capture confirmed." if peak > 1e-4 else " (silent — route audio to test sound.)"
        print("RESULT:", v)
        return 0
    print("RESULT: PARTIAL — %d callbacks, %d bytes." % (grabber.count, len(raw)))
    return 8


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("FATAL:", repr(e))
        traceback.print_exc()
        sys.exit(1)
