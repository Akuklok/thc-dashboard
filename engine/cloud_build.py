"""
CLOUD BUILD  --  the PC-free daily backend (runs on GitHub Actions).

No computer needed:
  1. Pull the newest inventory report from the SFTP (and the buyer/pricing files if present).
  2. Build per-department orders + transfer plans.
  3. Extract the price reference (retail / margin / deal) from the buyer files.
  4. Build the per-item inventory snapshots (enriched with that pricing).
  (the workflow then commits data/ so the hosted app + dashboard read the fresh numbers.)

Buyer files are optional: if IT hasn't dropped them on the SFTP yet, step 3 is skipped and
the last pushed Price Reference is kept, so nothing breaks.
"""
import os, paramiko

HERE = os.path.dirname(os.path.abspath(__file__))     # repo/engine
ROOT = os.path.dirname(HERE)                           # repo root
WORK = os.path.join(ROOT, "work")
DATA = os.path.join(ROOT, "data")
BUYER_PREFIXES = ("thc", "liquor", "wine", "beer", "spirits")


def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


def sftp_pull():
    host = _env("SFTP_HOST"); user = _env("SFTP_USER"); pw = _env("SFTP_PASS")
    port = int(_env("SFTP_PORT", "22"))
    inv_path = _env("SFTP_PATH", "/Inventory-Sales/")
    price_path = _env("SFTP_PRICING_PATH", inv_path)   # default: same folder as the report
    if not (host and user and pw):
        raise SystemExit("Missing SFTP_HOST/USER/PASS secrets.")
    os.makedirs(WORK, exist_ok=True)
    t = paramiko.Transport((host, port)); t.connect(username=user, password=pw)
    sftp = paramiko.SFTPClient.from_transport(t)

    inv = [f for f in sftp.listdir_attr(inv_path)
           if "full inventory sales report" in f.filename.lower() and not f.filename.startswith(".")]
    if not inv:
        raise SystemExit("No inventory report on the SFTP server.")
    newest = max(inv, key=lambda f: f.st_mtime)
    sftp.get(inv_path.rstrip("/") + "/" + newest.filename, os.path.join(WORK, newest.filename))
    print("Report:", newest.filename, f"({newest.st_size:,} bytes)")

    # buyer / pricing workbooks (optional) - newest of each prefix
    try:
        cand = [f for f in sftp.listdir_attr(price_path)
                if f.filename.lower().endswith(".xlsx") and not f.filename.startswith(".")
                and any(f.filename.lower().startswith(p) for p in BUYER_PREFIXES)]
        picked = {}
        for f in sorted(cand, key=lambda f: f.st_mtime, reverse=True):
            pre = next(p for p in BUYER_PREFIXES if f.filename.lower().startswith(p))
            if pre not in picked:           # keep only the newest per prefix
                picked[pre] = f
                sftp.get(price_path.rstrip("/") + "/" + f.filename, os.path.join(WORK, f.filename))
                print("Buyer file:", f.filename)
        if not picked:
            print("No buyer/pricing files on SFTP yet - keeping last pushed Price Reference.")
    except Exception as e:
        print("Could not list buyer files:", e)
    sftp.close(); t.close()


def main():
    sftp_pull()
    os.makedirs(DATA, exist_ok=True)
    import daily_buying_brief as dbb
    import recommended_order as ro
    import extract_price_ref as ep
    import build_snapshot as bs
    import copy_product_tabs as cp
    dbb.INPUT_FOLDERS = [WORK]      # engine reads the report (and any product files) from here
    ro.OUT_FOLDERS = [DATA]
    ep.FOLDERS = [WORK]; ep.OUT = [DATA]
    cp.FOLDERS = [WORK]; cp.OUT = [DATA]
    ep.main()                       # buyer cost + deals + Remove/New lists from product files (if present)
    ro.main()                       # orders + transfer plans (excludes Remove/discontinued items)
    bs.main()                       # per-item snapshots: retail from report, cost/deals from product files
    cp.main()                       # mirror the buyers' product-list tabs (if product files present)
    # --- best-effort extras (never break the core orders/snapshots build) ---
    PARQUET = os.path.join(DATA, "thc_history.parquet")
    try:
        import build_history as bh, analyze_history as ah
        bh.FOLDERS = [WORK]; bh.CACHE = PARQUET
        bh.LOG = os.path.join(WORK, "history_log.txt")
        bh.main()                   # append today's report to the seasonality history cache
        ah.CACHE = PARQUET; ah.OUT_FOLDERS = [DATA]
        ah.main()                   # refresh THC History Insights.xlsx (seasonality)
    except (Exception, SystemExit) as e:
        print("Seasonality refresh skipped:", e)
    try:
        import batch_deal_eval as bd
        bd.BUYER_FOLDERS = [WORK]; bd.OUT_FOLDERS = [DATA]
        bd.main()                   # refresh THC Deal Evaluation.xlsx
    except (Exception, SystemExit) as e:
        print("Deal evaluation skipped:", e)
    try:
        import pull_wine_report as pwr   # wine report prepped from today's inventory pull (Inventory Calc, ready to paste)
        pwr.build(folders=[WORK], out_dir=DATA)
    except (Exception, SystemExit) as e:
        print("Wine report prep skipped:", e)
    try:
        import pull_open_pos as pop       # wine Open POs pulled + filtered from Cloud Retailer (needs CR_* env)
        pop.build(out_dir=DATA)
    except (Exception, SystemExit) as e:
        print("Open POs pull skipped:", e)
    print("Cloud build complete -> orders + snapshots in", DATA)


if __name__ == "__main__":
    main()
