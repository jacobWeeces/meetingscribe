import json
from unittest import mock
from meetingscribe import speakers
from meetingscribe.segments import merge_segments


def _seg(start, text, side):
    return {"start": start, "end": start + 1, "text": text, "side": side}


def test_parse_name_map_tolerates_str_keys():
    assert speakers._parse_name_map(json.dumps({"2": "Matt"})) == {2: "Matt"}


def test_name_speakers_applies_map(monkeypatch):
    merged = merge_segments([_seg(0, "hi", "local")], [_seg(2, "I'm Priscilla", "remote")])
    monkeypatch.setattr(speakers, "_call_llm", lambda prompt: json.dumps({"2": "Priscilla"}))
    named = speakers.name_speakers(merged, local_name="Jacob")
    assert named[0]["speaker"] == "Jacob"
    assert named[1]["speaker"] == "Priscilla"


def test_name_speakers_falls_back_on_llm_error(monkeypatch):
    merged = merge_segments([_seg(0, "hi", "local")], [_seg(2, "yo", "remote")])
    def boom(prompt):
        raise RuntimeError("api down")
    monkeypatch.setattr(speakers, "_call_llm", boom)
    named = speakers.name_speakers(merged, local_name="Jacob")
    assert named[0]["speaker"] == "Jacob"            # local default
    assert named[1]["speaker"] == "Remote speaker"   # graceful fallback
