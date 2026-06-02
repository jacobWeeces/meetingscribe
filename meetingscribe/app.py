import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import AppKit
import objc
import rumps

from meetingscribe.config import DATA_DIR, ensure_dirs
from meetingscribe.recorder import AudioRecorder, find_blackhole_device
from meetingscribe.transcriber import Transcriber
from meetingscribe.summarizer import Summarizer
from meetingscribe.notes import save_to_notes
from meetingscribe.progress import ProgressWindow
from meetingscribe.secrets import get_api_key, set_api_key
from meetingscribe.updater import init_sparkle, check_for_updates

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


def _is_already_running():
    running = AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(BUNDLE_ID)
    my_pid = os.getpid()
    for app in running:
        if app.processIdentifier() != my_pid:
            return True
    return False


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
    """Ask Laurelle for her Anthropic API key and store it. Returns the key or ''."""
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
        self.menu = [
            rumps.MenuItem(f"MeetingScribe v{_app_version()}"),  # no callback = disabled label
            None,
            rumps.MenuItem("Start Recording", callback=self.toggle_recording),
            None,
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

        log.info("MeetingScribe started (pid %d)", os.getpid())
        log.info("BlackHole device: %s", find_blackhole_device())

        if find_blackhole_device() is None:
            rumps.alert(
                title="MeetingScribe — BlackHole Not Found",
                message=(
                    "BlackHole audio driver is not installed or not active.\n"
                    "System audio capture will be unavailable — mic only.\n\n"
                    "Install with: brew install blackhole-2ch\n"
                    "Then set BlackHole as your system audio output or "
                    "create a Multi-Output Device in Audio MIDI Setup.\n\n"
                    "You may need to restart after installing."
                ),
            )

        if not get_api_key():
            prompt_for_api_key()

        init_sparkle()

    def set_api_key_clicked(self, _sender):
        prompt_for_api_key()

    def toggle_recording(self, sender):
        if self._processing:
            rumps.alert("MeetingScribe", "Still processing the last recording. Please wait.")
            return
        if not self._recording:
            self._start_recording(sender)
        else:
            self._stop_recording(sender)

    def _start_recording(self, sender):
        self._recorder = AudioRecorder()
        self._recorder.start()
        self._recording = True
        self._start_time = time.time()
        sender.title = "Stop Recording"
        self.title = ICON_RECORDING
        log.info("Recording started")

        self._timer_thread = threading.Thread(target=self._update_timer, daemon=True)
        self._timer_thread.start()

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

    def _show_progress(self):
        self._progress_window = ProgressWindow()
        self._progress_window.show()

    def _close_progress(self):
        if self._progress_window:
            self._progress_window.close()
            self._progress_window = None

    def _stop_recording(self, sender):
        self._recording = False
        wav_path = self._recorder.stop()
        sender.title = "Start Recording"
        self.title = "⏳"
        self._processing = True
        log.info("Recording stopped, saved to %s", wav_path)

        self._show_progress()

        thread = threading.Thread(
            target=self._process_recording,
            args=(wav_path,),
            daemon=True,
        )
        thread.start()

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
        self._close_progress()
        notify(title, message)
        _main_thread_alert(f"MeetingScribe — {title}", message)

    def _process_recording(self, wav_path):
        try:
            self._update_progress("Loading model...", detail="First time takes a moment")
            self._transcriber._load_model()

            self._update_progress("Transcribing...", pct=0.0, detail="Converting speech to text")

            def on_transcribe_progress(pct):
                self._update_progress(
                    "Transcribing...",
                    pct=pct,
                    detail=f"{int(pct * 100)}% complete",
                )

            transcript = self._transcriber.transcribe(wav_path, on_progress=on_transcribe_progress)
            log.info("Transcription complete, length: %d chars", len(transcript))

            if not transcript.strip():
                log.warning("No speech detected")
                self._finish("No speech detected", "The recording didn't contain any recognizable speech.")
                return

            self._update_progress("Summarizing...", detail="Generating meeting notes with AI")

            from meetingscribe.summarizer import NoAPIKeyError
            try:
                summary = self._summarizer.summarize(transcript)
            except NoAPIKeyError:
                from datetime import datetime as _dt
                save_to_notes(f"Meeting — {_dt.now():%Y-%m-%d %H:%M}", transcript)
                self._finish(
                    "No API key",
                    "Saved the transcript to Apple Notes, but skipped the AI summary — "
                    "set your Anthropic API key from the menu (Set API Key…).",
                )
                return
            log.info("Summarization complete, length: %d chars", len(summary))

            self._update_progress("Saving to Notes...", pct=0.95, detail="Almost done")

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            title = f"Meeting — {timestamp}"

            body = (
                f"MEETING NOTES — {timestamp}\n"
                f"{'=' * 50}\n\n"
                f"{summary}\n\n"
                f"{'=' * 50}\n"
                f"RAW TRANSCRIPT\n"
                f"{'=' * 50}\n\n"
                f"{transcript}"
            )

            saved = save_to_notes(title, body)

            if saved:
                log.info("Note saved: %s", title)
                try:
                    os.remove(wav_path)
                    log.info("Cleaned up recording: %s", wav_path)
                except OSError as cleanup_err:
                    log.warning("Could not delete recording: %s", cleanup_err)
                self._finish("Done!", f"Your meeting notes have been saved to Apple Notes.\n\n\"{title}\"")
            else:
                log.error("Failed to save to Notes")
                self._finish("Warning", "Transcription complete but couldn't save to Apple Notes.\nCheck that Notes is set up with an iCloud account.")

        except Exception as e:
            log.exception("Error processing recording")
            self._finish("Error", f"Something went wrong:\n\n{str(e)[:300]}")


def main():
    ensure_dirs()

    if _is_already_running():
        log.info("Another instance already running, exiting immediately")
        sys.exit(0)

    app = MeetingScribeApp()
    app.run()


if __name__ == "__main__":
    main()
