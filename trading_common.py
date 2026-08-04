import os
import re
import duckdb
import psycopg2


def normalize_symbol(symbol, default="btcusdt"):
    if not symbol:
        symbol = default
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", str(symbol).strip())
    if not cleaned:
        cleaned = default
    return cleaned.lower()


def use_postgres():
    return bool(os.getenv("DATABASE_URL"))


def get_db_url():
    return os.getenv("DATABASE_URL")


def get_db_path(symbol, base_dir=None):
    base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
    normalized = normalize_symbol(symbol)
    return os.path.join(base_dir, f"trading_data_{normalized}.duckdb")


def get_db_connection(symbol=None, base_dir=None):
    if use_postgres():
        db_url = get_db_url()
        return psycopg2.connect(db_url, connect_timeout=5)
    return duckdb.connect(get_db_path(symbol, base_dir))


def resolve_symbol(symbol=None, default="btcusdt"):
    return normalize_symbol(symbol, default=default)
