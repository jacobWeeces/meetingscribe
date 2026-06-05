import json
import logging
import os
import sys
import threading
import time
from datetime import datetime

import AppKit
import objc
import rumps

from meetingscribe.config import DATA_DIR, USER_PROFILE, ensure_dirs, SAMPLE_RATE, LIVE_CADENCE_SEC
from meetingscribe import settings
from meetingscribe.recorder import AudioRecorder
from meetingscribe.transcriber import Transcriber
from meetingscribe.summarizer import Summarizer
from meetingscribe.speakers import name_speakers
from meetingscribe.segments import format_transcript
from meetingscribe.system_audio import SystemAudioRecorder
from meetingscribe.meeting_detector import MeetingDetector
from meetingscribe.notes import save_to_notes
from meetingscribe.progress import ProgressWindow
from meetingscribe.secrets import get_api_key, set_api_key
from meetingscribe.updater import init_sparkle, check_for_updates
from meetingscribe.live_transcriber import LiveTranscriber, resolve_segments

ensure_dirs()
LOG_PATH = DATA_DIR / "meetingscribe.log"
logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("meetingscribe")

ICON_IDLE = "🎙"
ICON_RECORDING = "🔴"
BUNDLE_ID = "com.meetingscribe.app"
PROFILE_DISPLAY_NAME = USER_PROFILE.capitalize()

_AUTO_DETECT_PREF = DATA_DIR / "auto_detect.json"


def _load_auto_detect_pref() -> bool:
    """Return the saved auto-detect enabled state; default True if absent."""
    try:
        return json.loads(_AUTO_DETECT_PREF.read_text()).get("enabled", True)
    except Exception:
        return True


def _save_auto_detect_pref(enabled: bool):
    try:
        _AUTO_DETECT_PREF.write_text(json.dumps({"enabled": enabled}))
    except Exception:
        pass


def _bundle_id():
    """The app's ACTUAL bundle identifier in a frozen build — it varies per
    variant (com.meetingscribe.app vs com.meetingscribe.jacob), so the single-
    instance guard must read it at runtime rather than hardcode one. Falls back to
    the default constant in dev/source, where there is no app bundle id and we must
    NOT match unrelated python processes.
    """
    if getattr(sys, "frozen", False):
        try:
            bid = AppKit.NSBundle.mainBundle().bundleIdentifier()
            if bid:
                return str(bid)
        except Exception:
            pass
    return BUNDLE_ID


def _running_pids_for(bundle_id):
    apps = AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle_id)
    return [a.processIdentifier() for a in apps]


def _is_already_running():
    """True if another instance of THIS app (same bundle id) is already running.

    A frozen PyInstaller app can be transiently re-execed (e.g. a multiprocessing
    helper re-launching sys.executable); this guard makes such a child exit in
    main() before it ever builds a second menu-bar session.
    """
    my_pid = os.getpid()
    return any(pid != my_pid for pid in _running_pids_for(_bundle_id()))


def _app_version():
    """Return the app's short version string from the bundle Info.plist ('dev' from source)."""
    try:
        v = AppKit.NSBundle.mainBundle().objectForInfoDictionaryKey_("CFBundleShortVersionString")
        if v:
            return str(v)
    except Exception:
        pass
    return "dev"


def _bring_to_front():
    """Bring this LSUIElement (background) app's windows to the foreground.

    Without this, rumps alerts/windows open behind the active app and the user
    never sees them. Safe to call before any modal.
    """
    try:
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except Exception:
        pass


def notify(subtitle, message):
    try:
        rumps.notification(
            title="MeetingScribe",
            subtitle=subtitle,
            message=message,
        )
    except Exception:
        pass


def _main_thread_alert(title, message):
    """Show an alert on the main thread safely."""
    def _show():
        _bring_to_front()
        rumps.alert(title=title, message=message)

    if threading.current_thread() is threading.main_thread():
        _show()
    else:
        from PyObjCTools import AppHelper
        AppHelper.callAfter(_show)


def prompt_for_api_key():
    """Ask the user for their Anthropic API key and store it. Returns the key or ''."""
    win = rumps.Window(
        title="MeetingScribe — Anthropic API Key",
        message=(
            "Paste your Anthropic API key to enable AI meeting summaries.\n"
            "Get one at https://console.anthropic.com/settings/keys"
        ),
        default_text="",
        ok="Save",
        cancel="Skip",
        dimensions=(360, 24),
    )
    _bring_to_front()
    resp = win.run()
    if resp.clicked and resp.text.strip():
        set_api_key(resp.text)
        return resp.text.strip()
    return ""


class MeetingScribeApp(rumps.App):
    def __init__(self):
        super().__init__(ICON_IDLE, quit_button=None)

        # Build auto-detect menu item with persisted state
        _auto_detect_enabled = _load_auto_detect_pref()
        _auto_detect_item = rumps.MenuItem(
            "Auto-detect meetings", callback=self._toggle_auto_detect
        )
        _auto_detect_item.state = int(_auto_detect_enabled)

        live_item = rumps.MenuItem("Live transcription", callback=self.toggle_live_transcription)
        live_item.state = 1 if settings.live_transcription_enabled() else 0

        self.menu = [
            rumps.MenuItem(f"MeetingScribe v{_app_version()}"),
            None,
            rumps.MenuItem("Start Recording", callback=self.toggle_recording),
            None,
            _auto_detect_item,
            live_item,
            rumps.MenuItem("Set API Key…", callback=self.set_api_key_clicked),
            rumps.MenuItem("Check for Updates…", callback=check_for_updates),
            None,
            rumps.MenuItem("Quit", callback=rumps.quit_application),
        ]

        self._recorder = AudioRecorder()
        self._transcriber = Transcriber()
        self._summarizer = Summarizer()
        self._recording = False
        self._processing = False
        self._start_time = None
        self._timer_thread = None
        self._progress_window = None
        # Live worker placeholders — NOT started here; wired in Phase 4
        self._live_local = None
        self._live_remote = None
        self._live_worker_thread = None

        # Auto-detect detector
        self._detector = MeetingDetector(
            on_detect=self._on_meeting_detected,
            is_recording=lambda: self._recording,
        )
        if _auto_detect_enabled:
            self._detector.start()

        log.info("MeetingScribe started (pid %d)", os.getpid())

        if not get_api_key():
            prompt_for_api_key()

        init_sparkle()

        if not SystemAudioRecorder().available():
            rumps.alert(
                title="MeetingScribe — Screen Recording needed",
                message=(
                    "System-audio capture needs Screen Recording permission.\n"
                    "Grant it in System Settings → Privacy & Security → Screen Recording, "
                    "then reopen MeetingScribe.\n\n"
                    "Until then, recordings will capture your microphone only."
                ),
            )

    # ------------------------------------------------------------------
    # API key
    # ------------------------------------------------------------------

    def set_api_key_clicked(self, _sender):
        prompt_for_api_key()

    # ------------------------------------------------------------------
    # Live transcription toggle (checkbox only — worker not started yet)
    # ------------------------------------------------------------------

    def toggle_live_transcription(self, sender):
        sender.state = 0 if sender.state else 1
        settings.set_live_transcription(bool(sender.state))
        log.info("Live transcription set to %s (applies to next recording)", bool(sender.state))

    # ------------------------------------------------------------------
    # Recording toggle
    # ------------------------------------------------------------------

    def toggle_recording(self, sender):
        log.info("toggle_recording: recording=%s processing=%s", self._recording, self._processing)
        if self._processing:
            rumps.alert("MeetingScribe", "Still processing the last recording. Please wait.")
            return
        if not self._recording:
            self._start_recording(sender)
        else:
            self._stop_recording(sender)

    # ------------------------------------------------------------------
    # Auto-detect callbacks
    # ------------------------------------------------------------------

    def _toggle_auto_detect(self, sender):
        """Toggle the auto-detect pref and start/stop the detector accordingly."""
        sender.state = 0 if sender.state else 1
        enabled = bool(sender.state)
        _save_auto_detect_pref(enabled)
        if enabled:
            self._detector.start()
        else:
            self._detector.stop()
        log.info("Auto-detect meetings: %s", "on" if enabled else "off")

    def _on_meeting_detected(self):
        """Called from the detector thread; marshal the prompt to the main thread."""
        from PyObjCTools import AppHelper
        AppHelper.callAfter(self._prompt_record)

    def _prompt_record(self):
        """Show the meeting-detected confirmation popup, forced above other apps.

        MeetingScribe is an accessory app (LSUIElement), so a plain alert renders
        behind the frontmost app (Zoom/Teams). Activate the app, raise the alert's
        window level, and let it appear over full-screen Spaces.
        """
        if self._recording or self._processing:
            return

        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("Meeting detected")
        alert.setInformativeText_("Start recording this meeting?")
        alert.addButtonWithTitle_("Start")
        alert.addButtonWithTitle_("Not now")

        window = alert.window()
        window.setLevel_(AppKit.NSModalPanelWindowLevel)
        window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        )

        ret = alert.runModal()
        log.info("auto-detect: prompt closed (ret=%s, first-button=%s)", ret, AppKit.NSAlertFirstButtonReturn)
        if ret == AppKit.NSAlertFirstButtonReturn:
            # Defer the start out of this modal-completion context. Starting capture
            # here runs a nested run loop (ScreenCaptureKit handler pump) *inside* the
            # just-dismissed NSAlert, where it doesn't reliably proceed; a fresh
            # AppHelper.callAfter turn behaves like the normal menu-click path.
            from PyObjCTools import AppHelper
            log.info("auto-detect: Start chosen; scheduling _start_recording")
            AppHelper.callAfter(self.toggle_recording, self.menu["Start Recording"])

    # ------------------------------------------------------------------
    # Recording start / stop
    # ------------------------------------------------------------------

    def _start_recording(self, sender):
        log.info("_start_recording: entered")
        try:
            self._recorder = AudioRecorder()
            _t0 = time.time()
            self._recorder.start()
            log.info("_start_recording: recorder.start() in %.2fs (system_available=%s)",
                     time.time() - _t0, self._recorder.system_available())
            self._recording = True
            self._start_time = time.time()
            sender.title = "Stop Recording"
            self.title = ICON_RECORDING
            log.info("Recording started")

            self._timer_thread = threading.Thread(target=self._update_timer, daemon=True)
            self._timer_thread.start()

            if settings.live_transcription_enabled():
                self._live_local = LiveTranscriber(self._transcriber, SAMPLE_RATE, side="local")
                remote_rate = self._recorder.remote_rate() if self._recorder.system_available() else 48000
                self._live_remote = LiveTranscriber(self._transcriber, remote_rate, side="remote")
                self._live_worker_thread = threading.Thread(target=self._live_worker, daemon=True)
                self._live_worker_thread.start()
                log.info("Live transcription workers started (per-channel)")
            else:
                self._live_local = self._live_remote = self._live_worker_thread = None
        except Exception:
            log.exception("_start_recording FAILED")
            raise

    def _update_timer(self):
        while self._recording:
            elapsed = int(time.time() - self._start_time)
            mins, secs = divmod(elapsed, 60)
            hours, mins = divmod(mins, 60)
            if hours:
                self.title = f"{ICON_RECORDING} {hours}:{mins:02d}:{secs:02d}"
            else:
                self.title = f"{ICON_RECORDING} {mins}:{secs:02d}"
            time.sleep(1)

    def _live_worker(self):
        try:
            self._transcriber._load_model()  # preload so first tick & Stop never stall cold
        except Exception:
            log.exception("live: model preload failed; disabling live for this session")
            self._live_local = self._live_remote = None
            return
        while self._recording:
            for _ in range(LIVE_CADENCE_SEC):
                if not self._recording:
                    break
                time.sleep(1)
            if not self._recording:
                break
            for side, lt in (("local", self._live_local), ("remote", self._live_remote)):
                if lt is None:
                    continue
                try:
                    lt.process_tick(self._recorder.snapshot_side(side, lt.committed_sample))
                except Exception:
                    log.exception("live: %s tick failed", side)

    def _show_progress(self):
        self._progress_window = ProgressWindow()
        self._progress_window.show()

    def _close_progress(self):
        if self._progress_window:
            self._progress_window.close()
            self._progress_window = None

    def _stop_recording(self, sender):
        self._recording = False
        sender.title = "Start Recording"
        self.title = "⏳"
        self._processing = True
        log.info("Recording stopped, starting processing")

        self._show_progress()

        thread = threading.Thread(
            target=self._process_recording,
            daemon=True,
        )
        thread.start()

    # ------------------------------------------------------------------
    # Progress helpers
    # ------------------------------------------------------------------

    def _update_progress(self, stage, pct=None, detail=""):
        self.title = f"⏳ {stage}"
        log.info("Status: %s (%.0f%%)", stage, (pct or 0) * 100)
        if self._progress_window:
            self._progress_window.set_stage(stage)
            self._progress_window.set_detail(detail)
            if pct is not None:
                self._progress_window.set_indeterminate(False)
                self._progress_window.set_progress(pct * 100)
            else:
                self._progress_window.set_indeterminate(True)

    def _finish(self, title, message):
        self.title = ICON_IDLE
        self._processing = False
        self._live_local = None
        self._live_remote = None
        self._close_progress()
        notify(title, message)
        _main_thread_alert(f"MeetingScribe — {title}", message)

    # ------------------------------------------------------------------
    # Attributed post-Stop pipeline (live-aware)
    # ------------------------------------------------------------------

    def _process_recording(self):
        try:
            # Single Whisper model: fully stop the live worker before transcribing here.
            if self._live_worker_thread is not None:
                self._live_worker_thread.join()
                self._live_worker_thread = None

            self._update_progress("Loading model...", detail="First time takes a moment")
            self._transcriber._load_model()
            result = self._recorder.stop()

            # Safe to read here: the worker was joined above, so its happens-before
            # guarantee makes any None-write (preload failure) visible. Do not move
            # this read before the join.
            ll, lr = self._live_local, self._live_remote
            final_local = self._recorder.snapshot_side("local", ll.committed_sample) if ll is not None else None
            final_remote = self._recorder.snapshot_side("remote", lr.committed_sample) if lr is not None else None

            def on_tx(p):
                self._update_progress("Transcribing...", pct=p, detail=f"{int(p*100)}% complete")

            self._update_progress("Transcribing...", pct=0.0, detail="Converting speech to text")
            segments = resolve_segments(self._transcriber, ll, lr, final_local, final_remote, result, on_progress=on_tx)
            log.info("Transcription complete, %d segment(s)", len(segments))

            if not segments:
                log.warning("No speech detected")
                self._finish("No speech detected", "The recording didn't contain any recognizable speech.")
                return

            self._update_progress("Identifying speakers...", detail="Labeling who said what")
            named = name_speakers(segments, local_name=PROFILE_DISPLAY_NAME)
            transcript_text = format_transcript(named)
            log.info("Speaker naming complete, transcript length: %d chars", len(transcript_text))

            self._update_progress("Summarizing...", detail="Generating meeting notes with AI")
            from meetingscribe.summarizer import NoAPIKeyError
            try:
                summary = self._summarizer.summarize(transcript_text)
            except NoAPIKeyError:
                from datetime import datetime as _dt
                save_to_notes(f"Meeting — {_dt.now():%Y-%m-%d %H:%M}", transcript_text)
                self._finish(
                    "No API key",
                    "Saved the transcript to Apple Notes, but skipped the AI summary — "
                    "set your Anthropic API key (Set API Key…).",
                )
                return
            log.info("Summarization complete, length: %d chars", len(summary))

            self._update_progress("Saving to Notes...", pct=0.95, detail="Almost done")
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            title = f"Meeting — {timestamp}"
            body = (
                f"MEETING NOTES — {timestamp}\n{'=' * 50}\n\n{summary}\n\n"
                f"{'=' * 50}\nRAW TRANSCRIPT\n{'=' * 50}\n\n{transcript_text}"
            )
            saved = save_to_notes(title, body)
            if saved:
                log.info("Note saved: %s", title)
                self._finish("Done!", f"Your meeting notes have been saved to Apple Notes.\n\n\"{title}\"")
            else:
                log.error("Failed to save to Notes")
                self._finish("Warning", "Transcription complete but couldn't save to Apple Notes.\nCheck that Notes is set up with an iCloud account.")

        except Exception as e:
            log.exception("Error processing recording")
            self._finish("Error", f"Something went wrong:\n\n{str(e)[:300]}")
        finally:
            if getattr(self._recorder, "_sys", None) is not None:
                try:
                    self._recorder._sys.release()
                except Exception:
                    pass


def main():
    ensure_dirs()

    if _is_already_running():
        log.info("Another instance already running, exiting immediately")
        sys.exit(0)

    app = MeetingScribeApp()
    app.run()


if __name__ == "__main__":
    main()
