from meetingscribe import secrets


def test_prefers_keychain(monkeypatch):
    monkeypatch.setattr(secrets, "keychain_get", lambda *a, **k: "sk-keychain")
    monkeypatch.setattr(secrets, "_dev_fallback_key", lambda: "sk-dev")
    assert secrets.get_api_key() == "sk-keychain"


def test_falls_back_to_dev_env(monkeypatch):
    monkeypatch.setattr(secrets, "keychain_get", lambda *a, **k: "")
    monkeypatch.setattr(secrets, "_dev_fallback_key", lambda: "sk-dev")
    assert secrets.get_api_key() == "sk-dev"


def test_empty_when_nothing(monkeypatch):
    monkeypatch.setattr(secrets, "keychain_get", lambda *a, **k: "")
    monkeypatch.setattr(secrets, "_dev_fallback_key", lambda: "")
    assert secrets.get_api_key() == ""
