# Design — Dynamic Audio Capture + Speaker Attribution + Notes Accuracy

**Date:** 2026-06-01
**Status:** Brainstorm complete & validated; ScreenCaptureKit feasibility spiked (GREEN). Pending implementation plan.
**Author:** Jacob

---

## 1. Context & problem

MeetingScribe is a macOS menubar app (`rumps`) that records a meeting, transcribes it locally with `faster-whisper` (bundled large-v3 int8), summarizes with the Anthropic API, and saves notes + raw transcript to Apple Notes. Today:

- **Audio capture** ([recorder.py](../../meetingscribe/recorder.py)) records the **mic** (default input) and **system audio via BlackHole**, mixes them into a stereo WAV. System audio requires the user to hand-build a **Multi-Output Device in Audio MIDI Setup** (BlackHole + speakers/earbuds).
- **Transcription** ([transcriber.py](../../meetingscribe/transcriber.py)) hands the stereo WAV to Whisper, which **downmixes to mono** — all speaker separation is lost. Output is a flat, unattributed transcript.
- **Summarization** ([summarizer.py](../../meetingscribe/summarizer.py), [prompts.py](../../meetingscribe/prompts.py)) runs on **Haiku**, which has produced accuracy issues (see §6).

This design solves three problems that turn out to be **one recorder refactor**:

1. **Speaker attribution** — label the transcript with who said what.
2. **Dynamic audio** — eliminate BlackHole + Audio MIDI Setup so capture works regardless of earbuds (incl. AirPods) vs. built-in, with no manual routing.
3. **Notes accuracy** — fix the embellishment / contradiction / dropped-specific errors.

### Why combined

Speaker attribution wants two separate channels (local vs. remote). ScreenCaptureKit (SCK) gives us the remote channel natively, and the mic gives us the local channel. So adopting SCK **is** the channel split. Doing them together avoids building the same plumbing twice.

---

## 2. Goals / non-goals

**Goals**
- Capture system audio with **no BlackHole and no Audio MIDI Setup**, indifferent to the output device.
- Produce a transcript attributed at minimum as **local (you) vs. remote**, refined to **real names** where context allows.
- Materially reduce summarization errors (figures quoted exactly, contradictions reconciled, specifics retained).
- Preserve transcript **fidelity**: the LLM never rewrites transcript text.

**Non-goals (YAGNI)**
- True acoustic diarization (pyannote/WhisperX) — rejected: heavy deps, big bundle, slow CPU, and unnecessary given channel separation.
- Splitting multiple in-room speakers on the *mic* channel by voice.
- A second LLM "verification pass" over the notes (revisit only if §6 proves insufficient).
- Capturing the mic *through* SCK (macOS 15+ supports it) — keep `sounddevice` for the mic.

---

## 3. Architecture & data flow

```
RECORD (refactored)
  ├─ mic     : sounddevice, default input  → local mono stream  (44.1 kHz)
  └─ system  : ScreenCaptureKit            → remote mono stream (48 kHz, downmixed from SCK stereo)
        │  (both started at a shared t0; no BlackHole, no Multi-Output Device)
        ▼
TRANSCRIBE (refactored)
  ├─ resample each stream to 16 kHz, run Whisper separately
  ├─ per segment: {start, end, text, side}        side ∈ {local, remote}
  └─ merge both lists, sorted by start (common t0 timeline)
        ▼
NAME SPEAKERS (new)  speakers.py
  ├─ send side-labeled segments to Sonnet
  ├─ get back JSON {segment_id: name}; text stays verbatim, applied locally
  └─ local→profile name; remote→Priscilla/Matt/… or "Remote speaker"
        ▼
SUMMARIZE (refactored)  Sonnet + hardened prompts
        ▼
SAVE TO NOTES   notes (attributed transcript) + summary
```

---

## 4. Component 1 — Recorder refactor (`recorder.py`)

**Local (mic): unchanged in spirit.** Keep `sounddevice` on the default input — already dynamic (each recording uses whatever input is selected; no Audio MIDI Setup). AirPods caveat: using AirPods as *input* forces HFP/telephone quality. Mitigation (separate small feature): detect a Bluetooth-HFP input and recommend/prefer the built-in mic while listening on AirPods.

**Remote (system): ScreenCaptureKit.** Replace BlackHole. At record start:
- `SCShareableContent.getShareableContentWithCompletionHandler_` (triggers the Screen Recording permission grant the first time).
- `SCContentFilter` on the main display; `SCStreamConfiguration` with `capturesAudio=YES`, `excludesCurrentProcessAudio=YES`, `sampleRate=48000`, `channelCount=2`, minimal `width/height`.
- `SCStream` + an audio `SCStreamOutput`; accumulate PCM in the delegate callback.

**Spike evidence (2026-06-01, `spikes/sck_audio_spike.py`):** 302 audio callbacks in 6 s, 2.3 MB PCM, format **48 kHz / 2-ch / Float32 non-interleaved**, peak 0.74 (non-silent capture confirmed). PCM extraction that works in PyObjC 12.2:
- Format: `CMAudioFormatDescriptionGetStreamBasicDescription` returns a **tuple** (index `[0]=rate, [5]=bytesPerFrame, [6]=channels, [7]=bits, [2]=flags`).
- Data: `CMSampleBufferGetDataBuffer` → `CMBlockBufferGetDataLength` → **`CMBlockBufferCopyDataBytes(bb, 0, total, bytearray(total))`** (the `GetDataPointer` route returns an unusable tuple).
- Pure-Python helper methods on the ObjC class must be marked `@objc.python_method`.

**Two streams, not a stereo file.** SCK already gives us a separate remote stream, so we **skip merging into a stereo WAV**. The recorder returns two mono buffers (or two temp WAVs): `local` (44.1 kHz) and `remote` (48 kHz, SCK stereo downmixed to mono). This removes the channel-truncation/mixing logic entirely.

**Synchronization.** Both captures start in `_start_recording`; we record a shared `t0`. Whisper segment timestamps are relative to each stream's start, so with a common `t0` they're directly comparable for ordering. Expected start skew < ~200 ms — fine for "who spoke when." If we ever need tighter sync, SCK sample buffers carry presentation timestamps (`CMSampleBufferGetPresentationTimeStamp`) and `sounddevice` callbacks carry ADC time; we can align on those. **v1 uses simple t0-offset alignment.**

**Threading.** SCK delivers buffers on its own queue; the app already runs an `NSApplication`/`rumps` run loop, so callbacks fire correctly. Capture start/stop wires into the existing `_start_recording`/`_stop_recording`.

---

## 5. Component 2 — Two-stream transcription & merge (`transcriber.py`)

- Input: the two mono streams. For each: convert Float32 → mono, **resample to 16 kHz** (`scipy.signal.resample_poly`; mic 44100→16000 = `(160, 441)`, system 48000→16000 = `(1, 3)`), run `self._model.transcribe(arr, beam_size=5, vad_filter=True)`.
- Collect each segment as `{"start", "end", "text", "side"}`, `side ∈ {"local","remote"}`.
- Merge both lists, **sorted by `start`** → one time-ordered, side-attributed segment list.
- **Return type changes** `str` → `list[dict]`; [app.py](../../meetingscribe/app.py) updates to consume segments and build the raw-transcript text from them.
- **Progress** spans two passes, weighted by each stream's duration.

---

## 6. Component 3 — Speaker naming pass (new, `speakers.py`)

Turns `local`/`remote` into names **without touching transcript text**.

- **Fidelity:** the LLM receives numbered, side-labeled segments and returns **only** a JSON label map `{segment_id: name}`. We apply names to the verbatim Whisper segments locally. The model can mislabel *who*, never *what*. Output is tiny → cheap regardless of length.
- **Local prior:** local segments default to the profile name ([config.py](../../meetingscribe/config.py) `USER_PROFILE` → `Jacob`/`Laurelle`); name another in-room person only if clearly indicated.
- **Remote naming:** from self-introductions, direct address, turn-taking. Falls back to `Remote speaker` when unsure — **no invented names**.
- **Roster + long meetings:** establish a roster first, then assign per segment; chunk the segment list for long transcripts, carrying the roster forward for consistency.
- **Model:** Sonnet.

---

## 7. Component 4 — Summarization accuracy fixes (`summarizer.py`, `prompts.py`)

**Better input + model:** summarizer consumes the **named, attributed** transcript and runs on **Sonnet** (was Haiku, [config.py](../../meetingscribe/config.py) `ANTHROPIC_MODEL`). Attribution + a stronger model removes most errors at the root.

**Prompt hardening** — rules added to every profile prompt (`system`/`chunk`/`merge`), each mapped to a real error found in review (validation cases):

| Reported error | Rule |
|---|---|
| "$26 **statutory maximum**" (invention) | "Quote dollar amounts, dates, and figures exactly as spoken. Do not add legal/technical characterizations (e.g. 'statutory maximum') unless the speaker used that term." |
| **weekly/monthly** (unreconciled contradiction) | "When a decision is revised during the meeting, record only the final decision; never carry both an old and superseded value." |
| missing **"three people"** (dropped specific) | "Capture concrete specifics: counts, named people, recipients. 'Send it to those three people' → note three recipients." |

---

## 8. Output format (Apple Notes)

Notes section unchanged (now more accurate). RAW TRANSCRIPT becomes attributed, consecutive same-speaker turns grouped, light timestamps:

```
[0:12] Jacob: All I want are all the records from date of birth to present.
[0:18] Priscilla: So that's part of the pieces of information we pull...
[2:47] Matt: For workers' comp, they're supposed to charge us only 26 bucks...
```

---

## 9. Edge cases & error handling

Guiding rule: **always ship a transcript; degrade gracefully.**

- **Screen Recording permission denied / not yet granted** → no remote stream; record mic-only and label all `You`, with a one-time alert explaining how to grant it. Never blocks.
- **macOS < 13 / SCK unavailable** → fall back to mic-only (or legacy BlackHole path if present).
- **Naming/API failure** → keep side labels (`You` / `Remote speaker`).
- **One side silent / empty** → that stream yields no segments; fine.
- **Stream start skew** → tolerated for ordering; PTS alignment available if needed.
- **Long meetings** → chunked naming + chunked summary (existing 80k threshold).

---

## 10. Permissions, signing, packaging

- **TCC:** SCK needs Screen Recording authorization (one-time grant). **Ad-hoc signing re-prompts after each rebuild** because the signature identity changes — strong argument for a **stable (self-signed or Developer ID) signing identity**, which also fixes the recurring `xattr`/codesign failure seen during the Laurelle build.
- **PyInstaller:** add `hiddenimports` for `ScreenCaptureKit`, `CoreMedia`, `Quartz`, `AVFoundation` (pyobjc 12.2 now installed); drop BlackHole-specific assumptions. Bundle grows modestly.
- **Dependencies:** add `pyobjc-framework-ScreenCaptureKit` to `requirements.txt`/`setup.py`.

---

## 11. Testing strategy (TDD — repo currently has no tests; add `tests/`)

- **Pure logic, failing-tests-first:** resample correctness; **merge-by-timestamp ordering** across two segment lists; label-application (apply JSON name-map to segments, incl. fallback) with the LLM mocked.
- **Summarizer:** prompt assembly + chunking with `anthropic` mocked; assert the three §7 validation cases.
- **SCK capture:** not unit-testable headless; covered by the spike + one manual end-to-end recording (you on AirPods + one remote person) to eyeball attribution and sync.

---

## 12. Risks / open questions

- **Stream sync precision** — t0-offset assumed adequate; validate on a real recording, escalate to PTS if turns feel misaligned.
- **SCK non-interleaved de-interleave** — must split planar L/R correctly (spike's WAV doubled because it dumped planar as mono). Routine, but the first thing to get right.
- **Permission persistence under ad-hoc signing** — see §10; decide on signing identity.
- **In-room multi-speaker on mic** — out of scope; remains a single "local" label.

---

## 13. Sequencing (for the implementation plan)

1. **Recorder:** SCK system-audio capture (port the spike to a clean class), keep mic via sounddevice, return two mono streams + t0. Remove BlackHole.
2. **Transcriber:** two-stream transcribe + merge-by-timestamp; change return type; update `app.py`.
3. **Speakers:** naming pass + verbatim label application.
4. **Summarizer/prompts:** Sonnet + §7 rules.
5. **Output:** attributed transcript formatting.
6. **Packaging:** spec hiddenimports, requirements, signing decision.
7. **Tests** alongside each step.

Steps 1–2 are the critical path and unlock everything else.
```
