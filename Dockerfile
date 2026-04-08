FROM python:3.11-slim

WORKDIR /app

# System deps — no Tor in cloud (Railway blocks it), just PySocks for proxy support
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy everything
COPY . .

# Build React UI inside container (ensures fresh build)
RUN cd ui && npm install --silent && npm run build --silent

ENV DB_PATH=/tmp/bots.db
ENV LOG_DIR=/tmp

EXPOSE 8000

CMD ["python", "server.py"]
