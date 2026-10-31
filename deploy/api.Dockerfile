# syntax=docker/dockerfile:1.7
# Rulecam API image (WS5): Python 3.12, the locked runtime deps (uv.lock, no dev group), ffmpeg 7
# (Debian trixie; >= 5.1 is needed for -fps_mode, Ubuntu 22.04's 4.4 broke the planner), tini.
# Built and pushed by .github/workflows/images.yml as ghcr.io/jeevithg090/rulecam-api.
#
#   docker build -f deploy/api.Dockerfile -t rulecam-api .
#   docker run -p 8000:8000 -v rulecam:/workspace rulecam-api
#
# State, secrets and .env live under RULECAM_HOME (/workspace/rulecam) on the volume; see
# deploy/api-entrypoint.sh. The server runs as the unprivileged `rulecam` user.
# Base tags are pinned and at least two weeks old when chosen.
ARG PYTHON_IMAGE=python:3.12.14-slim-trixie

FROM ghcr.io/astral-sh/uv:0.13.1 AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
# Dependencies first (cached until uv.lock changes), then the project itself, non-editable.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM ${PYTHON_IMAGE}
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tini openssh-server \
    && rm -rf /var/lib/apt/lists/* /etc/ssh/ssh_host_* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin rulecam \
    && mkdir -p /workspace/rulecam \
    && chown rulecam: /workspace/rulecam
COPY --from=build /opt/venv /opt/venv
COPY deploy/entrypoint-lib.sh deploy/api-entrypoint.sh /opt/rulecam/deploy/
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    RULECAM_HOME=/workspace/rulecam \
    RULECAM_PORT=8000
WORKDIR /workspace/rulecam
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=6s --start-period=60s --retries=3 \
    CMD ["/opt/rulecam/deploy/api-entrypoint.sh", "healthcheck"]
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/rulecam/deploy/api-entrypoint.sh"]
CMD ["serve"]
