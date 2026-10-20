# syntax=docker/dockerfile:1.7
ARG RUNNER_BASE=ghcr.io/actions/actions-runner:latest
FROM ${RUNNER_BASE}
ARG TOOLCACHE_NODE="22 24"
ARG TOOLCACHE_GO="1.27"
ARG TOOLCACHE_PYTHON="3.11 3.12"
USER root
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl wget jq git git-lfs zip unzip tar gzip xz-utils \
    build-essential pkg-config python3 python3-pip python3-venv python-is-python3 \
    rsync openssh-client postgresql-client libssl-dev libffi-dev \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
    libasound2t64 libpango-1.0-0 libcairo2 fonts-liberation \
    docker-compose-v2 \
    && mkdir -p /home/runner/_work /opt/hostedtoolcache /home/runner/.cache/ms-playwright \
    && chown -R runner:runner /home/runner/_work /opt/hostedtoolcache /home/runner/.cache
# Fail the build instead of discovering a wrong/non-Debian base during CI.
RUN command -v apt-get && command -v sudo && command -v psql && docker compose version
# Common Node/Go/Python versions live in the tool cache so setup-* actions skip downloads.
COPY runner/toolcache.py /tmp/toolcache.py
RUN TOOLCACHE_NODE="$TOOLCACHE_NODE" TOOLCACHE_GO="$TOOLCACHE_GO" TOOLCACHE_PYTHON="$TOOLCACHE_PYTHON" \
    RUNNER_TOOL_CACHE=/opt/hostedtoolcache python3 /tmp/toolcache.py \
    && chown -R runner:runner /opt/hostedtoolcache && rm -f /tmp/toolcache.py
COPY runner/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh
USER runner
WORKDIR /home/runner
ENV AGENT_TOOLSDIRECTORY=/opt/hostedtoolcache \
    RUNNER_TOOL_CACHE=/opt/hostedtoolcache \
    ImageOS=ubuntu24 \
    ACTIONS_RUNNER_PRINT_LOG_TO_STDOUT=1
ENTRYPOINT ["/entrypoint.sh"]
