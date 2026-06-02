from meetingscribe import app


def test_bring_to_front_activates(monkeypatch):
    calls = {}

    class FakeApp:
        def activateIgnoringOtherApps_(self, flag):
            calls["flag"] = flag

    class FakeNSApplication:
        @staticmethod
        def sharedApplication():
            return FakeApp()

    monkeypatch.setattr(app.AppKit, "NSApplication", FakeNSApplication)
    app._bring_to_front()
    assert calls["flag"] is True


def test_prompt_brings_to_front_before_showing(monkeypatch):
    order = []
    monkeypatch.setattr(app, "_bring_to_front", lambda: order.append("front"))

    class FakeResp:
        clicked = 0
        text = ""

    class FakeWindow:
        def __init__(self, *a, **k):
            pass

        def run(self):
            order.append("run")
            return FakeResp()

    monkeypatch.setattr(app.rumps, "Window", FakeWindow)
    app.prompt_for_api_key()
    assert order == ["front", "run"]   # activation happens BEFORE the modal shows


def test_main_thread_alert_brings_to_front_first(monkeypatch):
    order = []
    monkeypatch.setattr(app, "_bring_to_front", lambda: order.append("front"))
    monkeypatch.setattr(app.rumps, "alert", lambda **k: order.append("alert"))
    app._main_thread_alert("T", "M")   # called on the main (test) thread -> runs synchronously
    assert order == ["front", "alert"]
