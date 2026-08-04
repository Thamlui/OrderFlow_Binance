# Lightweight Python image optimized for Railway
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_FILE_WATCHER_TYPE=none

# Minimal system deps (psycopg2-binary usually enough; curl for optional health)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

# Railway injects $PORT at runtime
EXPOSE 8080

# Default: run Streamlit dashboard (override Start Command in Railway for collector service)
CMD ["sh", "-c", "streamlit run dashboard.py \
  --server.address=0.0.0.0 \
  --server.port=${PORT:-8080} \
  --server.headless=true \
  --server.fileWatcherType=none \
  --browser.gatherUsageStats=false \
  --server.enableCORS=false \
  --server.enableXsrfProtection=false"]
