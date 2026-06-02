from meetingscribe import app


def test_app_version_reads_bundle(monkeypatch):
    class FakeBundle:
        def objectForInfoDictionaryKey_(self, key):
            assert key == "CFBundleShortVersionString"
            return "9.9.9"

    class FakeNSBundle:
        @staticmethod
        def mainBundle():
            return FakeBundle()

    monkeypatch.setattr(app.AppKit, "NSBundle", FakeNSBundle)
    assert app._app_version() == "9.9.9"


def test_app_version_fallback_when_missing(monkeypatch):
    class FakeBundle:
        def objectForInfoDictionaryKey_(self, key):
            return None

    class FakeNSBundle:
        @staticmethod
        def mainBundle():
            return FakeBundle()

    monkeypatch.setattr(app.AppKit, "NSBundle", FakeNSBundle)
    assert app._app_version() == "dev"
