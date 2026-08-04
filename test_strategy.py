import pandas as pd

from strategy import compute_strategy_signals


def test_long_signal_when_trend_and_volume_align():
    rows = []
    for i in range(60):
        price = 100 + i * 0.3
        quantity = 1.0 + (i % 5)
        side = False
        rows.append({"timestamp": i + 1, "price": price, "quantity": quantity, "is_buyer_maker": side})

    df = pd.DataFrame(rows)
    df["signed_qty"] = df.apply(lambda r: r["quantity"] if not r["is_buyer_maker"] else -r["quantity"], axis=1)
    result = compute_strategy_signals(df)
    assert result["signal"] == "long"
    assert result["entry_price"] > 0
    assert result["stop_loss"] < result["entry_price"]
    assert result["take_profit"] > result["entry_price"]


def test_short_signal_when_bearish_conditions_hold():
    rows = []
    for i in range(60):
        price = 200 - i * 0.3
        quantity = 1.0 + (i % 5)
        side = True
        rows.append({"timestamp": i + 1, "price": price, "quantity": quantity, "is_buyer_maker": side})

    df = pd.DataFrame(rows)
    df["signed_qty"] = df.apply(lambda r: r["quantity"] if not r["is_buyer_maker"] else -r["quantity"], axis=1)
    result = compute_strategy_signals(df)
    assert result["signal"] == "short"
    assert result["entry_price"] > 0
    assert result["stop_loss"] > result["entry_price"]
    assert result["take_profit"] < result["entry_price"]


def test_noise_regime_stays_on_wait():
    rows = []
    for i in range(60):
        price = 100 + ((i % 10) - 5) * 0.1
        quantity = 1.0 + (i % 3)
        side = False if i % 2 == 0 else True
        rows.append({"timestamp": i + 1, "price": price, "quantity": quantity, "is_buyer_maker": side})

    df = pd.DataFrame(rows)
    df["signed_qty"] = df.apply(lambda r: r["quantity"] if not r["is_buyer_maker"] else -r["quantity"], axis=1)
    result = compute_strategy_signals(df)
    assert result["signal"] == "none"
