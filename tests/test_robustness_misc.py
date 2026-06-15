"""Smaller robustness fixes surfaced by the bug hunt:
  * speakers._parse_name_map must tolerate prose/braces around the JSON object.
  * notes.save_to_notes must not crash if osascript can't be launched.
  * summarizer._first_text must guard empty / non-text API responses.
  * settings write must be atomic and round-trip.
"""

import pytest


# ---- speakers: robust JSON extraction --------------------------------------

def test_parse_name_map_tolerates_trailing_prose():
    from meetingscribe.speakers import _parse_name_map
    text = 'Sure! {"1": "Alice", "2": "Bob"} — hope that helps :) }'
    assert _parse_name_map(text) == {1: "Alice", 2: "Bob"}


def test_parse_name_map_plain_object():
    from meetingscribe.speakers import _parse_name_map
    assert _parse_name_map('{"3": "Carol"}') == {3: "Carol"}


def test_parse_name_map_no_json_returns_empty():
    from meetingscribe.speakers import _parse_name_map
    assert _parse_name_map("no json at all") == {}


# ---- notes: osascript launch failure ---------------------------------------

def test_save_to_notes_handles_osascript_launch_failure(monkeypatch):
    from meetingscribe import notes

    def boom(*a, **k):
        raise FileNotFoundError("osascript not found")

    monkeypatch.setattr(notes.subprocess, "run", boom)
    assert notes.save_to_notes("Title", "Body") is False   # returns False, does not raise


# ---- summarizer: response content guard ------------------------------------

def test_first_text_returns_first_text_block():
    from meetingscribe.summarizer import _first_text

    class _Block:
        def __init__(self, text):
            self.text = text

    class _Msg:
        content = [_Block(""), _Block("the summary")]

    assert _first_text(_Msg()) == "the summary"


def test_first_text_raises_on_empty_content():
    from meetingscribe.summarizer import _first_text

    class _Msg:
        content = []

    with pytest.raises(RuntimeError):
        _first_text(_Msg())


# ---- settings: atomic round-trip -------------------------------------------

def test_settings_atomic_roundtrip(tmp_path, monkeypatch):
    from meetingscribe import settings
    monkeypatch.delenv("MS_LIVE_TRANSCRIPTION", raising=False)
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")

    settings.set_live_transcription(False)
    assert settings.live_transcription_enabled() is False
    settings.set_live_transcription(True)
    assert settings.live_transcription_enabled() is True
    # no leftover temp files beside the settings file
    assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]
