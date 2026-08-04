#!/bin/sh
export STREAMLIT_SERVER_PORT="${PORT:-8080}"
exec streamlit run dashboard.py --server.address=0.0.0.0
