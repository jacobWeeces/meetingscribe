from meetingscribe import updater


def test_init_sparkle_noop_when_not_frozen(monkeypatch):
    # Running from source: framework absent -> must return None, not raise.
    monkeypatch.setattr(updater, "_framework_path", lambda: "/nonexistent/Sparkle.framework")
    assert updater.init_sparkle() is None


def test_check_for_updates_safe_without_controller():
    updater._updater_controller = None
    updater.check_for_updates(None)   # must not raise
