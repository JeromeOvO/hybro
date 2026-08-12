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
        fill_root_var_from NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY frontend/.env.local
        fill_root_var_from CLERK_SECRET_KEY frontend/.env.local
        fill_root_var_from CLERK_WEBHOOK_SECRET frontend/.env.local
        fill_root_var_from NEXT_PUBLIC_API_BASE_URL frontend/.env.local
        fill_root_var_from NEXT_PUBLIC_SERVER_URL frontend/.env.local
    fi
fi

# Retire legacy env files so they cannot shadow the root .env on host runs.
retire_legacy_env() {
    legacy=$1
    if [ -f "$legacy" ]; then
        mv "$legacy" "${legacy}.legacy"
        echo "Renamed $legacy -> ${legacy}.legacy (repo-root .env is the source of truth)"
    fi
}

if [ -f .env ]; then
    retire_legacy_env backend/.env
    retire_legacy_env default_agents/.env
    sh backend/scripts/ensure_webhook_signing_key.sh .env
    sh backend/scripts/ensure_registrar_token.sh .env
    sh backend/scripts/ensure_frontend_env.sh .env frontend/.env.local
fi

# The default-agent services in docker-compose.yml are generated from
# default_agents/agents.yaml, which is the single source of truth. Regenerate
# before starting anything so an edited manifest is reflected in this run -
# including for the one-shot registrar, which registers whatever Compose
# brought up. Only git and docker are guaranteed present, so fall back to a
# container when the host has no python3 + PyYAML.
echo "Generating default-agent services from default_agents/agents.yaml..."
if python3 -c "import yaml" >/dev/null 2>&1; then
    python3 default_agents/render_compose.py
elif command -v uv >/dev/null 2>&1; then
    uv run --with pyyaml python default_agents/render_compose.py
else
    echo "  (no host python3 + PyYAML; generating via Docker)"
    # --user keeps the rewritten file owned by the caller rather than root;
    # HOME must then point somewhere writable or pip's user install fails.
    docker run --rm \
        --user "$(id -u):$(id -g)" \
        -e HOME=/tmp \
        -v "$PWD:/repo" \
        -w /repo \
        python:3.12-slim \
        sh -c "pip install --quiet --disable-pip-version-check pyyaml \
               && python default_agents/render_compose.py"
fi

echo "Starting Docker containers..."
if docker compose version >/dev/null 2>&1; then
    docker compose up -d --build
elif docker-compose version >/dev/null 2>&1; then
    docker-compose up -d --build
else
    echo "Error: docker compose is not available."
    exit 1
fi

echo "========================================"
echo "Hybro AI is now running!"
echo "Hybro App: http://localhost:3000"
echo "API Server: http://localhost:8000"
echo "========================================"
