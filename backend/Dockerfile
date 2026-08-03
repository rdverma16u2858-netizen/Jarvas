# Backend container image.
# ---------------------------------------------------------------------------
# Multi-stage: the `builder` stage compiles wheels (which needs a C toolchain
# for asyncpg), the `runtime` stage copies only the finished packages. The
# compiler never ships, which keeps the image smaller and removes build tools
# an attacker could otherwise use inside a compromised container.

# --- Stage 1: build wheels -------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

# gcc and libpq-dev are needed to build asyncpg from source when no prebuilt
# wheel matches the platform.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy ONLY requirements first. Docker caches each layer, so this expensive
# step is skipped on every rebuild where dependencies did not change — copying
# the source first would invalidate the cache on every code edit.
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# --- Stage 2: runtime ------------------------------------------------------
FROM python:3.12-slim AS runtime

# PYTHONDONTWRITEBYTECODE  no .pyc files (pointless in an immutable image)
# PYTHONUNBUFFERED         flush stdout immediately, or `docker logs` lags
# PYTHONPATH               lets `import app.*` work without installing a package
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# Install from the wheels built in stage 1 — no compiler in this image.
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

COPY . .

# Run as a non-root user. If the app is compromised, the attacker lands as an
# unprivileged account rather than root inside the container.
RUN useradd --create-home --shell /bin/bash mathbot \
    && chown -R mathbot:mathbot /app
USER mathbot

EXPOSE 8000

# Lets Docker and orchestrators see the app is alive. Hits /health/live, which
# checks no dependencies — a database blip must not cause a container restart.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/v1/health/live').status==200 else 1)"

# Overridden by docker-compose in development to add --reload.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
