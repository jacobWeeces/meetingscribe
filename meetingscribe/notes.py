import subprocess
import html


def save_to_notes(title: str, body: str) -> bool:
    safe_title = html.escape(title).replace('"', '&quot;')
    body_html = "<br>".join(html.escape(line) for line in body.split("\n"))

    script = (
        'tell application "Notes"\n'
        '  tell account "iCloud"\n'
        f'    make new note at folder "Notes" with properties '
        f'{{name:"{safe_title}", body:"{body_html}"}}\n'
        '  end tell\n'
        'end tell'
    )

    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0
