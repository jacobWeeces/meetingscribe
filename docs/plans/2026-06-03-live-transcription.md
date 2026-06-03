# Live (During-Meeting) Transcription Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Transcribe meeting audio incrementally *during* recording so the post-Stop wait shrinks from "transcribe the whole meeting + summarize" to just "summarize."

**Architecture:** A background worker periodically pulls a mono snapshot of the audio captured so far and hands it to a new `LiveTranscriber`, which commits only the Whisper-delimited segments that ended before a 3 s trailing guard (re-transcribing the uncommitted tail each tick for clean silence boundaries). At Stop, one short `finalize()` pass flushes the tail; summarization + Notes save are unchanged. Any failure falls back to today's whole-WAV path — never worse than today. A visible "Live transcription" menu checkbox (default on, JSON-persisted) controls it.

**Tech Stack:** Python 3.10+, `faster-whisper` (bundled `medium` int8, CPU), `sounddevice`, `numpy`, `scipy.io.wavfile`, `rumps` (menu bar), `pytest` + `monkeypatch`. Design doc: [2026-06-03-live-transcription-design.md](2026-06-03-live-transcription-design.md).

**Relevant skills:** @superpowers:test-driven-development (RED→GREEN→REFACTOR), @superpowers:verification-before-completion.

**Working context:** Per Jacob's instruction, implement directly on `main`. Do **not** commit without his go-ahead — at each "Commit" step, stage and show the diff, but only run `git commit` when he confirms. Run the suite with `python3 -m pytest -q` (slow parity test excluded by default).

---

## Task 1: Config constants

**Files:**
- Modify: `meetingscribe/config.py` (add constants after line 12, near `ANTHROPIC_MODEL`)
- Test: `tests/test_live_config.py` (create)

**Step 1: Write the failing test**

```python
# tests/test_live_config.py
from meetingscribe import config


def test_live_constants_exist_with_defaults():
    assert config.LIVE_TRANSCRIPTION is True
    assert config.LIVE_CADENCE_SEC == 25
    assert config.LIVE_GUARD_SEC == 3
    assert config.LIVE_MAX_TAIL_SEC == 90
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_live_config.py -v`
Expected: FAIL — `AttributeError: module 'meetingscribe.config' has no attribute 'LIVE_TRANSCRIPTION'`

**Step 3: Write minimal implementation**

In `meetingscribe/config.py`, after the line `ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"`:

```python

# --- Live (during-meeting) transcription ---
LIVE_TRANSCRIPTION = True      # default when no per-user setting is stored yet
LIVE_CADENCE_SEC = 25          # worker wakes this often to transcribe the new tail
LIVE_GUARD_SEC = 3             # trailing audio never committed yet (may be mid-word)
LIVE_MAX_TAIL_SEC = 90         # safety cap: force-commit if no silence boundary appears
```

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_live_config.py -v`
Expected: PASS

**Step 5: Commit** (after Jacob's go-ahead)

```bash
git add meetingscribe/config.py tests/test_live_config.py
git commit -m "feat(live): add live-transcription config constants"
```

---

## Task 2: `settings.py` — JSON-backed "Live transcription" preference

**Files:**
- Create: `meetingscribe/settings.py`
- Test: `tests/test_settings.py` (create)

**Step 1: Write the failing test**

```python
# tests/test_settings.py
import importlib
from pathlib import Path
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
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'meetingscribe.settings'`

**Step 3: Write minimal implementation**

```python
# meetingscribe/settings.py
import json
import os

from meetingscribe.config import DATA_DIR, LIVE_TRANSCRIPTION

SETTINGS_PATH = DATA_DIR / "settings.json"

_FALSEY = {"0", "false", "no", "off", ""}


def _read():
    try:
        return json.loads(SETTINGS_PATH.read_text())
    except (OSError, ValueError):
        return {}


def _write(data):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data))


def live_transcription_enabled() -> bool:
    """Whether live transcription is on. Env var MS_LIVE_TRANSCRIPTION overrides the
    stored preference; otherwise the stored value, defaulting to config.LIVE_TRANSCRIPTION."""
    env = os.environ.get("MS_LIVE_TRANSCRIPTION")
    if env is not None:
        return env.strip().lower() not in _FALSEY
    return bool(_read().get("live_transcription", LIVE_TRANSCRIPTION))


def set_live_transcription(value: bool) -> None:
    data = _read()
    data["live_transcription"] = bool(value)
    _write(data)
```

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_settings.py -v`
Expected: PASS (4 passed)

**Step 5: Commit** (after go-ahead)

```bash
git add meetingscribe/settings.py tests/test_settings.py
git commit -m "feat(live): add JSON-backed live-transcription setting"
```

---

## Task 3: `Transcriber.transcribe_segments()` (additive)

Adds a method returning materialized `(start, end, text)` tuples, accepting either a path **or** a float32 ndarray (ndarray is written to a temp WAV at `SAMPLE_RATE` so Whisper's decoder resamples — exact parity with the file path). The existing `transcribe()` is left **unchanged** (zero risk to the proven Stop path); the ~1-line `model.transcribe(...)` duplication is an intentional safety-over-DRY choice.

**Files:**
- Modify: `meetingscribe/transcriber.py`
- Test: `tests/test_transcribe_segments.py` (create)

**Step 1: Write the failing test**

```python
# tests/test_transcribe_segments.py
import numpy as np
from meetingscribe.transcriber import Transcriber


class _Seg:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


class _FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, source, **kwargs):
        self.calls.append(source)
        info = type("Info", (), {"duration": 2.0})()
        return iter([_Seg(0.0, 1.0, "hello"), _Seg(1.0, 2.0, "world")]), info


def test_segments_from_path(monkeypatch):
    t = Transcriber()
    t._model = _FakeModel()
    monkeypatch.setattr(t, "_load_model", lambda: None)
    segs = t.transcribe_segments("/tmp/x.wav")
    assert segs == [(0.0, 1.0, "hello"), (1.0, 2.0, "world")]
    assert t._model.calls == ["/tmp/x.wav"]


def test_segments_from_ndarray_writes_tempwav(monkeypatch):
    t = Transcriber()
    t._model = _FakeModel()
    monkeypatch.setattr(t, "_load_model", lambda: None)
    audio = np.zeros(44100, dtype="float32")  # 1 s of silence
    segs = t.transcribe_segments(audio)
    assert segs == [(0.0, 1.0, "hello"), (1.0, 2.0, "world")]
    # the model was handed a real temp file path (str), not the array
    assert isinstance(t._model.calls[0], str) and t._model.calls[0].endswith(".wav")
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_transcribe_segments.py -v`
Expected: FAIL — `AttributeError: 'Transcriber' object has no attribute 'transcribe_segments'`

**Step 3: Write minimal implementation**

Add imports at the top of `meetingscribe/transcriber.py` (alongside the existing imports):

```python
import os
import tempfile

import numpy as np
from scipy.io import wavfile

from meetingscribe.config import WHISPER_COMPUTE_TYPE, whisper_model_path, SAMPLE_RATE
```

(Replace the existing `from meetingscribe.config import WHISPER_COMPUTE_TYPE, whisper_model_path` line with the one above, and add the `os`/`tempfile`/`numpy`/`wavfile` imports. `os` is already imported — keep a single import.)

Add the method to the `Transcriber` class (after `transcribe`):

```python
    def transcribe_segments(self, source):
        """Return a materialized list of (start, end, text) tuples.

        `source` may be a path (str/Path) or a float32 ndarray at SAMPLE_RATE. An
        ndarray is written to a temp WAV at SAMPLE_RATE so faster-whisper's decoder
        resamples it to 16 kHz exactly as it does for the on-disk recording — keeping
        live chunks on the same decode path as the end-of-meeting file.
        """
        self._load_model()
        if isinstance(source, np.ndarray):
            int_audio = np.clip(source * 32767, -32768, 32767).astype(np.int16)
            fd, tmp = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            try:
                wavfile.write(tmp, SAMPLE_RATE, int_audio)
                segments, _ = self._model.transcribe(tmp, beam_size=5, vad_filter=True)
                return [(s.start, s.end, s.text) for s in segments]
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        segments, _ = self._model.transcribe(str(source), beam_size=5, vad_filter=True)
        return [(s.start, s.end, s.text) for s in segments]
```

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_transcribe_segments.py -v`
Expected: PASS (2 passed)

**Step 5: Commit** (after go-ahead)

```bash
git add meetingscribe/transcriber.py tests/test_transcribe_segments.py
git commit -m "feat(live): add Transcriber.transcribe_segments (path or ndarray)"
```

---

## Task 4: `AudioRecorder.snapshot_mono()`

Thread-safe read of the audio captured so far, mixed to mono `(mic + sys) / 2` (mic-only if no BlackHole), sliced from `start_sample`. Capture and `stop()` are untouched.

**Files:**
- Modify: `meetingscribe/recorder.py` (add method to `AudioRecorder`)
- Test: `tests/test_recorder_snapshot.py` (create)

**Step 1: Write the failing test**

```python
# tests/test_recorder_snapshot.py
import numpy as np
from meetingscribe.recorder import AudioRecorder


def _frames(values):
    # frames are stored as (N, 1) float32 blocks, like the sounddevice callback
    return [np.array(values, dtype="float32").reshape(-1, 1)]


def test_mic_only_returns_mic(monkeypatch):
    monkeypatch.setattr(AudioRecorder, "__init__", lambda self: None)
    r = AudioRecorder()
    r._lock = __import__("threading").Lock()
    r._mic_frames = _frames([0.1, 0.2, 0.3, 0.4])
    r._sys_frames = []
    out = r.snapshot_mono(0)
    assert np.allclose(out, [0.1, 0.2, 0.3, 0.4])


def test_mic_plus_sys_is_averaged_and_clipped(monkeypatch):
    monkeypatch.setattr(AudioRecorder, "__init__", lambda self: None)
    r = AudioRecorder()
    r._lock = __import__("threading").Lock()
    r._mic_frames = _frames([1.0, 1.0, 1.0])
    r._sys_frames = _frames([0.0, 0.0])  # shorter -> clip to len 2
    out = r.snapshot_mono(0)
    assert np.allclose(out, [0.5, 0.5])


def test_start_sample_slices(monkeypatch):
    monkeypatch.setattr(AudioRecorder, "__init__", lambda self: None)
    r = AudioRecorder()
    r._lock = __import__("threading").Lock()
    r._mic_frames = _frames([0.0, 0.1, 0.2, 0.3])
    r._sys_frames = []
    out = r.snapshot_mono(2)
    assert np.allclose(out, [0.2, 0.3])
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_recorder_snapshot.py -v`
Expected: FAIL — `AttributeError: 'AudioRecorder' object has no attribute 'snapshot_mono'`

**Step 3: Write minimal implementation**

Add to `AudioRecorder` in `meetingscribe/recorder.py` (e.g. after `_sys_callback`):

```python
    def snapshot_mono(self, start_sample: int = 0) -> np.ndarray:
        """Mono mix (mic + sys)/2 of everything captured so far, from start_sample on.

        Mic-only when there's no system stream. Clipped to the shorter of the two
        streams, matching how stop() aligns them. Safe to call while recording.
        """
        with self._lock:
            mic = (
                np.concatenate(self._mic_frames)
                if self._mic_frames
                else np.zeros((0, 1), dtype="float32")
            )
            sys = np.concatenate(self._sys_frames) if self._sys_frames else None

        mic = mic[:, 0] if mic.ndim > 1 else mic
        if sys is not None and len(sys) > 0:
            sys = sys[:, 0] if sys.ndim > 1 else sys
            n = min(len(mic), len(sys))
            mono = (mic[:n] + sys[:n]) / 2.0
        else:
            mono = mic
        return mono[start_sample:].astype("float32")
```

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_recorder_snapshot.py -v`
Expected: PASS (3 passed)

**Step 5: Commit** (after go-ahead)

```bash
git add meetingscribe/recorder.py tests/test_recorder_snapshot.py
git commit -m "feat(live): add AudioRecorder.snapshot_mono"
```

---

## Task 5: `LiveTranscriber` — the segment-commit core

The heart of the feature, fully unit-tested with a **fake transcriber** (scripted segments, no audio, no threads).

**Files:**
- Create: `meetingscribe/live_transcriber.py`
- Test: `tests/test_live_transcriber.py` (create)

### Task 5a: commit-at-horizon + advance

**Step 1: Write the failing test**

```python
# tests/test_live_transcriber.py
import numpy as np
from meetingscribe.live_transcriber import LiveTranscriber, resolve_transcript

SR = 100  # tiny sample rate keeps the math readable


class FakeTranscriber:
    """Returns a scripted list of (start, end, text) per call, ignoring the audio."""
    def __init__(self, scripts):
        self._scripts = list(scripts)
        self.calls = []

    def transcribe_segments(self, source):
        self.calls.append(source)
        return self._scripts.pop(0) if self._scripts else []

    def transcribe(self, wav_path, on_progress=None):
        return "FULL-FILE"


def _tail(seconds, sr=SR):
    return np.zeros(int(seconds * sr), dtype="float32")


def test_commits_segments_before_horizon_holds_the_rest():
    # tail = 20 s, guard = 3 -> horizon = 17. Seg A ends at 10 (commit),
    # Seg B ends at 19 (held, inside guard).
    fake = FakeTranscriber([[(0.0, 10.0, "alpha"), (10.0, 19.0, "beta")]])
    lt = LiveTranscriber(fake, sample_rate=SR, guard_sec=3, max_tail_sec=90)
    lt.process_tick(_tail(20))
    assert lt.text() == "alpha"
    assert lt.committed_sample == int(10.0 * SR)
    assert lt.ever_committed is True


def test_short_tail_commits_nothing():
    fake = FakeTranscriber([[(0.0, 1.0, "hi")]])
    lt = LiveTranscriber(fake, sample_rate=SR, guard_sec=3, max_tail_sec=90)
    lt.process_tick(_tail(4))  # < guard + 2
    assert lt.text() == ""
    assert lt.committed_sample == 0
    assert lt.ever_committed is False
    assert fake.calls == []  # didn't even bother transcribing
```

**Step 2: Run** `python3 -m pytest tests/test_live_transcriber.py -v` → FAIL (`ModuleNotFoundError`).

**Step 3: Write minimal implementation**

```python
# meetingscribe/live_transcriber.py
import logging

from meetingscribe.config import LIVE_GUARD_SEC, LIVE_MAX_TAIL_SEC

log = logging.getLogger("meetingscribe")


class LiveTranscriber:
    """Commits Whisper-delimited segments that ended before a trailing guard, so the
    transcript is built up during recording. See docs/plans/2026-06-03-live-transcription-design.md.
    """

    def __init__(self, transcriber, sample_rate, guard_sec=LIVE_GUARD_SEC,
                 max_tail_sec=LIVE_MAX_TAIL_SEC):
        self._t = transcriber
        self._sr = sample_rate
        self._guard = guard_sec
        self._max_tail = max_tail_sec
        self._committed = []          # list[str]
        self.committed_sample = 0     # absolute sample index transcribed so far
        self._ever_committed = False

    @property
    def ever_committed(self) -> bool:
        return self._ever_committed

    def text(self) -> str:
        return "\n".join(self._committed)

    def process_tick(self, tail) -> None:
        """Transcribe the uncommitted tail; commit segments that ended before the guard."""
        tail_len_s = len(tail) / self._sr
        if tail_len_s < self._guard + 2:
            return
        try:
            segments = list(self._t.transcribe_segments(tail))
        except Exception:
            log.exception("live: transcribe_segments failed; will retry next tick")
            return
        if not segments:
            return

        horizon = tail_len_s - self._guard
        force = tail_len_s >= self._max_tail
        last_end = None
        for start, end, text in segments:
            committable = end <= horizon or (force and start < horizon)
            if not committable:
                break
            cleaned = text.strip()
            if cleaned:
                self._committed.append(cleaned)
            last_end = end
        if last_end is not None:
            self.committed_sample += int(last_end * self._sr)
            self._ever_committed = True
```

(Add `finalize` and `resolve_transcript` in 5b/5c.)

**Step 4: Run** `python3 -m pytest tests/test_live_transcriber.py -v` → the two tests above PASS. (Import of `resolve_transcript` at the top will currently fail — add a temporary `def resolve_transcript(*a, **k): ...` stub now, or write the 5a tests importing only `LiveTranscriber` and add the `resolve_transcript` import in 5c. **Recommended:** import only `LiveTranscriber` here and add the `resolve_transcript` import line in Task 5c's test step.)

**Step 5: Commit** (after go-ahead) — defer to end of Task 5 (one commit for the whole component) to keep history clean.

### Task 5b: no-duplication across ticks + max-tail cap + finalize

**Step 1: Add these tests** to `tests/test_live_transcriber.py`:

```python
def test_no_duplicate_text_across_ticks():
    # Tick 1: tail 20 s -> commit "alpha" (ends 10), advance to sample 1000.
    # Tick 2: a fresh 15 s tail (audio from sample 1000 on) -> commit "gamma" (ends 9).
    fake = FakeTranscriber([
        [(0.0, 10.0, "alpha"), (10.0, 19.0, "beta")],
        [(0.0, 9.0, "gamma"), (9.0, 14.0, "delta")],
    ])
    lt = LiveTranscriber(fake, sample_rate=SR, guard_sec=3, max_tail_sec=90)
    lt.process_tick(_tail(20))
    lt.process_tick(_tail(15))
    assert lt.text() == "alpha\ngamma"          # no "beta" duplication
    assert lt.committed_sample == int(10.0 * SR) + int(9.0 * SR)


def test_max_tail_cap_force_commits():
    # tail 95 s (> max 90), single long segment ending at 94 (> horizon 92) -> force commit.
    fake = FakeTranscriber([[(0.0, 94.0, "monologue")]])
    lt = LiveTranscriber(fake, sample_rate=SR, guard_sec=3, max_tail_sec=90)
    lt.process_tick(_tail(95))
    assert lt.text() == "monologue"
    assert lt.committed_sample == int(94.0 * SR)


def test_finalize_flushes_remaining_tail_with_no_guard():
    fake = FakeTranscriber([
        [(0.0, 10.0, "alpha"), (10.0, 19.0, "beta")],   # tick commits alpha
        [(0.0, 4.0, "omega")],                          # finalize commits everything
    ])
    lt = LiveTranscriber(fake, sample_rate=SR, guard_sec=3, max_tail_sec=90)
    lt.process_tick(_tail(20))
    result = lt.finalize(_tail(5))
    assert result == "alpha\nomega"


def test_finalize_with_empty_tail_returns_committed():
    fake = FakeTranscriber([[(0.0, 10.0, "alpha")]])
    lt = LiveTranscriber(fake, sample_rate=SR, guard_sec=3, max_tail_sec=90)
    lt.process_tick(_tail(20))
    assert lt.finalize(np.zeros(0, dtype="float32")) == "alpha"
```

**Step 2: Run** → the `finalize` tests FAIL (`AttributeError: ... 'finalize'`).

**Step 3: Add `finalize` to `LiveTranscriber`:**

```python
    def finalize(self, tail) -> str:
        """Commit any remaining tail with no guard (end of meeting), return the full text."""
        if tail is not None and len(tail) > 0:
            try:
                for start, end, text in self._t.transcribe_segments(tail):
                    cleaned = text.strip()
                    if cleaned:
                        self._committed.append(cleaned)
                self._ever_committed = True
            except Exception:
                log.exception("live: finalize tail transcription failed")
        return self.text()
```

**Step 4: Run** `python3 -m pytest tests/test_live_transcriber.py -v` → all PASS.

### Task 5c: `resolve_transcript` (fallback orchestration)

**Step 1: Add tests** (and update the import line at the top of the test file to `from meetingscribe.live_transcriber import LiveTranscriber, resolve_transcript`):

```python
def test_resolve_uses_live_when_committed():
    fake = FakeTranscriber([])
    lt = LiveTranscriber(fake, sample_rate=SR)
    lt._committed = ["live text"]
    lt._ever_committed = True
    out = resolve_transcript(fake, lt, np.zeros(0, dtype="float32"), "/tmp/x.wav")
    assert out == "live text"


def test_resolve_falls_back_to_whole_file_when_live_none():
    fake = FakeTranscriber([])
    out = resolve_transcript(fake, None, None, "/tmp/x.wav")
    assert out == "FULL-FILE"


def test_resolve_falls_back_when_live_never_committed():
    fake = FakeTranscriber([])
    lt = LiveTranscriber(fake, sample_rate=SR)  # nothing committed
    out = resolve_transcript(fake, lt, None, "/tmp/x.wav")
    assert out == "FULL-FILE"
```

**Step 2: Run** → FAIL (`ImportError: cannot import name 'resolve_transcript'`).

**Step 3: Add the free function** to `meetingscribe/live_transcriber.py`:

```python
def resolve_transcript(transcriber, live, final_tail, wav_path, on_progress=None):
    """Decide the final transcript: the live one if it ran and produced text, else
    today's whole-WAV pass (the safety net — never worse than today)."""
    if live is not None and live.ever_committed:
        return live.finalize(final_tail)
    return transcriber.transcribe(wav_path, on_progress=on_progress)
```

**Step 4: Run** `python3 -m pytest tests/test_live_transcriber.py -v` → all PASS (9 tests).

**Step 5: Commit** the whole component (after go-ahead):

```bash
git add meetingscribe/live_transcriber.py tests/test_live_transcriber.py
git commit -m "feat(live): add LiveTranscriber segment-commit core + fallback resolver"
```

---

## Task 6: Wire into `app.py` — worker lifecycle, finalize, checkbox

This task is mostly integration (threads + rumps + AppKit), verified by the full unit suite plus a manual smoke test. Keep edits surgical.

**Files:**
- Modify: `meetingscribe/app.py`

**Step 1: Imports & menu.** Add imports near the existing ones:

```python
from meetingscribe.config import DATA_DIR, ensure_dirs, SAMPLE_RATE, LIVE_CADENCE_SEC
from meetingscribe.live_transcriber import LiveTranscriber, resolve_transcript
from meetingscribe import settings
```

(The first line replaces the existing `from meetingscribe.config import DATA_DIR, ensure_dirs`.)

In `__init__`, add a checkable menu item in the settings cluster and initialize its state. Replace the `self.menu = [...]` block with:

```python
        live_item = rumps.MenuItem("Live transcription", callback=self.toggle_live_transcription)
        live_item.state = 1 if settings.live_transcription_enabled() else 0
        self.menu = [
            rumps.MenuItem(f"MeetingScribe v{_app_version()}"),
            None,
            rumps.MenuItem("Start Recording", callback=self.toggle_recording),
            None,
            live_item,
            rumps.MenuItem("Set API Key…", callback=self.set_api_key_clicked),
            rumps.MenuItem("Check for Updates…", callback=check_for_updates),
            None,
            rumps.MenuItem("Quit", callback=rumps.quit_application),
        ]
```

Add new instance attrs after `self._progress_window = None`:

```python
        self._live = None
        self._live_worker_thread = None
        self._final_tail = None
```

**Step 2: Checkbox callback.** Add method:

```python
    def toggle_live_transcription(self, sender):
        sender.state = 0 if sender.state else 1
        settings.set_live_transcription(bool(sender.state))
        log.info("Live transcription set to %s (applies to next recording)", bool(sender.state))
```

**Step 3: Start the worker on record.** In `_start_recording`, after the timer thread is started, add:

```python
        if settings.live_transcription_enabled():
            self._live = LiveTranscriber(self._transcriber, SAMPLE_RATE)
            self._live_worker_thread = threading.Thread(target=self._live_worker, daemon=True)
            self._live_worker_thread.start()
            log.info("Live transcription worker started")
        else:
            self._live = None
            self._live_worker_thread = None
```

Add the worker method:

```python
    def _live_worker(self):
        try:
            self._transcriber._load_model()   # preload so the first tick & Stop never stall cold
        except Exception:
            log.exception("live: model preload failed; disabling live for this session")
            self._live = None
            return
        while self._recording:
            for _ in range(LIVE_CADENCE_SEC):
                if not self._recording:
                    break
                time.sleep(1)
            if not self._recording:
                break
            live = self._live
            if live is None:
                break
            try:
                tail = self._recorder.snapshot_mono(live.committed_sample)
                live.process_tick(tail)
            except Exception:
                log.exception("live: worker tick failed")
```

**Step 4: Stop → join worker → capture tail.** In `_stop_recording`, change the top so the worker is joined *before* `stop()` and the remaining tail is captured:

```python
    def _stop_recording(self, sender):
        self._recording = False
        if self._live_worker_thread is not None:
            self._live_worker_thread.join(timeout=10)
            self._live_worker_thread = None
        live = self._live
        self._final_tail = (
            self._recorder.snapshot_mono(live.committed_sample) if live is not None else None
        )
        wav_path = self._recorder.stop()
        sender.title = "Start Recording"
        self.title = "⏳"
        self._processing = True
        log.info("Recording stopped, saved to %s", wav_path)

        self._show_progress()

        thread = threading.Thread(
            target=self._process_recording,
            args=(wav_path,),
            daemon=True,
        )
        thread.start()
```

**Step 5: Use the resolver in `_process_recording`.** Replace the transcription call (the block around `transcript = self._transcriber.transcribe(...)`) with:

```python
            self._update_progress("Transcribing...", pct=0.0, detail="Converting speech to text")

            def on_transcribe_progress(pct):
                self._update_progress(
                    "Transcribing...",
                    pct=pct,
                    detail=f"{int(pct * 100)}% complete",
                )

            transcript = resolve_transcript(
                self._transcriber,
                self._live,
                self._final_tail,
                wav_path,
                on_progress=on_transcribe_progress,
            )
            log.info("Transcription complete, length: %d chars", len(transcript))
```

Leave the rest of `_process_recording` (summarize → save → cleanup) unchanged.

**Step 6: Verify the suite still passes.**

Run: `python3 -m pytest -q`
Expected: PASS — the prior 21 tests + all new unit tests (no regressions).

**Step 7: Commit** (after go-ahead):

```bash
git add meetingscribe/app.py
git commit -m "feat(live): wire live transcription into app (worker, finalize, checkbox)"
```

---

## Task 7: Slow parity test (the "same result" guarantee)

Proves the live pipeline ≈ today's whole-file pass on real audio. Opt-in (`-m slow`) since it loads the bundled model.

**Files:**
- Create: `pytest.ini`
- Create: `tests/test_live_parity.py`

**Step 1: Register the `slow` marker and exclude it by default.** Create `pytest.ini`:

```ini
[pytest]
markers =
    slow: loads the real Whisper model / long-running (run with: pytest -m slow)
addopts = -m "not slow"
```

**Step 2: Write the parity test.**

```python
# tests/test_live_parity.py
import difflib
from pathlib import Path

import numpy as np
import pytest
from scipy.io import wavfile

from meetingscribe.transcriber import Transcriber
from meetingscribe.live_transcriber import LiveTranscriber

SAMPLE_WAV = Path(__file__).resolve().parent.parent / "spikes" / "sck_capture.wav"


def _read_mono(path):
    sr, data = wavfile.read(str(path))
    audio = data.astype("float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    peak = np.abs(audio).max() or 1.0
    return sr, (audio / peak).astype("float32")


@pytest.mark.slow
def test_live_pipeline_matches_whole_file():
    if not SAMPLE_WAV.exists():
        pytest.skip(f"sample audio not present: {SAMPLE_WAV}")

    sr, audio = _read_mono(SAMPLE_WAV)

    reference = Transcriber().transcribe(str(SAMPLE_WAV))

    lt = LiveTranscriber(Transcriber(), sample_rate=sr)
    step = int(25 * sr)  # ~25 s slices, like the worker cadence
    pos = step
    while pos < len(audio):
        lt.process_tick(audio[lt.committed_sample:pos])
        pos += step
    live = lt.finalize(audio[lt.committed_sample:])

    def norm(s):
        return " ".join(s.lower().split())

    ratio = difflib.SequenceMatcher(None, norm(reference), norm(live)).ratio()
    assert ratio >= 0.95, f"live transcript diverged from whole-file (ratio={ratio:.3f})"
```

**Step 3: Run the slow test explicitly.**

Run: `python3 -m pytest tests/test_live_parity.py -m slow -v -s`
Expected: PASS (or SKIP if `spikes/sck_capture.wav` is absent). If it FAILS with a ratio just under 0.95, inspect the diff — small tail/punctuation differences are expected; lower the threshold only with Jacob's sign-off.

**Step 4: Confirm default runs still skip it.**

Run: `python3 -m pytest -q`
Expected: PASS; the parity test is deselected (shown as deselected, not run).

**Step 5: Commit** (after go-ahead):

```bash
git add pytest.ini tests/test_live_parity.py
git commit -m "test(live): add opt-in parity test vs whole-file transcription"
```

---

## Task 8: Manual smoke test & doc status

**Step 1: Run the app from source and record a short live meeting.**

```bash
ANTHROPIC_API_KEY=... MS_PROFILE=jacob python3 -m meetingscribe.app
```
- Confirm the menu shows **✓ Live transcription**.
- Start recording, talk for ~90 s (play some system audio too), watch `~/.meetingscribe/meetingscribe.log` for `Live transcription worker started` and per-tick activity (no tracebacks).
- Hit Stop: the "Transcribing…" phase should be near-instant (just the tail), then "Summarizing…", then the Notes alert.
- Open Apple Notes and sanity-check the transcript quality.

**Step 2: Toggle off and verify fallback.** Uncheck **Live transcription**, record a short meeting, confirm the log shows *no* worker start and Stop transcribes the whole WAV (today's behavior), with a correct note.

**Step 3: Update the design doc status.** In [2026-06-03-live-transcription-design.md](2026-06-03-live-transcription-design.md), change the `**Status:**` line to: `Implemented; manual smoke test passed.`

**Step 4: Final full suite.**

Run: `python3 -m pytest -q` → all pass.
Run (optional, before any release): `python3 -m pytest -m slow -q` → parity passes.

**Step 5: Commit** (after go-ahead):

```bash
git add docs/plans/2026-06-03-live-transcription-design.md
git commit -m "docs(live): mark design implemented"
```

---

## Notes / guardrails

- **DRY/YAGNI:** the live path reuses `Transcriber` and the existing `_process_recording` summary/save tail; no live summarization, no live UI, no diarization.
- **Never worse than today:** every failure mode (live off, model load fail, tick exception, empty result) routes through `resolve_transcript` → today's whole-WAV pass.
- **One model, no concurrency:** the worker is joined before `finalize()`/`stop()`, so Whisper is never run from two threads at once.
- **Weak machines (Laurelle):** if she runs sub-real-time, the checkbox turns it off; behavior then equals today exactly.
