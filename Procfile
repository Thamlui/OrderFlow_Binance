# Railway note: multi-process Procfile is NOT fully supported.
# Recommended setup on Railway:
#   1. Service "web"     → Start Command: (leave empty / use Dockerfile CMD) or `sh start.sh`
#   2. Service "collector" → Start Command: `python Colector.py ${SYMBOL:-btcusdt} --no-ui`
# Both services must share the same DATABASE_URL (Railway Postgres plugin).
web: sh start.sh
collector: python Colector.py ${SYMBOL:-btcusdt} --no-ui
