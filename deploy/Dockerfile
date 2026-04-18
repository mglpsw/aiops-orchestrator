FROM python:3.11-slim

LABEL maintainer="homelab-admin"
LABEL description="AIOps Orchestrator - AI-powered homelab management"

# Install Docker CLI for the docker executor adapter
# docker.io installs the daemon; docker-cli is the actual CLI binary
RUN apt-get update && apt-get install -y --no-install-recommends \
    docker-cli \
    && rm -rf /var/lib/apt/lists/*

# Security: run as non-root; socket access is granted via group_add in docker-compose
RUN groupadd -r aiops && useradd -r -g aiops -d /app -s /sbin/nologin aiops

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY app/ ./app/
COPY config/ ./config/

# Create data directory for SQLite
RUN mkdir -p /app/data && chown -R aiops:aiops /app

USER aiops

EXPOSE 8000 8010

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import httpx; r = httpx.get('http://localhost:8000/health', timeout=5); assert r.status_code == 200"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
