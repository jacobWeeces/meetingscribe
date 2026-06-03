# Design — Live (During-Meeting) Transcription

**Date:** 2026-06-03
**Status:** Brainstorm complete & validated. Pending implementation plan.
**Author:** Jacob

---

## 1. Context & problem

MeetingScribe records a meeting, then on **Stop** runs the whole recording through `faster-whisper` (bundled `medium` int8, CPU), summarizes with the Anthropic API, and saves notes + transcript to Apple Notes.

Today, **all transcription happens after Stop**:

- [recorder.py](../../meetingscribe/recorder.py) piles raw mic + BlackHole-system frames into in-memory lists during recording. Nothing is transcribed yet.
- On Stop, [recorder.py](../../meetingscribe/recorder.py) mixes the frames to one stereo WAV; then [_process_recording](../../meetingscribe/app.py) hands the whole file to [transcriber.py](../../meetingscribe/transcriber.py), summarizes, and saves.

`faster-whisper` runs **several× faster than real time** on the target machine (Apple M4 Pro, 14 cores, 64 GB). So the user waits through a "Transcribing…" progress bar after every meeting for work that *could* have happened while the meeting was still going — the raw audio is already sitting in memory in chunks as it records.

**The problem:** the post-meeting wait is pure dead time. We want to spend it during the meeting instead.

---

## 2. Goals / non-goals

**Goals**
- Transcribe **incrementally during recording** so that by the time the user hits Stop, transcription is essentially caught up.
- Shrink the post-Stop wait from "transcribe the whole meeting + summarize" to **just "summarize"** (a single Claude/Haiku call, a few seconds).
- Preserve output **quality**: the final transcript must be quality-equivalent to today's whole-file pass.
- **Never do worse than today** — any failure in the live path falls back to the current behavior.

**Non-goals (YAGNI)**
- **Live display / captions.** The user never looks at the live text; this is purely a turnaround optimization. No live transcript window.
- **Live/incremental summarization.** The summary stays as one call at Stop, on the full transcript (best quality). No live map-reduce.
- **Speaker attribution / diarization.** Out of scope here (tracked separately).
- **Auto-detecting slow machines** and adapting cadence. The on/off checkbox (§6) is the escape hatch.
- **Periodic WAV flush** to survive a mid-meeting crash. Today already loses in-memory audio on a crash; no regression, revisit later if needed.

### Decisions (validated during brainstorm)
1. **Goal:** faster turnaround — identical end result, ready faster. *Not* live captions or a live assistant.
2. **Summary:** stays at Stop, one call on the full transcript.
3. **Chunking:** **segment-commit** — commit only Whisper-delimited segments that ended at a silence boundary, holding back a trailing guard. (Chosen over naive fixed-interval and overlap-and-stitch.)
4. **Control:** a visible **"Live transcription" menu checkbox**, default on, persisted.

---

## 3. Architecture & data flow

**New component:** a `LiveTranscriber` ([live_transcriber.py](../../meetingscribe/live_transcriber.py)) that wraps the existing [Transcriber](../../meetingscribe/transcriber.py). It owns the committed-transcript-so-far and the audio position up to which we've transcribed (`committed_sample`).

**Recorder change:** [recorder.py](../../meetingscribe/recorder.py) keeps capturing into `_mic_frames` / `_sys_frames` exactly as today, but gains one thread-safe read method — `snapshot_mono(start_sample)` — returning the mono mix `(mic + sys) / 2` from `start_sample` to "now" (mic-only when there's no BlackHole; clip to the shorter stream, matching `stop()`). This is the same effective mix Whisper produces by downmixing today's stereo file, so live output matches the final-file output. **Capture itself is untouched.**

**Worker thread:** while recording, a daemon loop wakes every `LIVE_CADENCE_SEC` (~25 s), asks the recorder for the audio since `committed_sample`, and hands it to `LiveTranscriber.process_tick(tail)`, which transcribes and commits the "settled" part (§4).

**At Stop:** instead of transcribing the whole WAV, [_process_recording](../../meetingscribe/app.py) calls `LiveTranscriber.finalize()` — one short pass over the remaining tail — then runs summarization + Notes save **exactly as today**.

**Safety net:** the WAV is still written at Stop. If live transcription was off or errored, `finalize()` transcribes the whole WAV — worst case equals today.

**Threading model:** capture callbacks (audio thread) → frame lists (under lock) → worker thread reads mono snapshots → commits to a transcript list → Stop thread reads the result. One Whisper model instance; never used concurrently (§5).

---

## 4. The segment-commit algorithm

**State in `LiveTranscriber`:** `committed_text` (list of committed segment strings), `committed_sample` (audio index — everything before it is done). Tunables (from [config.py](../../meetingscribe/config.py)): **`LIVE_CADENCE_SEC = 25`** (worker sleep), **`LIVE_GUARD_SEC = 3`** (trailing audio never committed yet), **`LIVE_MAX_TAIL_SEC = 90`** (safety cap).

**`process_tick(tail)`** — where `tail` is mono audio from `committed_sample` → now:
1. If `tail` shorter than ~`guard` + a few seconds, skip.
2. Write `tail` to a temp WAV at `SAMPLE_RATE` (44100) and run it through the existing `Transcriber` path — so Whisper's decoder does the 44.1k→16k resample and `vad_filter` segmentation. **Zero new deps; exact parity with today's decode.**
3. Whisper returns segments with `(start, end, text)` relative to the tail. Compute `horizon = tail_len_s − guard`. **Commit** every segment ending at/before `horizon`; append its text; advance `committed_sample` by `last_committed_end × SAMPLE_RATE`.
4. Segments past `horizon` stay uncommitted — re-transcribed next tick from the new `committed_sample`, so they get more trailing context and a clean silence boundary.
5. **Cap:** if `tail` exceeds `LIVE_MAX_TAIL_SEC` with nothing committable (continuous speech, no silence), force-commit up to `horizon` to bound cost.

Each tick only re-transcribes the small **uncommitted tail** (normally < ~30–50 s), so **CPU per tick is bounded regardless of meeting length** — a 2-hour meeting costs the same per tick as a 10-minute one.

**`finalize()` at Stop:** one pass over `committed_sample → end` with guard = 0 (commit everything, including the final partial), append, return the joined transcript.

**Parity:** because we commit on Whisper's own VAD segment boundaries and re-transcribe the uncommitted tail with context each tick, the committed text is quality-equivalent to a whole-file pass (not byte-identical — Whisper's context differs slightly across windows).

**Small refactor:** add `Transcriber.transcribe_segments(source)` yielding `(start, end, text)`; today's `transcribe()` becomes a thin wrapper that joins them — so the Stop/fallback path and `on_progress` behavior are unchanged.

**Deliberate non-change:** keep *all* frames in memory (as today) so the Stop WAV and the full-file fallback stay intact. Fine on 64 GB.

---

## 5. Error handling, fallback & edge cases

The live path is a pure optimization over today's flow; anything that goes wrong falls back to current behavior.

- **A tick throws** (transient temp-file / model hiccup): caught per-tick, logged, `committed_sample` unchanged, next tick retries the same tail. One bad tick can't corrupt state.
- **`finalize()` fallback:** if the worker produced nothing (live disabled, model load failure, immediate crash) — `committed_sample == 0` and no committed text — `finalize()` transcribes the **whole WAV** via the existing path. Result and behavior are then exactly today's.
- **Model loading:** preload the Whisper model when recording **starts** (in the worker), so the first tick is ready and the Stop path never stalls on a cold model.
- **No speech / very short meeting:** committed text empty + empty tail → same "No speech detected" message as today. A 10-second meeting never commits; `finalize()` transcribes the whole short tail — today's path.
- **Slow machine can't keep up** (relevant to Laurelle's variant): `committed_sample` lags further behind; `finalize()` then has a larger tail. Degrades **smoothly** toward today's wait, never breaks — and the §6 checkbox turns it off.
- **Concurrency — the one rule:** a single Whisper model instance means two transcriptions must never run at once. At Stop we **signal the worker, join it, *then* call `finalize()`** — single owner, no race, `committed_sample` stable.
- **Cleanup:** each tick's temp WAV is deleted in a `finally`.
- **Crash mid-meeting:** loses in-memory audio exactly as today (the WAV only exists post-Stop) — no regression.

---

## 6. Config surface & "Live transcription" checkbox

**Menu item:** a checkable **"Live transcription"** item in [app.py](../../meetingscribe/app.py), in the settings cluster:

```
MeetingScribe vX
─────────────
Start Recording
─────────────
✓ Live transcription      ← new, checkable
Set API Key…
Check for Updates…
─────────────
Quit
```

Clicking toggles `sender.state` (rumps renders the ✓) and persists the new value. Callback: `toggle_live_transcription`.

**Persistence — new tiny [settings.py](../../meetingscribe/settings.py):** reads/writes `~/.meetingscribe/settings.json` (parallel to how [secrets.py](../../meetingscribe/secrets.py) owns the Keychain key). Exposes `live_transcription_enabled()` / `set_live_transcription(bool)`. JSON-file backing keeps this layer free of AppKit, so it's trivially unit-testable.

**Config constants** in [config.py](../../meetingscribe/config.py): `LIVE_TRANSCRIPTION = True` (default when nothing stored yet), plus `LIVE_CADENCE_SEC = 25`, `LIVE_GUARD_SEC = 3`, `LIVE_MAX_TAIL_SEC = 90`.

**Default & precedence:** first launch defaults from `config.LIVE_TRANSCRIPTION`. An optional `MS_LIVE_TRANSCRIPTION` env var can force the value for dev/builds, but the checkbox is the primary control and reflects the persisted value. The checkbox's initial state is set from `live_transcription_enabled()` at startup.

**When it takes effect:** the setting is read **once at recording start**, so toggling mid-meeting never starts/stops a worker on a live session — it applies to the **next** recording. The checkbox still updates immediately.

**Unchecked behavior:** worker never starts; Stop transcribes the whole WAV — today's exact path.

---

## 7. Testing strategy

**Design-for-test:** the worker thread is a thin timer that calls a pure `LiveTranscriber.process_tick(tail)`. Tests call `process_tick` / `finalize` **synchronously** (no threads, no sleeps) and inject a **fake transcriber** returning scripted `(start, end, text)` segments — so the commit logic is tested without Whisper.

- **Unit — commit logic** (`tests/test_live_transcriber.py`):
  - Segments ending before `horizon = tail_len − guard` commit; later ones held.
  - `committed_sample` advances by exactly the last committed segment's end.
  - Next tick re-presenting the held tail + new audio produces **no duplicate** text.
  - Max-tail cap force-commits when no silence boundary appears.
  - `finalize()` flushes the remaining tail with guard = 0.
  - **Fallback:** nothing committed → `finalize()` transcribes the whole WAV (assert the fake got the full-file request).
- **Unit — recorder snapshot** (`tests/test_recorder_snapshot.py`): push synthetic mic/sys frames; assert `snapshot_mono(start)` returns `(mic+sys)/2`, handles mic-only, clips to the shorter stream.
- **Unit — settings** (`tests/test_settings.py`): point `DATA_DIR` at `tmp_path`; default-True when absent, set/get round-trips, `MS_LIVE_TRANSCRIPTION` override honored.
- **Parity / regression** (`tests/test_live_parity.py`, `@pytest.mark.slow`): run today's whole-file `transcribe()` vs. the live pipeline fed the **same** audio (`spikes/sck_capture.wav`) in ~25 s slices; assert transcripts ≥ ~0.95 similar (token-overlap ratio, not exact match). Opt-in; loads the bundled model. **This is the "same result" guarantee.**

---

## 8. Files touched (for the plan)

| File | Change |
|------|--------|
| [recorder.py](../../meetingscribe/recorder.py) | Add `snapshot_mono(start_sample)` (thread-safe mono mix); no change to capture or `stop()`. |
| [transcriber.py](../../meetingscribe/transcriber.py) | Add `transcribe_segments()` yielding `(start, end, text)`; `transcribe()` becomes a thin wrapper. |
| `live_transcriber.py` *(new)* | `LiveTranscriber`: `process_tick`, `finalize`, commit state, fallback. |
| `settings.py` *(new)* | JSON-backed `live_transcription_enabled()` / `set_live_transcription()`. |
| [config.py](../../meetingscribe/config.py) | `LIVE_TRANSCRIPTION`, `LIVE_CADENCE_SEC`, `LIVE_GUARD_SEC`, `LIVE_MAX_TAIL_SEC`. |
| [app.py](../../meetingscribe/app.py) | Worker thread lifecycle (start on record, join on stop); "Live transcription" checkbox; `_process_recording` calls `finalize()`. |
| `tests/test_live_transcriber.py`, `tests/test_recorder_snapshot.py`, `tests/test_settings.py`, `tests/test_live_parity.py` *(new)* | Per §7. |

---

## 9. Open questions / future work

- **Free committed frames** to cap memory on multi-hour meetings — deferred; would complicate the Stop WAV write. Fine on 64 GB.
- **Periodic WAV flush** to survive a mid-meeting crash — deferred (no regression vs. today).
- **Per-variant default** for Laurelle (default the checkbox off on a slower Mac) — easy follow-up via the existing per-variant build if her machine runs sub-real-time.
