#!/bin/sh
PORT="${PORT:-8080}"
exec streamlit run dashboard.py --server.address=0.0.0.0 --server.port="$PORT"
