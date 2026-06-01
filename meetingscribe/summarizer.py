import logging

import anthropic

from meetingscribe.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, USER_PROFILE
from meetingscribe.prompts import PROFILES

log = logging.getLogger("meetingscribe")

MAX_CHUNK_CHARS = 80000


def _split_transcript(transcript, max_chars=MAX_CHUNK_CHARS):
    lines = transcript.split("\n")
    chunks = []
    current = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1
        if current_len + line_len > max_chars and current:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len

    if current:
        chunks.append("\n".join(current))

    return chunks


class Summarizer:
    def __init__(self):
        self._client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self._prompts = PROFILES[USER_PROFILE]
        log.info("Summarizer using profile: %s", USER_PROFILE)

    def _call(self, system, user_content):
        message = self._client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
        return message.content[0].text

    def summarize(self, transcript: str) -> str:
        if len(transcript) <= MAX_CHUNK_CHARS:
            log.info("Transcript fits in single chunk (%d chars)", len(transcript))
            return self._call(
                self._prompts["system"],
                f"Here is the meeting transcript:\n\n{transcript}",
            )

        chunks = _split_transcript(transcript)
        log.info("Transcript split into %d chunks (%d chars total)", len(chunks), len(transcript))

        chunk_summaries = []
        for i, chunk in enumerate(chunks):
            log.info("Summarizing chunk %d/%d (%d chars)...", i + 1, len(chunks), len(chunk))
            summary = self._call(
                self._prompts["chunk"],
                f"Here is section {i + 1} of {len(chunks)} of the meeting transcript:\n\n{chunk}",
            )
            chunk_summaries.append(f"--- Section {i + 1} of {len(chunks)} ---\n{summary}")

        merged_input = "\n\n".join(chunk_summaries)
        log.info("Merging %d chunk summaries (%d chars)...", len(chunk_summaries), len(merged_input))

        return self._call(
            self._prompts["merge"],
            f"Here are the extracted notes from each section of the meeting:\n\n{merged_input}",
        )
