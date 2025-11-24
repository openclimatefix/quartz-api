FROM python:3.11-slim-bookworm

# install requirements
RUN apt-get clean
RUN apt-get update && apt-get install -y --no-install-recommends \
  build-essential \
  libatlas-base-dev \
  libgdal-dev \
  gfortran

# Copy required files.
WORKDIR /app
COPY pyproject.toml pyproject.toml
COPY .git .git
COPY src src
COPY README.md README.md

# set working directory
WORKDIR /app

# Install python requirements.
RUN pip install .

# health check and entrypoint
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:${PORT}/health || exit 1
ENTRYPOINT ["india-api"]
