# Sparkle Auto-Update Distribution — Design

**Date:** 2026-06-02
**Status:** Approved (brainstorm complete, ready for implementation plan)
**Scope:** Add Sparkle 2.x auto-update to Laurelle's `MeetingScribe.app` build, hosted on
GitHub Releases, driven by a one-command release script; plus move the Anthropic API key
out of the bundle into a first-run Keychain prompt.

## Context

MeetingScribe is a macOS menu-bar app (`rumps`, `LSUIElement`) packaged with PyInstaller
into `MeetingScribe.app` (arm64). "Laurelle's version" is the build configured with
`USER_PROFILE = "laurelle"` in `meetingscribe/config.py` plus her custom prompts in
`meetingscribe/prompts.py`. We want Laurelle to receive seamless updates without us
re-sending a `.app` each time.

## Confirmed constraints / decisions

- **Code signing:** Developer ID Application certificate available; app will be signed +
  notarized (the clean path for Sparkle on modern Gatekeeper).
- **Hosting:** GitHub Releases — update `.zip` and `appcast.xml` as release assets.
- **Variants:** Only Laurelle's build for now → single appcast feed, designed to leave
  room for per-profile feeds later.
- **Release pipeline:** Full one-command release (`./release.sh 0.2.0`) doing
  build → sign → notarize → staple → zip → EdDSA-sign → appcast → GitHub upload.
- **API key:** Laurelle uses **her own** Anthropic key. Removed from the bundle entirely;
  prompted once on first launch and stored in macOS Keychain. Your key never ships.
- **Sparkle framework:** Not vendored in the repo. The release script downloads a pinned
  Sparkle release at build time (cached in gitignored `build/`).
- **End-user priority:** Minimize friction for Laurelle — her only setup step is pasting
  her API key once.

## Architecture overview

Five moving parts:

1. **`Sparkle.framework` embedded** at `MeetingScribe.app/Contents/Frameworks/Sparkle.framework`
   (contains Sparkle's helper apps + XPC service that perform download-and-replace).
2. **PyObjC glue** — at startup, load the framework and create an
   `SPUStandardUpdaterController`. Adds a "Check for Updates…" menu item; auto-checks daily.
3. **Info.plist keys** baked into the `.spec`: `SUFeedURL`, `SUPublicEDKey`,
   `SUEnableAutomaticChecks`, `SUScheduledCheckInterval`.
4. **`appcast.xml` on GitHub** — the feed Sparkle polls; each item carries an EdDSA signature.
5. **`release.sh`** — one-command release pipeline.

**Update flow:** app polls `appcast.xml` → sees newer `CFBundleVersion` → downloads zip →
verifies EdDSA signature + Developer ID → swaps app → relaunches.

## In-app Sparkle wiring (PyObjC)

New module `meetingscribe/updater.py`:

```python
import objc
from Foundation import NSBundle

_updater_controller = None  # module-level ref so it's never GC'd

def init_sparkle():
    global _updater_controller
    bundle_path = NSBundle.mainBundle().privateFrameworksPath() + "/Sparkle.framework"
    objc.loadBundle("Sparkle", globals(), bundle_path=bundle_path)
    _updater_controller = SPUStandardUpdaterController.alloc(
        ).initWithStartingUpdater_updaterDelegate_userDriverDelegate_(True, None, None)
    return _updater_controller

def check_for_updates(_sender=None):
    if _updater_controller:
        _updater_controller.checkForUpdates_(None)
```

Wiring into `app.py`:
- Call `init_sparkle()` once during startup (main thread, after the rumps app exists).
- Add `rumps.MenuItem("Check for Updates…", callback=check_for_updates)`.
- Automatic scheduled checks configured via Info.plist (no extra code).

Design notes:
- **Graceful degradation:** wrap `init_sparkle()` in try/except. Running from source (not
  frozen) has no embedded framework → log and skip. Updates only matter in the packaged app.
- **Module-level reference:** the controller must outlive the call or PyObjC deallocates it
  and auto-checks die silently.
- **No delegate initially:** `None` delegates use Sparkle's standard update UI.

## `.spec` and Info.plist changes

1. **Info.plist keys** added to the `BUNDLE(info_plist={...})` block:

   ```python
   'SUFeedURL': 'https://github.com/<you>/<repo>/releases/latest/download/appcast.xml',
   'SUPublicEDKey': '<base64 EdDSA public key from generate_keys>',
   'SUEnableAutomaticChecks': True,
   'SUScheduledCheckInterval': 86400,   # once a day
   'SUEnableInstallerLauncherService': True,
   ```

   The `latest/download/` URL means the feed URL never changes across versions.

2. **Version parameterized.** `CFBundleVersion`/`CFBundleShortVersionString` read from an
   env var the release script sets, since Sparkle compares `CFBundleVersion` to detect
   newer builds and it must increment every release:

   ```python
   import os
   _version = os.environ.get('MS_VERSION', '0.1.0')
   ```

3. **Framework embedding** handled by the release script (post-build copy + signing pass),
   not the `.spec` — PyInstaller doesn't place `.framework` bundles cleanly and the
   framework needs its own code-signing anyway.

## Release pipeline (`release.sh`)

`./release.sh 0.2.0`, in order (signing order matters):

1. **Fetch pinned Sparkle** — download `Sparkle-2.x.x.tar.xz` from the official GitHub
   release into gitignored `build/sparkle/` (cached). Provides `Sparkle.framework` plus the
   `generate_keys` / `generate_appcast` / `sign_update` tools.
2. **Build** — `MS_VERSION=0.2.0 pyinstaller MeetingScribe.spec` → `dist/MeetingScribe.app`.
3. **Embed framework** — copy `Sparkle.framework` into `.../Contents/Frameworks/`.
4. **Codesign inside-out** — sign Sparkle's nested helpers (XPC service, Autoupdate,
   Updater.app), then the framework, then the app, all with Developer ID Application,
   `--options runtime`, `--timestamp`. Nested-first ordering required or notarization fails.
5. **Notarize + staple** — zip, `xcrun notarytool submit --wait`, then `xcrun stapler staple`.
6. **Package** — zip the stapled app into `MeetingScribe-0.2.0.zip` (Sparkle update format).
7. **EdDSA-sign the update** — `sign_update MeetingScribe-0.2.0.zip` using the private key
   from Keychain (created once via `generate_keys`).
8. **Generate appcast** — maintain a canonical `appcast.xml` (committed in the repo / on a
   `releases` branch) that the script appends the new signed entry to and re-uploads each
   release — more reliable than reconstructing from GitHub assets.
9. **Publish** — `gh release create v0.2.0 MeetingScribe-0.2.0.zip appcast.xml --notes ...`.

Secrets referenced by name, never committed: Developer ID identity, a `notarytool`
keychain profile (App Store Connect API key or app-specific password), and the Sparkle
EdDSA private key (Keychain).

## API key remediation (first-run, Keychain)

Remove the hardcoded key from `config.py` entirely. Replace with a Keychain-backed lookup
in a small `secrets.py`:

```python
def get_api_key():
    key = keychain_get("MeetingScribe", "anthropic_api_key")
    if not key:
        key = prompt_for_key()      # rumps.Window, first run
        if key:
            keychain_set("MeetingScribe", "anthropic_api_key", key)
    return key
```

- **Implementation:** PyObjC `Security` framework directly (no new dependency — PyObjC is
  already bundled), using `kSecAttrAccessibleWhenUnlockedThisDeviceOnly` so the key never
  syncs to iCloud Keychain and requires the device unlocked. Chosen as the most secure
  option (vs. the `keyring` library, which can't set device-only access).
- **Laurelle's experience:** first launch shows a small dialog ("Enter your Anthropic API
  key to enable summaries") with a link to where she gets one. Pastes once → stored → never
  asked again. Recording/transcription still work if blank; only the AI summary is skipped
  with a gentle notification. A "Set API Key…" menu item lets her update it later.
- **Security:** Keychain entries are per-user, encrypted at rest, device-only — the key
  never touches the bundle or the repo.

## Testing & verification

1. **Unit-testable (TDD):** Keychain read/write/delete round-trip; `get_api_key()` fallback
   logic (Keychain hit, prompt-on-empty, summary-skip path); release version-string parsing.
2. **Sparkle integration (manual checklist):** `init_sparkle()` no-ops cleanly from source;
   in a built `.app`, "Check for Updates…" appears and opens Sparkle's panel.
3. **End-to-end staging appcast:** build v0.1.0 and install as Laurelle would; build v0.2.0
   with a visible change; `./release.sh 0.2.0` to a test repo/pre-release; confirm v0.1.0
   detects → downloads → verifies → swaps → relaunches as v0.2.0. Proves EdDSA signing,
   notarization, and appcast wiring line up.
4. **Gatekeeper sanity:** `spctl -a -vvv MeetingScribe.app` and
   `codesign --verify --deep --strict` pass on the notarized build (quarantine simulated).

## Future room (not in scope now)

- Per-profile appcast feeds / Sparkle channels when more user variants are distributed.
- Optional custom Sparkle updater delegate for tailored UI/behavior.
