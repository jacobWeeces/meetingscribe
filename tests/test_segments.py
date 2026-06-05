from meetingscribe.segments import merge_segments, apply_speaker_map, format_transcript


def seg(start, end, text, side):
    return {"start": start, "end": end, "text": text, "side": side}


def test_merge_sorts_by_start_and_keeps_side():
    local = [seg(0.0, 1.0, "hi", "local"), seg(5.0, 6.0, "bye", "local")]
    remote = [seg(2.0, 3.0, "hello", "remote")]
    merged = merge_segments(local, remote)
    assert [s["text"] for s in merged] == ["hi", "hello", "bye"]
    assert [s["side"] for s in merged] == ["local", "remote", "local"]
    assert all("id" in s for s in merged)  # stable ids assigned


def test_apply_speaker_map_defaults_local_to_profile_name():
    merged = merge_segments([seg(0.0, 1.0, "hi", "local")], [seg(2.0, 3.0, "yo", "remote")])
    named = apply_speaker_map(merged, name_map={2: "Priscilla"}, local_name="Jacob")  # remote seg has merged id 2
    assert named[0]["speaker"] == "Jacob"        # local prior
    assert named[1]["speaker"] == "Priscilla"    # from map by id


def test_apply_speaker_map_remote_fallback():
    merged = merge_segments([], [seg(0.0, 1.0, "yo", "remote")])
    named = apply_speaker_map(merged, name_map={}, local_name="Jacob")
    assert named[0]["speaker"] == "Remote speaker"


def test_apply_speaker_map_can_name_local_in_room_speaker():
    local = [seg(0.0, 1.0, "a", "local"), seg(4.0, 5.0, "b", "local")]
    remote = [seg(2.0, 3.0, "c", "remote")]
    merged = merge_segments(local, remote)  # ids by start: 1 local, 2 remote, 3 local
    named = apply_speaker_map(merged, name_map={3: "Matt"}, local_name="Jacob")
    by_id = {s["id"]: s["speaker"] for s in named}
    assert by_id[1] == "Jacob"           # local default
    assert by_id[2] == "Remote speaker"  # remote, not in map
    assert by_id[3] == "Matt"            # local overridden by map


def test_format_groups_consecutive_same_speaker_with_timestamp():
    named = [
        {"start": 12.0, "speaker": "Jacob", "text": "A."},
        {"start": 13.0, "speaker": "Jacob", "text": "B."},
        {"start": 167.0, "speaker": "Priscilla", "text": "C."},
    ]
    out = format_transcript(named)
    assert "[0:12] Jacob: A. B." in out
    assert "[2:47] Priscilla: C." in out
