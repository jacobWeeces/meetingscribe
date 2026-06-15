"""LLM speaker-naming pass.

Assigns real names to transcript segments by producing a JSON label map
{segment_id: name} and applying it via apply_speaker_map. Verbatim transcript
text is never modified — only a `speaker` key is attached to each segment.

NOTE: This module makes a single LLM call for the whole transcript (YAGNI).
For very long recordings, a future improvement would chunk the segments into
windows of ~100 lines, carry the established name roster forward into each
subsequent prompt so names stay consistent across chunks.
"""

import json
import logging

import anthropic

from meetingscribe.config import ANTHROPIC_MODEL
from meetingscribe.secrets import get_api_key
from meetingscribe.segments import apply_speaker_map

log = logging.getLogger("meetingscribe")


def _build_prompt(segments: list[dict], local_name: str) -> str:
    """Build the LLM prompt listing each segment as: {id} | {side} | {text}."""
    lines = [
        f"{s['id']} | {s['side']} | {s['text'].strip()}"
        for s in segments
    ]
    segment_block = "\n".join(lines)

    return (
        "You are labeling who spoke each line of a meeting transcript. "
        "Return ONLY a JSON object mapping segment id (number) to speaker name.\n"
        f"The 'local' channel is {local_name} unless a line is clearly a different "
        "in-room person. "
        "Name 'remote' speakers from context (self-introductions, direct address, "
        "turn-taking). "
        "Use 'Remote speaker' when unsure. "
        "Do NOT invent names. "
        "Do NOT include segments you can't confidently name.\n\n"
        f"{segment_block}"
    )


def _call_llm(prompt: str) -> str:
    """Send the prompt to the Anthropic API and return the raw text response.

    Defined as a module-level function so tests can monkeypatch it.
    """
    client = anthropic.Anthropic(api_key=get_api_key())
    message = client.messages.create(
        model=ANTHROPIC_MODEL,
        # The id->name map for a long meeting (hundreds of segments) can exceed 1024
        # tokens; truncation makes the JSON unparseable and drops ALL names.
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def _parse_name_map(text: str) -> dict[int, str]:
    """Extract and parse the JSON object from LLM response text.

    Tolerates surrounding prose by slicing from the first '{' to the last '}'.
    Coerces string keys to int; drops entries that can't be coerced or are empty.
    Returns {} on any parse failure.
    """
    try:
        start = text.index("{")
        # Decode the first complete JSON object from the first '{'. Using rindex to
        # the LAST '}' breaks when the model appends any prose containing a brace
        # (it would swallow the trailing text and fail to parse, dropping ALL names).
        raw, _ = json.JSONDecoder().raw_decode(text[start:])
        if not isinstance(raw, dict):
            return {}
        result = {}
        for k, v in raw.items():
            if not v:
                continue
            try:
                result[int(k)] = str(v)
            except (ValueError, TypeError):
                pass
        return result
    except (ValueError, json.JSONDecodeError, AttributeError):
        return {}


def name_speakers(segments: list[dict], local_name: str) -> list[dict]:
    """Attach speaker names to each segment using a single LLM call.

    Returns the segment list with a `speaker` key on every entry. On any error
    (LLM failure, bad JSON, empty response) falls back gracefully to side labels:
    local segments → local_name, remote segments → 'Remote speaker'.
    """
    try:
        prompt = _build_prompt(segments, local_name)
        raw = _call_llm(prompt)
        if not raw:
            log.warning("speakers: empty LLM response, falling back to side labels")
            return apply_speaker_map(segments, {}, local_name)
        name_map = _parse_name_map(raw)
        log.info("speakers: resolved %d name(s) from LLM", len(name_map))
        return apply_speaker_map(segments, name_map, local_name)
    except Exception as exc:  # noqa: BLE001
        log.warning("speakers: LLM naming failed (%s), falling back to side labels", exc)
        return apply_speaker_map(segments, {}, local_name)
