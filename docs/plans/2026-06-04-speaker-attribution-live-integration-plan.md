# Speaker Attribution × Live Transcription — Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Land the already-built speaker-attribution feature (SCK two-stream capture, merge, LLM naming, Sonnet + fidelity rules, auto-detect) on top of current `main`, integrated with live transcription via **per-channel live** so attribution and fast turnaround coexist — without losing main's live/Sparkle/Keychain spine.

**Architecture:** Capture two mono streams started at a shared `t0` — mic (`sounddevice`, local) and system audio (ScreenCaptureKit, remote). Both the **live** path (two `LiveTranscriber`s, one per side, sharing one Whisper model, ticked sequentially) and the **fallback** path (post-Stop `transcribe_streams`) converge on one side-tagged segment list, which is merged by timestamp → LLM-named (verbatim-preserving) → formatted → summarized (Sonnet) → saved to Notes. Pure logic lives in small TDD'd modules (`audio_format`, `segments`, side-aware `LiveTranscriber`); I/O wrappers (`system_audio`, `recorder`, `app`) stay thin and are verified manually.

**Tech Stack:** Python 3.14, `faster-whisper`, `scipy`/`numpy`, PyObjC ScreenCaptureKit/CoreMedia/Security, Anthropic SDK (Sonnet), `rumps`, Sparkle, PyInstaller. Tests: `pytest`.

**Source design:** [`docs/plans/2026-06-04-speaker-attribution-live-integration-design.md`](2026-06-04-speaker-attribution-live-integration-design.md)
**Reference implementation (stranded branch):** `feature/audio-speaker-attribution` (worktree `.worktrees/audio-speaker-attribution`)
**Reference spike (working SCK capture):** `spikes/sck_audio_spike.py`

**Conventions:** TDD (red → green → commit). DRY. YAGNI. One logical change per commit. Run **all** commands from the integration worktree root `.worktrees/speaker-attribution-integration/`. Tests: `pytest` (slow tests auto-skipped via `pytest.ini`). **Plain commit messages — never add a Claude/AI co-author trailer.** Transplants use `git checkout feature/audio-speaker-attribution -- <path>` to copy the exact reference file, then adapt where noted.

**Branch:** `feature/speaker-attribution-integration` (already created off `main`; baseline 49 tests green).

---

## Phase 0 — Setup (DONE)

- [x] Worktree `.worktrees/speaker-attribution-integration` off `main` (includes the design doc).
- [x] Baseline: `pytest` → **49 passed, 1 deselected**.

If resuming cold, re-verify: `pytest` → 49 passed.

---

## Phase 1 — Transplant the pure, self-contained modules

These come from the reference branch with their tests. Low risk: pure functions, no main conflicts.

### Task 1.1: `audio_format.py` (planar→mono, resample, **per-chunk** mono)

**Files:**
- Create: `meetingscribe/audio_format.py` (transplant + extend)
- Test: `tests/test_audio_format.py` (transplant + extend)

**Step 1: Transplant the reference file + test**

Run:
```bash
git checkout feature/audio-speaker-attribution -- meetingscribe/audio_format.py tests/test_audio_format.py
```

**Step 2: Run to confirm transplant is green**

Run: `pytest tests/test_audio_format.py -v`
Expected: 3 passed (`planar_float32_to_mono` averages channels; mono passthrough; resample length).

**Step 3: Write the failing test for per-chunk conversion**

The recorder accumulates SCK audio as a *list of planar chunks*; joining them and converting once scrambles channels across chunk boundaries. Add `planar_chunks_to_mono`. Append to `tests/test_audio_format.py`:

```python
def test_planar_chunks_to_mono_converts_each_chunk_independently():
    # Two separate planar stereo buffers. Each chunk is [L..., R...] on its own.
    import numpy as np
    from meetingscribe.audio_format import planar_chunks_to_mono
    c1 = np.array([1.0, 1.0,  0.0, 0.0], dtype="<f4").tobytes()  # L=[1,1] R=[0,0] -> mono [0.5,0.5]
    c2 = np.array([0.0, 0.0,  1.0, 1.0], dtype="<f4").tobytes()  # L=[0,0] R=[1,1] -> mono [0.5,0.5]
    mono = planar_chunks_to_mono([c1, c2], channels=2)
    np.testing.assert_allclose(mono, [0.5, 0.5, 0.5, 0.5], atol=1e-6)


def test_planar_chunks_to_mono_empty_is_empty_float32():
    import numpy as np
    from meetingscribe.audio_format import planar_chunks_to_mono
    out = planar_chunks_to_mono([], channels=2)
    assert out.dtype == np.float32 and out.size == 0
```

**Step 4: Run, expect fail**

Run: `pytest tests/test_audio_format.py -k planar_chunks -v`
Expected: FAIL (`planar_chunks_to_mono` not defined).

**Step 5: Implement**

Append to `meetingscribe/audio_format.py`:

```python
def planar_chunks_to_mono(chunks: list[bytes], channels: int) -> np.ndarray:
    """Convert a list of independent planar Float32 PCM chunks to one mono array.

    SCK delivers each sample buffer as its own planar block ([all L, all R]).
    Concatenating raw bytes first and de-interleaving once would mix channels
    across chunk boundaries — so convert each chunk independently, then join.
    """
    if not chunks:
        return np.zeros(0, dtype="float32")
    return np.concatenate([planar_float32_to_mono(c, channels) for c in chunks])
```

**Step 6: Run, expect pass**

Run: `pytest tests/test_audio_format.py -v`
Expected: 5 passed.

**Step 7: Commit**

```bash
git add meetingscribe/audio_format.py tests/test_audio_format.py
git commit -m "feat: add audio-format helpers (planar->mono, per-chunk mono, resample 16k)"
```

---

### Task 1.2: `segments.py` (merge, speaker-map, formatting)

**Files:**
- Create: `meetingscribe/segments.py`
- Test: `tests/test_segments.py`

**Step 1: Transplant**

Run:
```bash
git checkout feature/audio-speaker-attribution -- meetingscribe/segments.py tests/test_segments.py
```

**Step 2: Run, expect pass**

Run: `pytest tests/test_segments.py -v`
Expected: 4 passed (merge sorts + keeps side + assigns id; local→profile name; remote fallback; grouped `[m:ss] Name:` formatting).

**Step 3: Commit**

```bash
git add meetingscribe/segments.py tests/test_segments.py
git commit -m "feat: add segment merge, speaker-map application, transcript formatting"
```

---

### Task 1.3: Sonnet model + fidelity rules (`config.py`, `prompts.py`)

Keep main's `config.py` (dynamic `_load_profile`, `LIVE_*`, `_load_api_key`) — change **only** the model. Keep main's `prompts.py` profiles — append the shared `ACCURACY_RULES`.

**Files:**
- Modify: `meetingscribe/config.py:12`
- Modify: `meetingscribe/prompts.py` (append rules loop)
- Test: `tests/test_prompts.py` (transplant from branch)

**Step 1: Transplant the prompts test**

Run: `git checkout feature/audio-speaker-attribution -- tests/test_prompts.py`

**Step 2: Run, expect fail**

Run: `pytest tests/test_prompts.py -v`
Expected: FAIL (rules not yet present in main's prompts).

**Step 3: Implement — model**

Edit `meetingscribe/config.py:12`:
```python
ANTHROPIC_MODEL = "claude-sonnet-4-6"
```

**Step 4: Implement — rules**

Append to the **end** of `meetingscribe/prompts.py` (after the `PROFILES` dict):

```python
ACCURACY_RULES = """

Accuracy rules (apply strictly):
1. Quote dollar amounts, dates, and figures exactly as spoken; do not add legal or technical characterizations (e.g. do not call an amount a "statutory maximum") unless the speaker used that term.
2. When a decision is revised during the meeting, record only the final decision; never carry both an old and a superseded value.
3. Preserve concrete specifics: counts, named people, and recipients (e.g. "send it to those three people" -> note that there are three recipients)."""

for _profile in PROFILES.values():
    for _key in ("system", "chunk", "merge"):
        _profile[_key] = _profile[_key] + ACCURACY_RULES
```

**Step 5: Run, expect pass**

Run: `pytest tests/test_prompts.py tests/test_profile.py -v`
Expected: all pass (rules present in every prompt; profile resolution intact).

**Step 6: Commit**

```bash
git add meetingscribe/config.py meetingscribe/prompts.py tests/test_prompts.py
git commit -m "feat: Sonnet model + anti-embellishment/consistency/specifics prompt rules"
```

---

## Phase 2 — Two-stream capture + transcription (the fallback path)

End state: a post-Stop attributed pipeline works (live still off). This is already a usable attributed build.

### Task 2.1: `system_audio.py` (SCK capture, per-chunk mono, live snapshot)

Transplant the branch's SCK class, then adapt: (a) convert planar→mono **per chunk** via `audio_format.planar_chunks_to_mono`; (b) add a thread-safe `snapshot(start_frame)` for live; (c) `stop()` keeps the buffer so post-Stop snapshots still work. SCK isn't unit-testable headless → manual verification, but the conversion path is covered by Task 1.1.

**Files:**
- Create: `meetingscribe/system_audio.py`

**Step 1: Transplant the reference**

Run: `git checkout feature/audio-speaker-attribution -- meetingscribe/system_audio.py`

**Step 2: Confirm it imports (graceful even if SCK missing)**

Run: `python3 -c "from meetingscribe.system_audio import SystemAudioRecorder; print('import OK', SystemAudioRecorder().available.__name__)"`
Expected: `import OK available` (no exception). If `ScreenCaptureKit` isn't installed locally, `available()` will return False later — that's fine for now; install with `python3 -m pip install --break-system-packages 'pyobjc-framework-ScreenCaptureKit>=12.2'` before the manual SCK check.

**Step 3: Adapt — per-chunk mono + thread-safe snapshot + buffer-preserving stop**

Replace the chunk handling in `meetingscribe/system_audio.py`:

- The delegate's `_extract` already returns raw `bytes` per buffer and `stream_didOutputSampleBuffer_ofType_` appends to `self._chunks`. Add a `threading.Lock` (`self._lock = threading.Lock()` in `init`) and guard the append:
  ```python
  data = self._extract(sbuf)
  if data:
      with self._lock:
          self._chunks.append(data)
  ```
- Add a delegate helper (mark `@objc.python_method`):
  ```python
  @objc.python_method
  def snapshot_mono(self, start_frame: int) -> "np.ndarray":
      from meetingscribe.audio_format import planar_chunks_to_mono
      with self._lock:
          chunks = list(self._chunks)          # copy refs cheaply; convert outside the lock
          channels = int(self._channels)
      mono = planar_chunks_to_mono(chunks, channels)
      return mono[start_frame:]
  ```
- In `SystemAudioRecorder`, replace `stop()`'s join-all-then-convert with the per-chunk path **and keep the buffer** so the final live tail is still readable after Stop:
  ```python
  def stop(self) -> tuple[np.ndarray, int]:
      # ... stop the stream (await stop handler) exactly as before, BUT:
      # do NOT null out self._delegate / self._keep yet — the final live tail
      # is read from the delegate after Stop. Mark stopped instead.
      self._stopped = True
      delegate = self._delegate
      if delegate is None:
          return (np.zeros(0, dtype="float32"), 48000)
      rate = int(delegate._rate)
      return (delegate.snapshot_mono(0), rate)

  def snapshot(self, start_frame: int) -> "np.ndarray":
      """Mono system audio from start_frame to now (valid during and after capture)."""
      d = self._delegate
      if d is None:
          return np.zeros(0, dtype="float32")
      return d.snapshot_mono(start_frame)

  def rate(self) -> int:
      return int(self._delegate._rate) if self._delegate is not None else 48000

  def release(self):
      """Drop ObjC refs once the recording is fully processed."""
      self._stream = None
      self._delegate = None
      self._keep = []
  ```
  (Add `import numpy as np` at top if not present; add `self._stopped = False` in `__init__`.)

**Step 4: Manual SCK verification** (requires Screen Recording permission for the host terminal/python)

Run:
```bash
python3 -c "
import time, subprocess, numpy as np
from meetingscribe.system_audio import SystemAudioRecorder
r = SystemAudioRecorder()
print('avail', r.available())
r.start(); subprocess.Popen(['say','testing one two three four five'])
time.sleep(3); mid = r.snapshot(0); print('mid samples', len(mid))
time.sleep(3); mono, sr = r.stop()
print('rate', sr, 'samples', len(mono), 'peak', float(np.max(np.abs(mono))) if mono.size else 0.0)
r.release()
"
```
Expected: `avail True`, mid samples > 0 (snapshot works mid-capture), rate 48000, peak > 0.01. If `avail False`: grant Screen Recording to the host app (System Settings → Privacy & Security → Screen Recording) and retry.

**Step 5: Commit**

```bash
git add meetingscribe/system_audio.py
git commit -m "feat: SCK system-audio recorder with per-chunk mono + live snapshot"
```

---

### Task 2.2: `recorder.py` — two streams, shared t0, per-side snapshots

Replace main's BlackHole recorder. `stop()` returns both streams + metadata and **keeps buffers**; `snapshot_side(side, start_frame)` feeds the two live workers (Phase 4) and is valid post-Stop.

**Files:**
- Modify: `meetingscribe/recorder.py`
- Test: `tests/test_recorder.py` (transplant + extend), remove `tests/test_recorder_snapshot.py` (mono-mix snapshot no longer exists)

**Step 1: Transplant the branch recorder + its test as a starting point**

Run:
```bash
git checkout feature/audio-speaker-attribution -- meetingscribe/recorder.py tests/test_recorder.py
git rm tests/test_recorder_snapshot.py
```

**Step 2: Write failing tests for `snapshot_side`**

Append to `tests/test_recorder.py`:

```python
import numpy as np
from unittest import mock


def _mk_recorder(monkeypatch):
    from meetingscribe import recorder
    monkeypatch.setattr(recorder.AudioRecorder, "__init__", lambda self: None)
    r = recorder.AudioRecorder()
    r._lock = __import__("threading").Lock()
    r._mic_frames = []
    r._sys = None
    r._system_available = False
    return r


def test_snapshot_side_local_concats_and_slices(monkeypatch):
    r = _mk_recorder(monkeypatch)
    r._mic_frames = [np.array([0.0, 0.1, 0.2, 0.3], dtype="float32").reshape(-1, 1)]
    out = r.snapshot_side("local", 2)
    assert np.allclose(out, [0.2, 0.3])
    assert out.dtype == np.float32


def test_snapshot_side_local_empty_is_float32(monkeypatch):
    r = _mk_recorder(monkeypatch)
    out = r.snapshot_side("local", 0)
    assert out.size == 0 and out.dtype == np.float32


def test_snapshot_side_remote_delegates_to_system(monkeypatch):
    r = _mk_recorder(monkeypatch)
    fake_sys = mock.MagicMock()
    fake_sys.snapshot.return_value = np.array([0.5, 0.5], dtype="float32")
    r._sys = fake_sys
    r._system_available = True
    out = r.snapshot_side("remote", 7)
    fake_sys.snapshot.assert_called_once_with(7)
    assert np.allclose(out, [0.5, 0.5])


def test_snapshot_side_remote_mic_only_is_empty(monkeypatch):
    r = _mk_recorder(monkeypatch)
    out = r.snapshot_side("remote", 0)   # no system stream
    assert out.size == 0 and out.dtype == np.float32
```

**Step 3: Run, expect fail**

Run: `pytest tests/test_recorder.py -k snapshot_side -v`
Expected: FAIL (`snapshot_side` not defined).

**Step 4: Implement**

Add to `meetingscribe/recorder.py` `AudioRecorder` (the branch version already keeps `_mic_frames` under `_lock` and holds `self._sys`):

```python
import numpy as np  # ensure imported

def snapshot_side(self, side: str, start_frame: int = 0) -> np.ndarray:
    """Thread-safe mono audio for one side, from start_frame to now.

    Valid during recording and after stop() (stop() does not clear buffers).
    'local' = mic frames; 'remote' = system stream (empty when mic-only).
    """
    if side == "local":
        with self._lock:
            blocks = list(self._mic_frames)
        if not blocks:
            return np.zeros(0, dtype="float32")
        mono = np.concatenate(blocks).reshape(-1).astype("float32")
        return mono[start_frame:]
    if side == "remote":
        if self._system_available and self._sys is not None:
            return self._sys.snapshot(start_frame).astype("float32")
        return np.zeros(0, dtype="float32")
    raise ValueError(f"unknown side: {side}")
```

Ensure `stop()` does **not** clear `_mic_frames` and calls `self._sys.stop()` (which now keeps the SCK buffer) — the branch's `stop()` already returns `{local, local_rate, remote, remote_rate, t0, system_available}`; leave it, but after building the return dict do **not** release `self._sys` (Phase 4's finalize reads its post-Stop tail; release happens in `app._finish`).

**Step 5: Run, expect pass**

Run: `pytest tests/test_recorder.py -v`
Expected: all pass.

**Step 6: Commit**

```bash
git add meetingscribe/recorder.py tests/test_recorder.py
git rm tests/test_recorder_snapshot.py 2>/dev/null; git add -A tests/
git commit -m "refactor: two-stream recorder (mic + SCK) with per-side live snapshots, shared t0"
```

---

### Task 2.3: `transcriber.py` — two-stream + merge, keep `transcribe_segments`

Main's `transcriber.py` has `transcribe()` (whole-file) and `transcribe_segments()` (used by live). Add the branch's `transcribe_streams()` for the post-Stop attributed path. Keep `transcribe_segments()` (Phase 4 live needs it).

**Files:**
- Modify: `meetingscribe/transcriber.py`
- Test: `tests/test_transcriber.py` (transplant + keep main's `tests/test_transcribe_segments.py`)

**Step 1: Transplant the branch transcriber test**

Run: `git checkout feature/audio-speaker-attribution -- tests/test_transcriber.py`

**Step 2: Run, expect fail**

Run: `pytest tests/test_transcriber.py -v`
Expected: FAIL (`transcribe_streams` not defined on main's Transcriber).

**Step 3: Implement — add `transcribe_streams`, keep existing methods**

Add to `meetingscribe/transcriber.py` (imports: `from meetingscribe.audio_format import resample_to_16k`; `from meetingscribe.segments import merge_segments`). Add the method (verbatim from the branch — see `.worktrees/audio-speaker-attribution/meetingscribe/transcriber.py:33-92`):

```python
def transcribe_streams(self, local, local_rate, remote, remote_rate, on_progress=None) -> list[dict]:
    """Transcribe local + remote streams separately, merge into one
    side-attributed, timestamp-ordered segment list ({start,end,text,side,id})."""
    self._load_model()
    streams = [("local", local, local_rate), ("remote", remote, remote_rate)]
    def _dur(arr, rate):
        return arr.size / rate if rate > 0 and arr.size > 0 else 0.0
    total_dur = sum(_dur(a, r) for _, a, r in streams)
    elapsed, local_segs, remote_segs = 0.0, [], []
    for side, arr, rate in streams:
        if arr.size == 0:
            if on_progress and total_dur > 0:
                on_progress(min(elapsed / total_dur, 1.0))
            continue
        arr16 = resample_to_16k(arr, rate)
        raw, _info = self._model.transcribe(arr16, beam_size=5, vad_filter=True)
        segs = [{"start": s.start, "end": s.end, "text": s.text.strip(), "side": side} for s in raw]
        if side == "local":
            local_segs = segs
        else:
            remote_segs = segs
        elapsed += _dur(arr, rate)
        if on_progress and total_dur > 0:
            on_progress(min(elapsed / total_dur, 1.0))
    return merge_segments(local_segs, remote_segs)
```

**Important:** do **not** delete `transcribe()` or `transcribe_segments()` — they back the live path and the whole-file fallback inside `resolve_transcript`. (`transcribe_segments` currently writes a temp WAV at `SAMPLE_RATE`; it stays for the local-side live ticks at 44.1k. For the remote side at 48k, see Phase 4 note.)

**Step 4: Run, expect pass**

Run: `pytest tests/test_transcriber.py tests/test_transcribe_segments.py -v`
Expected: all pass.

**Step 5: Commit**

```bash
git add meetingscribe/transcriber.py tests/test_transcriber.py
git commit -m "feat: two-stream transcribe_streams merged into side-attributed segments"
```

---

### Task 2.4: `speakers.py` — naming pass, keyed off the Keychain

Transplant the branch's naming pass, but switch the key source from `config.ANTHROPIC_API_KEY` to `secrets.get_api_key()` (main's Keychain-first resolver) and keep using `config.ANTHROPIC_MODEL` (now Sonnet).

**Files:**
- Create: `meetingscribe/speakers.py`
- Test: `tests/test_speakers.py`

**Step 1: Transplant**

Run: `git checkout feature/audio-speaker-attribution -- meetingscribe/speakers.py tests/test_speakers.py`

**Step 2: Run, expect pass (transplant baseline)**

Run: `pytest tests/test_speakers.py -v`
Expected: pass (tests monkeypatch `_call_llm`, so key source is irrelevant to them).

**Step 3: Adapt `_call_llm` to the Keychain**

In `meetingscribe/speakers.py`, change the import and `_call_llm`:
```python
from meetingscribe.config import ANTHROPIC_MODEL
from meetingscribe.secrets import get_api_key
from meetingscribe.segments import apply_speaker_map

def _call_llm(prompt: str) -> str:
    client = anthropic.Anthropic(api_key=get_api_key())
    message = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text
```
(Remove the `ANTHROPIC_API_KEY` import.) `name_speakers` already catches all exceptions and falls back to side labels, so a missing key degrades gracefully.

**Step 4: Add a test for graceful fallback when the key/LLM is unavailable**

Append to `tests/test_speakers.py`:
```python
def test_name_speakers_falls_back_on_llm_error(monkeypatch):
    from meetingscribe import speakers
    from meetingscribe.segments import merge_segments
    def boom(_prompt):
        raise RuntimeError("no key / network down")
    monkeypatch.setattr(speakers, "_call_llm", boom)
    merged = merge_segments([{"start": 0, "end": 1, "text": "hi", "side": "local"}],
                            [{"start": 2, "end": 3, "text": "yo", "side": "remote"}])
    named = speakers.name_speakers(merged, local_name="Jacob")
    assert named[0]["speaker"] == "Jacob"
    assert named[1]["speaker"] == "Remote speaker"
```

**Step 5: Run, expect pass**

Run: `pytest tests/test_speakers.py -v`
Expected: all pass.

**Step 6: Commit**

```bash
git add meetingscribe/speakers.py tests/test_speakers.py
git commit -m "feat: LLM speaker-naming pass (Keychain-keyed, verbatim-preserving, graceful fallback)"
```

---

### Task 2.5: `summarizer.py` — keep main's lazy Keychain client, feed attributed text

Main's summarizer already resolves the key lazily via `get_api_key()` and raises `NoAPIKeyError` (handled in `app.py`). **No structural change** — it already accepts a transcript string and will receive the attributed transcript. Just confirm it still works with the Sonnet model + new prompts.

**Files:**
- Verify only: `meetingscribe/summarizer.py`, `tests/test_summarizer_key.py`

**Step 1: Run the summarizer key tests**

Run: `pytest tests/test_summarizer_key.py -v`
Expected: pass (NoAPIKeyError when no key; client built from Keychain otherwise).

No commit (no change). If a test references the old Haiku model string, update it to `claude-sonnet-4-6` and commit `test: update summarizer model assertion to Sonnet`.

---

### Phase 2 checkpoint

Run the whole suite: `pytest`
Expected: green. At this point the *fallback* attributed pipeline (capture → `transcribe_streams` → `name_speakers` → `format_transcript` → summarize) exists in modules but is not yet wired into `app.py`. That's Phase 3.

---

## Phase 3 — App reconciliation (spine + attributed pipeline + auto-detect)

The highest-conflict surface. Rebuild `app.py` deliberately to carry **main's spine** (Keychain prompt, Sparkle, dynamic profile, "Live transcription" + "Set API Key…" + "Check for Updates…" menu, version) **and** the branch's additions (auto-detect + confirm popup, SCK permission alert) **and** the new attributed pipeline. Live stays **off** here (deferred to Phase 4); the checkbox still persists its pref.

### Task 3.1: Transplant `meeting_detector.py` (+ spike + test)

**Files:**
- Create: `meetingscribe/meeting_detector.py`, `spikes/mic_in_use_spike.py`
- Test: `tests/test_meeting_detector.py`

**Step 1: Transplant**

Run:
```bash
git checkout feature/audio-speaker-attribution -- meetingscribe/meeting_detector.py spikes/mic_in_use_spike.py tests/test_meeting_detector.py
```

**Step 2: Run, expect pass**

Run: `pytest tests/test_meeting_detector.py -v`
Expected: pass (`should_prompt` truth table).

**Step 3: Commit**

```bash
git add meetingscribe/meeting_detector.py spikes/mic_in_use_spike.py tests/test_meeting_detector.py
git commit -m "feat: add MeetingDetector (CoreAudio mic-in-use + meeting-app poll)"
```

---

### Task 3.2: Reconcile `app.py` (manual-verified wiring)

**Files:**
- Modify: `meetingscribe/app.py`
- Keep green: `tests/test_foreground.py`, `tests/test_version.py`

**Step 1: Write `app.py`** combining both worlds. Key requirements (use main's `app.py` as the base, graft the branch's pieces):

- **Imports:** main's (`settings`, `secrets.get_api_key/set_api_key`, `updater.init_sparkle/check_for_updates`, `progress`, `notes`) **plus** `from meetingscribe.recorder import AudioRecorder`, `from meetingscribe.transcriber import Transcriber`, `from meetingscribe.speakers import name_speakers`, `from meetingscribe.segments import merge_segments, format_transcript`, `from meetingscribe.system_audio import SystemAudioRecorder`, `from meetingscribe.meeting_detector import MeetingDetector`. Remove `from meetingscribe.recorder import find_blackhole_device`.
- **Profile display name:** `from meetingscribe.config import USER_PROFILE`; `PROFILE_DISPLAY_NAME = USER_PROFILE.capitalize()` (restores dynamic profile — no hardcoding).
- **Menu** (union, order):
  ```
  MeetingScribe v{_app_version()}
  ──
  Start Recording
  ──
  ✓ Auto-detect meetings      (callback: _toggle_auto_detect, state from _load_auto_detect_pref)
  ✓ Live transcription        (callback: toggle_live_transcription, state from settings.live_transcription_enabled)
  Set API Key…                (callback: set_api_key_clicked)
  Check for Updates…          (callback: check_for_updates)
  ──
  Quit
  ```
- **Startup:** keep main's `if not get_api_key(): prompt_for_api_key()` and `init_sparkle()`. Replace main's BlackHole-missing alert with the branch's **Screen Recording** alert when `SystemAudioRecorder().available()` is False (mic-only still allowed). Start `MeetingDetector` if the auto-detect pref is on (branch's `_on_meeting_detected` / `_prompt_record` forced-foreground popup — transplant verbatim).
- **`_process_recording`** (attributed, fallback-only for now):
  ```python
  def _process_recording(self):
      try:
          self._update_progress("Loading model...", detail="First time takes a moment")
          self._transcriber._load_model()
          result = self._recorder.stop()

          def on_tx(p):
              self._update_progress("Transcribing...", pct=p, detail=f"{int(p*100)}% complete")

          self._update_progress("Transcribing...", pct=0.0, detail="Converting speech to text")
          segments = self._transcriber.transcribe_streams(
              result["local"], result["local_rate"],
              result["remote"], result["remote_rate"], on_progress=on_tx,
          )
          if not segments:
              self._finish("No speech detected", "The recording didn't contain any recognizable speech.")
              return

          self._update_progress("Identifying speakers...", detail="Labeling who said what")
          named = name_speakers(segments, local_name=PROFILE_DISPLAY_NAME)
          transcript_text = format_transcript(named)

          self._update_progress("Summarizing...", detail="Generating meeting notes with AI")
          from meetingscribe.summarizer import NoAPIKeyError
          try:
              summary = self._summarizer.summarize(transcript_text)
          except NoAPIKeyError:
              from datetime import datetime as _dt
              save_to_notes(f"Meeting — {_dt.now():%Y-%m-%d %H:%M}", transcript_text)
              self._finish("No API key", "Saved the transcript to Apple Notes, but skipped the AI summary — set your Anthropic API key (Set API Key…).")
              return

          # ... build Notes body (MEETING NOTES + RAW TRANSCRIPT = transcript_text), save_to_notes,
          #     success/failure _finish, release SCK refs: 
          if self._recorder._sys is not None:
              self._recorder._sys.release()
      except Exception as e:
          log.exception("Error processing recording")
          self._finish("Error", f"Something went wrong:\n\n{str(e)[:300]}")
  ```
  Note: `_stop_recording` no longer pre-writes a WAV (the recorder doesn't write one); `_process_recording` calls `self._recorder.stop()` itself (as the branch does). Keep main's progress window + foreground-alert helpers.
- **Recorder no WAV cleanup:** delete the `os.remove(wav_path)` block (no WAV exists). Notes-save success path just `_finish("Done!", ...)`.
- **Live wiring:** do NOT start a live worker yet. Keep `toggle_live_transcription` (persists the pref) and the checkbox. Phase 4 makes it functional.

**Step 2: Lint-import check**

Run: `python3 -c "import ast; ast.parse(open('meetingscribe/app.py').read()); print('app.py parses')"`
Expected: parses. Then `pytest tests/test_foreground.py tests/test_version.py -v` → pass.

**Step 3: Manual smoke (mic-only path, no SCK needed)**

Run from the worktree root: `MS_LIVE_TRANSCRIPTION=0 python3 -m meetingscribe.app`
- Confirm the menu shows all items, version, both checkboxes.
- Record ~15 s talking; Stop; confirm a Note is created with a `[m:ss] {Profile}:` attributed transcript (all local if no SCK) + a Sonnet summary.

**Step 4: Commit**

```bash
git add meetingscribe/app.py
git commit -m "feat: wire attributed pipeline + auto-detect into app; reconcile with live/Sparkle/Keychain spine"
```

---

### Phase 3 checkpoint

Run: `pytest` → green. Manual: an attributed Note is produced post-Stop. Auto-detect popup appears on a Zoom/Teams join. (Live still off.)

---

## Phase 4 — Per-channel live transcription

Make the "Live transcription" checkbox functional **with** attribution: two `LiveTranscriber`s (one per side) commit side-tagged segments during the meeting; Stop merges them. Live-off / error → the Phase 2/3 `transcribe_streams` fallback. Both converge on `merge_segments → name_speakers → format_transcript`.

### Task 4.1: Side-aware `LiveTranscriber` (retain absolute segments + side)

Extend main's `LiveTranscriber` to retain committed segments with **absolute** start/end (from `committed_sample`) and a `side` label, without breaking its 17 existing tests (which don't pass `side`).

**Files:**
- Modify: `meetingscribe/live_transcriber.py`
- Test: `tests/test_live_transcriber.py` (extend)

**Step 1: Write failing tests** — append to `tests/test_live_transcriber.py`:

```python
def test_committed_segments_have_absolute_times_and_side():
    fake = FakeTranscriber([[(0.0, 10.0, "alpha"), (10.0, 19.0, "beta")]])
    lt = LiveTranscriber(fake, sample_rate=SR, guard_sec=3, max_tail_sec=90, side="remote")
    lt.process_tick(_tail(20))                       # commits "alpha" (ends 10)
    segs = lt.committed_segments()
    assert len(segs) == 1
    assert segs[0]["text"] == "alpha"
    assert segs[0]["side"] == "remote"
    assert segs[0]["start"] == 0.0 and segs[0]["end"] == 10.0


def test_committed_segments_absolute_across_ticks():
    fake = FakeTranscriber([
        [(0.0, 10.0, "alpha"), (10.0, 19.0, "beta")],   # tick1 commits alpha, base advances to 10s
        [(0.0, 9.0, "gamma")],                          # tick2 tail starts at 10s -> gamma abs 10..19
    ])
    lt = LiveTranscriber(fake, sample_rate=SR, guard_sec=3, max_tail_sec=90, side="local")
    lt.process_tick(_tail(20))
    lt.process_tick(_tail(15))
    segs = lt.committed_segments()
    assert [s["text"] for s in segs] == ["alpha", "gamma"]
    assert segs[1]["start"] == 10.0 and segs[1]["end"] == 19.0   # base offset applied


def test_finalize_appends_absolute_segment():
    fake = FakeTranscriber([[(0.0, 4.0, "omega")]])
    lt = LiveTranscriber(fake, sample_rate=SR, guard_sec=3, max_tail_sec=90, side="local")
    lt.finalize(_tail(5))                              # no prior commits, base 0
    segs = lt.committed_segments()
    assert segs[0]["text"] == "omega" and segs[0]["start"] == 0.0 and segs[0]["end"] == 4.0
```

**Step 2: Run, expect fail**

Run: `pytest tests/test_live_transcriber.py -k "committed_segments or finalize_appends_absolute" -v`
Expected: FAIL (`side` kwarg / `committed_segments` missing).

**Step 3: Implement** — minimal edits to `meetingscribe/live_transcriber.py`:

- `__init__(..., side=None)`: store `self._side = side`; `self._committed_segments = []`.
- In `process_tick`, capture the base **before** the commit loop and record each committed segment:
  ```python
  base_s = self.committed_sample / self._sr
  ...
  for start, end, text in segments:
      committable = end <= horizon or (force and start < horizon)
      if not committable:
          break
      cleaned = text.strip()
      if cleaned:
          self._committed.append(cleaned)
          self._committed_segments.append(
              {"start": base_s + start, "end": base_s + end, "text": cleaned, "side": self._side}
          )
      last_end = end
  ```
- In `finalize`, same base capture + record:
  ```python
  base_s = self.committed_sample / self._sr
  if tail is not None and len(tail) > 0:
      for start, end, text in list(self._transcriber.transcribe_segments(tail)):
          cleaned = text.strip()
          if cleaned:
              self._committed.append(cleaned)
              self._committed_segments.append(
                  {"start": base_s + start, "end": base_s + end, "text": cleaned, "side": self._side}
              )
              self._ever_committed = True
  return self.text()
  ```
- Add:
  ```python
  def committed_segments(self) -> list[dict]:
      return list(self._committed_segments)
  ```

**Step 4: Run, expect pass (new + all 17 existing)**

Run: `pytest tests/test_live_transcriber.py -v`
Expected: all pass (existing tests unaffected — `side` defaults to None; `text()` unchanged).

**Step 5: Commit**

```bash
git add meetingscribe/live_transcriber.py tests/test_live_transcriber.py
git commit -m "feat: side-aware LiveTranscriber retains absolute-timed segments for merge"
```

---

### Task 4.2: `resolve_segments` — live-or-fallback to one merged list

Add a segment-level resolver mirroring `resolve_transcript`: prefer the live committed segments (merged), else the post-Stop `transcribe_streams`; on live finalize error, fall back. Put it in `live_transcriber.py` (next to `resolve_transcript`).

**Files:**
- Modify: `meetingscribe/live_transcriber.py`
- Test: `tests/test_live_transcriber.py` (extend)

**Step 1: Failing tests** — append:

```python
class FakeStreamTranscriber:
    """transcribe_streams returns a scripted merged list; transcribe_segments scripted per call."""
    def __init__(self, streams_result, seg_scripts=None):
        self._streams = streams_result
        self._scripts = list(seg_scripts or [])
    def transcribe_streams(self, *a, **k):
        return self._streams
    def transcribe_segments(self, source):
        return self._scripts.pop(0) if self._scripts else []


def test_resolve_segments_uses_live_when_committed():
    from meetingscribe.live_transcriber import resolve_segments
    t = FakeStreamTranscriber(streams_result=[{"start": 9, "end": 9, "text": "FALLBACK", "side": "local", "id": 1}])
    local = LiveTranscriber(t, sample_rate=SR, side="local"); local._ever_committed = True
    local._committed_segments = [{"start": 0.0, "end": 1.0, "text": "L", "side": "local"}]
    remote = LiveTranscriber(t, sample_rate=SR, side="remote"); remote._ever_committed = True
    remote._committed_segments = [{"start": 0.5, "end": 1.5, "text": "R", "side": "remote"}]
    merged = resolve_segments(t, local, remote, _tail(0), _tail(0),
                              {"local": _tail(0), "local_rate": SR, "remote": _tail(0), "remote_rate": SR})
    assert [s["text"] for s in merged] == ["L", "R"]   # merged by start, not the FALLBACK
    assert all("id" in s for s in merged)


def test_resolve_segments_falls_back_when_no_live():
    from meetingscribe.live_transcriber import resolve_segments
    t = FakeStreamTranscriber(streams_result=[{"start": 0, "end": 1, "text": "FB", "side": "local", "id": 1}])
    merged = resolve_segments(t, None, None, None, None,
                              {"local": _tail(1), "local_rate": SR, "remote": _tail(0), "remote_rate": SR})
    assert [s["text"] for s in merged] == ["FB"]
```

**Step 2: Run, expect fail.** `pytest tests/test_live_transcriber.py -k resolve_segments -v`

**Step 3: Implement** — add to `meetingscribe/live_transcriber.py`:

```python
from meetingscribe.segments import merge_segments

def resolve_segments(transcriber, live_local, live_remote, final_local_tail,
                     final_remote_tail, stream_result, on_progress=None):
    """Return one merged side-tagged segment list. Prefer the per-channel live
    commits (finalize both, merge); else (or on finalize error) the post-Stop
    transcribe_streams fallback — never worse than the non-live path."""
    live_ran = (live_local is not None and live_remote is not None
                and (live_local.ever_committed or live_remote.ever_committed))
    if live_ran:
        try:
            live_local.finalize(final_local_tail)
            live_remote.finalize(final_remote_tail)
            merged = merge_segments(live_local.committed_segments(),
                                    live_remote.committed_segments())
            if on_progress:
                on_progress(1.0)
            return merged
        except Exception:
            log.exception("live: finalize failed; falling back to transcribe_streams")
    return transcriber.transcribe_streams(
        stream_result["local"], stream_result["local_rate"],
        stream_result["remote"], stream_result["remote_rate"], on_progress=on_progress,
    )
```

**Step 4: Run, expect pass.** `pytest tests/test_live_transcriber.py -v`

**Step 5: Commit**

```bash
git add meetingscribe/live_transcriber.py tests/test_live_transcriber.py
git commit -m "feat: resolve_segments — per-channel live merge with transcribe_streams fallback"
```

---

### Task 4.3: Wire two live workers + Stop resolution into `app.py`

**Files:**
- Modify: `meetingscribe/app.py`

**Step 1: Implement.**

- **`_start_recording`:** after `self._recorder.start()`, if `settings.live_transcription_enabled()`:
  ```python
  from meetingscribe.config import SAMPLE_RATE
  self._live_local = LiveTranscriber(self._transcriber, SAMPLE_RATE, side="local")
  remote_rate = self._recorder.remote_rate() if self._recorder.system_available() else 48000
  self._live_remote = LiveTranscriber(self._transcriber, remote_rate, side="remote")
  self._live_worker_thread = threading.Thread(target=self._live_worker, daemon=True)
  self._live_worker_thread.start()
  ```
  else set all three to `None`. (Add `remote_rate()`/`system_available()` accessors to `AudioRecorder`, or read `self._recorder._sys.rate()` / `self._recorder._system_available`.)
- **`_live_worker`:** preload model once; each cadence tick local then remote **sequentially** (single model — never concurrent):
  ```python
  def _live_worker(self):
      try:
          self._transcriber._load_model()
      except Exception:
          log.exception("live: preload failed; disabling live")
          self._live_local = self._live_remote = None
          return
      while self._recording:
          for _ in range(LIVE_CADENCE_SEC):
              if not self._recording:
                  break
              time.sleep(1)
          if not self._recording:
              break
          for side, lt in (("local", self._live_local), ("remote", self._live_remote)):
              if lt is None:
                  continue
              try:
                  lt.process_tick(self._recorder.snapshot_side(side, lt.committed_sample))
              except Exception:
                  log.exception("live: %s tick failed", side)
  ```
- **`_process_recording`:** join the worker first (single-model safety), take final tails, resolve to merged segments:
  ```python
  if self._live_worker_thread is not None:
      self._live_worker_thread.join()
      self._live_worker_thread = None
  result = self._recorder.stop()
  ll, lr = self._live_local, self._live_remote
  final_local = self._recorder.snapshot_side("local", ll.committed_sample) if ll else None
  final_remote = self._recorder.snapshot_side("remote", lr.committed_sample) if lr else None
  segments = resolve_segments(self._transcriber, ll, lr, final_local, final_remote, result, on_progress=on_tx)
  ```
  Then `name_speakers` → `format_transcript` → summarize → Notes exactly as Phase 3. Release the SCK refs (`self._recorder._sys.release()`) and clear `self._live_local/_live_remote = None` in `_finish`.

**Step 2: Import check + existing tests**

Run: `python3 -c "import ast; ast.parse(open('meetingscribe/app.py').read()); print('ok')"` then `pytest`
Expected: parses; full suite green.

**Step 3: Manual verification — live ON, two voices**

Run from worktree root: `python3 -m meetingscribe.app` (live default on). Record ~60 s: you talking + a remote voice on a Zoom/Teams call (or `say` piped through a call). Stop.
Confirm: (a) Stop returns quickly (live caught up); (b) Note transcript alternates `[m:ss] {Profile}:` and `[m:ss] {RemoteName|Remote speaker}:`; (c) summary accurate.
Then toggle Live OFF, record again, confirm the **fallback** still yields the same attributed shape (post-Stop).

**Step 4: Commit**

```bash
git add meetingscribe/app.py meetingscribe/recorder.py
git commit -m "feat: per-channel live workers + Stop merge (live), transcribe_streams fallback (off)"
```

---

### Task 4.4: Parity test — merge-from-live ≡ merge-from-streams

Lock in the convergence guarantee with a pure test (no Whisper).

**Files:**
- Test: `tests/test_attribution_parity.py` (new)

**Step 1: Write the test**

```python
from meetingscribe.segments import merge_segments
from meetingscribe.live_transcriber import LiveTranscriber, resolve_segments


class _T:
    def __init__(self, streams): self._streams = streams
    def transcribe_streams(self, *a, **k): return self._streams
    def transcribe_segments(self, source): return []


def test_live_and_stream_paths_produce_same_merged_order():
    local_segs = [{"start": 0.0, "end": 1.0, "text": "L1", "side": "local"},
                  {"start": 4.0, "end": 5.0, "text": "L2", "side": "local"}]
    remote_segs = [{"start": 2.0, "end": 3.0, "text": "R1", "side": "remote"}]
    # Stream path: transcribe_streams already returns the merged list.
    streams_merged = merge_segments(list(local_segs), list(remote_segs))
    t = _T(streams_merged)
    # Live path: two transcribers carrying the same committed segments.
    import numpy as np
    ll = LiveTranscriber(t, sample_rate=100, side="local"); ll._ever_committed = True
    ll._committed_segments = list(local_segs)
    lr = LiveTranscriber(t, sample_rate=100, side="remote"); lr._ever_committed = True
    lr._committed_segments = list(remote_segs)
    live_merged = resolve_segments(t, ll, lr, np.zeros(0, "float32"), np.zeros(0, "float32"),
                                   {"local": np.zeros(0, "float32"), "local_rate": 100,
                                    "remote": np.zeros(0, "float32"), "remote_rate": 100})
    assert [(s["text"], s["side"]) for s in live_merged] == [(s["text"], s["side"]) for s in streams_merged]
```

**Step 2: Run, expect pass.** `pytest tests/test_attribution_parity.py -v`

**Step 3: Commit**

```bash
git add tests/test_attribution_parity.py
git commit -m "test: live and fallback attribution paths produce identical merged segments"
```

---

### Phase 4 checkpoint

Run: `pytest` → green. Manual: live-on fast attributed Note; live-off fallback attributed Note. The feature is functionally complete.

---

## Phase 5 — Packaging & dependencies

### Task 5.1: PyInstaller spec + requirements

**Files:**
- Modify: `MeetingScribe.spec`, `requirements.txt`, `setup.py`

**Step 1: Implement.**
- `requirements.txt`: add `pyobjc-framework-ScreenCaptureKit>=12.2`; **keep** `pyobjc-framework-Security>=10.0` (Keychain). (Sparkle is bundled, not pip.)
- `MeetingScribe.spec` `hiddenimports`: add `ScreenCaptureKit`, `CoreMedia`, `Quartz`, `CoreVideo`, `CoreAudio` (for `meeting_detector`'s ctypes is fine, but the pyobjc frameworks used by `system_audio` must be hidden-imported). Keep existing `.env` `datas` and Sparkle framework bundling.
- `setup.py`: mirror the new dependency.

**Step 2: Verify a build** (on the dev Mac)

Run: `pyinstaller MeetingScribe.spec --noconfirm && codesign --force --deep -s - dist/MeetingScribe.app && codesign --verify --verbose=2 dist/MeetingScribe.app`
Expected: build completes; signature valid. Launch `dist/MeetingScribe.app`, confirm the menu + a short attributed recording.
**Note (design §9 risk):** ad-hoc signing re-prompts Screen Recording per rebuild — the stable-signing/TCC-persistence fix is tracked with the Sparkle Developer-ID work, not gated here.

**Step 3: Commit**

```bash
git add MeetingScribe.spec requirements.txt setup.py
git commit -m "build: bundle ScreenCaptureKit deps; keep Security/Sparkle; note signing follow-up"
```

---

## Phase 6 — End-to-end verification & finish

### Task 6.1: Real-meeting E2E

1. Real ~2-min call on AirPods + one remote participant, Live **on**.
2. Confirm: (a) system audio captured with no BlackHole / Audio MIDI Setup; (b) transcript attributed local vs. remote, real names where context allows, **verbatim** text; (c) summary free of the three validation errors (no invented "statutory maximum"; single final billing frequency; recipient counts preserved); (d) Stop turnaround is short (live caught up).
3. Repeat with Live **off** → same attributed shape via fallback (slower Stop).
4. Note any cross-channel sync skew; if turns feel misaligned, escalate to PTS alignment (design 2026-06-01 §12).

### Task 6.2: Finish the branch

Run the full suite once more: `pytest` → green. Then use **superpowers:finishing-a-development-branch** to choose merge/PR into `main`.

---

## Open items carried from design
- **Signing / TCC persistence** for SCK across Sparkle updates — tracked with the Sparkle Developer-ID work (blocked on Apple/GitHub secrets); not gated here.
- **2× live CPU** on Laurelle's slower Mac — mitigated by the Live checkbox + per-variant default; consider defaulting Live off on her variant build.
- **AirPods-as-mic HFP quality** — optional follow-up to prefer the built-in mic for input while listening on AirPods.
- **Multi-speaker on the local (mic) channel** — remains a single "local" label (out of scope).
- **Auto-stop on meeting end** (mic released / meeting app quit) — deferred follow-up to the auto-detect feature.
```
