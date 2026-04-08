FROM python:3.11-slim

WORKDIR /app

# Install Tor for VPN routing of restricted platforms
RUN apt-get update && apt-get install -y tor --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy full app (UI dist is pre-built locally before deploy)
COPY . .

# Persistent data directory (mounted as Fly volume)
RUN mkdir -p /data

ENV DB_PATH=/data/bots.db
ENV LOG_DIR=/data

EXPOSE 8000

CMD ["python", "server.py"]
