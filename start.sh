#!/bin/sh
set -e
PORT="${PORT:-8080}"
exec streamlit run dashboard.py \
  --server.address=0.0.0.0 \
  --server.port="$PORT" \
  --server.headless=true \
  --server.fileWatcherType=none \
  --browser.gatherUsageStats=false \
  --server.enableCORS=false \
  --server.enableXsrfProtection=false
