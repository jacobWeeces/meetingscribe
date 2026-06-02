#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:?usage: ./release.sh X.Y.Z}"
SPARKLE_VER="2.6.4"
SIGN_ID="${MS_SIGN_IDENTITY:-Developer ID Application: Jacob Weeces (W88MVXL7D2)}"
NOTARY_PROFILE="${MS_NOTARY_PROFILE:-meetingscribe-notary}"
REPO="${MS_REPO:-jacobWeeces/meetingscribe}"
APP="dist/MeetingScribe.app"
ZIP="dist/MeetingScribe-${VERSION}.zip"
SPARKLE_DIR="build/sparkle"

echo "==> 1/9 Fetch pinned Sparkle ($SPARKLE_VER)"
if [ ! -d "$SPARKLE_DIR/Sparkle.framework" ]; then
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
# Sign every nested Mach-O first (XPC services, helper apps, dylibs), then framework, then app.
find "$FW" -type f \( -name "*.dylib" -o -name "Autoupdate" -o -name "*.xpc" -o -perm -111 \) \
  -exec codesign -f -s "$SIGN_ID" -o runtime --timestamp {} + 2>/dev/null || true
find "$FW" -name "*.xpc" -type d -exec codesign -f -s "$SIGN_ID" -o runtime --timestamp {} + 2>/dev/null || true
find "$FW" -name "*.app" -type d -exec codesign -f -s "$SIGN_ID" -o runtime --timestamp {} + 2>/dev/null || true
codesign -f -s "$SIGN_ID" -o runtime --timestamp "$FW"
find "$APP" -type f \( -name "*.dylib" -o -name "*.so" \) \
  -exec codesign -f -s "$SIGN_ID" -o runtime --timestamp {} + 2>/dev/null || true
codesign -f -s "$SIGN_ID" -o runtime --timestamp --deep "$APP"
codesign --verify --strict --verbose=2 "$APP"

echo "==> 5/9 Notarize + staple"
/usr/bin/ditto -c -k --keepParent "$APP" "$ZIP"
xcrun notarytool submit "$ZIP" --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$APP"

echo "==> 6/9 Re-zip stapled app"
rm -f "$ZIP"
/usr/bin/ditto -c -k --keepParent "$APP" "$ZIP"

echo "==> 7/9 + 8/9 EdDSA-sign + appcast"
cp appcast.xml dist/appcast.xml 2>/dev/null || true
"$SPARKLE_DIR/bin/generate_appcast" \
  --download-url-prefix "https://github.com/${REPO}/releases/download/v${VERSION}/" \
  dist/
cp dist/appcast.xml appcast.xml

echo "==> 9/9 Publish to GitHub Releases"
gh release create "v${VERSION}" "$ZIP" appcast.xml \
  --repo "$REPO" --title "MeetingScribe ${VERSION}" --notes "Release ${VERSION}" \
  || gh release upload "v${VERSION}" "$ZIP" appcast.xml --repo "$REPO" --clobber

echo "==> Done. appcast.xml regenerated; commit it after the release."
