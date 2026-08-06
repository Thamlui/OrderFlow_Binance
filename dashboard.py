import os
import threading
import time

import duckdb
import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from Colector import init_db, run_collector_loop
from strategy import compute_volume_profile
from trading_common import get_db_connection, get_db_path, normalize_symbol, use_postgres, is_railway

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SYMBOL = "btcusdt"

st.set_page_config(page_title="Trading Dashboard", page_icon="⚡", layout="wide")
st.title("⚡ Binance Futures Order Flow Dashboard")

if "symbol" not in st.session_state:
    st.session_state.symbol = DEFAULT_SYMBOL
if "active_symbol" not in st.session_state:
    st.session_state.active_symbol = DEFAULT_SYMBOL
if "collector_thread" not in st.session_state:
    st.session_state.collector_thread = None
if "collector_stop_event" not in st.session_state:
    st.session_state.collector_stop_event = None


def start_collector(symbol):
    normalized = normalize_symbol(symbol, default=DEFAULT_SYMBOL)
    if (
        st.session_state.collector_thread is not None
        and st.session_state.collector_thread.is_alive()
        and st.session_state.active_symbol == normalized
    ):
        return

    if st.session_state.collector_stop_event is not None:
        st.session_state.collector_stop_event.set()
        if st.session_state.collector_thread is not None:
            st.session_state.collector_thread.join(timeout=1)

    stop_event = threading.Event()
    thread = threading.Thread(target=run_collector_loop, args=(normalized, stop_event, False), daemon=True)
    thread.start()

    st.session_state.collector_thread = thread
    st.session_state.collector_stop_event = stop_event
    st.session_state.active_symbol = normalized


def stop_collector():
    if st.session_state.collector_stop_event is not None:
        st.session_state.collector_stop_event.set()
    if st.session_state.collector_thread is not None:
        st.session_state.collector_thread.join(timeout=3)
    st.session_state.collector_thread = None
    st.session_state.collector_stop_event = None
    st.session_state.active_symbol = None


def open_db_connection(path=None, symbol=None, max_retries=5):
    for attempt in range(max_retries):
        try:
            if use_postgres():
                return get_db_connection(symbol=symbol)
            return duckdb.connect(path)
        except Exception as e:
            msg = str(e).lower()
            if any(t in msg for t in [
                "different configuration",
                "being used by another process",
                "can't open a connection",
                "cannot open file",
                "could not connect to server",
                "connection pool exhausted",
                "too many clients",
                "ssl",
                "timeout",
                "server closed the connection",
            ]):
                time.sleep(0.25 + attempt * 0.15)
                continue
            raise
    raise RuntimeError("Không thể mở database sau nhiều lần thử.")


def _pg_read_df(conn, sql, params=None):
    """Read SQL into DataFrame without pandas/SQLAlchemy warning (raw psycopg2)."""
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=cols)


def fetch_recent_trades(conn, limit=8000):
    if use_postgres():
        return _pg_read_df(
            conn,
            "SELECT timestamp, price, quantity, is_buyer_maker FROM trades ORDER BY timestamp DESC LIMIT %s",
            (limit,),
        )
    return conn.execute(
        """
        SELECT timestamp, price, quantity, is_buyer_maker
        FROM trades
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        [limit],
    ).fetchdf()


def prepare_trade_dataframe(df):
    df["side"] = np.where(df["is_buyer_maker"], "Sell", "Buy")
    df["buy_qty"] = np.where(~df["is_buyer_maker"], df["quantity"], 0.0)
    df["sell_qty"] = np.where(df["is_buyer_maker"], df["quantity"], 0.0)
    df["signed_qty"] = df["buy_qty"] - df["sell_qty"]
    df = df.iloc[::-1].reset_index(drop=True)
    df["cvd"] = df["signed_qty"].cumsum()
    return df


with st.sidebar:
    st.header("🔧 Điều khiển")
    symbol_input = st.text_input("Nhập Symbol", value=st.session_state.symbol, help="Ví dụ: BTCUSDT, ETHUSDT, SOLUSDT")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Start / Switch"):
            st.session_state.symbol = normalize_symbol(symbol_input, default=DEFAULT_SYMBOL)
            start_collector(st.session_state.symbol)
            st.rerun()
    with col2:
        if st.button("Stop"):
            stop_collector()
            st.rerun()

    if is_railway():
        st.caption("🚂 Railway mode | Dùng PostgreSQL (DATABASE_URL) | Collector nên chạy service riêng")
    else:
        st.caption("Mỗi symbol có DB riêng (DuckDB) hoặc dùng chung PostgreSQL nếu có DATABASE_URL")


symbol = st.session_state.symbol
init_db(symbol)
db_path = get_db_path(symbol, BASE_DIR)
db_display = "PostgreSQL (DATABASE_URL)" if use_postgres() else db_path

st.caption(f"Đang theo dõi: {symbol.upper()} | Database: {db_display}")

if is_railway() and use_postgres():
    st.info("🚂 Railway: Nên chạy **Collector** như một service riêng (Start Command: `python Colector.py btcusdt --no-ui`). Nút Start bên dưới chỉ dùng tạm thời trong web process.")
elif is_railway() and not use_postgres():
    st.error("⚠️ Railway + DuckDB = dữ liệu sẽ mất khi restart. Hãy thêm PostgreSQL plugin và set biến DATABASE_URL.")

if st.session_state.collector_thread is None or not st.session_state.collector_thread.is_alive():
    st.info("Chưa có collector chạy trong process này. Hãy nhập symbol rồi nhấn Start / Switch (hoặc dùng service collector riêng).")
else:
    st.success(f"Collector đang chạy cho {symbol.upper()}.")


@st.fragment(run_every=2)
def render_live_dashboard(symbol_name):
    conn = open_db_connection(db_path, symbol=symbol)
    try:
        if use_postgres():
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM trades")
                total_trades = int(cur.fetchone()[0])
        else:
            total_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        st.write(f"**Tổng số trade đã ghi:** {total_trades}")

        if total_trades == 0:
            st.warning("Chưa có dữ liệu. Hãy đợi vài giây để collector ghi tín hiệu vào database.")
            return

        LONG_TERM_TRADE_COUNT = 8000
        df = fetch_recent_trades(conn, LONG_TERM_TRADE_COUNT)
        df = prepare_trade_dataframe(df)

        latest_ts = int(df["timestamp"].iloc[-1])
        one_day_ms = 24 * 60 * 60 * 1000
        if use_postgres():
            df_long = _pg_read_df(
                conn,
                "SELECT timestamp, price, quantity, is_buyer_maker FROM trades WHERE timestamp >= %s ORDER BY timestamp ASC",
                (latest_ts - one_day_ms,),
            )
        else:
            df_long = conn.execute(
                """
                SELECT timestamp, price, quantity, is_buyer_maker
                FROM trades
                WHERE timestamp >= ?
                ORDER BY timestamp ASC
                """,
                [latest_ts - one_day_ms],
            ).fetchdf()
        if df_long.empty:
            df_long = df.copy()
        else:
            df_long = prepare_trade_dataframe(df_long)

        last_price = float(df.iloc[-1]["price"])
        latest_cvd = float(df_long.iloc[-1]["cvd"])
        fifteen_min_ms = 15 * 60 * 1000
        df_short = df[df["timestamp"] >= (latest_ts - fifteen_min_ms)]

        short_buy = float(df_short["buy_qty"].sum())
        short_sell = float(df_short["sell_qty"].sum())
        short_delta = short_buy - short_sell
        short_vol = short_buy + short_sell
        short_buy_pct = (short_buy / short_vol * 100) if short_vol > 0 else 0
        short_sell_pct = (short_sell / short_vol * 100) if short_vol > 0 else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Last Price", f"{last_price:,.2f}")
        c2.metric("Delta (15 phút)", f"{short_delta:+.3f}")
        c3.metric("CVD (24h)", f"{latest_cvd:,.2f}")
        c4.metric("Volume (15 phút)", f"{short_vol:,.3f}")

        st.subheader("📊 Volume Dominance - 15 phút gần nhất")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("### 🟢 Buy Volume")
            st.metric("Khối lượng Mua", f"{short_buy:,.3f}")
            st.progress(min(short_buy_pct / 100, 1.0))
            st.write(f"**{short_buy_pct:.1f}%**")
        with col2:
            st.markdown("### 🔴 Sell Volume")
            st.metric("Khối lượng Bán", f"{short_sell:,.3f}")
            st.progress(min(short_sell_pct / 100, 1.0))
            st.write(f"**{short_sell_pct:.1f}%**")

        if short_delta > 0:
            st.success(f"🟢 Buy đang áp đảo | Delta 15p: +{short_delta:.3f}")
        elif short_delta < 0:
            st.error(f"🔴 Sell đang áp đảo | Delta 15p: {short_delta:.3f}")
        else:
            st.info("⚖️ Hai bên cân bằng trong 15 phút gần nhất")

        st.subheader("📊 Volume Profile Buy / Sell")
        # Prefer longer history (df_long ~24h) so 4H and 1D profiles have enough ticks
        vp_source = df_long if not df_long.empty else df
        vp_timeframes = ["30m", "1H", "4H", "1D"]
        vp_results = {tf: compute_volume_profile(vp_source, timeframe=tf) for tf in vp_timeframes}

        vp_cols = st.columns(4)
        for idx, tf in enumerate(vp_timeframes):
            vp = vp_results[tf]
            with vp_cols[idx]:
                bias = vp.get("bias", "neutral")
                if bias == "buy":
                    st.success(f"{tf} → BUY dominant")
                elif bias == "sell":
                    st.error(f"{tf} → SELL dominant")
                else:
                    st.info(f"{tf} → NEUTRAL")

                if vp.get("vpoc") is not None:
                    st.caption(f"VPOC: {vp['vpoc']:.2f}")
                    st.caption(f"VA: {vp['val']:.2f} – {vp['vah']:.2f}")
                st.caption(f"Buy: {vp['total_buy']:,.3f} ({vp['buy_pct']:.1f}%)")
                st.caption(f"Sell: {vp['total_sell']:,.3f} ({vp['sell_pct']:.1f}%)")
                st.caption(f"Delta: {vp['delta']:+.3f}")

                if vp.get("buy_zones"):
                    z = vp["buy_zones"][0]
                    st.caption(
                        f"🟢 Buy zone: {z['price_low']:.2f}–{z['price_high']:.2f} | V: {z['buy_vol']:.3f}"
                    )
                if vp.get("sell_zones"):
                    z = vp["sell_zones"][0]
                    st.caption(
                        f"🔴 Sell zone: {z['price_low']:.2f}–{z['price_high']:.2f} | V: {z['sell_vol']:.3f}"
                    )
                st.caption(f"Ticks: {vp.get('bars_used', 0)}")

        # Summary strip across timeframes
        buy_tfs = [tf for tf, v in vp_results.items() if v.get("bias") == "buy"]
        sell_tfs = [tf for tf, v in vp_results.items() if v.get("bias") == "sell"]
        if len(buy_tfs) >= 3:
            st.success(f"🟢 Volume Profile nghiêng Buy trên: {', '.join(buy_tfs)}")
        elif len(sell_tfs) >= 3:
            st.error(f"🔴 Volume Profile nghiêng Sell trên: {', '.join(sell_tfs)}")
        elif buy_tfs and not sell_tfs:
            st.success(f"🟢 Buy bias: {', '.join(buy_tfs)}")
        elif sell_tfs and not buy_tfs:
            st.error(f"🔴 Sell bias: {', '.join(sell_tfs)}")
        else:
            st.info("⚖️ Volume Profile lẫn lộn giữa các khung — chưa có bias rõ")

        st.subheader("📈 Cumulative Volume Delta")
        fig = px.line(df, x="timestamp", y="cvd", template="plotly_dark")
        fig.update_layout(height=420, margin=dict(t=20, b=20))
        st.plotly_chart(fig, width="stretch")

        with st.expander("Xem 20 trade gần nhất"):
            st.dataframe(df.tail(20)[["timestamp", "price", "quantity", "side", "signed_qty"]], width="stretch")

        conn.close()
    except Exception as e:
        st.error(f"Lỗi đọc Database: {e}")


render_live_dashboard(symbol)
