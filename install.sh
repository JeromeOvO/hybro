#!/bin/sh
set -e

echo "Installing Hybro"
for command in git docker uv; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Error: $command is required; install it and rerun." >&2
        exit 1
    fi
done
INSTALL_DIR="${INSTALL_DIR:-$HOME/hybro}"
if [ -d "$INSTALL_DIR" ]; then
    cd "$INSTALL_DIR"
    git pull
else
    git clone https://github.com/hybroai/hybro.git "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi
# Never create, merge, rename or delete user environment files.
echo "Configure models with ./scripts/hybro setup, then run ./scripts/hybro start --build."
echo "Existing YAML setup: ./scripts/hybro config migrate --from-env .env (before removing the old files)."
if [ -t 0 ] && [ -t 1 ]; then
    exec sh scripts/hybro
fi
