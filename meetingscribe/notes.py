import subprocess
import html


def _escape_applescript(s: str) -> str:
    """Escape a string for embedding inside an AppleScript double-quoted literal.

    AppleScript treats backslash as an escape character inside string literals, so
    a backslash in the content (file paths like C:\\Users, code, math) — which
    html.escape does NOT touch — would otherwise corrupt the script and make
    osascript fail, silently losing the whole note. Backslashes must be doubled
    BEFORE quotes are escaped.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _note_body_html(body: str) -> str:
    """Render the note body as HTML (Notes stores bodies as HTML), one <br> per line."""
    return "<br>".join(html.escape(line) for line in body.split("\n"))


def _build_note_script(title: str, body: str) -> str:
    # html.escape makes the text valid HTML for the Notes body and neutralizes
    # quotes; _escape_applescript then makes it safe inside the AppleScript string
    # literal (handles backslashes html.escape leaves untouched).
    safe_title = _escape_applescript(html.escape(title))
    safe_body = _escape_applescript(_note_body_html(body))
    return (
        'tell application "Notes"\n'
        '  tell account "iCloud"\n'
        f'    make new note at folder "Notes" with properties '
        f'{{name:"{safe_title}", body:"{safe_body}"}}\n'
        '  end tell\n'
        'end tell'
    )


def save_to_notes(title: str, body: str) -> bool:
    try:
        result = subprocess.run(
            ["osascript", "-e", _build_note_script(title, body)],
            capture_output=True,
            text=True,
        )
    except Exception:
        # osascript missing / not launchable — report failure rather than crashing
        # the (already last-chance) save path.
        return False
    return result.returncode == 0
