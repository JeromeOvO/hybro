#!/bin/sh
# Build the standalone hybro CLI for the host platform.
#
# Output: packaging/cli/dist/hybro/ (a PyInstaller onedir bundle). The npm
# platform packages ship exactly that directory, so this is the only build step
# between the Python sources and a published CLI.
set -eu

cd "$(dirname "$0")/../.."

requirements=""
while read -r requirement; do
    case "$requirement" in
        ""|\#*) continue ;;
    esac
    requirements="$requirements --with $requirement"
done < packaging/cli/requirements.txt

# shellcheck disable=SC2086  # $requirements is a deliberate list of uv flags.
exec uv run --no-project --python 3.12 $requirements --with pyinstaller -- \
    pyinstaller \
    --clean \
    --noconfirm \
    --distpath packaging/cli/dist \
    --workpath packaging/cli/build \
    packaging/cli/hybro.spec
