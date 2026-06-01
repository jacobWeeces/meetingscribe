import os
import sys
from pathlib import Path

DATA_DIR = Path.home() / ".meetingscribe"
RECORDINGS_DIR = DATA_DIR / "recordings"

WHISPER_COMPUTE_TYPE = "int8"
SAMPLE_RATE = 44100
CHANNELS = 1
BLACKHOLE_DEVICE_NAME = "BlackHole"
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
USER_PROFILE = "laurelle"


def _load_api_key():
    """Read the Anthropic key from $ANTHROPIC_API_KEY or a gitignored .env.

    Kept out of source so the key never enters git history. For local dev, put
    `ANTHROPIC_API_KEY=...` in a `.env` at the project root. Frozen builds read a
    `.env` bundled beside the app resources (see datas in MeetingScribe.spec).
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys._MEIPASS) / ".env")
    candidates.append(Path(__file__).resolve().parent.parent / ".env")
    for env_path in candidates:
        try:
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    s = line.strip()
                    if s and not s.startswith("#") and s.startswith("ANTHROPIC_API_KEY="):
                        return s.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            pass
    return ""


ANTHROPIC_API_KEY = _load_api_key()


def _resource_path():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).parent.parent


def whisper_model_path():
    bundled = _resource_path() / "whisper_model"
    if bundled.exists():
        return str(bundled)
    return "medium"


def ensure_dirs():
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
