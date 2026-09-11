# Generic Dockerfile that builds ANY default agent.


FROM python:3.12-slim

ARG AGENT_DIR
ENV AGENT_DIR=${AGENT_DIR}

# Pinned to match backend/Dockerfile; each agent image resolves from the same uv.
RUN pip install --no-cache-dir uv==0.7.13

WORKDIR /app/${AGENT_DIR}
COPY ${AGENT_DIR}/ /app/${AGENT_DIR}/
# Scoped JSON projection reader, with no dotenv discovery or Provider credentials.
COPY runtime_config.py /app/${AGENT_DIR}/runtime_config.py

RUN uv sync --no-install-project

ENV SERVER_HOST=0.0.0.0

CMD ["uv", "run", "--no-sync", "--no-env-file", "python", "__main__.py"]
