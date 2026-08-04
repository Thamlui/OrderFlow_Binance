# Deploy OrderFlow_Binance trên Railway.com

## Kiến trúc khuyến nghị (2 services)

Railway **không hỗ trợ tốt** multi-process Procfile kiểu Heroku. Hãy tạo **2 services** từ cùng một repo:

### 1. Service "web" (Dashboard Streamlit)
- **Root Directory**: `/` (mặc định)
- **Start Command**: để trống (dùng Dockerfile CMD) hoặc  
  `sh start.sh`
- **Variables**:
  - `DATABASE_URL` = (tự inject từ Postgres plugin)
  - (optional) `SYMBOL=btcusdt`

### 2. Service "collector" (Worker lấy dữ liệu Binance)
- **Start Command**:  
  `python Colector.py ${SYMBOL:-btcusdt} --no-ui`
- **Variables**:
  - `DATABASE_URL` = **cùng** Postgres plugin (share variable)
  - `SYMBOL=btcusdt` (hoặc ethusdt, solusdt...)

## Bước setup nhanh

1. Tạo Project mới trên Railway → Deploy from GitHub repo này.
2. Thêm **PostgreSQL** plugin vào project.
3. Trong service web: Settings → Variables → Reference `DATABASE_URL` từ Postgres.
4. Tạo service thứ 2 (New → Empty Service → Connect cùng GitHub repo).
5. Service 2 (collector): Settings → Start Command = `python Colector.py btcusdt --no-ui`
6. Service 2 cũng Reference cùng `DATABASE_URL`.
7. Deploy cả hai.

## Lưu ý quan trọng

- **Không dùng DuckDB trên Railway** (filesystem ephemeral → mất data khi restart). Code đã cảnh báo nếu thiếu `DATABASE_URL`.
- Collector service phải chạy **liên tục** để WebSocket Binance không bị đứt.
- Dashboard chỉ **đọc** DB; nút Start trong UI chỉ dành cho local / tạm thời.
- Port: Railway tự inject `$PORT` → Streamlit đã bind đúng `0.0.0.0:$PORT`.
- Healthcheck: để mặc định `/` hoặc tắt nếu bị 502.

## Biến môi trường hữu ích

| Biến | Ý nghĩa |
|------|---------|
| `DATABASE_URL` | Bắt buộc trên Railway (Postgres) |
| `SYMBOL` | Symbol mặc định (btcusdt) |
| `RAILWAY_*` | Tự có, code dùng để detect môi trường |

## Local vs Railway

- Local: có thể dùng DuckDB + Start collector từ UI.
- Railway: Postgres + 2 services (web + collector).


## Tối ưu PostgreSQL trên Railway

Code đã được tối ưu sẵn:

| Tối ưu | Chi tiết |
|--------|----------|
| **Connection pool** | `ThreadedConnectionPool` (min=1, max=4 mặc định). Tránh "too many clients". Điều chỉnh bằng env `PG_POOL_MAX`. |
| **SSL** | Tự thêm `sslmode=require` vào `DATABASE_URL` nếu thiếu. Override bằng env `PGSSLMODE=disable` (nếu dùng internal URL + lỗi SSL). |
| **Bulk insert** | `psycopg2.extras.execute_values` — nhanh hơn `executemany` nhiều lần. |
| **Index** | `(timestamp DESC)`, `(symbol, timestamp DESC)` — hỗ trợ query dashboard. |
| **agg_id** | Lưu Binance aggregate trade ID + partial unique index để giảm duplicate khi reconnect. |
| **Retry** | Tự retry khi SSL / timeout / connection reset / pool exhausted. |

### Biến môi trường Postgres hữu ích

```
DATABASE_URL=<từ Railway Postgres plugin>
PG_POOL_MAX=4          # số connection tối đa mỗi process (web + collector)
PGSSLMODE=require      # hoặc disable nếu internal network lỗi SSL handshake
SYMBOL=btcusdt
```

### Nếu gặp "too many clients already"

1. Giảm `PG_POOL_MAX=2` trên cả web và collector.
2. Chỉ chạy **1 replica** cho mỗi service.
3. (Nâng cao) Thêm template **PgBouncer** trên Railway và trỏ `DATABASE_URL` sang URL của PgBouncer.

### Internal vs Public URL

- Trong cùng project Railway: dùng **DATABASE_URL** (internal) — nhanh, không tính egress.
- Từ máy local: dùng **DATABASE_PUBLIC_URL** + `sslmode=require`.
