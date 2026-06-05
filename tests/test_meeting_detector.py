from meetingscribe.meeting_detector import should_prompt


def test_prompts_when_mic_active_and_meeting_app_running():
    assert should_prompt(mic_in_use=True, meeting_app=True, recording=False, already_prompted=False) is True


def test_no_prompt_without_meeting_app():
    assert should_prompt(mic_in_use=True, meeting_app=False, recording=False, already_prompted=False) is False


def test_no_prompt_while_recording_or_already_prompted():
    assert should_prompt(True, True, recording=True, already_prompted=False) is False
    assert should_prompt(True, True, recording=False, already_prompted=True) is False
