# Sparkle Auto-Update Distribution — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ship Laurelle's `MeetingScribe.app` with Sparkle 2.x auto-updates (GitHub-hosted
appcast, one-command release) and move the Anthropic API key into a first-run macOS Keychain
prompt so no key is ever bundled.

**Architecture:** Sparkle.framework is embedded in the `.app` and driven from Python via
PyObjC (`SPUStandardUpdaterController`). A `release.sh` script builds, signs, notarizes,
EdDSA-signs, generates the appcast, and publishes to GitHub Releases. The API key is read
from the Keychain (device-only access) with the existing env/`.env` reads kept only as a
dev-machine fallback; the bundled `.env` is removed from the `.spec`.

**Tech Stack:** Python 3.14 (Homebrew), PyInstaller, PyObjC (AppKit/Foundation/objc +
**new** `pyobjc-framework-Security`), rumps, Sparkle 2.x, pytest 9, `gh` CLI, `codesign`,
`notarytool`.

**Design reference:** `docs/plans/2026-06-02-sparkle-distribution-design.md`

**Conventions for the executor:**
- Run all commands from the worktree root:
  `/Users/jacobweeces/Documents/personalprojects/meetingtranscribe/.worktrees/sparkle-distribution`
- Python interpreter: `/opt/homebrew/bin/python3` (run tests with `python3 -m pytest`).
- This repo had **no test suite** before this plan — Task 1 creates the `tests/` scaffold.
- Commit after every green step. Never add Claude as a git co-author.

---

## Phase 0 — Prerequisites (one-time, human-in-the-loop)

These need real accounts/secrets and are **not** code. Confirm or perform before Phase 3.

### Task 0a: Confirm signing identity
- Run: `security find-identity -v -p codesigning`
- Expected: a line containing `Developer ID Application: <Your Name> (<TEAMID>)`.
- Record the full identity string; the release script needs it (`MS_SIGN_IDENTITY`).

### Task 0b: Create a notarytool keychain profile
- Run (interactive, with an App Store Connect API key OR app-specific password):
  `xcrun notarytool store-credentials "meetingscribe-notary" --apple-id <you@example.com> --team-id <TEAMID>`
- Expected: "stored credentials" success. The script references the profile by name only.

### Task 0c: Create the GitHub repo + remote (no remote exists yet)
- Run: `gh repo create <OWNER>/meetingscribe --private --source . --remote origin --push`
- Record `<OWNER>/meetingscribe`; it determines `SUFeedURL`.

> If any of 0a–0c can't be completed yet, STOP and report — Phases 3–5 depend on them.
> Phases 1–2 (API key + in-app Sparkle wiring) can proceed without them.

---

## Phase 1 — API key → macOS Keychain (TDD)

### Task 1: Test scaffold + Security dependency

**Files:**
- Create: `tests/__init__.py` (empty)
- Create: `tests/conftest.py`
- Modify: `requirements.txt`
- Modify: `setup.py:install_requires`

**Step 1: Install the Security bindings**

Run: `python3 -m pip install pyobjc-framework-Security`
Then verify:
Run: `python3 -c "import Security; print(Security.kSecClassGenericPassword)"`
Expected: prints a non-empty constant (no `ModuleNotFoundError`).

**Step 2: Record the dependency**

Add to `requirements.txt`:
```
pyobjc-framework-Security>=10.0
```
Add `"pyobjc-framework-Security>=10.0",` to `install_requires` in `setup.py`.

**Step 3: Create the test scaffold**

`tests/__init__.py`: empty file.

`tests/conftest.py`:
```python
import uuid
import pytest


@pytest.fixture
def kc_namespace():
    """A unique Keychain service name so tests never touch real data."""
    return f"MeetingScribeTest-{uuid.uuid4()}"
```

**Step 4: Commit**
```bash
git add requirements.txt setup.py tests/__init__.py tests/conftest.py
git commit -m "test: add pytest scaffold and pyobjc-framework-Security dependency"
```

---

### Task 2: Keychain primitives (`secrets.py`)

**Files:**
- Create: `meetingscribe/secrets.py`
- Test: `tests/test_secrets_keychain.py`

**Step 1: Write the failing test** (real Keychain round-trip, isolated service)

`tests/test_secrets_keychain.py`:
```python
from meetingscribe import secrets


def test_set_get_delete_roundtrip(kc_namespace):
    acct = "anthropic_api_key"
    assert secrets.keychain_get(acct, kc_namespace) == ""        # absent
    assert secrets.keychain_set("sk-test-123", acct, kc_namespace) is True
    assert secrets.keychain_get(acct, kc_namespace) == "sk-test-123"
    # overwrite is idempotent
    assert secrets.keychain_set("sk-test-456", acct, kc_namespace) is True
    assert secrets.keychain_get(acct, kc_namespace) == "sk-test-456"
    secrets.keychain_delete(acct, kc_namespace)
    assert secrets.keychain_get(acct, kc_namespace) == ""        # gone


def test_unicode_value(kc_namespace):
    secrets.keychain_set("sk-✓-key", "acct", kc_namespace)
    assert secrets.keychain_get("acct", kc_namespace) == "sk-✓-key"
    secrets.keychain_delete("acct", kc_namespace)
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_secrets_keychain.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'meetingscribe.secrets'`.

**Step 3: Write minimal implementation**

`meetingscribe/secrets.py`:
```python
"""Secure storage for the Anthropic API key in the macOS Keychain.

Uses generic-password Keychain items with device-only access so the key never
syncs to iCloud Keychain and is unreadable while the device is locked.
"""
import logging

import Security

log = logging.getLogger("meetingscribe")

DEFAULT_SERVICE = "MeetingScribe"
DEFAULT_ACCOUNT = "anthropic_api_key"
_OK = 0  # errSecSuccess


def keychain_get(account=DEFAULT_ACCOUNT, service=DEFAULT_SERVICE):
    query = {
        Security.kSecClass: Security.kSecClassGenericPassword,
        Security.kSecAttrService: service,
        Security.kSecAttrAccount: account,
        Security.kSecReturnData: True,
        Security.kSecMatchLimit: Security.kSecMatchLimitOne,
    }
    status, data = Security.SecItemCopyMatching(query, None)
    if status != _OK or not data:
        return ""
    return bytes(data).decode("utf-8")


def keychain_set(value, account=DEFAULT_ACCOUNT, service=DEFAULT_SERVICE):
    keychain_delete(account, service)  # overwrite cleanly
    attrs = {
        Security.kSecClass: Security.kSecClassGenericPassword,
        Security.kSecAttrService: service,
        Security.kSecAttrAccount: account,
        Security.kSecValueData: value.encode("utf-8"),
        Security.kSecAttrAccessible: Security.kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
    }
    status, _ = Security.SecItemAdd(attrs, None)
    if status != _OK:
        log.error("Keychain write failed (OSStatus %d)", status)
    return status == _OK


def keychain_delete(account=DEFAULT_ACCOUNT, service=DEFAULT_SERVICE):
    query = {
        Security.kSecClass: Security.kSecClassGenericPassword,
        Security.kSecAttrService: service,
        Security.kSecAttrAccount: account,
    }
    Security.SecItemDelete(query)
```

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_secrets_keychain.py -v`
Expected: PASS (2 tests). If macOS shows a Keychain access prompt during the test, click
Allow — this only happens for unsigned dev `python3`.

**Step 5: Commit**
```bash
git add meetingscribe/secrets.py tests/test_secrets_keychain.py
git commit -m "feat: add Keychain get/set/delete primitives (device-only access)"
```

---

### Task 3: Key resolution (`get_api_key`) with dev fallback (TDD)

**Files:**
- Modify: `meetingscribe/secrets.py`
- Test: `tests/test_get_api_key.py`

**Step 1: Write the failing test** (monkeypatch — no real Keychain/network)

`tests/test_get_api_key.py`:
```python
from meetingscribe import secrets


def test_prefers_keychain(monkeypatch):
    monkeypatch.setattr(secrets, "keychain_get", lambda *a, **k: "sk-keychain")
    monkeypatch.setattr(secrets, "_dev_fallback_key", lambda: "sk-dev")
    assert secrets.get_api_key() == "sk-keychain"


def test_falls_back_to_dev_env(monkeypatch):
    monkeypatch.setattr(secrets, "keychain_get", lambda *a, **k: "")
    monkeypatch.setattr(secrets, "_dev_fallback_key", lambda: "sk-dev")
    assert secrets.get_api_key() == "sk-dev"


def test_empty_when_nothing(monkeypatch):
    monkeypatch.setattr(secrets, "keychain_get", lambda *a, **k: "")
    monkeypatch.setattr(secrets, "_dev_fallback_key", lambda: "")
    assert secrets.get_api_key() == ""
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_get_api_key.py -v`
Expected: FAIL — `AttributeError: module 'meetingscribe.secrets' has no attribute 'get_api_key'`.

**Step 3: Add implementation** to `meetingscribe/secrets.py`:
```python
def _dev_fallback_key():
    """Dev-machine convenience only: $ANTHROPIC_API_KEY or a .env. Never bundled."""
    from meetingscribe import config
    return config._load_api_key()


def get_api_key():
    """Resolve the key: Keychain first, then dev env/.env. '' if unset."""
    key = keychain_get()
    if key:
        return key
    return _dev_fallback_key()


def set_api_key(value):
    """Persist Laurelle's key to the Keychain. Returns True on success."""
    return keychain_set(value.strip())
```

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_get_api_key.py -v`
Expected: PASS (3 tests).

**Step 5: Commit**
```bash
git add meetingscribe/secrets.py tests/test_get_api_key.py
git commit -m "feat: get_api_key resolves Keychain-first with dev env fallback"
```

---

### Task 4: Make `Summarizer` lazy + key-aware

**Files:**
- Modify: `meetingscribe/summarizer.py:1-47`
- Test: `tests/test_summarizer_key.py`

**Step 1: Write the failing test**

`tests/test_summarizer_key.py`:
```python
import pytest
from meetingscribe import summarizer as sm


def test_summarize_without_key_raises_no_key(monkeypatch):
    monkeypatch.setattr(sm, "get_api_key", lambda: "")
    s = sm.Summarizer()                       # construction must NOT need a key
    with pytest.raises(sm.NoAPIKeyError):
        s.summarize("hello world")


def test_client_built_lazily_with_key(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, api_key=None):
            captured["key"] = api_key

    monkeypatch.setattr(sm, "get_api_key", lambda: "sk-live")
    monkeypatch.setattr(sm.anthropic, "Anthropic", FakeClient)
    s = sm.Summarizer()
    assert "key" not in captured                # not built at construction
    s._ensure_client()
    assert captured["key"] == "sk-live"         # built on demand with the key
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_summarizer_key.py -v`
Expected: FAIL — `NoAPIKeyError` / `_ensure_client` don't exist; construction currently
builds the client eagerly.

**Step 3: Edit `summarizer.py`**

Replace the import line `from meetingscribe.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, USER_PROFILE`
with:
```python
from meetingscribe.config import ANTHROPIC_MODEL, USER_PROFILE
from meetingscribe.secrets import get_api_key
```
Add after the imports:
```python
class NoAPIKeyError(RuntimeError):
    """Raised when summarization is attempted without an Anthropic API key."""
```
Replace `Summarizer.__init__` and add `_ensure_client`:
```python
class Summarizer:
    def __init__(self):
        self._client = None
        self._prompts = PROFILES[USER_PROFILE]
        log.info("Summarizer using profile: %s", USER_PROFILE)

    def _ensure_client(self):
        if self._client is None:
            key = get_api_key()
            if not key:
                raise NoAPIKeyError("No Anthropic API key configured")
            self._client = anthropic.Anthropic(api_key=key)
        return self._client
```
In `_call`, change `self._client.messages.create(` to `self._ensure_client().messages.create(`.

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_summarizer_key.py -v`
Expected: PASS (2 tests).

**Step 5: Commit**
```bash
git add meetingscribe/summarizer.py tests/test_summarizer_key.py
git commit -m "feat: build Anthropic client lazily; raise NoAPIKeyError when key missing"
```

---

### Task 5: First-run prompt + "Set API Key…" menu + graceful skip

**Files:**
- Modify: `meetingscribe/app.py` (imports, `__init__` menu, startup check, summary skip)

**Step 1: Add a key-prompt helper to `app.py`** (after `_main_thread_alert`, ~line 64):
```python
from meetingscribe.secrets import get_api_key, set_api_key


def prompt_for_api_key():
    """Ask Laurelle for her Anthropic API key and store it. Returns the key or ''."""
    win = rumps.Window(
        title="MeetingScribe — Anthropic API Key",
        message=(
            "Paste your Anthropic API key to enable AI meeting summaries.\n"
            "Get one at https://console.anthropic.com/settings/keys"
        ),
        default_text="",
        ok="Save",
        cancel="Skip",
        dimensions=(360, 24),
    )
    resp = win.run()
    if resp.clicked and resp.text.strip():
        set_api_key(resp.text)
        return resp.text.strip()
    return ""
```

**Step 2: Add menu items** in `MeetingScribeApp.__init__` (the `self.menu = [...]` block):
```python
self.menu = [
    rumps.MenuItem("Start Recording", callback=self.toggle_recording),
    None,
    rumps.MenuItem("Set API Key…", callback=self.set_api_key_clicked),
    rumps.MenuItem("Check for Updates…", callback=check_for_updates),  # added in Task 7
    None,
    rumps.MenuItem("Quit", callback=rumps.quit_application),
]
```
> If Task 7 isn't done yet, omit the "Check for Updates…" line for now and add it in Task 7.

**Step 3: Add the menu callback** to `MeetingScribeApp`:
```python
def set_api_key_clicked(self, _sender):
    prompt_for_api_key()
```

**Step 4: First-run check** — at the end of `MeetingScribeApp.__init__`:
```python
if not get_api_key():
    prompt_for_api_key()
```

**Step 5: Graceful skip** in `_process_recording`, wrap the summarize call (line ~200):
```python
            from meetingscribe.summarizer import NoAPIKeyError
            try:
                summary = self._summarizer.summarize(transcript)
            except NoAPIKeyError:
                self._finish(
                    "No API key",
                    "Saved the transcript, but skipped AI summary — set your "
                    "Anthropic API key from the menu (Set API Key…).",
                )
                save_to_notes(f"Meeting — {datetime.now():%Y-%m-%d %H:%M}", transcript)
                return
```

**Step 6: Manual verification** (GUI — no automated test)

Run: `python3 -m meetingscribe.app`
- First launch with no key in Keychain/env → the key prompt appears.
- Click Skip → app still runs; menu shows "Set API Key…".
- Click "Set API Key…" → enter a dummy value → reopen → no first-run prompt next launch.
- Confirm stored: `security find-generic-password -s MeetingScribe -a anthropic_api_key`
  prints the item (value hidden). Clean up dummy: add `-g` to read, or delete with
  `security delete-generic-password -s MeetingScribe -a anthropic_api_key`.

**Step 7: Commit**
```bash
git add meetingscribe/app.py
git commit -m "feat: first-run API key prompt, Set API Key menu, graceful summary skip"
```

---

## Phase 2 — In-app Sparkle wiring

### Task 6: `updater.py` with graceful degradation (TDD where possible)

**Files:**
- Create: `meetingscribe/updater.py`
- Test: `tests/test_updater.py`

**Step 1: Write the failing test** (only the no-framework/dev path is unit-testable)

`tests/test_updater.py`:
```python
from meetingscribe import updater


def test_init_sparkle_noop_when_not_frozen(monkeypatch):
    # Running from source: framework absent → must return None, not raise.
    monkeypatch.setattr(updater, "_framework_path", lambda: "/nonexistent/Sparkle.framework")
    assert updater.init_sparkle() is None


def test_check_for_updates_safe_without_controller():
    updater._updater_controller = None
    updater.check_for_updates(None)   # must not raise
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_updater.py -v`
Expected: FAIL — module/attributes missing.

**Step 3: Write `meetingscribe/updater.py`**:
```python
"""Sparkle auto-update integration (loaded only in the packaged .app)."""
import logging
import os

from Foundation import NSBundle

log = logging.getLogger("meetingscribe")

_updater_controller = None  # module-level ref so PyObjC never deallocates it


def _framework_path():
    priv = NSBundle.mainBundle().privateFrameworksPath()
    return os.path.join(priv or "", "Sparkle.framework")


def init_sparkle():
    """Start Sparkle's updater. No-op (returns None) when the framework is absent."""
    global _updater_controller
    path = _framework_path()
    if not os.path.exists(path):
        log.info("Sparkle.framework not present (%s); skipping auto-update", path)
        return None
    try:
        import objc
        objc.loadBundle("Sparkle", globals(), bundle_path=path)
        _updater_controller = SPUStandardUpdaterController.alloc(  # noqa: F821
            ).initWithStartingUpdater_updaterDelegate_userDriverDelegate_(True, None, None)
        log.info("Sparkle updater started")
        return _updater_controller
    except Exception:
        log.exception("Failed to start Sparkle")
        return None


def check_for_updates(_sender=None):
    if _updater_controller is not None:
        _updater_controller.checkForUpdates_(None)
```

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_updater.py -v`
Expected: PASS (2 tests).

**Step 5: Commit**
```bash
git add meetingscribe/updater.py tests/test_updater.py
git commit -m "feat: add Sparkle updater module with dev-mode no-op"
```

---

### Task 7: Wire Sparkle into `app.py`

**Files:**
- Modify: `meetingscribe/app.py`

**Step 1:** Add import near the top: `from meetingscribe.updater import init_sparkle, check_for_updates`.

**Step 2:** Ensure the menu has the "Check for Updates…" item (added in Task 5 Step 2).

**Step 3:** Call `init_sparkle()` once at the end of `MeetingScribeApp.__init__`
(after the first-run key check):
```python
init_sparkle()
```

**Step 4: Manual verification**

Run: `python3 -m meetingscribe.app`
- App launches; log shows "Sparkle.framework not present … skipping" (expected from source).
- "Check for Updates…" menu item is present and clicking it does nothing/raises nothing
  (no controller in dev). Real behavior is verified in Phase 5 on the built app.
Run the full suite: `python3 -m pytest -v` → all green.

**Step 5: Commit**
```bash
git add meetingscribe/app.py
git commit -m "feat: initialize Sparkle and add Check for Updates menu item"
```

---

## Phase 3 — `.spec` / Info.plist

### Task 8: Parameterize version, add Sparkle plist keys, drop bundled .env

**Files:**
- Modify: `MeetingScribe.spec`

**Step 1:** Remove the bundled `.env` — delete the line `('.env', '.'),` from `datas`.

**Step 2:** Add `'Security'` to `hiddenimports` (so the frozen app can talk to Keychain).

**Step 3:** Parameterize the version near the top of the file (after `import os`):
```python
_version = os.environ.get('MS_VERSION', '0.1.0')
```

**Step 4:** In the `BUNDLE(... info_plist={...})` block, replace the two hardcoded version
strings with `_version`, and add the Sparkle keys:
```python
        'CFBundleVersion': _version,
        'CFBundleShortVersionString': _version,
        'NSMicrophoneUsageDescription': 'MeetingScribe needs microphone access to record meetings.',
        'SUFeedURL': 'https://github.com/<OWNER>/meetingscribe/releases/latest/download/appcast.xml',
        'SUPublicEDKey': 'REPLACE_WITH_ED_PUBLIC_KEY',   # filled in Task 9
        'SUEnableAutomaticChecks': True,
        'SUScheduledCheckInterval': 86400,
        'SUEnableInstallerLauncherService': True,
```
Replace `<OWNER>` with the value from Task 0c.

**Step 5: Verify the spec parses**

Run: `MS_VERSION=0.1.1 python3 -c "import ast; ast.parse(open('MeetingScribe.spec').read()); print('ok')"`
Expected: `ok`.

**Step 6: Commit**
```bash
git add MeetingScribe.spec
git commit -m "build: parameterize version, add Sparkle plist keys, stop bundling .env"
```

---

## Phase 4 — Release tooling

### Task 9: Generate EdDSA keys (one-time) and record the public key

**Files:**
- Modify: `MeetingScribe.spec` (fill `SUPublicEDKey`)

**Step 1:** Download a pinned Sparkle once to get its tools:
```bash
mkdir -p build/sparkle && cd build/sparkle
curl -L -o sparkle.tar.xz https://github.com/sparkle-project/Sparkle/releases/download/2.6.4/Sparkle-2.6.4.tar.xz
tar xf sparkle.tar.xz && cd ../..
```
(Pin 2.6.4 or the latest 2.x; keep the version in `release.sh`.)

**Step 2:** Generate keys (private key goes into the login Keychain):
```bash
./build/sparkle/bin/generate_keys
```
Expected: prints a base64 **public** key. Copy it.

**Step 3:** Put the public key into `MeetingScribe.spec` `SUPublicEDKey` (replace the
placeholder). **Never** commit the private key — it lives only in the Keychain.

**Step 4: Commit**
```bash
git add MeetingScribe.spec
git commit -m "build: set Sparkle EdDSA public key"
```

### Task 10: `release.sh`

**Files:**
- Create: `release.sh` (chmod +x)
- Modify: `.gitignore` (add `appcast.xml` artifacts? No — keep canonical appcast tracked; ignore `*.zip`)

**Step 1:** Create `release.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:?usage: ./release.sh X.Y.Z}"
SPARKLE_VER="2.6.4"
SIGN_ID="${MS_SIGN_IDENTITY:?set MS_SIGN_IDENTITY to your Developer ID Application identity}"
NOTARY_PROFILE="${MS_NOTARY_PROFILE:-meetingscribe-notary}"
REPO="${MS_REPO:?set MS_REPO to <OWNER>/meetingscribe}"
APP="dist/MeetingScribe.app"
ZIP="dist/MeetingScribe-${VERSION}.zip"
SPARKLE_DIR="build/sparkle"

echo "==> 1/9 Fetch pinned Sparkle ($SPARKLE_VER)"
if [ ! -x "$SPARKLE_DIR/bin/generate_appcast" ]; then
  mkdir -p "$SPARKLE_DIR"
  curl -L -o "$SPARKLE_DIR/sparkle.tar.xz" \
    "https://github.com/sparkle-project/Sparkle/releases/download/${SPARKLE_VER}/Sparkle-${SPARKLE_VER}.tar.xz"
  tar xf "$SPARKLE_DIR/sparkle.tar.xz" -C "$SPARKLE_DIR"
fi

echo "==> 2/9 Build"
rm -rf build/MeetingScribe dist
MS_VERSION="$VERSION" python3 -m PyInstaller MeetingScribe.spec --noconfirm

echo "==> 3/9 Embed Sparkle.framework"
mkdir -p "$APP/Contents/Frameworks"
cp -R "$SPARKLE_DIR/Sparkle.framework" "$APP/Contents/Frameworks/"

echo "==> 4/9 Codesign inside-out"
FW="$APP/Contents/Frameworks/Sparkle.framework"
codesign -f -s "$SIGN_ID" -o runtime --timestamp \
  "$FW/Versions/B/XPCServices/Downloader.xpc" \
  "$FW/Versions/B/XPCServices/Installer.xpc" 2>/dev/null || true
codesign -f -s "$SIGN_ID" -o runtime --timestamp \
  "$FW/Versions/B/Autoupdate" \
  "$FW/Versions/B/Updater.app" || true
codesign -f -s "$SIGN_ID" -o runtime --timestamp "$FW"
# Sign every nested Mach-O, then the app last
find "$APP" -type f \( -name "*.dylib" -o -name "*.so" \) \
  -exec codesign -f -s "$SIGN_ID" -o runtime --timestamp {} +
codesign -f -s "$SIGN_ID" -o runtime --timestamp --deep "$APP"
codesign --verify --strict --verbose=2 "$APP"

echo "==> 5/9 Notarize + staple"
/usr/bin/ditto -c -k --keepParent "$APP" "$ZIP"
xcrun notarytool submit "$ZIP" --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$APP"

echo "==> 6/9 Re-zip stapled app"
rm -f "$ZIP"
/usr/bin/ditto -c -k --keepParent "$APP" "$ZIP"

echo "==> 7/9 EdDSA-sign + 8/9 appcast"
# generate_appcast reads release notes/links and signs each zip in dist/ with the
# private key from the Keychain, updating the canonical appcast.xml.
cp appcast.xml dist/appcast.xml 2>/dev/null || true
"$SPARKLE_DIR/bin/generate_appcast" \
  --download-url-prefix "https://github.com/${REPO}/releases/download/v${VERSION}/" \
  dist/
cp dist/appcast.xml appcast.xml

echo "==> 9/9 Publish to GitHub Releases"
gh release create "v${VERSION}" "$ZIP" appcast.xml \
  --repo "$REPO" --title "MeetingScribe ${VERSION}" --notes "Release ${VERSION}" \
  || gh release upload "v${VERSION}" "$ZIP" appcast.xml --repo "$REPO" --clobber

echo "==> Done. appcast.xml updated and committed candidate ready."
```

**Step 2:** `chmod +x release.sh`

**Step 3:** Add to `.gitignore`: `dist/` is already ignored; add `*.zip` for safety. Keep
`appcast.xml` **tracked** (canonical history).

**Step 4: Verify the script is well-formed (dry parse)**

Run: `bash -n release.sh && echo "syntax ok"`
Expected: `syntax ok`.

**Step 5: Commit**
```bash
git add release.sh .gitignore
git commit -m "build: add one-command Sparkle release script"
```

> NOTE for executor: the inside-out codesign paths (`Versions/B`, XPCService names) must be
> verified against the actually-downloaded Sparkle 2.6.4 layout — run `ls -R "$FW"` once and
> adjust the explicit paths if they differ. The `find … -exec codesign` sweep + final
> `--deep` is the safety net.

---

## Phase 5 — End-to-end verification (manual, gated on Phase 0)

### Task 11: Staging appcast round-trip
1. `MS_REPO=<OWNER>/meetingscribe MS_SIGN_IDENTITY="Developer ID Application: …" ./release.sh 0.1.0`
2. Download the v0.1.0 zip from the GitHub release, unzip, move to `/Applications`, launch.
   - First run: enter the API key, confirm summaries work end-to-end on a short recording.
3. Make a visible change (e.g. menu title), `./release.sh 0.2.0`.
4. In the running v0.1.0 app, click "Check for Updates…" → Sparkle offers 0.2.0 → install →
   app relaunches as 0.2.0. **This proves EdDSA signing + appcast + notarization line up.**

### Task 12: Gatekeeper sanity on the notarized build
Run:
```bash
spctl -a -vvv --type exec dist/MeetingScribe.app
codesign --verify --deep --strict --verbose=2 dist/MeetingScribe.app
xcrun stapler validate dist/MeetingScribe.app
```
Expected: `accepted`, `source=Notarized Developer ID`; verify reports valid; stapler `valid`.

### Task 13: Finish the branch
Use superpowers:finishing-a-development-branch to present merge/PR options.

---

## Risk notes / things the executor must watch
- **PyObjC `SecItem` return shape:** `SecItemCopyMatching`/`SecItemAdd` return
  `(status, out)` tuples in PyObjC. Task 2's test is the guardrail — if it fails on the
  unpack, inspect the actual return with a quick REPL before "fixing" the code.
- **Sparkle framework internal layout** can differ by version (XPC service names, Versions
  symlink). Verify with `ls -R` (see Task 10 note) before trusting hardcoded sign paths.
- **Keychain prompt during tests:** unsigned dev `python3` triggers an OS Allow dialog the
  first time. Acceptable locally; in CI you'd pre-authorize or skip the integration test.
- **`app.py` still references BlackHole** (`find_blackhole_device`) though the recorder moved
  to ScreenCaptureKit per git history — out of scope here; do not refactor it in this branch.
- **First summary call cost:** Phase 5 Task 11 makes real Anthropic API calls (small).
```
