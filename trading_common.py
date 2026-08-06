import os
import threading
import re
import atexit
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import duckdb
import psycopg2
from psycopg2 import pool
from psycopg2.extras import execute_values


def is_railway() -> bool:
    """Detect if running on Railway.com"""
    return bool(
        os.getenv("RAILWAY_ENVIRONMENT")
        or os.getenv("RAILWAY_PROJECT_ID")
        or os.getenv("RAILWAY_SERVICE_ID")
    )


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


def _normalize_database_url(url: str) -> str:
    """Ensure DATABASE_URL works well with Railway Postgres (self-signed SSL).

    - Adds sslmode=require if missing (needed for public / TCP proxy).
    - For internal *.railway.internal hosts, keep require (or allow override via PGSSLMODE).
    """
    if not url:
        return url

    # Allow explicit override
    force_sslmode = os.getenv("PGSSLMODE")  # e.g. require | disable | prefer
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    if force_sslmode:
        qs["sslmode"] = [force_sslmode]
    elif "sslmode" not in qs:
        # Default: require — works for most Railway public + internal cases
        qs["sslmode"] = ["require"]

    # Keep connect_timeout reasonable
    if "connect_timeout" not in qs:
        qs["connect_timeout"] = ["10"]

    new_query = urlencode({k: v[0] for k, v in qs.items()})
    return urlunparse(parsed._replace(query=new_query))


def get_db_path(symbol, base_dir=None):
    base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
    normalized = normalize_symbol(symbol)
    return os.path.join(base_dir, f"trading_data_{normalized}.duckdb")


# --- Connection pool (critical on Railway: limited max_connections) ---

_pg_pool = None
_pool_lock = threading.Lock()
# Web (Streamlit) needs more concurrent conns than collector; default 8 is safe on Railway
_POOL_MIN = 1
_POOL_MAX = int(os.getenv("PG_POOL_MAX", "8"))


def _init_pg_pool():
    global _pg_pool
    with _pool_lock:
        if _pg_pool is not None:
            return _pg_pool
        db_url = _normalize_database_url(get_db_url())
        if not db_url:
            raise RuntimeError("DATABASE_URL is not set")
        _pg_pool = pool.ThreadedConnectionPool(
            minconn=_POOL_MIN,
            maxconn=_POOL_MAX,
            dsn=db_url,
        )
        print(f"[DB] Postgres pool ready (min={_POOL_MIN}, max={_POOL_MAX})")
        return _pg_pool


def _close_pg_pool():
    global _pg_pool
    with _pool_lock:
        if _pg_pool is not None:
            try:
                _pg_pool.closeall()
            except Exception:
                pass
            _pg_pool = None


atexit.register(_close_pg_pool)


class _PooledConnection:
    """Context manager that returns connection to the pool on exit."""

    def __init__(self, conn, pool_ref):
        self.conn = conn
        self._pool = pool_ref

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn is None:
            return
        conn, self.conn = self.conn, None
        try:
            if exc_type is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            # putconn(close=True) discards broken connections instead of recycling them
            try:
                self._pool.putconn(conn, close=(exc_type is not None))
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
        except Exception:
            try:
                conn.close()
            except Exception:
                pass

    # Allow use without context manager (dashboard pattern)
    def close(self):
        self.__exit__(None, None, None)

    def cursor(self, *args, **kwargs):
        return self.conn.cursor(*args, **kwargs)

    def commit(self):
        return self.conn.commit()

    def rollback(self):
        return self.conn.rollback()

    def __getattr__(self, name):
        return getattr(self.conn, name)


def get_db_connection(symbol=None, base_dir=None):
    """Return a DB connection.

    - Postgres: pooled connection (must call .close() or use as context manager to return to pool).
    - DuckDB: normal file connection.
    """
    if use_postgres():
        last_err = None
        for attempt in range(3):
            try:
                p = _init_pg_pool()
                raw = p.getconn()
                raw.autocommit = False
                return _PooledConnection(raw, p)
            except Exception as e:
                last_err = e
                msg = str(e).lower()
                # Pool exhausted / closed → reset and retry
                if "pool" in msg or "exhausted" in msg or "closed" in msg:
                    print(f"[DB] pool issue ({e}) → reset pool (attempt {attempt + 1})")
                    _close_pg_pool()
                    import time as _time
                    _time.sleep(0.15 * (attempt + 1))
                    continue
                raise
        raise last_err

    if is_railway():
        print(
            "[WARNING] Running on Railway without DATABASE_URL. "
            "Data will NOT persist. Add a PostgreSQL plugin and set DATABASE_URL."
        )
    return duckdb.connect(get_db_path(symbol, base_dir))


def resolve_symbol(symbol=None, default="btcusdt"):
    return normalize_symbol(symbol, default=default)


def pg_execute_values(cur, sql, rows, page_size=200):
    """Fast bulk insert helper (psycopg2.extras.execute_values)."""
    execute_values(cur, sql, rows, page_size=page_size)
