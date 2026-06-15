"""App-level safety nets:
  * Quit must not silently discard an in-flight recording/processing meeting.
  * The no-API-key save path must report failure if Notes save fails (not lie "saved").
  * A failure before recorder.stop() must still close the audio streams (no leak).
"""

from unittest import mock

import numpy as np


def _bare_app(monkeypatch):
    from meetingscribe import app as app_module
    app = object.__new__(app_module.MeetingScribeApp)  # bypass rumps.App.__init__
    monkeypatch.setattr(app_module, "_bring_to_front", lambda: None)
    return app_module, app


# --------------------------------------------------------------------------
# Quit guard
# --------------------------------------------------------------------------

def test_quit_idle_quits_immediately(monkeypatch):
    app_module, app = _bare_app(monkeypatch)
    app._recording = False
    app._processing = False
    quit_called = []
    monkeypatch.setattr(app_module.rumps, "quit_application", lambda *a: quit_called.append(True))
    monkeypatch.setattr(app_module.rumps, "alert", lambda **k: 1)  # should not be consulted

    app._on_quit_clicked(None)
    assert quit_called == [True]


def test_quit_while_recording_cancelled_does_not_quit(monkeypatch):
    app_module, app = _bare_app(monkeypatch)
    app._recording = True
    app._processing = False
    quit_called = []
    monkeypatch.setattr(app_module.rumps, "quit_application", lambda *a: quit_called.append(True))
    monkeypatch.setattr(app_module.rumps, "alert", lambda **k: 0)  # user clicks "Keep recording"

    app._on_quit_clicked(None)
    assert quit_called == []                      # meeting NOT discarded


def test_quit_while_processing_confirmed_quits(monkeypatch):
    app_module, app = _bare_app(monkeypatch)
    app._recording = False
    app._processing = True
    quit_called = []
    monkeypatch.setattr(app_module.rumps, "quit_application", lambda *a: quit_called.append(True))
    monkeypatch.setattr(app_module.rumps, "alert", lambda **k: 1)  # user clicks "Quit anyway"

    app._on_quit_clicked(None)
    assert quit_called == [True]


# --------------------------------------------------------------------------
# _process_recording resource cleanup + no-key save failure
# --------------------------------------------------------------------------

def _pipeline_app(monkeypatch, *, load_model_error=None, save_ok=True, summarize_side_effect=None):
    from meetingscribe import app as app_module
    app = object.__new__(app_module.MeetingScribeApp)
    app._live_worker_thread = None
    app._live_local = None
    app._live_remote = None
    app._progress_window = None
    app._update_progress = lambda *a, **k: None
    app._finish = lambda title, message: setattr(app, "_finished", (title, message))

    rec = mock.MagicMock()
    rec.stop.return_value = {
        "local": np.full(50, 0.2, dtype="float32"), "local_rate": 24000,
        "remote": np.full(50, 0.2, dtype="float32"), "remote_rate": 48000,
        "t0": 0.0, "system_available": True,
    }
    rec._sys = None
    app._recorder = rec

    app._transcriber = mock.MagicMock()
    if load_model_error is not None:
        app._transcriber._load_model.side_effect = load_model_error

    from meetingscribe.summarizer import NoAPIKeyError
    summ = mock.MagicMock()
    summ.summarize.side_effect = summarize_side_effect or NoAPIKeyError("no key")
    app._summarizer = summ

    monkeypatch.setattr(app_module, "resolve_segments",
                        lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hi", "side": "local", "id": 1}])
    monkeypatch.setattr(app_module, "name_speakers", lambda segs, **k: segs)
    monkeypatch.setattr(app_module, "format_transcript", lambda named: "Jacob: hi")
    monkeypatch.setattr(app_module, "save_to_notes", lambda *a, **k: save_ok)
    return app, rec


def test_streams_closed_when_pipeline_raises_before_stop(monkeypatch):
    # _load_model raises before recorder.stop() -> finally must still stop the recorder.
    app, rec = _pipeline_app(monkeypatch, load_model_error=RuntimeError("model load boom"))
    app._process_recording()
    rec.stop.assert_called()                      # streams closed despite early failure


def test_no_api_key_save_failure_reports_couldnt_save(monkeypatch):
    app, rec = _pipeline_app(monkeypatch, save_ok=False)   # NoAPIKeyError + Notes save fails
    app._process_recording()
    title, _msg = app._finished
    assert title == "Couldn't save"               # must NOT falsely claim it was saved
