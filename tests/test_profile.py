import sys
from meetingscribe import config


def test_env_profile(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setenv("MS_PROFILE", "jacob")
    assert config._load_profile() == "jacob"


def test_default_when_unset(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.delenv("MS_PROFILE", raising=False)
    assert config._load_profile() == "laurelle"


def test_unknown_profile_falls_back(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setenv("MS_PROFILE", "nobody")
    assert config._load_profile() == "laurelle"
