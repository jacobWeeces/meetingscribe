"""ProgressWindow must mutate AppKit objects only on the main thread.

_update_progress runs on the background _process_recording thread. set_stage /
set_detail / close already marshal via performSelectorOnMainThread, but
set_progress and set_indeterminate touched NSProgressIndicator DIRECTLY from the
background thread — the same illegal off-main-thread AppKit use that produced
"NSWindow should only be instantiated on the main thread!" crashes in the field
log. They must marshal too.
"""

from unittest import mock


def _simulate_background_thread(monkeypatch, progress):
    main = object()
    monkeypatch.setattr(progress.threading, "main_thread", lambda: main)
    monkeypatch.setattr(progress.threading, "current_thread", lambda: object())


def test_set_progress_marshals_to_main_thread(monkeypatch):
    from meetingscribe import progress
    _simulate_background_thread(monkeypatch, progress)
    scheduled = []
    monkeypatch.setattr(progress.AppHelper, "callAfter",
                        lambda fn, *a, **k: scheduled.append(fn))

    w = progress.ProgressWindow()
    bar = mock.MagicMock()
    w._progress_bar = bar
    w.set_progress(50)

    bar.setDoubleValue_.assert_not_called()          # NOT touched on the bg thread
    assert scheduled, "set_progress did not marshal to the main thread"
    scheduled[0]()                                   # run the main-thread block
    bar.setDoubleValue_.assert_called_once_with(50)


def test_set_indeterminate_marshals_to_main_thread(monkeypatch):
    from meetingscribe import progress
    _simulate_background_thread(monkeypatch, progress)
    scheduled = []
    monkeypatch.setattr(progress.AppHelper, "callAfter",
                        lambda fn, *a, **k: scheduled.append(fn))

    w = progress.ProgressWindow()
    bar = mock.MagicMock()
    w._progress_bar = bar
    w.set_indeterminate(True)

    bar.setIndeterminate_.assert_not_called()
    assert scheduled
    scheduled[0]()
    bar.setIndeterminate_.assert_called_once_with(True)
    bar.startAnimation_.assert_called_once_with(None)


def test_set_progress_runs_inline_on_main_thread(monkeypatch):
    from meetingscribe import progress
    main = object()
    monkeypatch.setattr(progress.threading, "main_thread", lambda: main)
    monkeypatch.setattr(progress.threading, "current_thread", lambda: main)  # ON main thread

    w = progress.ProgressWindow()
    bar = mock.MagicMock()
    w._progress_bar = bar
    w.set_progress(75)

    bar.setDoubleValue_.assert_called_once_with(75)   # no scheduling needed on main thread
