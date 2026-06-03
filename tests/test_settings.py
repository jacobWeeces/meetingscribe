from meetingscribe import settings


def _point_at(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")


def test_default_is_true_when_unset(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch)
    monkeypatch.delenv("MS_LIVE_TRANSCRIPTION", raising=False)
    assert settings.live_transcription_enabled() is True


def test_set_and_get_round_trips(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch)
    monkeypatch.delenv("MS_LIVE_TRANSCRIPTION", raising=False)
    settings.set_live_transcription(False)
    assert settings.live_transcription_enabled() is False
    settings.set_live_transcription(True)
    assert settings.live_transcription_enabled() is True


def test_env_override_wins(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch)
    settings.set_live_transcription(True)
    monkeypatch.setenv("MS_LIVE_TRANSCRIPTION", "0")
    assert settings.live_transcription_enabled() is False
    monkeypatch.setenv("MS_LIVE_TRANSCRIPTION", "1")
    assert settings.live_transcription_enabled() is True


def test_corrupt_file_falls_back_to_default(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch)
    monkeypatch.delenv("MS_LIVE_TRANSCRIPTION", raising=False)
    (tmp_path / "settings.json").write_text("{not json")
    assert settings.live_transcription_enabled() is True


def test_blank_env_falls_through_to_stored(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch)
    settings.set_live_transcription(False)
    monkeypatch.setenv("MS_LIVE_TRANSCRIPTION", "")      # set but blank
    assert settings.live_transcription_enabled() is False  # falls through to stored False
    monkeypatch.setenv("MS_LIVE_TRANSCRIPTION", "   ")    # whitespace only
    assert settings.live_transcription_enabled() is False
    settings.set_live_transcription(True)
    monkeypatch.setenv("MS_LIVE_TRANSCRIPTION", "")
    assert settings.live_transcription_enabled() is True   # falls through to stored True
