# Multi-Variant Distribution (Jacob + Laurelle) — Design

**Date:** 2026-06-02
**Status:** Approved
**Builds on:** `docs/plans/2026-06-02-sparkle-distribution-design.md` (single-variant pipeline, shipped v0.1.0–0.3.0)

## Goal

Ship two **fully isolated** per-user builds from one codebase — Laurelle's and Jacob's —
each with its own prompt profile and its own Sparkle update feed, so neither can ever be
offered the other's build. Releases are **lockstep**: one `./release.sh X.Y.Z` builds, signs,
notarizes, and publishes BOTH variants into a single GitHub release.

## Per-variant differentiators (only three things change)

| | Laurelle (unchanged) | Jacob (new) |
|---|---|---|
| `USER_PROFILE` | `laurelle` | `jacob` (already in `prompts.py`) |
| `bundle_identifier` | `com.meetingscribe.app` | `com.meetingscribe.jacob` |
| `SUFeedURL` | `…/releases/latest/download/appcast.xml` | `…/releases/latest/download/appcast-jacob.xml` |
| Display name | `MeetingScribe` | `MeetingScribe` (same, per decision) |

**Isolation guarantee:** different `SUFeedURL` ⇒ each app only ever polls its own appcast.
Different bundle IDs also separate Keychain entries and Sparkle state. Laurelle's identity is
byte-for-byte unchanged, so her installed 0.3.0 keeps updating with zero disruption.

## How the profile travels into the build

Currently `config.py` hardcodes `USER_PROFILE = "laurelle"`. Change it to resolve, in order:
1. **Frozen app:** read the `MSUserProfile` key from the bundle `Info.plist` (via `NSBundle`).
2. **Dev (from source):** `$MS_PROFILE` env var.
3. **Default:** `laurelle`.

The `.spec` writes `MSUserProfile` into `Info.plist` from `$MS_PROFILE` at build time, alongside
`bundle_identifier` from `$MS_BUNDLE_ID` and `SUFeedURL` from `$MS_FEED_URL` — all defaulting to
Laurelle's values so an un-parameterized build stays identical to today.

## Release structure (one release, four assets)

`v0.4.0` (first dual release) contains:
- `MeetingScribe-0.4.0.zip` + `appcast.xml` — Laurelle's feed (keeps history 0.1.0→0.4.0)
- `MeetingScribe-Jacob-0.4.0.zip` + `appcast-jacob.xml` — Jacob's feed (starts at 0.4.0)

Both feed URLs use `latest/download/`, so they always resolve to the newest release.

## release.sh changes

Refactor the single pipeline into a `build_variant(profile, bundle_id, feed_url, zip_name,
appcast_name)` function (build → embed Sparkle → xattr-strip → codesign inside-out → notarize →
staple → zip → EdDSA-sign → regenerate that variant's appcast). Run it for `laurelle` then
`jacob` (each in its own temp build root, both outside iCloud). Then a single `gh release create
vX.Y.Z` uploads all four assets. Each variant keeps its own canonical appcast committed in the
repo (`appcast.xml`, `appcast-jacob.xml`).

## Versioning

Lockstep: both variants share the version number. Next release = **0.4.0** (Laurelle 0.3.0→0.4.0;
Jacob's first install is 0.4.0, downloaded from the release page). Per-variant targeting is a
possible future add (`./release.sh X.Y.Z [laurelle|jacob|both]`) — not built now (YAGNI).

## Verification

- Both `.app`s notarized + `spctl` accepted; staple valid.
- `Info.plist` `MSUserProfile` = correct profile in each bundle; `CFBundleIdentifier` differs.
- `appcast.xml` advertises 0.4.0 and contains NO Jacob entries; `appcast-jacob.xml` advertises
  0.4.0 and contains NO Laurelle entries (isolation).
- `config.py` profile-resolution unit tests (frozen-plist path, env path, default).
- Both zips publicly downloadable anonymously.
