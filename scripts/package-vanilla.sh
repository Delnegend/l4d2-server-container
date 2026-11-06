#!/usr/bin/env bash
#
# Download official Left 4 Dead 2 dedicated server files, compress with zstd level 3,
# split if exceeding GitHub release asset size limits (2 GiB), and create a draft release.
#
# Required environment variables:
#   BUILD_ID          - Steam public build ID
#   GAME_MANIFEST     - Steam manifest ID for depot 222861 (game files)
#   LAUNCHER_MANIFEST - Steam manifest ID for depot 222863 (launcher)
#
# Optional environment variables:
#   IS_DRAFT          - "true" (default) or "false"
#   TARGET_BRANCH     - Target git branch or SHA (default: main)
#   DEPOT_DOWNLOADER_VERSION - Version of DepotDownloader to use (default: 3.4.0)
#   MAX_DOWNLOADS     - Max concurrent chunk downloads (default: 16)
#   WORKDIR           - Working directory (default: mktemp)
#
set -euo pipefail

: "${BUILD_ID:?package-vanilla: BUILD_ID is required}"
: "${GAME_MANIFEST:?package-vanilla: GAME_MANIFEST is required}"
: "${LAUNCHER_MANIFEST:?package-vanilla: LAUNCHER_MANIFEST is required}"

IS_DRAFT="${IS_DRAFT:-true}"
TARGET_BRANCH="${TARGET_BRANCH:-main}"
DEPOT_DOWNLOADER_VERSION="${DEPOT_DOWNLOADER_VERSION:-3.4.0}"
MAX_DOWNLOADS="${MAX_DOWNLOADS:-16}"

APP_ID=222860
LAUNCHER_DEPOT=222863
GAME_DEPOT=222861

# 2000 MiB threshold (GitHub Release limit is 2 GiB per asset)
MAX_ASSET_BYTES=$((2000 * 1024 * 1024))

WORKDIR="${WORKDIR:-$(mktemp -d /tmp/vanilla-pkg.XXXXXX)}"
SERVER_DIR="$WORKDIR/l4d2"
DIST_DIR="$WORKDIR/dist"
BIN_DIR="$WORKDIR/bin"

mkdir -p "$SERVER_DIR" "$DIST_DIR" "$BIN_DIR"

cleanup() {
    rm -rf "$WORKDIR"
}
trap cleanup EXIT

echo "==> Preparing DepotDownloader..."
DD_BIN=""
if command -v DepotDownloader >/dev/null 2>&1; then
    DD_BIN="$(command -v DepotDownloader)"
elif [ -x /opt/depotdownloader/DepotDownloader ]; then
    DD_BIN="/opt/depotdownloader/DepotDownloader"
else
    DD_ZIP="$WORKDIR/dd.zip"
    curl -fsSL "https://github.com/SteamRE/DepotDownloader/releases/download/DepotDownloader_${DEPOT_DOWNLOADER_VERSION}/DepotDownloader-linux-x64.zip" -o "$DD_ZIP"
    unzip -q "$DD_ZIP" -d "$BIN_DIR/depotdownloader"
    chmod +x "$BIN_DIR/depotdownloader/DepotDownloader"
    DD_BIN="$BIN_DIR/depotdownloader/DepotDownloader"
    rm -f "$DD_ZIP"
fi

echo "==> Downloading launcher depot ($LAUNCHER_DEPOT)..."
"$DD_BIN" \
    -app "$APP_ID" \
    -depot "$LAUNCHER_DEPOT" \
    -manifest "$LAUNCHER_MANIFEST" \
    -dir "$SERVER_DIR" \
    -max-downloads "$MAX_DOWNLOADS"

echo "==> Downloading game depot ($GAME_DEPOT)..."
"$DD_BIN" \
    -app "$APP_ID" \
    -depot "$GAME_DEPOT" \
    -manifest "$GAME_MANIFEST" \
    -dir "$SERVER_DIR" \
    -max-downloads "$MAX_DOWNLOADS"

rm -rf "$SERVER_DIR/.DepotDownloader"

echo "==> Resolving version from steam.inf..."
STEAM_INF="$SERVER_DIR/left4dead2/steam.inf"
PATCH_VERSION=""
if [ -f "$STEAM_INF" ]; then
    PATCH_VERSION=$(grep -Ei '^[Pp]atch[Vv]ersion=' "$STEAM_INF" | cut -d= -f2 | tr -d '\r\n[:space:]')
fi

VERSION="${PATCH_VERSION:-$BUILD_ID}"
TAG="vanilla-${VERSION}"

# If a release with this tag already exists on GitHub, disambiguate with build ID
if gh release view "$TAG" >/dev/null 2>&1; then
    existing_body="$(gh release view "$TAG" --json body -q .body 2>/dev/null || true)"
    if ! echo "$existing_body" | grep -q "Build ID: $BUILD_ID"; then
        echo "Release $TAG exists with different build ID; tagging as vanilla-${VERSION}-${BUILD_ID}"
        TAG="vanilla-${VERSION}-${BUILD_ID}"
    fi
fi

ARCHIVE_NAME="l4d2-server-vanilla-${VERSION}.tar.zst"
ARCHIVE_PATH="$WORKDIR/$ARCHIVE_NAME"

echo "==> Compressing server directory using zstd level 3..."
# -T0 uses all available CPU threads for fast compression
tar -C "$SERVER_DIR" -cf - . | zstd -3 -T0 -o "$ARCHIVE_PATH"

ARCHIVE_SIZE=$(stat -c%s "$ARCHIVE_PATH")
echo "==> Compressed archive size: $ARCHIVE_SIZE bytes"

# Check GitHub Releases 2 GiB per-asset ceiling
if [ "$ARCHIVE_SIZE" -gt "$MAX_ASSET_BYTES" ]; then
    echo "==> Archive size exceeds 2 GiB limit; splitting into 1900 MiB parts..."
    split -d -b 1900M "$ARCHIVE_PATH" "$DIST_DIR/${ARCHIVE_NAME}.part"
    rm -f "$ARCHIVE_PATH"
else
    mv "$ARCHIVE_PATH" "$DIST_DIR/"
fi

# Reclaim disk space before generating checksums and uploading
rm -rf "$SERVER_DIR"

echo "==> Generating SHA256 checksums..."
(cd "$DIST_DIR" && sha256sum * > sha256sums.txt)

NOTES_FILE="$WORKDIR/release-notes.md"
cat <<EOF > "$NOTES_FILE"
Left 4 Dead 2 Dedicated Server (Vanilla)

- **Version**: \`${VERSION}\`
- **Steam Build ID**: \`${BUILD_ID}\`
- **Game Manifest**: \`${GAME_MANIFEST}\`
- **Launcher Manifest**: \`${LAUNCHER_MANIFEST}\`
- **Depots**: 222861 (Game), 222863 (Launcher)
- **Compression**: zstd level 3

<!-- metadata:
build_id: ${BUILD_ID}
game_manifest: ${GAME_MANIFEST}
launcher_manifest: ${LAUNCHER_MANIFEST}
patch_version: ${VERSION}
-->

### Integrity Check

\`\`\`bash
sha256sum -c sha256sums.txt
\`\`\`

### Extraction

EOF

if compgen -G "$DIST_DIR/${ARCHIVE_NAME}.part*" >/dev/null; then
cat <<EOF >> "$NOTES_FILE"
\`\`\`bash
# Multi-part archive: concatenate parts and extract
cat ${ARCHIVE_NAME}.part* | tar --zstd -xf -
\`\`\`
EOF
else
cat <<EOF >> "$NOTES_FILE"
\`\`\`bash
# Single archive: extract directly
tar --zstd -xf ${ARCHIVE_NAME}
\`\`\`
EOF
fi

echo "==> Draft release notes:"
cat "$NOTES_FILE"

echo "==> Publishing release to GitHub..."
DRAFT_FLAG=""
if [ "$IS_DRAFT" = "true" ]; then
    DRAFT_FLAG="--draft"
fi

if gh release view "$TAG" >/dev/null 2>&1; then
    echo "Release $TAG already exists on GitHub. Uploading assets..."
    gh release upload "$TAG" "$DIST_DIR"/* --clobber
else
    echo "Creating new release $TAG (draft=$IS_DRAFT)..."
    # shellcheck disable=SC2086
    gh release create "$TAG" "$DIST_DIR"/* \
        $DRAFT_FLAG \
        --latest=false \
        --title "$TAG" \
        --notes-file "$NOTES_FILE" \
        --target "$TARGET_BRANCH"
fi

echo "==> Done: release $TAG created/updated."
