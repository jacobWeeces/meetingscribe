# Design — Speaker Attribution × Live Transcription Integration

**Date:** 2026-06-04
**Status:** Brainstorm complete & validated. Pending implementation plan.
**Author:** Jacob
**Supersedes/extends:** [2026-06-01 dynamic-audio + speaker-attribution design](2026-06-01-dynamic-audio-and-speaker-attribution-design.md) (the attribution architecture is unchanged; this doc covers integrating it with features that landed on `main` afterward).

---

## 1. Context & problem

Speaker attribution is **already fully built** on `feature/audio-speaker-attribution` (16 commits): SCK system-audio capture, two-stream transcription, merge-by-timestamp, an LLM naming pass, Sonnet summarization + fidelity rules, plus a **bonus meeting auto-detector**. It implements the 2026-06-01 design end-to-end and is good quality (clean modules, graceful fallbacks, tests).

The problem is **divergence**. That branch was cut ~June 1, *before* three things since shipped to `main`:

1. **Live (during-meeting) transcription** ([2026-06-03 design](2026-06-03-live-transcription-design.md)) — commits Whisper segments during recording off a **mono mix**, to kill the post-Stop wait.
2. **Sparkle auto-update** (`updater.py`, appcast, `release.sh`).
3. **Keychain API-key storage** (`secrets.py`, `get_api_key`).

A naive merge would revert all three. The branch also **regressed** two things it must not carry back: it hardcodes `USER_PROFILE = "laurelle"` (losing `config._load_profile()`), and it reads the API key from `.env` instead of the Keychain. And it does **post-Stop** two-stream transcription, not the **per-channel live** path we want.

So this is a **reconciliation + integration** effort, not a greenfield build.

---

## 2. Decisions (validated during brainstorm, 2026-06-04)

1. **Scope:** the full 2026-06-01 design (SCK capture + two-stream + naming + Sonnet/fidelity), nothing dropped.
2. **Live × attribution:** **per-channel live** — run one `LiveTranscriber` per side so during-meeting work is preserved *and* side-labeled; Stop stays fast. (Alternatives rejected: post-Stop-only reintroduces the wait; energy-based labeling is the diarization the design rejected.)
3. **Integration strategy:** **fresh integration off `main`** in a new worktree. Keep main's spine; transplant the branch's self-contained modules as-is; rebuild the seam. (Rebase/merge rejected: hard conflicts in recorder/transcriber/app + faithfully re-introduce the key/profile regressions.)
4. **Auto-detect meetings:** **included** in this integration (transplant `meeting_detector.py` + menu toggle + confirm popup).
5. **Signing / TCC persistence:** a **tracked dependency** of the Sparkle work, **not a blocker** here — the feature degrades to mic-only without Screen Recording, and graceful degradation is already built.

---

## 3. Goals / non-goals

**Goals**
- Land the built attribution feature on `main` without losing live transcription, Sparkle, or Keychain.
- Preserve the live-transcription turnaround win **and** produce an attributed transcript — via per-channel live.
- Unify key handling on the Keychain and restore dynamic profile resolution.
- Keep "always ship a transcript; degrade gracefully" intact across every path.

**Non-goals (YAGNI)**
- True acoustic diarization (unchanged rejection).
- Splitting multiple in-room mic speakers by voice.
- Live captions / live summarization (live remains a turnaround optimization, never displayed).
- New summary-prompt work beyond the branch's existing fidelity rules.

---

## 4. Source-of-truth reuse map

**Transplant from the branch as-is (self-contained, conflict-free):**
- `system_audio.py` — SCK capture (delegate, run-loop pumping, planar PCM extraction).
- `audio_format.py` — `planar_float32_to_mono`, `resample_to_16k`.
- `segments.py` — `merge_segments`, `apply_speaker_map`, `format_transcript`.
- `speakers.py` — `name_speakers` (but switch its key source to the Keychain — see §6).
- `meeting_detector.py` + `spikes/mic_in_use_spike.py` and their tests.
- Prompt fidelity rules + Sonnet model constant.
- Tests: `test_audio_format`, `test_segments`, `test_speakers`, `test_transcriber`, `test_recorder`, `test_prompts`, `test_meeting_detector`, `test_smoke`.

**Keep main's spine (do not regress):**
- `live_transcriber.py`, `settings.py` (live toggle), `secrets.py` (Keychain), `updater.py` (Sparkle), `config._load_profile()`, the API-key prompt UX, appcast/`release.sh`.

**Rebuild the seam on main (where both diverged):** `recorder.py`, `transcriber.py`, `app.py`, `config.py`, `MeetingScribe.spec`, `requirements.txt`.

---

## 5. Architecture & data flow

The key insight: **the branch already built everything from `segments` onward.** Per-channel live only swaps the *segment-production front-end*. Both paths converge:

```
LIVE ON   → two LiveTranscribers (local 44.1k, remote 48k), one Whisper model,
            ticked sequentially each cadence; each commits {start,end,text,side}
LIVE OFF  → transcribe_streams() post-Stop   ← the branch's existing path = the fallback
   /short        │
   /error        ▼   both yield the same side-tagged segment list
        merge_segments → name_speakers → format_transcript → summarize → Notes
                       (all reused unchanged from the branch)
```

**Recorder** keeps both raw streams in memory (mic frames; SCK planar chunks) and exposes **thread-safe per-side live snapshots** from a committed frame offset — the local side mirrors main's existing `snapshot_mono` pattern; the remote side joins the delegate's accumulated chunks-so-far and converts via `planar_float32_to_mono`, slicing from the committed remote-frame offset.

**Synchronization** uses the branch's shared `t0`: each `LiveTranscriber`'s committed segments carry absolute times (`committed_offset/side_rate + segment_relative_time`), so `merge_segments` orders both sides on one timeline (start skew < ~200 ms, tolerated for ordering).

---

## 6. Components & changes

**`recorder.py` (rebuild):** branch's two-stream `start()`/`stop()` (mic + `SystemAudioRecorder`, returns `{local, local_rate, remote, remote_rate, t0, system_available}`), **plus** `snapshot_side(side, start_frame)` for the two live workers.

**`live_transcriber.py` (extend main's):** make it **side-aware** — retain committed segments as `{start,end,text,side}` (absolute times) instead of flat strings, tagged with the instance's `side`. Two instances. One shared Whisper model, **never concurrent**: the single live worker ticks local then remote sequentially; at Stop, signal → join → `finalize()` both. Cost: ~2× live CPU per tick (acceptable on M4 Pro; the existing checkbox + per-variant default cover Laurelle's slower Mac).

**`transcribe_streams()` (transplant):** becomes the **live-off / fallback** producer of the merged segment list — symmetric with the live path.

**`segments.py` / `speakers.py` (transplant):** unchanged, except `speakers.py` reads the key via `secrets.get_api_key()` (Keychain), not `config.ANTHROPIC_API_KEY`.

**`app.py` (reconcile — the hard part):** one menu carrying **both** main's items (Live transcription, Set API Key…, Check for Updates…, version) **and** the branch's (Auto-detect meetings). `_process_recording` becomes: join live worker → if live ran, merge committed segments; else `transcribe_streams` → `name_speakers` → `format_transcript` → summarize → save. Keep main's API-key prompt, Sparkle init, foreground/alert helpers; keep the branch's auto-detect wiring + forced-foreground confirm popup + SCK permission alert.

**`config.py` (unify):** Sonnet model + restore `_load_profile()` + keep the `LIVE_*` constants.

---

## 7. Edge cases & error handling

Guiding rule unchanged: **always ship a transcript; degrade gracefully.**
- **Live on, succeeds** → merged committed segments → name → format.
- **Live off / too short / a tick or finalize errors** → `transcribe_streams` post-Stop (the proven fallback) → same downstream. Never worse than the branch.
- **Screen Recording denied / SCK unavailable** → no remote stream; mic-only, all segments `local` → profile name; one-time alert.
- **Naming/API failure** → side labels (`<profile>` / `Remote speaker`), per `speakers.py`.
- **One side silent** → that side yields no segments; merge handles it.
- **Single-model concurrency** → one live worker, sequential per-side ticks, join-before-finalize.

---

## 8. Testing strategy (TDD)

- **Reuse** the branch's pure-logic tests (audio_format, segments merge/format, speakers map+fallback, transcriber, prompts, meeting_detector) and main's (live_transcriber, settings, secrets, parity).
- **New:** side-aware `LiveTranscriber` commit (two instances, absolute times, no duplicates across ticks); **merge-from-live equals merge-from-`transcribe_streams`** given equivalent segments; recorder `snapshot_side` correctness (mic + planar remote, clip/offset).
- **SCK** stays non-unit-tested: covered by `sck_audio_spike.py` + **one manual E2E** (you on AirPods + one remote speaker) checking attribution, sync, and the live-vs-fallback parity.

---

## 9. Risks / dependencies

- **Signing / TCC persistence** — Screen Recording re-prompts under unstable signing across Sparkle updates. Tracked with the Sparkle Developer-ID work (blocked on Apple/GitHub secrets), not gated here.
- **2× live CPU** — per-channel live doubles per-tick cost; fine on M4 Pro, mitigated by the live checkbox + per-variant default on Laurelle's machine.
- **Key/profile unification correctness** — the one place the branch regressed; explicit tests + review to ensure summarizer *and* speakers use the Keychain and the profile resolves dynamically.
- **`app.py` reconciliation** — highest-conflict surface; rebuilt deliberately on main rather than via 3-way merge.
- **Remote live snapshot** — reading the SCK delegate's chunks mid-capture (frame-offset slicing of planar bytes) is the main net-new correctness detail.

---

## 10. Sequencing (shippable increments → for the plan)

1. **Worktree off `main`** + transplant the self-contained modules and their tests (green on transplanted units).
2. **Two-stream recorder + transcriber** on main (`transcribe_streams`, SCK `SystemAudioRecorder`, drop BlackHole); wire the post-Stop attributed path end-to-end (live still mono here) — a working attributed build, fallback-only.
3. **Reconcile `app.py` / `config.py`** with main's spine (Keychain, Sparkle, dynamic profile, menu union, auto-detect).
4. **Per-channel live** — `snapshot_side`, side-aware `LiveTranscriber` ×2, worker ticks both, merge-from-live.
5. **Packaging** — `MeetingScribe.spec` hiddenimports (ScreenCaptureKit/CoreMedia/Quartz/AVFoundation), `requirements` (+ScreenCaptureKit, keep Security), signing note.
6. **Manual E2E** + parity check.

Steps 1–2 unlock a working attributed build; 4 adds the live win.
