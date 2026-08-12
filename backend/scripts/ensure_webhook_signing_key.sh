#!/bin/sh
set -eu

env_file=${1:-}

if [ -z "$env_file" ] || [ ! -f "$env_file" ]; then
    echo "Error: pass an existing environment file" >&2
    exit 1
fi

existing_value=$(
    awk '
        /^[[:space:]]*WEBHOOK_SIGNING_KEY[[:space:]]*=/ {
            sub(/^[^=]*=/, "")
            gsub(/^[[:space:]]+|[[:space:]]+$/, "")
            print
            exit
        }
    ' "$env_file"
)

if [ -n "$existing_value" ]; then
    exit 0
fi

if command -v openssl >/dev/null 2>&1; then
    signing_key=$(openssl rand -hex 32)
elif [ -r /dev/urandom ]; then
    signing_key=$(od -An -N32 -tx1 /dev/urandom | tr -d '[:space:]')
else
    echo "Error: openssl or /dev/urandom is required to generate WEBHOOK_SIGNING_KEY" >&2
    exit 1
fi

temp_file="${env_file}.tmp.$$"
trap 'rm -f "$temp_file"' 0 1 2 15

awk -v signing_key="$signing_key" '
    BEGIN {
        updated = 0
    }
    /^[[:space:]]*WEBHOOK_SIGNING_KEY[[:space:]]*=/ {
        if (!updated) {
            print "WEBHOOK_SIGNING_KEY=" signing_key
            updated = 1
        }
        next
    }
    {
        print
    }
    END {
        if (!updated) {
            print ""
            print "WEBHOOK_SIGNING_KEY=" signing_key
        }
    }
' "$env_file" > "$temp_file"

chmod 600 "$temp_file"
mv "$temp_file" "$env_file"
trap - 0 1 2 15

echo "Generated a persistent WEBHOOK_SIGNING_KEY in $env_file"
