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
if [ ! -f backend/.env ]; then
    if [ -f backend/.env.example ]; then
        cp backend/.env.example backend/.env
        echo "Created backend/.env from example"
    fi
fi

if [ -f backend/.env ]; then
    sh backend/scripts/ensure_webhook_signing_key.sh backend/.env
fi

if [ ! -f frontend/.env.local ]; then
    if [ -f frontend/.env.example ]; then
        cp frontend/.env.example frontend/.env.local
        echo "Created frontend/.env.local from example"
    fi
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
