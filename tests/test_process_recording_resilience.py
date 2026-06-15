"""The post-Stop pipeline must never lose the transcript.

The recording is never persisted to disk — the ONLY durable output is the Apple
Note saved at the end of MeetingScribeApp._process_recording. A transient
Anthropic/API error from summarize() previously threw straight to the generic
"Error" handler, which does NOT save anything — so the entire meeting transcript
was lost on any network hiccup. The no-API-key path already saved the transcript;
a failed summary must do the same.
"""

from unittest import mock

import numpy as np
import pytest


def _make_app(monkeypatch, summarize_side_effect):
    from meetingscribe import app as app_module

    app = object.__new__(app_module.MeetingScribeApp)  # bypass rumps.App.__init__

    app._live_worker_thread = None
    app._live_local = None
    app._live_remote = None
    app._progress_window = None
    app._update_progress = lambda *a, **k: None

    rec = mock.MagicMock()
    rec.stop.return_value = {
        "local": np.full(100, 0.2, dtype="float32"),
        "local_rate": 24000,
        "remote": np.full(100, 0.2, dtype="float32"),
        "remote_rate": 48000,
        "t0": 0.0,
        "system_available": True,
    }
    rec._sys = None
    app._recorder = rec

    app._transcriber = mock.MagicMock()
    summ = mock.MagicMock()
    summ.summarize.side_effect = summarize_side_effect
    app._summarizer = summ

    monkeypatch.setattr(app_module, "resolve_segments",
                        lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hello", "side": "local", "id": 1}])
    monkeypatch.setattr(app_module, "name_speakers", lambda segs, **k: segs)
    monkeypatch.setattr(app_module, "format_transcript", lambda named: "Jacob: hello there")

    saved = {}
    monkeypatch.setattr(app_module, "save_to_notes",
                        lambda title, body: saved.update(title=title, body=body) or True)

    finished = {}
    app._finish = lambda title, message: finished.update(title=title, message=message)

    return app, saved, finished


def test_transient_summary_error_still_saves_transcript(monkeypatch):
    # Simulate a network / Anthropic API failure (NOT a missing key).
    app, saved, finished = _make_app(monkeypatch, summarize_side_effect=RuntimeError("API connection error"))

    app._process_recording()

    # The transcript MUST be persisted despite the summary failing.
    assert "body" in saved, "transcript was lost when summarize() raised"
    assert "Jacob: hello there" in saved["body"]
    # And the user is told the summary failed (not a bare generic crash with no save).
    assert finished.get("title") not in (None, "Error")


def test_successful_path_still_saves_with_summary(monkeypatch):
    app, saved, finished = _make_app(monkeypatch, summarize_side_effect=None)
    app._summarizer.summarize.return_value = "SUMMARY: did stuff"

    app._process_recording()

    assert "SUMMARY: did stuff" in saved["body"]
    assert "Jacob: hello there" in saved["body"]
    assert finished.get("title") == "Done!"
