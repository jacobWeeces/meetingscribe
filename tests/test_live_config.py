from meetingscribe import config


def test_live_constants_exist_with_defaults():
    assert config.LIVE_TRANSCRIPTION is True
    assert config.LIVE_CADENCE_SEC == 25
    assert config.LIVE_GUARD_SEC == 3
    assert config.LIVE_MAX_TAIL_SEC == 90
