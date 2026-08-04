import argparse
import asyncio
import json
import os
import random
import subprocess
import sys
import time

import duckdb
import websockets

from trading_common import get_db_connection, get_db_path, get_db_url, normalize_symbol, use_postgres, is_railway, pg_execute_values

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SYMBOL = "btcusdt"
BATCH_SIZE = 120


def init_db(symbol=None, base_dir=None):
    symbol = normalize_symbol(symbol, default=DEFAULT_SYMBOL)
    if use_postgres():
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Base table (compatible with previous deployments)
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS trades (
                        timestamp       BIGINT NOT NULL,
                        symbol          VARCHAR(32) NOT NULL,
                        price           DOUBLE PRECISION NOT NULL,
                        quantity        DOUBLE PRECISION NOT NULL,
                        is_buyer_maker  BOOLEAN NOT NULL
                    )
                    """
                )
                # Add agg_id for deduplication (Binance aggregate trade ID)
                cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS agg_id BIGINT")
                # Indexes optimized for dashboard queries (ORDER BY timestamp DESC LIMIT, symbol filter)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades (timestamp DESC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_trades_symbol_ts ON trades (symbol, timestamp DESC)"
                )
                # Unique constraint for dedup — only if no conflicting duplicates
                cur.execute(
                    """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_constraint WHERE conname = 'uq_trades_symbol_agg_id'
                        ) THEN
                            BEGIN
                                -- Partial unique: ignore NULL / 0 agg_id from legacy rows
                                CREATE UNIQUE INDEX IF NOT EXISTS uq_trades_symbol_agg_id
                                    ON trades (symbol, agg_id)
                                    WHERE agg_id IS NOT NULL AND agg_id > 0;
                            EXCEPTION WHEN others THEN
                                RAISE NOTICE 'Skip unique index: %', SQLERRM;
                            END;
                        END IF;
                    END $$;
                    """
                )
            conn.commit()
        db_path = get_db_url()
        print(f"[SUCCESS] Postgres database sẵn sàng (pooled) tại: {db_path}")
        return db_path

    db_path = get_db_path(symbol, base_dir or BASE_DIR)
    with duckdb.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                timestamp UBIGINT,
                symbol VARCHAR,
                price DOUBLE,
                quantity DOUBLE,
                is_buyer_maker BOOLEAN
            )
            """
        )
    print(f"[SUCCESS] Database sẵn sàng tại: {db_path}")
    return db_path


def save_trades_batch(db_path, trade_batch, max_retries=7):
    if not trade_batch:
        return

    # trade_batch items: (agg_id, timestamp, symbol, price, qty, is_buyer_maker)
    # DuckDB path still uses 5-tuple without agg_id for backward compat
    is_pg = use_postgres()

    for attempt in range(max_retries):
        try:
            if is_pg:
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        # Fast bulk insert via execute_values
                        sql = """
                            INSERT INTO trades (agg_id, timestamp, symbol, price, quantity, is_buyer_maker)
                            VALUES %s
                        """
                        pg_execute_values(cur, sql, trade_batch, page_size=150)
                    conn.commit()
            else:
                # DuckDB: drop agg_id if present (5-tuple expected)
                duck_batch = [
                    (t[1], t[2], t[3], t[4], t[5]) if len(t) == 6 else t
                    for t in trade_batch
                ]
                query = "INSERT INTO trades (timestamp, symbol, price, quantity, is_buyer_maker) VALUES (?, ?, ?, ?, ?)"
                with duckdb.connect(db_path) as conn:
                    conn.executemany(query, duck_batch)
            return
        except Exception as e:
            err = str(e).lower()
            if any(x in err for x in (
                "being used by another process",
                "cannot open file",
                "connection",
                "server closed",
                "ssl",
                "timeout",
                "too many clients",
            )):
                sleep_time = 0.25 + attempt * 0.2 + random.uniform(0, 0.15)
                print(f"[DB RETRY {attempt+1}/{max_retries}] {e}")
                time.sleep(sleep_time)
                # Reset pool on severe connection errors
                if is_pg and attempt >= 2:
                    try:
                        from trading_common import _close_pg_pool
                        _close_pg_pool()
                    except Exception:
                        pass
            else:
                print(f"[DB ERROR] {e}")
                return
    print(f"[DB WARNING] Bỏ qua 1 batch sau {max_retries} lần thử")


class OrderFlowEngine:
    def __init__(self, symbol, db_path):
        self.symbol = normalize_symbol(symbol, default=DEFAULT_SYMBOL).upper()
        self.db_path = db_path
        self.trade_buffer = []
        self.counter = 0

    def process_trade(self, trade):
        try:
            timestamp = int(trade["T"])
            price = float(trade["p"])
            qty = float(trade["q"])
            is_buyer_maker = trade["m"]
            # Aggregate trade ID from Binance (used for dedup on reconnect)
            agg_id = int(trade.get("a") or 0)

            self.counter += 1
            if self.counter <= 10 or self.counter % 100 == 0:
                print(f"[DATA] #{self.counter} | {self.symbol} | Giá: {price} | Qty: {qty}")

            # (agg_id, timestamp, symbol, price, qty, is_buyer_maker)
            self.trade_buffer.append((agg_id, timestamp, self.symbol, price, qty, is_buyer_maker))

            if len(self.trade_buffer) >= BATCH_SIZE:
                save_trades_batch(self.db_path, self.trade_buffer)
                self.trade_buffer.clear()

        except Exception as e:
            print(f"[PROCESS ERROR] {e}")

    def flush(self):
        if self.trade_buffer:
            save_trades_batch(self.db_path, self.trade_buffer)
            self.trade_buffer.clear()


def launch_dashboard(symbol):
    print("[INFO] Đang khởi động Streamlit Dashboard...")
    dashboard_path = os.path.join(BASE_DIR, "dashboard.py")
    subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", dashboard_path, "--server.headless", "true", "--server.port", "8501", "--", symbol],
        cwd=BASE_DIR,
    )
    print("[INFO] Dashboard đã được mở.\n")


def run_collector_loop(symbol=None, stop_event=None, launch_ui=True):
    symbol = normalize_symbol(symbol, default=DEFAULT_SYMBOL)
    db_path = init_db(symbol)

    # On Railway never spawn Streamlit from collector process
    if is_railway():
        launch_ui = False
        print("[INFO] Detected Railway environment → running as pure collector (no UI)")

    if launch_ui:
        launch_dashboard(symbol)
        time.sleep(1.5)

    uri = f"wss://fstream.binance.com/market/ws/{symbol}@aggTrade"
    engine = OrderFlowEngine(symbol, db_path)

    print(f"Đang kết nối tới: {uri}")
    print(f"Symbol đang chạy: {symbol.upper()}")

    while True:
        if stop_event and stop_event.is_set():
            break
        try:
            async def receive_messages():
                async with websockets.connect(uri, ping_interval=20, ping_timeout=20) as ws:
                    print("[SUCCESS] Kết nối WebSocket thành công! Đang nhận dữ liệu...\n")
                    while not (stop_event and stop_event.is_set()):
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        data = json.loads(msg)
                        engine.process_trade(data)

            asyncio.run(receive_messages())

        except Exception as e:
            print(f"[WS ERROR] {e} → Thử lại sau 5 giây...")
            engine.flush()
            if stop_event and stop_event.is_set():
                break
            time.sleep(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect Binance futures aggregate trades into a DuckDB database")
    parser.add_argument("symbol", nargs="?", default=os.getenv("SYMBOL", DEFAULT_SYMBOL), help="Symbol ví dụ: btcusdt, ethusdt, solusdt")
    parser.add_argument("--no-ui", action="store_true", help="Không khởi động Streamlit dashboard (dùng cho process collector)")
    args = parser.parse_args()

    try:
        run_collector_loop(args.symbol, launch_ui=not args.no_ui)
    except KeyboardInterrupt:
        print("\n[INFO] Đang tắt chương trình...")
