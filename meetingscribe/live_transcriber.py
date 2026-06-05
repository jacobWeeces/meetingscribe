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
        self._committed_segments = []  # list[dict] with absolute times + side
        # Absolute sample index transcribed so far. Written only by process_tick/
        # finalize; read by the Stop thread after the worker is joined (Task 6).
        self.committed_sample = 0
        self._ever_committed = False

    @property
    def ever_committed(self) -> bool:
        return self._ever_committed

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

    def finalize(self, tail) -> None:
        """Commit any remaining tail with no guard (end of meeting).

        Unlike process_tick (which swallows a per-tick error and retries next tick), this
        lets a transcription error PROPAGATE so the caller (resolve_segments) can fall back
        to transcribe_streams — keeping the result never worse than today.

        `tail` must be the audio starting at `committed_sample`
        (`AudioRecorder.snapshot_mono(self.committed_sample)`), or None/empty if there is
        nothing left.
        """
        if tail is not None and len(tail) > 0:
            base_s = self.committed_sample / self._sr
            # Materialize before appending (like process_tick) so a mid-iteration error
            # raises before we touch _committed_segments — finalize stays all-or-nothing.
            for start, end, text in list(self._transcriber.transcribe_segments(tail, self._sr)):
                cleaned = text.strip()
                if cleaned:
                    self._committed_segments.append({
                        "start": base_s + start,
                        "end": base_s + end,
                        "text": cleaned,
                        "side": self._side,
                    })
                    self._ever_committed = True


def resolve_segments(transcriber, live_local, live_remote, final_local_tail,
                     final_remote_tail, stream_result, on_progress=None):
    """Return one merged side-tagged segment list. Prefer the per-channel live
    commits (finalize both, merge); else (or on finalize error) the post-Stop
    transcribe_streams fallback — never worse than the non-live path.

    The returned list is the only authoritative result: on a mid-finalize error
    one LiveTranscriber may be left partially finalized, so callers must not read
    live_local/live_remote.committed_segments() after this returns.
    """
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
