# Generic Dockerfile that builds ANY default agent.


FROM python:3.12-slim

ARG AGENT_DIR
ENV AGENT_DIR=${AGENT_DIR}

RUN pip install --no-cache-dir uv

WORKDIR /app/${AGENT_DIR}
COPY ${AGENT_DIR}/ /app/${AGENT_DIR}/
# Host/dev helper also used when agents are started outside Compose; harmless in
# containers where Compose already injects process env.
COPY load_repo_env.py /app/${AGENT_DIR}/load_repo_env.py

RUN uv sync --no-install-project

ENV SERVER_HOST=0.0.0.0

CMD ["uv", "run", "--no-sync", "python", "__main__.py"]
