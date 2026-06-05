"""The single-instance guard must use the app's ACTUAL bundle id.

Regression: a non-default build variant (e.g. com.meetingscribe.jacob) was not
detected by `_is_already_running()` because it queried a hardcoded
"com.meetingscribe.app", so a re-launched child became a visible second session.
"""
import os
from unittest import mock

from meetingscribe import app


def test_bundle_id_uses_runtime_identifier_when_frozen(monkeypatch):
    monkeypatch.setattr(app.sys, "frozen", True, raising=False)
    fake_appkit = mock.MagicMock()
    fake_appkit.NSBundle.mainBundle.return_value.bundleIdentifier.return_value = "com.meetingscribe.jacob"
    monkeypatch.setattr(app, "AppKit", fake_appkit)
    assert app._bundle_id() == "com.meetingscribe.jacob"


def test_bundle_id_falls_back_to_constant_when_not_frozen(monkeypatch):
    # In dev/source there is no app bundle id — must NOT match unrelated python procs.
    monkeypatch.setattr(app.sys, "frozen", False, raising=False)
    assert app._bundle_id() == app.BUNDLE_ID


def test_bundle_id_falls_back_when_identifier_missing(monkeypatch):
    monkeypatch.setattr(app.sys, "frozen", True, raising=False)
    fake_appkit = mock.MagicMock()
    fake_appkit.NSBundle.mainBundle.return_value.bundleIdentifier.return_value = None
    monkeypatch.setattr(app, "AppKit", fake_appkit)
    assert app._bundle_id() == app.BUNDLE_ID


def test_is_already_running_queries_the_runtime_bundle_id(monkeypatch):
    captured = {}
    monkeypatch.setattr(app, "_bundle_id", lambda: "com.meetingscribe.jacob")

    def fake_pids(bundle_id):
        captured["bid"] = bundle_id
        return [os.getpid() + 1]  # a different instance is running

    monkeypatch.setattr(app, "_running_pids_for", fake_pids)
    assert app._is_already_running() is True
    assert captured["bid"] == "com.meetingscribe.jacob"  # the RIGHT id, not the hardcoded one


def test_is_already_running_ignores_self_only(monkeypatch):
    monkeypatch.setattr(app, "_bundle_id", lambda: "com.meetingscribe.jacob")
    monkeypatch.setattr(app, "_running_pids_for", lambda bid: [os.getpid()])
    assert app._is_already_running() is False
