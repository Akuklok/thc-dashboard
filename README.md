# Top Ten Buyer

**The live app is in `buyer_client/`.** It is deployed to https://topten-buyer.onrender.com
via `render.yaml` (which runs `buyer_client/server.py`). This is the buying assistant the
buyers actually use.

## What's where
- `buyer_client/` - the live buying app (web + installable PWA). The one in use.
- `engine/` - the nightly/hourly backend: pulls the sales report over SFTP, builds the
  recommended orders and inventory snapshots, and the live sales feed (`cloud_retailer_api.py`).
- `data/` - the built data the app reads (orders, inventory snapshots, `live_sales.json`).
- `.github/workflows/` - scheduled jobs: `daily_build.yml` (nightly orders),
  `live_sales.yml` (hourly "today's sales").
- `feedback/` - beta feedback inbox storage.

## Retired (not part of the live app)
- `streamlit_app.py`, `all_departments_app.py`, and the root `requirements.txt` are an
  earlier Streamlit dashboard, kept for reference only. The live app does not use them.
