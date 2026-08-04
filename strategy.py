import numpy as np
import pandas as pd


def _compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    # Fix: when avg_loss == 0 (pure uptrend), RSI must be 100, not 0
    rs = avg_gain / avg_loss
    rsi = np.where(
        avg_loss == 0,
        np.where(avg_gain > 0, 100.0, 50.0),
        100.0 - (100.0 / (1.0 + rs)),
    )
    return pd.Series(rsi, index=series.index).fillna(50)


def _evaluate_row(df: pd.DataFrame, idx: int):
    latest = df.iloc[idx]
    prev = df.iloc[idx - 1] if idx > 0 else latest

    trend_bullish = latest["ema_fast"] > latest["ema_slow"]
    trend_bearish = latest["ema_fast"] < latest["ema_slow"]

    price_change_3 = latest["price_change_3"]
    cvd_change_3 = latest["cvd_change_3"]
    rsi = latest["rsi"]
    volume_confirm = latest["quantity"] >= latest["avg_quantity"]
    price_momentum = latest["price"] - prev["price"]

    zone_long = latest["price"] >= latest["support_zone"] and latest["price"] <= latest["support_zone"] * 1.003
    zone_short = latest["price"] <= latest["resistance_zone"] and latest["price"] >= latest["resistance_zone"] * 0.997

    score_long = 0
    score_short = 0

    if trend_bullish:
        score_long += 1.5
    if trend_bearish:
        score_short += 1.5
    if price_change_3 > 0.001:
        score_long += 1.5
    if price_change_3 < -0.001:
        score_short += 1.5
    if cvd_change_3 > 0.1:
        score_long += 1.5
    if cvd_change_3 < -0.1:
        score_short += 1.5
    if 55 <= rsi <= 70:
        score_long += 1
    if 30 <= rsi <= 45:
        score_short += 1
    if price_momentum > 0:
        score_long += 1
    if price_momentum < 0:
        score_short += 1
    if volume_confirm:
        score_long += 0.5
        score_short += 0.5
    if zone_long:
        score_long += 1.5
    if zone_short:
        score_short += 1.5

    # Threshold 6: strong trend+momentum+cvd+volume already scores 6 even without RSI/zone points
    if score_long >= 6 and score_long >= score_short + 1.5:
        return "long"
    if score_short >= 6 and score_short >= score_long + 1.5:
        return "short"
    return "none"


def _prepare_timeframe_features(df: pd.DataFrame):
    df = df.copy()
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["cvd"] = df["signed_qty"].cumsum()
    df["ema_fast"] = df["price"].ewm(span=10, adjust=False).mean()
    df["ema_slow"] = df["price"].ewm(span=30, adjust=False).mean()
    df["price_change_3"] = df["price"].pct_change(3)
    df["cvd_change_3"] = df["cvd"].diff(3)
    df["rsi"] = _compute_rsi(df["price"], period=14)
    df["avg_quantity"] = df["quantity"].rolling(window=10, min_periods=10).mean()
    df["support_zone"] = df["price"].rolling(window=20, min_periods=20).min()
    df["resistance_zone"] = df["price"].rolling(window=20, min_periods=20).max()
    return df


def _timeframe_to_ms(timeframe: str) -> int:
    if timeframe.endswith("m"):
        return int(timeframe[:-1]) * 60 * 1000
    if timeframe.endswith("H") or timeframe.endswith("h"):
        return int(timeframe[:-1]) * 60 * 60 * 1000
    return 15 * 60 * 1000


def _orderblock_settings(timeframe: str):
    tf = timeframe.upper()
    settings = {
        "15M": {"imbalance_threshold": 0.58, "lookback": 3, "top_bars": 2, "candidate_mult": 4},
        "30M": {"imbalance_threshold": 0.55, "lookback": 3, "top_bars": 2, "candidate_mult": 4},
        "1H": {"imbalance_threshold": 0.52, "lookback": 4, "top_bars": 2, "candidate_mult": 5},
        "4H": {"imbalance_threshold": 0.48, "lookback": 5, "top_bars": 3, "candidate_mult": 5},
    }
    return settings.get(tf, settings["15M"])


def compute_order_blocks(df: pd.DataFrame, timeframe: str = "15m"):
    if df.empty:
        return {
            "timeframe": timeframe,
            "sma": None,
            "vpoc": None,
            "buy_zone": None,
            "sell_zone": None,
            "buy_volume": 0.0,
            "sell_volume": 0.0,
            "buy_strength": 0.0,
            "sell_strength": 0.0,
            "zone_candles": 0,
        }

    settings = _orderblock_settings(timeframe)
    imbalance_threshold = settings["imbalance_threshold"]
    lookback = settings["lookback"]
    top_bars = settings["top_bars"]
    candidate_bars_count = lookback * settings["candidate_mult"]

    df = df.copy().sort_values("timestamp").reset_index(drop=True)
    df["signed_qty"] = np.where(df["is_buyer_maker"], -df["quantity"], df["quantity"])
    df["buy_qty"] = np.where(df["signed_qty"] > 0, df["quantity"], 0.0)
    df["sell_qty"] = np.where(df["signed_qty"] < 0, df["quantity"], 0.0)
    df["price_qty"] = df["price"] * df["quantity"]
    period_ms = _timeframe_to_ms(timeframe)
    df["bucket"] = (df["timestamp"] // period_ms) * period_ms

    bars = df.groupby("bucket", observed=True).agg(
        timestamp=("timestamp", "min"),
        open=("price", "first"),
        high=("price", "max"),
        low=("price", "min"),
        close=("price", "last"),
        buy_qty=("buy_qty", "sum"),
        sell_qty=("sell_qty", "sum"),
        volume=("quantity", "sum"),
        price_qty=("price_qty", "sum"),
    ).reset_index()
    bars["vwap"] = bars["price_qty"] / bars["volume"].replace({0: 1.0})
    bars["sma"] = bars["close"].rolling(window=lookback, min_periods=1).mean()
    bars["imbalance"] = bars["buy_qty"] - bars["sell_qty"]
    bars["imbalance_ratio"] = bars["imbalance"] / bars["volume"].replace({0: 1.0})

    vpoc_df = df.groupby(["bucket", "price"], observed=True)["quantity"].sum().reset_index()
    vpoc_df = vpoc_df.sort_values(["bucket", "quantity"], ascending=[True, False])
    vpoc_prices = vpoc_df.drop_duplicates(subset=["bucket"], keep="first").set_index("bucket")["price"]
    bars = bars.merge(vpoc_prices.rename("vpoc_price"), left_on="bucket", right_index=True, how="left")

    last_bar = bars.iloc[-1]
    confirmed_buy = last_bar["close"] > last_bar["sma"] and last_bar["imbalance_ratio"] >= imbalance_threshold
    confirmed_sell = last_bar["close"] < last_bar["sma"] and last_bar["imbalance_ratio"] <= -imbalance_threshold

    vpoc_price = float(last_bar["vpoc_price"]) if not np.isnan(last_bar["vpoc_price"]) else float(last_bar["vwap"])
    sma = float(last_bar["sma"])

    candidate_bars = bars.tail(candidate_bars_count).copy()
    candidate_bars["score"] = candidate_bars["imbalance_ratio"].abs() * candidate_bars["volume"]
    candidate_bars["recency_score"] = np.linspace(0.5, 1.0, len(candidate_bars))
    candidate_bars["combined_score"] = candidate_bars["score"] * candidate_bars["recency_score"]

    if confirmed_buy:
        strong_bars = candidate_bars[
            (candidate_bars["imbalance_ratio"] >= imbalance_threshold)
            & (candidate_bars["close"] > candidate_bars["sma"])
        ]
        strong_bars = strong_bars.sort_values(["combined_score", "volume"], ascending=[False, False])
    elif confirmed_sell:
        strong_bars = candidate_bars[
            (candidate_bars["imbalance_ratio"] <= -imbalance_threshold)
            & (candidate_bars["close"] < candidate_bars["sma"])
        ]
        strong_bars = strong_bars.sort_values(["combined_score", "volume"], ascending=[False, False])
    else:
        strong_bars = pd.DataFrame()

    if not strong_bars.empty:
        top_bars = strong_bars.head(max(1, min(len(strong_bars), top_bars)))
        zone_low = float(top_bars["low"].min())
        zone_high = float(top_bars["high"].max())
        buy_zone = (zone_low, zone_high) if confirmed_buy else None
        sell_zone = (zone_low, zone_high) if confirmed_sell else None
        zone_candles = len(top_bars)
    else:
        buy_zone = None
        sell_zone = None
        zone_candles = 0

    return {
        "timeframe": timeframe,
        "sma": round(sma, 2),
        "vpoc": round(vpoc_price, 2),
        "buy_zone": buy_zone,
        "sell_zone": sell_zone,
        "zone_candles": zone_candles,
        "buy_volume": float(last_bar["buy_qty"]),
        "sell_volume": float(last_bar["sell_qty"]),
        "buy_strength": round(last_bar["imbalance_ratio"] * 100, 1) if confirmed_buy else 0.0,
        "sell_strength": round(last_bar["imbalance_ratio"] * 100, 1) if confirmed_sell else 0.0,
    }


def get_timeframe_context(df: pd.DataFrame, timeframe: str = "15m"):
    df = _prepare_timeframe_features(df)
    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else latest
    trend_strength = ((latest["price"] - prev["price"]) / prev["price"] * 100) if prev["price"] else 0.0
    trend_bias = "bullish" if latest["ema_fast"] > latest["ema_slow"] else "bearish"

    return {
        "timeframe": timeframe,
        "price": float(latest["price"]),
        "support_zone": float(latest["support_zone"]),
        "resistance_zone": float(latest["resistance_zone"]),
        "trend_strength": round(trend_strength, 2),
        "trend_bias": trend_bias,
        "rsi": round(float(latest["rsi"]), 2),
        "cvd": round(float(latest["cvd"]), 2),
    }


def compute_strategy_signals(df: pd.DataFrame, timeframe="15m"):
    if df.empty:
        return {
            "signal": "none",
            "entry_price": None,
            "stop_loss": None,
            "take_profit": None,
            "reason": "Không đủ dữ liệu",
        }

    df = df.copy()
    df = df.sort_values("timestamp").reset_index(drop=True)

    if "price" not in df.columns or "quantity" not in df.columns:
        raise ValueError("DataFrame phải có cột price và quantity")

    if "signed_qty" not in df.columns:
        df["signed_qty"] = df.apply(lambda r: r["quantity"] if not r["is_buyer_maker"] else -r["quantity"], axis=1)

    df = _prepare_timeframe_features(df)

    if len(df) < 8:
        return {
            "signal": "none",
            "entry_price": float(df.iloc[-1]["price"]),
            "stop_loss": None,
            "take_profit": None,
            "reason": "Không đủ dữ liệu để xác nhận",
        }

    start_idx = max(0, len(df) - 4)
    recent_signals = [_evaluate_row(df, idx) for idx in range(start_idx, len(df))]

    long_count = recent_signals.count("long")
    short_count = recent_signals.count("short")

    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else latest
    entry_price = float(latest["price"])

    trend_long = latest["ema_fast"] > latest["ema_slow"] and prev["ema_fast"] >= prev["ema_slow"]
    trend_short = latest["ema_fast"] < latest["ema_slow"] and prev["ema_fast"] <= prev["ema_slow"]
    momentum_strength = abs((latest["price"] - prev["price"]) / prev["price"] * 100) if prev["price"] else 0.0
    choppy = abs(latest["price_change_3"]) < 0.001 and abs(latest["cvd_change_3"]) < 0.1

    if long_count >= 3 and latest["price"] > prev["price"] and trend_long and momentum_strength >= 0.03 and not choppy:
        signal = "long"
    elif short_count >= 3 and latest["price"] < prev["price"] and trend_short and momentum_strength >= 0.03 and not choppy:
        signal = "short"
    else:
        signal = "none"

    if signal == "long":
        stop_loss = entry_price - max(0.5, entry_price * 0.004)
        take_profit = entry_price + max(1.0, entry_price * 0.008)
        return {
            "signal": "long",
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "reason": f"Trend + momentum + flow đồng thuận trên {timeframe}",
            "trend_strength": round(((entry_price - prev["price"]) / prev["price"] * 100) if prev["price"] else 0.0, 2),
        }

    if signal == "short":
        stop_loss = entry_price + max(0.5, entry_price * 0.004)
        take_profit = entry_price - max(1.0, entry_price * 0.008)
        return {
            "signal": "short",
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "reason": f"Trend + momentum + flow đồng thuận trên {timeframe}",
            "trend_strength": round(((entry_price - prev["price"]) / prev["price"] * 100) if prev["price"] else 0.0, 2),
        }

    return {
        "signal": "none",
        "entry_price": entry_price,
        "stop_loss": None,
        "take_profit": None,
        "reason": "Không đủ tín hiệu xác nhận",
    }
