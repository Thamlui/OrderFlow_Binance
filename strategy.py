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
    tf = str(timeframe).strip().upper()
    if tf in ("1D", "1DAY", "D", "DAY"):
        return 24 * 60 * 60 * 1000
    if timeframe.endswith("m"):
        return int(timeframe[:-1]) * 60 * 1000
    if timeframe.endswith("H") or timeframe.endswith("h"):
        return int(timeframe[:-1]) * 60 * 60 * 1000
    if tf.endswith("M"):
        return int(tf[:-1]) * 60 * 1000
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


def compute_volume_profile(df: pd.DataFrame, timeframe: str = "30m", n_bins: int = 24):
    """Volume Profile Buy/Sell for a rolling window (30m, 1H, 4H, 1D).

    Builds price bins over the last `timeframe` window, aggregates buy/sell volume,
    finds VPOC and approximate Value Area (70%), and ranks buy/sell dominant zones.
    """
    empty = {
        "timeframe": timeframe,
        "vpoc": None,
        "vah": None,
        "val": None,
        "total_buy": 0.0,
        "total_sell": 0.0,
        "total_volume": 0.0,
        "delta": 0.0,
        "buy_pct": 0.0,
        "sell_pct": 0.0,
        "bias": "neutral",
        "buy_zones": [],
        "sell_zones": [],
        "poc_buy_vol": 0.0,
        "poc_sell_vol": 0.0,
        "bars_used": 0,
    }

    if df is None or df.empty:
        return empty

    work = df.copy()
    if "buy_qty" not in work.columns or "sell_qty" not in work.columns:
        if "is_buyer_maker" in work.columns:
            work["buy_qty"] = np.where(~work["is_buyer_maker"], work["quantity"], 0.0)
            work["sell_qty"] = np.where(work["is_buyer_maker"], work["quantity"], 0.0)
        elif "signed_qty" in work.columns:
            work["buy_qty"] = np.where(work["signed_qty"] > 0, work["quantity"], 0.0)
            work["sell_qty"] = np.where(work["signed_qty"] < 0, work["quantity"], 0.0)
        else:
            work["buy_qty"] = work["quantity"] * 0.5
            work["sell_qty"] = work["quantity"] * 0.5

    work = work.sort_values("timestamp").reset_index(drop=True)
    latest_ts = int(work["timestamp"].iloc[-1])
    period_ms = _timeframe_to_ms(timeframe)
    window = work[work["timestamp"] >= (latest_ts - period_ms)].copy()
    if window.empty:
        window = work.tail(min(500, len(work))).copy()

    if window.empty:
        return empty

    price_min = float(window["price"].min())
    price_max = float(window["price"].max())
    if not np.isfinite(price_min) or not np.isfinite(price_max):
        return empty

    if price_max <= price_min:
        mid = price_min
        total_buy = float(window["buy_qty"].sum())
        total_sell = float(window["sell_qty"].sum())
        total = total_buy + total_sell
        bias = "buy" if total_buy > total_sell * 1.05 else ("sell" if total_sell > total_buy * 1.05 else "neutral")
        empty.update({
            "vpoc": round(mid, 2),
            "vah": round(mid, 2),
            "val": round(mid, 2),
            "total_buy": total_buy,
            "total_sell": total_sell,
            "total_volume": total,
            "delta": total_buy - total_sell,
            "buy_pct": (total_buy / total * 100) if total > 0 else 0.0,
            "sell_pct": (total_sell / total * 100) if total > 0 else 0.0,
            "bias": bias,
            "bars_used": len(window),
        })
        return empty

    n_bins = max(8, min(int(n_bins), 48))
    bins = np.linspace(price_min, price_max, n_bins + 1)
    mids = (bins[:-1] + bins[1:]) / 2.0
    labels = list(range(n_bins))
    window["bin_id"] = pd.cut(
        window["price"], bins=bins, labels=labels, include_lowest=True, duplicates="drop"
    )

    grouped = window.groupby("bin_id", observed=True).agg(
        buy_vol=("buy_qty", "sum"),
        sell_vol=("sell_qty", "sum"),
        total_vol=("quantity", "sum"),
    )
    grouped = grouped.reindex(labels, fill_value=0.0)
    grouped["mid"] = mids
    grouped["delta"] = grouped["buy_vol"] - grouped["sell_vol"]

    total_buy = float(grouped["buy_vol"].sum())
    total_sell = float(grouped["sell_vol"].sum())
    total = total_buy + total_sell

    poc_idx = int(grouped["total_vol"].idxmax()) if grouped["total_vol"].sum() > 0 else 0
    vpoc = float(grouped.loc[poc_idx, "mid"])
    poc_buy = float(grouped.loc[poc_idx, "buy_vol"])
    poc_sell = float(grouped.loc[poc_idx, "sell_vol"])

    # Value Area ~70% volume expanding from POC
    target = total * 0.70
    lo = hi = poc_idx
    cum = float(grouped.loc[poc_idx, "total_vol"])
    while cum < target and (lo > 0 or hi < n_bins - 1):
        left_vol = float(grouped.loc[lo - 1, "total_vol"]) if lo > 0 else -1.0
        right_vol = float(grouped.loc[hi + 1, "total_vol"]) if hi < n_bins - 1 else -1.0
        if right_vol >= left_vol and hi < n_bins - 1:
            hi += 1
            cum += max(right_vol, 0.0)
        elif lo > 0:
            lo -= 1
            cum += max(left_vol, 0.0)
        else:
            break
    val = float(grouped.loc[lo, "mid"])
    vah = float(grouped.loc[hi, "mid"])

    buy_sorted = grouped.sort_values("buy_vol", ascending=False).head(3)
    sell_sorted = grouped.sort_values("sell_vol", ascending=False).head(3)

    def _zones(frame, side: str):
        out = []
        for idx, row in frame.iterrows():
            vol = float(row["buy_vol"] if side == "buy" else row["sell_vol"])
            if vol <= 0:
                continue
            i = int(idx)
            out.append({
                "price_low": round(float(bins[i]), 2),
                "price_high": round(float(bins[i + 1]), 2),
                "mid": round(float(row["mid"]), 2),
                "buy_vol": round(float(row["buy_vol"]), 4),
                "sell_vol": round(float(row["sell_vol"]), 4),
                "total_vol": round(float(row["total_vol"]), 4),
                "delta": round(float(row["delta"]), 4),
            })
        return out

    buy_zones = _zones(buy_sorted, "buy")
    sell_zones = _zones(sell_sorted, "sell")

    if total_buy > total_sell * 1.08:
        bias = "buy"
    elif total_sell > total_buy * 1.08:
        bias = "sell"
    else:
        bias = "neutral"

    return {
        "timeframe": timeframe,
        "vpoc": round(vpoc, 2),
        "vah": round(vah, 2),
        "val": round(val, 2),
        "total_buy": round(total_buy, 4),
        "total_sell": round(total_sell, 4),
        "total_volume": round(total, 4),
        "delta": round(total_buy - total_sell, 4),
        "buy_pct": round(total_buy / total * 100, 1) if total > 0 else 0.0,
        "sell_pct": round(total_sell / total * 100, 1) if total > 0 else 0.0,
        "bias": bias,
        "buy_zones": buy_zones,
        "sell_zones": sell_zones,
        "poc_buy_vol": round(poc_buy, 4),
        "poc_sell_vol": round(poc_sell, 4),
        "bars_used": int(len(window)),
    }
