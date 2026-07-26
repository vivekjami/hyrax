FROM python:3.11-slim

LABEL org.opencontainers.image.title="hyrax" \
      org.opencontainers.image.description="OpenLineage → OpenTelemetry bridge for data pipeline observability" \
      org.opencontainers.image.licenses="Apache-2.0"

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.4.18 /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency manifests first for layer caching
COPY pyproject.toml .

# Install only runtime deps (no dev extras)
RUN uv sync --no-dev --no-editable

# Copy application source
COPY hyrax/ hyrax/

# Non-root user for security
RUN adduser --disabled-password --gecos "" hyrax && chown -R hyrax /app
USER hyrax

EXPOSE 5050

HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5050/health')" || exit 1

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

CMD ["uv", "run", "python", "-m", "hyrax.listener"]
