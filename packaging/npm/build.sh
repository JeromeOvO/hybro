#!/bin/sh
# Stage the frozen CLI into the npm platform package for one platform.
#
# Usage: packaging/npm/build.sh <darwin-arm64|darwin-x64|linux-arm64|linux-x64>
#
# Build the executable first (packaging/cli/build.sh), then run this on a runner
# for the matching platform. It copies packaging/cli/dist/hybro into
# packaging/npm/cli-<platform>/hybro/ and refuses to stage a mismatch between the
# CLI version and the published package versions.
#
# It stages one file on purpose. npm's packer drops symbolic links, which broke
# the previous onedir layout; the package must contain no links to survive.
#
# The staged executable is a build artifact and is not committed; publish with
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

executable="packaging/cli/dist/hybro"
if [ ! -x "$executable" ] || [ -d "$executable" ]; then
    echo "Missing $executable (expected the onefile executable); run packaging/cli/build.sh first." >&2
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
cp "$executable" "$destination/hybro"
chmod 755 "$destination/hybro"

# The published package is unusable if any link survived into it, and npm would
# drop it silently, so fail here instead of at a user's first run.
if [ -n "$(find "$destination" -type l -print -quit)" ]; then
    echo "Staged package contains a symbolic link; npm would drop it." >&2
    exit 1
fi

# Start the staged executable. A release once shipped a CLI whose interpreter
# was a dropped link, so size and file-count checks are not enough: the artifact
# has to run before it is published.
staged_version=$(HYBRO_HOME="$(mktemp -d)" "$destination/hybro" --version 2>&1) || {
    echo "The staged executable does not run:" >&2
    echo "$staged_version" >&2
    exit 1
}
if [ "$staged_version" != "$version" ]; then
    echo "Staged executable reports '$staged_version'; expected $version." >&2
    exit 1
fi

echo "Staged $version into packaging/npm/cli-$platform/hybro"
echo "Runs and reports $staged_version."
echo "Publish: npm publish packaging/npm/cli-$platform"
echo "Then:    npm publish packaging/npm/cli"
