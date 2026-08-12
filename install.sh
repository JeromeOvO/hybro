#!/bin/sh
set -e

echo "========================================"
echo "    Installing Hybro AI Open Source     "
echo "========================================"

# Check for git
if ! command -v git >/dev/null 2>&1; then
    echo "Error: git is required but not installed."
    echo "Please install git and try again."
    exit 1
fi

# Check for docker
if ! command -v docker >/dev/null 2>&1; then
    echo "Error: docker is required but not installed."
    echo "Please install Docker and try again."
    exit 1
fi

# Determine installation directory
INSTALL_DIR="${INSTALL_DIR:-$HOME/hybro}"

if [ -d "$INSTALL_DIR" ]; then
    echo "Directory $INSTALL_DIR already exists."
    echo "Pulling latest changes..."
    cd "$INSTALL_DIR"
    git pull
else
    echo "Cloning Hybro repository to $INSTALL_DIR..."
    git clone https://github.com/hybroai/hybro.git "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

echo "Setting up environment variables..."

# Read KEY=value from an env file (empty when unset/blank).
read_env_var() {
    env_path=$1
    key=$2
    if [ ! -f "$env_path" ]; then
        return 0
    fi
    awk -v key="$key" '
        $0 ~ "^[[:space:]]*" key "[[:space:]]*=" {
            sub(/^[^=]*=/, "")
            gsub(/^[[:space:]]+|[[:space:]]+$/, "")
            print
            exit
        }
    ' "$env_path"
}

# Append KEY=value to root .env when the root value is currently empty.
fill_root_var_from() {
    key=$1
    source_file=$2
    root_value=$(read_env_var .env "$key")
    if [ -n "$root_value" ]; then
        return 0
    fi
    source_value=$(read_env_var "$source_file" "$key")
    if [ -z "$source_value" ]; then
        return 0
    fi
    printf '\n%s=%s\n' "$key" "$source_value" >> .env
    echo "Merged $key from $source_file into .env"
}

# Promote a legacy backend/.env to the new repo-root .env when needed.
if [ ! -f .env ] && [ -f backend/.env ]; then
    cp backend/.env .env
    echo "Migrated backend/.env to repo-root .env"
fi

if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        echo "Created .env from .env.example"
        echo "NOTE: set OPENAI_API_KEY in .env so the backend and default agents can respond."
    fi
fi

# Pull any still-missing secrets from legacy per-service env files.
if [ -f .env ]; then
    if [ -f default_agents/.env ]; then
        fill_root_var_from OPENAI_API_KEY default_agents/.env
        fill_root_var_from AGENT_REGISTRAR_TOKEN default_agents/.env
        fill_root_var_from DEFAULT_AGENT_REGISTRAR_TOKEN default_agents/.env
        fill_root_var_from OPENAI_MODEL default_agents/.env
        fill_root_var_from IMAGE_MODEL default_agents/.env
        fill_root_var_from IMAGE_SIZE default_agents/.env
        # If only AGENT_REGISTRAR_TOKEN was merged, mirror it for the backend name.
        backend_token=$(read_env_var .env DEFAULT_AGENT_REGISTRAR_TOKEN)
        agents_token=$(read_env_var .env AGENT_REGISTRAR_TOKEN)
        if [ -z "$backend_token" ] && [ -n "$agents_token" ]; then
            printf '\nDEFAULT_AGENT_REGISTRAR_TOKEN=%s\n' "$agents_token" >> .env
        fi
        if [ -z "$agents_token" ] && [ -n "$backend_token" ]; then
            printf '\nAGENT_REGISTRAR_TOKEN=%s\n' "$backend_token" >> .env
        fi
    fi
    if [ -f frontend/.env.local ]; then
        # Migrate every documented frontend key so `ensure_frontend_env.sh` does
        # not silently drop values that used to live in `frontend/.env.local`.
        fill_root_var_from NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY frontend/.env.local
        fill_root_var_from CLERK_SECRET_KEY frontend/.env.local
        fill_root_var_from CLERK_WEBHOOK_SECRET frontend/.env.local
        fill_root_var_from NEXT_PUBLIC_CLERK_SIGN_IN_URL frontend/.env.local
        fill_root_var_from NEXT_PUBLIC_CLERK_SIGN_UP_URL frontend/.env.local
        fill_root_var_from NEXT_PUBLIC_CLERK_SIGN_IN_FALLBACK_REDIRECT_URL frontend/.env.local
        fill_root_var_from NEXT_PUBLIC_CLERK_SIGN_UP_FALLBACK_REDIRECT_URL frontend/.env.local
        fill_root_var_from NEXT_PUBLIC_API_BASE_URL frontend/.env.local
        fill_root_var_from NEXT_PUBLIC_API_PREFIX frontend/.env.local
        fill_root_var_from NEXT_PUBLIC_SERVER_URL frontend/.env.local
        fill_root_var_from NEXT_PUBLIC_ENABLE_WAITLIST frontend/.env.local
        fill_root_var_from NEXT_PUBLIC_MAX_MESSAGE_LENGTH frontend/.env.local
        fill_root_var_from NEXT_PUBLIC_INSPECTION_TIMEOUT_MS frontend/.env.local
        fill_root_var_from E2E_CLERK_USER_EMAIL frontend/.env.local
        fill_root_var_from E2E_CLERK_USER_PASSWORD frontend/.env.local
        fill_root_var_from E2E_TEST_ROOM_PATH frontend/.env.local
        # Back the legacy file up before ensure_frontend_env.sh overwrites it
        # so an operator can recover any custom keys we didn't know to migrate.
        if [ ! -f frontend/.env.local.legacy ]; then
            cp frontend/.env.local frontend/.env.local.legacy
            echo "Backed up frontend/.env.local -> frontend/.env.local.legacy"
        fi
    fi
fi

# Bootstrap complete. Hand off to the lifecycle CLI for the actual run.
# scripts/hybro owns retire_legacy_env, ensure_*, render_compose, and the
# banner - so day-2 `./scripts/hybro start` behaves the same as install.sh's
# initial run, except we force --build --recreate here to build fresh images
# and pick up the just-written .env.
exec sh scripts/hybro start --build --recreate
