# Hybro AI - Open Source Edition

Hybro AI is a powerful, local-first multi-agent orchestration platform. This is the open-source release of the core engine that powers the Hybro Agent Network.

## Overview
Hybro AI allows you to run, manage, and coordinate a cluster of intelligent agents locally. It provides a developer-friendly API and a slick React frontend to visualize agent communication, monitor tasks, and manage execution rooms.

### Features
- **Local-First Architecture**: Run completely offline. No external dependencies, no API keys, no forced authentication.
- **Zero-Config Developer Mode**: Start the frontend and backend instantly with a mocked local developer account.
- **Multi-Agent Rooms**: Create complex agent interactions and workflows in dedicated execution rooms.
- **Protocol Agnostic**: Connects seamlessly with the Agent2Agent (A2A) Protocol SDK.

## Getting Started

### Prerequisites
- Docker and Docker Compose
- Node.js 20.9+ (if running the frontend outside of Docker)
- Python 3.12+ (if running the backend outside of Docker)

### Quick Start (Docker)
The easiest way to get started is using the automated installation script, which will clone the repository, set up the environment, and spin up the Docker containers.

```bash
curl -fsSL https://raw.githubusercontent.com/hybroai/hybro/main/install.sh | sh
```

Alternatively, you can manually clone and run:

```bash
git clone https://github.com/hybroai/hybro.git
cd hybro
docker compose up -d --build
```

- **Developer Portal**: http://localhost:3000
- **API Server**: http://localhost:8000

## Architecture
The repository is split into two primary components:
- `backend/`: A FastAPI application that serves as the orchestration engine, utilizing Redis for real-time pub/sub and MongoDB for persistence.
- `frontend/`: A Next.js 16 (Turbopack) application that provides the Developer Portal and Inspector UI.

## Contributing
We welcome contributions from the community! Whether you are fixing a bug, adding a feature, or improving documentation, please feel free to open a pull request.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'feat: add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License
MIT License
