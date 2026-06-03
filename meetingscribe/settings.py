"""JSON-backed user preferences for MeetingScribe.

config.LIVE_TRANSCRIPTION is only the *default* used when no value has been
stored yet.  The JSON file (and the MS_LIVE_TRANSCRIPTION env var) supersede it
at runtime.
"""

import json
import os

from meetingscribe.config import DATA_DIR, LIVE_TRANSCRIPTION

SETTINGS_PATH = DATA_DIR / "settings.json"

_FALSEY = {"0", "false", "no", "off"}


def _read():
    try:
        return json.loads(SETTINGS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write(data):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data))


def live_transcription_enabled() -> bool:
    """Whether live transcription is on. A non-empty MS_LIVE_TRANSCRIPTION env var
    overrides the stored preference; an unset or blank env var falls through to the
    stored value, which defaults to config.LIVE_TRANSCRIPTION."""
    env = os.environ.get("MS_LIVE_TRANSCRIPTION")
    if env is not None and env.strip():
        return env.strip().lower() not in _FALSEY
    return bool(_read().get("live_transcription", LIVE_TRANSCRIPTION))


def set_live_transcription(value: bool) -> None:
    data = _read()
    data["live_transcription"] = bool(value)
    _write(data)
