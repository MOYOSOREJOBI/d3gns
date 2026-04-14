FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

ARG APP_VERSION=dev
ARG BUILD_TIMESTAMP=unknown
ARG GIT_COMMIT=unknown

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev nodejs npm \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 appuser

# Python dependencies (including websocket-client for real-time Binance feed)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir websocket-client>=1.7.0

# Install UI deps separately for better build caching
COPY ui/package.json ui/package-lock.json /app/ui/
RUN cd ui && npm ci --silent

# Copy everything else
COPY . .

# Build React UI inside container (ensures fresh build)
RUN cd ui && npm run build --silent

RUN chown -R appuser:appuser /app

ENV DB_PATH=/data/bots.db
ENV LOG_DIR=/data
ENV APP_VERSION=${APP_VERSION}
ENV BUILD_TIMESTAMP=${BUILD_TIMESTAMP}
ENV GIT_COMMIT=${GIT_COMMIT}
# Bots start OFF — user must activate via dashboard or API
ENV LAB_AUTO_START=false
ENV MALL_AUTO_START=false
ENV REALTIME_AUTO_START=false
ENV PAPER_MODE=true

EXPOSE 8000

USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"

CMD ["python", "server.py"]
