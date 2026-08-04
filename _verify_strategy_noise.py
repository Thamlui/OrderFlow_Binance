import pandas as pd
from strategy import compute_strategy_signals

rows = []
for i in range(60):
    rows.append({
        'timestamp': i + 1,
        'price': 100 + ((i % 10) - 5) * 0.1,
        'quantity': 1.0 + (i % 3),
        'is_buyer_maker': False if i % 2 == 0 else True,
    })

df = pd.DataFrame(rows)
df['signed_qty'] = df.apply(lambda r: r['quantity'] if not r['is_buyer_maker'] else -r['quantity'], axis=1)
print(compute_strategy_signals(df))
