FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8868 \
    WORKER_THREADS=24

# Install curl (used by live transit/geocoding scrapers) and nodejs (used by Trip.com SSR sandbox parser)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    nodejs \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m -u 10001 appuser && \
    mkdir -p /app/.cache && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8868

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8868/health || exit 1

CMD ["python3", "server.py"]
