FROM python:3.12-slim-bookworm AS build-deps

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libatlas-base-dev \
    libgdal-dev \
    gfortran \
    git

WORKDIR /opt/app
COPY pyproject.toml /opt/app/pyproject.toml

# * Compile bytecode to reduce startup time
# * Disable cache to reduce image size
ENV UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1 \
    UV_LINK_MODE=copy

RUN mkdir src && \
    uv sync --no-dev --no-install-project --no-editable

RUN rm -rf /opt/app/.venv/lib/python3.12/site-packages/**/tests


# --- App builder image --- #
FROM build-deps AS build-app

# * .git: Required for setuptools-git-versioning
COPY src /opt/app/src
COPY .git /opt/app/.git
COPY README.md /opt/app/README.md
RUN uv sync --no-dev --no-editable

# Delete package tests
RUN rm -rf /opt/app/.venv/lib/python3.12/site-packages/**/test_*

# --- Runtime image (use distroless if feasible for 100MB saving) --- #
FROM python:3.12-slim-bookworm AS runtime

WORKDIR /opt/app
# Copy just the virtual environment into a runtime image
COPY --from=build-app /opt/app/.venv /opt/app/.venv

# Health check and entrypoint
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:${PORT}/health || exit 1
ENTRYPOINT ["/opt/app/.venv/bin/quartz-api"]
