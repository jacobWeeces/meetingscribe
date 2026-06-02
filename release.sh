#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:?usage: ./release.sh X.Y.Z}"
SPARKLE_VER="2.6.4"
SIGN_ID="${MS_SIGN_IDENTITY:-Developer ID Application: Jacob Weeces (W88MVXL7D2)}"
NOTARY_PROFILE="${MS_NOTARY_PROFILE:-meetingscribe-notary}"
REPO="${MS_REPO:-jacobWeeces/meetingscribe}"
SPARKLE_DIR="build/sparkle"
BUILD_ROOT="${MS_BUILD_ROOT:-/private/tmp/meetingscribe-release}"

echo "==> 1/4 Fetch pinned Sparkle ($SPARKLE_VER)"
if [ ! -d "$SPARKLE_DIR/Sparkle.framework" ]; then
  mkdir -p "$SPARKLE_DIR"
  curl -L -o "$SPARKLE_DIR/sparkle.tar.xz" \
    "https://github.com/sparkle-project/Sparkle/releases/download/${SPARKLE_VER}/Sparkle-${SPARKLE_VER}.tar.xz"
  tar xf "$SPARKLE_DIR/sparkle.tar.xz" -C "$SPARKLE_DIR"
fi

# Build + sign + notarize + appcast one variant. Args:
#   profile bundle_id feed_url zip_name appcast_name
build_variant() {
  local profile="$1" bundle_id="$2" feed_url="$3" zip_name="$4" appcast_name="$5"
  local vroot="$BUILD_ROOT/$profile"
  local app="$vroot/dist/MeetingScribe.app"
  local zip="$vroot/dist/$zip_name"
  local FW="$app/Contents/Frameworks/Sparkle.framework"

  echo "==> [$profile] build (profile=$profile id=$bundle_id)"
  rm -rf "$vroot"; mkdir -p "$vroot"
  export MS_VERSION="$VERSION" MS_PROFILE="$profile" MS_BUNDLE_ID="$bundle_id" MS_FEED_URL="$feed_url"
  python3 -m PyInstaller MeetingScribe.spec --noconfirm --distpath "$vroot/dist" --workpath "$vroot/build"

  echo "==> [$profile] embed Sparkle.framework"
  mkdir -p "$app/Contents/Frameworks"
  /usr/bin/ditto "$SPARKLE_DIR/Sparkle.framework" "$FW"
  /usr/bin/xattr -cr "$app"

  echo "==> [$profile] codesign inside-out"
  find "$FW/Versions" -type f -perm -111 \
    -exec codesign -f -s "$SIGN_ID" -o runtime --timestamp {} +
  for nested in "$FW"/Versions/*/XPCServices/*.xpc "$FW"/Versions/*/Updater.app; do
    [ -e "$nested" ] && codesign -f -s "$SIGN_ID" -o runtime --timestamp "$nested"
  done
  codesign -f -s "$SIGN_ID" -o runtime --timestamp "$FW"
  if [ -d "$app/Contents/Frameworks/Python.framework" ]; then
    for pybin in "$app"/Contents/Frameworks/Python.framework/Versions/*/Python; do
      [ -e "$pybin" ] && codesign -f -s "$SIGN_ID" -o runtime --timestamp "$pybin"
    done
    codesign -f -s "$SIGN_ID" -o runtime --timestamp "$app/Contents/Frameworks/Python.framework"
  fi
  find "$app" -path "$FW" -prune -o -type f \( -name "*.so" -o -name "*.dylib" \) -print0 \
    | xargs -0 -r codesign -f -s "$SIGN_ID" -o runtime --timestamp
  codesign -f -s "$SIGN_ID" -o runtime --timestamp "$app"
  codesign --verify --strict --verbose=2 "$app"

  echo "==> [$profile] notarize + staple"
  /usr/bin/ditto -c -k --keepParent "$app" "$zip"
  xcrun notarytool submit "$zip" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$app"
  rm -f "$zip"; /usr/bin/ditto -c -k --keepParent "$app" "$zip"

  echo "==> [$profile] EdDSA-sign + appcast ($appcast_name)"
  cp "$appcast_name" "$vroot/dist/appcast.xml" 2>/dev/null || true
  "$SPARKLE_DIR/bin/generate_appcast" \
    --download-url-prefix "https://github.com/${REPO}/releases/download/v${VERSION}/" \
    "$vroot/dist/"
  cp "$vroot/dist/appcast.xml" "$appcast_name"
}

echo "==> 2/4 Build + sign + notarize BOTH variants (~10 min: two notarizations)"
rm -rf "$BUILD_ROOT"
build_variant "laurelle" "com.meetingscribe.app" \
  "https://github.com/${REPO}/releases/latest/download/appcast.xml" \
  "MeetingScribe-${VERSION}.zip" "appcast.xml"
build_variant "jacob" "com.meetingscribe.jacob" \
  "https://github.com/${REPO}/releases/latest/download/appcast-jacob.xml" \
  "MeetingScribe-Jacob-${VERSION}.zip" "appcast-jacob.xml"

echo "==> 3/4 Collect assets"
LZIP="$BUILD_ROOT/laurelle/dist/MeetingScribe-${VERSION}.zip"
JZIP="$BUILD_ROOT/jacob/dist/MeetingScribe-Jacob-${VERSION}.zip"

echo "==> 4/4 Publish to GitHub Releases (both variants, one release)"
gh release create "v${VERSION}" "$LZIP" "$JZIP" appcast.xml appcast-jacob.xml \
  --repo "$REPO" --title "MeetingScribe ${VERSION}" --notes "Release ${VERSION} (Laurelle + Jacob)" \
  || gh release upload "v${VERSION}" "$LZIP" "$JZIP" appcast.xml appcast-jacob.xml --repo "$REPO" --clobber

echo "==> Done. Commit appcast.xml and appcast-jacob.xml after the release."
