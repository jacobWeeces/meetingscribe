import logging

from meetingscribe.config import LIVE_GUARD_SEC, LIVE_MAX_TAIL_SEC
from meetingscribe.segments import merge_segments

log = logging.getLogger("meetingscribe")


class LiveTranscriber:
    """Commits Whisper-delimited segments that ended before a trailing guard, so the
    transcript is built up during recording. See
    docs/plans/2026-06-03-live-transcription-design.md.
    """

    def __init__(self, transcriber, sample_rate, guard_sec=LIVE_GUARD_SEC,
                 max_tail_sec=LIVE_MAX_TAIL_SEC, side=None):
        self._transcriber = transcriber
        self._sr = sample_rate
        self._guard = guard_sec
        self._max_tail = max_tail_sec
        self._side = side
        self._committed = []          # list[str]
        self._committed_segments = []  # list[dict] with absolute times + side
        # Absolute sample index transcribed so far. Written only by process_tick/
        # finalize; read by the Stop thread after the worker is joined (Task 6).
        self.committed_sample = 0
        self._ever_committed = False

    @property
    def ever_committed(self) -> bool:
        return self._ever_committed

    def text(self) -> str:
        return "\n".join(self._committed)

    def committed_segments(self) -> list:
        """Return a copy of the committed segments with absolute timestamps and side tag."""
        return list(self._committed_segments)

    def process_tick(self, tail) -> None:
        """Transcribe the uncommitted tail; commit segments that ended before the guard.

        `tail` MUST be the audio starting exactly at `committed_sample` — i.e.
        `AudioRecorder.snapshot_mono(self.committed_sample)`. Segment timestamps are
        interpreted relative to the start of `tail`, so passing anything else (e.g. the
        full recording) would corrupt `committed_sample`.
        """
        tail_len_s = len(tail) / self._sr
        if tail_len_s < self._guard + 2:
            return
        base_s = self.committed_sample / self._sr
        try:
            segments = list(self._transcriber.transcribe_segments(tail, self._sr))
        except Exception:
            log.exception("live: transcribe_segments failed; will retry next tick")
            return
        if not segments:
            return

        horizon = tail_len_s - self._guard
        force = tail_len_s >= self._max_tail
        last_end = None
        for start, end, text in segments:
            # Normal: commit segments that ended before the guard. Force (max-tail cap):
            # also commit a segment that merely STARTED before the horizon, to bound cost
            # when there is no silence boundary. Segments are time-ordered, so the first
            # non-committable segment means the rest are too -> break.
            committable = end <= horizon or (force and start < horizon)
            if not committable:
                break
            cleaned = text.strip()
            if cleaned:
                self._committed.append(cleaned)
                self._committed_segments.append({
                    "start": base_s + start,
                    "end": base_s + end,
                    "text": cleaned,
                    "side": self._side,
                })
            last_end = end
        if last_end is not None:
            self.committed_sample += int(last_end * self._sr)
            self._ever_committed = True

    def finalize(self, tail) -> str:
        """Commit any remaining tail with no guard (end of meeting); return the full text.

        Unlike process_tick (which swallows a per-tick error and retries next tick), this
        lets a transcription error PROPAGATE so the caller (resolve_transcript) can fall
        back to the whole-file pass — keeping the result never worse than today.

        `tail` must be the audio starting at `committed_sample`
        (`AudioRecorder.snapshot_mono(self.committed_sample)`), or None/empty if there is
        nothing left.
        """
        if tail is not None and len(tail) > 0:
            base_s = self.committed_sample / self._sr
            # Materialize before appending (like process_tick) so a mid-iteration error
            # raises before we touch _committed — finalize stays all-or-nothing.
            for start, end, text in list(self._transcriber.transcribe_segments(tail, self._sr)):
                cleaned = text.strip()
                if cleaned:
                    self._committed.append(cleaned)
                    self._committed_segments.append({
                        "start": base_s + start,
                        "end": base_s + end,
                        "text": cleaned,
                        "side": self._side,
                    })
                    self._ever_committed = True
        return self.text()


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


def resolve_transcript(transcriber, live, final_tail, wav_path, on_progress=None):
    """Decide the final transcript: the live one if it ran and produced text, else
    today's whole-WAV pass (the safety net — never worse than today). If the live
    finalize() fails, fall back to the whole-WAV pass rather than returning a partial."""
    if live is not None and live.ever_committed:
        try:
            transcript = live.finalize(final_tail)
        except Exception:
            log.exception("live: finalize failed; falling back to whole-file transcription")
        else:
            if on_progress:
                on_progress(1.0)  # live path has no incremental progress; jump the bar to done
            return transcript
    return transcriber.transcribe(wav_path, on_progress=on_progress)
