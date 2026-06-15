"""Apple Notes save must survive backslashes and quotes in the transcript/summary.

Regression: the note body was only `html.escape`d, which neutralizes quotes but
NOT backslashes. A backslash (file paths like C:\\Users\\bob, code, math) made the
generated AppleScript a syntax error -> osascript returned non-zero ->
save_to_notes() returned False -> the ENTIRE meeting note (transcript + summary)
was silently lost with only a generic "couldn't save" warning.
"""

import subprocess

import pytest


def _osascript_parses(literal_value: str) -> bool:
    """True if `set x to "<literal_value>"` is a valid AppleScript (parses + runs)."""
    script = f'set x to "{literal_value}"\nreturn x'
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.returncode == 0


def test_escape_applescript_escapes_backslash_then_quote():
    from meetingscribe.notes import _escape_applescript
    assert _escape_applescript(r"a\b") == r"a\\b"
    assert _escape_applescript('a"b') == 'a\\"b'
    # backslash must be doubled BEFORE quotes are escaped (order matters)
    assert _escape_applescript('\\"') == '\\\\\\"'


@pytest.mark.parametrize("text", [
    "plain text",
    'she said "hi"',
    r"path is C:\Users\bob",          # the original failing case
    r"trailing backslash\\",
    r'mix of \ and " and \\ together',
    "ünïcödé and emoji 🎙",
])
def test_note_body_literal_is_valid_applescript(text):
    from meetingscribe import notes
    escaped = notes._escape_applescript(notes._note_body_html(text))
    assert _osascript_parses(escaped), f"AppleScript corrupted by: {text!r}"


def test_save_to_notes_escapes_backslashes_in_script(monkeypatch):
    from meetingscribe import notes
    captured = {}

    class _R:
        returncode = 0

    def fake_run(cmd, **kwargs):
        captured["script"] = cmd[2]
        return _R()

    monkeypatch.setattr(notes.subprocess, "run", fake_run)
    ok = notes.save_to_notes("Meeting — 2026-06-15", r'see C:\Users\bob and "quotes"')

    assert ok is True
    # The backslashes are doubled for AppleScript; no lone backslash can break the literal.
    assert r"C:\\Users\\bob" in captured["script"]
    # html.escape already turned the raw quotes into &quot; so none remain to break out
    assert '"quotes"' not in captured["script"].replace('body:"', "").replace('name:"', "")


def test_save_to_notes_returns_false_on_osascript_failure(monkeypatch):
    from meetingscribe import notes

    class _R:
        returncode = 1

    monkeypatch.setattr(notes.subprocess, "run", lambda *a, **k: _R())
    assert notes.save_to_notes("t", "b") is False
