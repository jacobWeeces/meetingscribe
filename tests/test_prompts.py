from meetingscribe.prompts import PROFILES


def test_all_prompts_have_accuracy_rules():
    needles = ["exactly as spoken", "final decision", "specifics"]
    for profile in PROFILES.values():
        for key in ("system", "chunk", "merge"):
            text = profile[key].lower()
            assert all(n.lower() in text for n in needles), (profile, key)
