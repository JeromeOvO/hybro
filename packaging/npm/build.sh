#!/bin/sh
# Stage the frozen CLI into the npm platform package for one platform.
#
# Usage: packaging/npm/build.sh <darwin-arm64|darwin-x64|linux-arm64|linux-x64>
#
# Build the bundle first (packaging/cli/build.sh), then run this on a runner for
# the matching platform. It copies packaging/cli/dist/hybro/ into
# packaging/npm/cli-<platform>/hybro/ and refuses to stage a mismatch between the
# CLI version and the published package versions.
#
# The staged bundle is a build artifact and is not committed; publish with
# `npm publish packaging/npm/cli-<platform>` and then `npm publish packaging/npm/cli`.
set -eu

cd "$(dirname "$0")/../.."

platform="${1:-}"
case "$platform" in
    darwin-arm64 | darwin-x64 | linux-arm64 | linux-x64) ;;
    *)
        echo "usage: packaging/npm/build.sh <darwin-arm64|darwin-x64|linux-arm64|linux-x64>" >&2
        exit 2
        ;;
esac

bundle="packaging/cli/dist/hybro"
if [ ! -x "$bundle/hybro" ]; then
    echo "Missing $bundle; run packaging/cli/build.sh first." >&2
    exit 1
fi

version=$(cat VERSION)
for manifest in packaging/npm/cli/package.json packaging/npm/cli-*/package.json; do
    # release-please keeps these in lockstep with VERSION through extra-files.
    declared=$(sed -n 's/.*"version": "\([^"]*\)".*/\1/p' "$manifest" | head -1)
    if [ "$declared" != "$version" ]; then
        echo "$manifest has version $declared but VERSION is $version." >&2
        echo "Run the release task; do not hand-edit version fields." >&2
        exit 1
    fi
done

destination="packaging/npm/cli-$platform/hybro"
rm -rf "$destination"
mkdir -p "$destination"
cp -R "$bundle/." "$destination/"
chmod 755 "$destination/hybro"

echo "Staged $version into packaging/npm/cli-$platform/hybro"
echo "Publish: npm publish packaging/npm/cli-$platform"
echo "Then:    npm publish packaging/npm/cli"
