import pytest
from meetingscribe import summarizer as sm


def test_summarize_without_key_raises_no_key(monkeypatch):
    monkeypatch.setattr(sm, "get_api_key", lambda: "")
    s = sm.Summarizer()                       # construction must NOT need a key
    with pytest.raises(sm.NoAPIKeyError):
        s.summarize("hello world")


def test_client_built_lazily_with_key(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, api_key=None):
            captured["key"] = api_key

    monkeypatch.setattr(sm, "get_api_key", lambda: "sk-live")
    monkeypatch.setattr(sm.anthropic, "Anthropic", FakeClient)
    s = sm.Summarizer()
    assert "key" not in captured                # not built at construction
    s._ensure_client()
    assert captured["key"] == "sk-live"         # built on demand with the key
