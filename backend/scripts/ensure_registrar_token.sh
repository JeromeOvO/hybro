#!/bin/sh
set -eu

# Ensure DEFAULT_AGENT_REGISTRAR_TOKEN and AGENT_REGISTRAR_TOKEN are set to the
# same non-empty value in a single env file (repo-root .env).

env_file=${1:-}

if [ -z "$env_file" ] || [ ! -f "$env_file" ]; then
    echo "Error: pass an existing environment file" >&2
    exit 1
fi

# Read the current value of a variable from an env file ("" when unset/blank).
read_var() {
    awk -v key="$2" '
        $0 ~ "^[[:space:]]*" key "[[:space:]]*=" {
            sub(/^[^=]*=/, "")
            gsub(/^[[:space:]]+|[[:space:]]+$/, "")
            print
            exit
        }
    ' "$1"
}

# Set (or append) a variable in an env file, preserving everything else.
write_var() {
    target=$1
    key=$2
    value=$3

    temp_file="${target}.tmp.$$"
    trap 'rm -f "$temp_file"' 0 1 2 15

    awk -v key="$key" -v value="$value" '
        BEGIN { updated = 0 }
        $0 ~ "^[[:space:]]*" key "[[:space:]]*=" {
            if (!updated) {
                print key "=" value
                updated = 1
            }
            next
        }
        { print }
        END {
            if (!updated) {
                print ""
                print key "=" value
            }
        }
    ' "$target" > "$temp_file"

    chmod 600 "$temp_file"
    mv "$temp_file" "$target"
    trap - 0 1 2 15
}

backend_token=$(read_var "$env_file" DEFAULT_AGENT_REGISTRAR_TOKEN)
agents_token=$(read_var "$env_file" AGENT_REGISTRAR_TOKEN)

# Already configured and consistent - leave the existing secret alone.
if [ -n "$backend_token" ] && [ "$backend_token" = "$agents_token" ]; then
    exit 0
fi

# Adopt whichever side is already set so a hand-configured token survives;
# otherwise mint a new one.
if [ -n "$backend_token" ]; then
    token=$backend_token
elif [ -n "$agents_token" ]; then
    token=$agents_token
elif command -v openssl >/dev/null 2>&1; then
    token=$(openssl rand -hex 32)
elif [ -r /dev/urandom ]; then
    token=$(od -An -N32 -tx1 /dev/urandom | tr -d '[:space:]')
else
    echo "Error: openssl or /dev/urandom is required to generate the registrar token" >&2
    exit 1
fi

write_var "$env_file" DEFAULT_AGENT_REGISTRAR_TOKEN "$token"
write_var "$env_file" AGENT_REGISTRAR_TOKEN "$token"

echo "Configured a shared default-agent registrar token in $env_file"
