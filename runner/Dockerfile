ARG RUNNER_BASE=ghcr.io/actions/actions-runner:latest
FROM ${RUNNER_BASE}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl wget jq git git-lfs zip unzip tar gzip xz-utils \
    build-essential pkg-config python3 python3-pip python3-venv python-is-python3 \
    rsync openssh-client postgresql-client libssl-dev libffi-dev \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
    libasound2t64 libpango-1.0-0 libcairo2 fonts-liberation \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /home/runner/_work /opt/hostedtoolcache \
    && chown -R runner:runner /home/runner/_work /opt/hostedtoolcache
# Fail the build instead of discovering a wrong/non-Debian base during CI.
RUN command -v apt-get && command -v sudo && command -v psql
# Official runner image already includes Docker CLI/buildx. Use Ubuntu's
# Compose v2 plugin for existing workflows that call `docker compose`.
RUN apt-get update && apt-get install -y --no-install-recommends docker-compose-v2 \
    && rm -rf /var/lib/apt/lists/*
COPY runner/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh
USER runner
WORKDIR /home/runner
ENV AGENT_TOOLSDIRECTORY=/opt/hostedtoolcache \
    RUNNER_TOOL_CACHE=/opt/hostedtoolcache \
    ImageOS=ubuntu24 \
    ACTIONS_RUNNER_PRINT_LOG_TO_STDOUT=1
ENTRYPOINT ["/entrypoint.sh"]
