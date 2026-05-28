# syntax=docker/dockerfile:1.7
#
# Rev 12 SRE-14/15 — multi-stage build pinned to a specific Python patch.
# The builder stage compiles native wheels (asyncpg, bcrypt, scipy, etc) using
# the full toolchain. The runtime stage carries only the resolved site-packages
# under ``/home/appuser/.local`` plus the libpq runtime + curl for HEALTHCHECK,
# so the deploy artifact ships no compiler. Pinning to the exact 3.11 patch
# tag (3.11.9-slim-bookworm) prevents silent CVE-fix drift between rebuilds
# and keeps the runtime ABI stable for the wheels we already cached.

# ── Builder stage ───────────────────────────────────────────────────────────
FROM python:3.11.9-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential gcc libpq-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Install requirements into ``/root/.local`` so the runtime stage can copy
# the resolved site-packages without dragging the toolchain along. When a
# tool-generated ``requirements.lock.txt`` is present we install it with
# ``--require-hashes`` for supply-chain integrity (Rev 12 SRE-16); otherwise
# we fall back to ``requirements.txt`` so the build keeps working until the
# lockfile is generated. See OPS.md §7b for the pip-compile workflow.
COPY requirements.txt requirements.lock.txt* ./
RUN if [ -s requirements.lock.txt ]; then \
        pip install --user --no-cache-dir --require-hashes -r requirements.lock.txt; \
    else \
        pip install --user --no-cache-dir -r requirements.txt; \
    fi

# ── Runtime stage ───────────────────────────────────────────────────────────
FROM python:3.11.9-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Only the libpq runtime (so asyncpg/psycopg can resolve at import) and curl
# (for HEALTHCHECK below) — no compiler, no headers. Slimmer attack surface.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl libpq5 \
 && rm -rf /var/lib/apt/lists/*

# Run as a dedicated non-root user. uid 1001 is preserved from the previous
# single-stage Dockerfile so any host-side bind-mount permissions still work.
RUN groupadd --system --gid 1001 app \
 && useradd --system --uid 1001 --gid app --home-dir /home/appuser --create-home \
        --shell /bin/sh appuser

WORKDIR /app

# Copy the user-installed wheels from the builder stage. Owned by appuser so
# the non-root process can read them without further chown traversal.
COPY --from=builder --chown=appuser:app /root/.local /home/appuser/.local
COPY --chown=appuser:app . .

USER appuser
ENV PATH=/home/appuser/.local/bin:$PATH

EXPOSE 8000

# Container-level liveness probe. The /health endpoint stays at HTTP 200
# while the process is up; orchestrators can drive restart-on-unhealthy
# from this. The 60s start period gives Alembic + the forced startup
# pipeline tick time to finish before the first probe. (SRE-5)
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD curl -fsS http://localhost:8000/health || exit 1

# Run Alembic migrations, then start the API server.
# Force --loop asyncio: the databento Live client is incompatible with uvloop's
# transport implementation (raises "Connection does not support read-write
# operations" inside connection_made). Plain asyncio works fine.
#
# SRE-2: ``SKIP_MIGRATIONS=1`` lets operators bypass the alembic step on
# boot — useful when a deploy is failing on migration and the operator
# needs to break the boot loop while diagnosing. The default behaviour
# is unchanged.
#
# SRE-13: ``--proxy-headers`` is now opt-in via ``TRUST_PROXY_HEADERS=true``.
# Trusting X-Forwarded-* by default lets a remote client spoof their
# IP whenever the app is exposed without a stripping proxy in front.
# When enabled, ``--forwarded-allow-ips`` pins the trusted hop list
# (default 127.0.0.1) so only the configured edge can prepend.
CMD ["sh", "-c", "if [ \"$SKIP_MIGRATIONS\" != \"1\" ]; then alembic upgrade head; else echo 'Skipping migrations (SKIP_MIGRATIONS=1)'; fi && PROXY_FLAGS=''; case \"${TRUST_PROXY_HEADERS:-false}\" in true|1|TRUE|True) PROXY_FLAGS=\"--proxy-headers --forwarded-allow-ips=${FORWARDED_ALLOW_IPS:-127.0.0.1}\";; esac && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --loop asyncio $PROXY_FLAGS"]
