# syntax=docker/dockerfile:1.7

# ---------- Stage 1: build wheel cache ----------
FROM python:3.12-slim AS deps

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Copy only the dependency manifest first so this layer is cached unless deps change.
COPY pyproject.toml ./
# Bring in a stub README so setuptools is happy (it reads `readme = "README.md"`).
RUN echo "# placeholder" > README.md

# Install runtime deps into a venv so we can copy them cleanly into the final image.
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip && \
    /opt/venv/bin/pip install \
      "fastapi==0.115.0" \
      "uvicorn[standard]==0.30.6" \
      "pydantic==2.9.2" \
      "pydantic-settings==2.5.2" \
      "PyYAML==6.0.2" \
      "httpx==0.27.2" \
      "python-dotenv==1.0.1" \
      "SQLAlchemy[asyncio]==2.0.36" \
      "aiosqlite==0.20.0" \
      "Jinja2==3.1.4" \
      "greenlet==3.1.1"


# ---------- Stage 2: runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app/packages:/app/apps/server"

# Run as a non-root user — minimal hardening for default deployments.
RUN groupadd --system --gid 1001 app && \
    useradd  --system --uid 1001 --gid app --home /home/app --create-home app

WORKDIR /app

COPY --from=deps /opt/venv /opt/venv

# Copy source. .dockerignore strips secrets / tests / data from the build context.
COPY --chown=app:app apps ./apps
COPY --chown=app:app packages ./packages
COPY --chown=app:app configs ./configs
COPY --chown=app:app README.md ./README.md

# Persistent log lives here at runtime; declare as a volume for compose mounts.
RUN mkdir -p /app/data && chown -R app:app /app/data
VOLUME ["/app/data"]

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; \
    sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
