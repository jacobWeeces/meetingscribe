REMOTE_FALLBACK = "Remote speaker"


def merge_segments(local: list[dict], remote: list[dict]) -> list[dict]:
    """Merge two side-tagged segment lists into one, ordered by start time.

    Assumes both streams started at the same t0, so `start` values share a
    timeline. Assigns a stable 1-based `id` to each merged segment.
    """
    merged = sorted([*local, *remote], key=lambda s: (s["start"], s["end"]))
    for i, s in enumerate(merged, start=1):
        s["id"] = i
    return merged


def apply_speaker_map(segments: list[dict], name_map: dict, local_name: str) -> list[dict]:
    """Attach a `speaker` to each segment. Verbatim text is never modified.

    The map is keyed by merged segment `id` (uniform for all segments):
      - if a segment's id is in name_map -> use that name (lets the naming pass
        label an in-room local speaker as well as remote speakers);
      - else local segments -> local_name;
      - else remote segments -> REMOTE_FALLBACK.
    """
    out = []
    for s in segments:
        mapped = name_map.get(s["id"]) or name_map.get(str(s["id"]))
        if mapped:
            speaker = mapped
        elif s["side"] == "local":
            speaker = local_name
        else:
            speaker = REMOTE_FALLBACK
        out.append({**s, "speaker": speaker})
    return out


def _mmss(seconds: float) -> str:
    m, sec = divmod(int(seconds), 60)
    return f"{m}:{sec:02d}"


def format_transcript(named: list[dict]) -> str:
    """Render named segments, grouping consecutive same-speaker turns."""
    lines, group = [], []

    def flush():
        if group:
            text = " ".join(s["text"].strip() for s in group)
            lines.append(f"[{_mmss(group[0]['start'])}] {group[0]['speaker']}: {text}")

    for s in named:
        if group and s["speaker"] != group[0]["speaker"]:
            flush()
            group = []
        group.append(s)
    flush()
    return "\n".join(lines)
