#!/bin/sh
set -eu

# Write a filtered frontend/.env.local from the repo-root .env for non-Docker
# `npm run dev`. Never copies backend/agent secrets (OPENAI_API_KEY, webhook
# signing key, registrar tokens, Mongo/Redis URLs, etc.).

root_env=${1:-}
frontend_env=${2:-}

if [ -z "$root_env" ] || [ ! -f "$root_env" ]; then
    echo "Error: pass an existing root environment file" >&2
    exit 1
fi

if [ -z "$frontend_env" ]; then
    echo "Error: pass a destination frontend/.env.local path" >&2
    exit 1
fi

frontend_dir=$(dirname "$frontend_env")
mkdir -p "$frontend_dir"

temp_file="${frontend_env}.tmp.$$"
trap 'rm -f "$temp_file"' 0 1 2 15

{
    echo "# Generated from repo-root .env for non-Docker frontend (npm run dev)."
    echo "# Do not hand-edit as the source of truth; edit the root .env instead."
    echo "# Docker Compose does not use this file."
    echo "#"
    awk '
        BEGIN { FS = "=" }
        /^[[:space:]]*#/ { next }
        /^[[:space:]]*$/ { next }
        {
            key = $1
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
            if (key ~ /^NEXT_PUBLIC_/ ||
                key == "CLERK_SECRET_KEY" ||
                key == "CLERK_WEBHOOK_SECRET" ||
                key ~ /^E2E_/) {
                print
            }
        }
    ' "$root_env"
} > "$temp_file"

chmod 600 "$temp_file"
mv "$temp_file" "$frontend_env"
trap - 0 1 2 15

echo "Wrote filtered frontend env to $frontend_env"
