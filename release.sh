#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:?usage: ./release.sh X.Y.Z}"
SPARKLE_VER="2.6.4"
SIGN_ID="${MS_SIGN_IDENTITY:-Developer ID Application: Jacob Weeces (W88MVXL7D2)}"
NOTARY_PROFILE="${MS_NOTARY_PROFILE:-meetingscribe-notary}"
REPO="${MS_REPO:-jacobWeeces/meetingscribe}"
SPARKLE_DIR="build/sparkle"
BUILD_ROOT="${MS_BUILD_ROOT:-/private/tmp/meetingscribe-release}"
APP="$BUILD_ROOT/dist/MeetingScribe.app"
ZIP="$BUILD_ROOT/dist/MeetingScribe-${VERSION}.zip"

echo "==> 1/9 Fetch pinned Sparkle ($SPARKLE_VER)"
if [ ! -d "$SPARKLE_DIR/Sparkle.framework" ]; then
  mkdir -p "$SPARKLE_DIR"
  curl -L -o "$SPARKLE_DIR/sparkle.tar.xz" \
    "https://github.com/sparkle-project/Sparkle/releases/download/${SPARKLE_VER}/Sparkle-${SPARKLE_VER}.tar.xz"
  tar xf "$SPARKLE_DIR/sparkle.tar.xz" -C "$SPARKLE_DIR"
fi

echo "==> 2/9 Build"
rm -rf "$BUILD_ROOT"
mkdir -p "$BUILD_ROOT"
export MS_VERSION="$VERSION"
python3 -m PyInstaller MeetingScribe.spec --noconfirm \
  --distpath "$BUILD_ROOT/dist" --workpath "$BUILD_ROOT/build"

echo "==> 3/9 Embed Sparkle.framework"
mkdir -p "$APP/Contents/Frameworks"
/usr/bin/ditto "$SPARKLE_DIR/Sparkle.framework" "$APP/Contents/Frameworks/Sparkle.framework"
/usr/bin/xattr -cr "$APP/Contents/Frameworks/Sparkle.framework"

echo "==> 4/9 Codesign inside-out"
FW="$APP/Contents/Frameworks/Sparkle.framework"
# Strip extended attributes (resource forks / Finder info) from the whole bundle;
# codesign rejects them under hardened runtime ("...detritus not allowed").
/usr/bin/xattr -cr "$APP"
# (a) Sign nested Mach-O executables inside the framework first (main binary, Autoupdate,
#     and the executables inside the XPC services / Updater.app).
find "$FW/Versions" -type f -perm -111 \
  -exec codesign -f -s "$SIGN_ID" -o runtime --timestamp {} +
# (b) Sign the XPC service bundles and the Updater.app bundle.
for nested in "$FW"/Versions/*/XPCServices/*.xpc "$FW"/Versions/*/Updater.app; do
  [ -e "$nested" ] && codesign -f -s "$SIGN_ID" -o runtime --timestamp "$nested"
done
# (c) Sign the framework bundle itself.
codesign -f -s "$SIGN_ID" -o runtime --timestamp "$FW"
# (d) Sign the remaining app Mach-O (PyInstaller .so/.dylib), excluding the framework already signed.
find "$APP" -path "$FW" -prune -o -type f \( -name "*.so" -o -name "*.dylib" \) -print0 \
  | xargs -0 -r codesign -f -s "$SIGN_ID" -o runtime --timestamp
# (e) Sign the outer app bundle LAST (no --deep).
codesign -f -s "$SIGN_ID" -o runtime --timestamp "$APP"
codesign --verify --strict --verbose=2 "$APP"

echo "==> 5/9 Notarize + staple"
/usr/bin/ditto -c -k --keepParent "$APP" "$ZIP"
xcrun notarytool submit "$ZIP" --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$APP"

echo "==> 6/9 Re-zip stapled app"
rm -f "$ZIP"
/usr/bin/ditto -c -k --keepParent "$APP" "$ZIP"

echo "==> 7/9 + 8/9 EdDSA-sign + appcast"
cp appcast.xml "$BUILD_ROOT/dist/appcast.xml" 2>/dev/null || true
"$SPARKLE_DIR/bin/generate_appcast" \
  --download-url-prefix "https://github.com/${REPO}/releases/download/v${VERSION}/" \
  "$BUILD_ROOT/dist/"
cp "$BUILD_ROOT/dist/appcast.xml" appcast.xml

echo "==> 9/9 Publish to GitHub Releases"
gh release create "v${VERSION}" "$ZIP" appcast.xml \
  --repo "$REPO" --title "MeetingScribe ${VERSION}" --notes "Release ${VERSION}" \
  || gh release upload "v${VERSION}" "$ZIP" appcast.xml --repo "$REPO" --clobber

echo "==> Done. appcast.xml regenerated; commit it after the release."
