#!/bin/sh
set -eu

# Generate the shared secret the one-shot default-agent registrar uses to call
# the protected /agent/registerAgent endpoint.
#
# The same random value is written to both sides under their respective names:
#   backend/.env         DEFAULT_AGENT_REGISTRAR_TOKEN
#   default_agents/.env  AGENT_REGISTRAR_TOKEN
#
# The backend compares the two with hmac.compare_digest (common/auth.py), so
# they must match exactly. An empty value disables service-token auth entirely,
# which makes every registration attempt fail with 401.
#
# Idempotent: if BOTH files already carry a non-empty value, nothing changes.
# Reused values are never printed.
#
# Usage: sh backend/scripts/ensure_registrar_token.sh backend/.env default_agents/.env

backend_env=${1:-}
agents_env=${2:-}

for env_file in "$backend_env" "$agents_env"; do
    if [ -z "$env_file" ] || [ ! -f "$env_file" ]; then
        echo "Error: pass two existing environment files" >&2
        exit 1
    fi
done

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
    env_file=$1
    key=$2
    value=$3

    temp_file="${env_file}.tmp.$$"
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
    ' "$env_file" > "$temp_file"

    chmod 600 "$temp_file"
    mv "$temp_file" "$env_file"
    trap - 0 1 2 15
}

backend_token=$(read_var "$backend_env" DEFAULT_AGENT_REGISTRAR_TOKEN)
agents_token=$(read_var "$agents_env" AGENT_REGISTRAR_TOKEN)

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

write_var "$backend_env" DEFAULT_AGENT_REGISTRAR_TOKEN "$token"
write_var "$agents_env" AGENT_REGISTRAR_TOKEN "$token"

echo "Configured a shared default-agent registrar token in $backend_env and $agents_env"
