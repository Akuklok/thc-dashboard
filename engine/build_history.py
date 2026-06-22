"""
BUILD THC SALES HISTORY  --  turns 500+ daily inventory snapshots into one
clean daily sales history (real velocity + seasonality, not a 1-day guess).

Each daily "Full Inventory Sales Report - YYYY-MM-DD.xlsx" carries 'Sold 1D'
(units sold that day) and 'TSold1D' ($ that day) per product per store. This
script reads every day, keeps THC, sums across stores, and stores one row per
product per day in a fast Parquet cache.

- First run reads the whole archive (~1 hour). It CHECKPOINTS every 40 files,
  so if it stops you can just run it again and it resumes.
- After that it's instant: it only reads days not already in the cache (the
  daily pipeline adds today's file).
"""
import glob, os, re, datetime
import pandas as pd

FOLDERS = [r"C:\Users\Anna K\OneDrive - Top Ten Liquors\Top Ten OneDrive - Reports - New Version",
           r"C:\Users\Anna K\OneDrive - Top Ten Liquors\Documents",
           r"C:\Users\Anna K\Downloads"]
PATTERN = "Full Inventory Sales Report*.xlsx"   # excludes the old "New Inventory" format
CACHE   = r"C:\Users\Anna K\OneDrive - Top Ten Liquors\Documents\Claude\thc_history.parquet"
LOG     = r"C:\Users\Anna K\OneDrive - Top Ten Liquors\Documents\Claude\history_build_log.txt"
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
CHECKPOINT_EVERY = 40

def log(msg):
    line = f"{datetime.datetime.now():%H:%M:%S}  {msg}"
    print(line)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def file_date(path):
    m = DATE_RE.search(os.path.basename(path))
    return m.group(1) if m else None

def newest_by_date():
    """date string -> file path (one file per date; prefer the synced archive)."""
    best = {}
    for folder in FOLDERS:
        for f in glob.glob(os.path.join(folder, PATTERN)):
            d = file_date(f)
            if d and d not in best:      # FOLDERS order = priority
                best[d] = f
    return best

def extract(path, date):
    df = pd.read_excel(path, sheet_name=0, engine="calamine")
    if "Department" not in df.columns:
        return None
    df = df[df["Department"].astype(str).str.contains("THC", case=False, na=False)]
    from config_locations import drop_warehouses
    df = drop_warehouses(df)   # exclude warehouse stock from chain-wide history
    if df.empty:
        return None
    for c in ("Sold 1D", "TSold1D", "OH"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    g = df.groupby("Product Code").agg(
        **{"Product Description": ("Product Description", "first"),
           "Category": ("Category", "first"),
           "Units": ("Sold 1D", "sum"),
           "Revenue": ("TSold1D", "sum"),
           "OnHand": ("OH", "sum")}).reset_index()
    g.insert(0, "Date", pd.Timestamp(date))
    return g

def main():
    have = set()
    if os.path.exists(CACHE):
        old = pd.read_parquet(CACHE)
        have = set(old["Date"].dt.strftime("%Y-%m-%d"))
        log(f"cache has {len(have)} days already")
    else:
        old = None

    files = newest_by_date()
    todo = sorted(d for d in files if d not in have)
    log(f"{len(todo)} new day(s) to read")

    batch, done = [], 0
    for i, d in enumerate(todo, 1):
        try:
            g = extract(files[d], d)
            if g is not None:
                batch.append(g)
                done += 1
        except Exception as e:
            log(f"  SKIP {d}: {e}")
        if i % CHECKPOINT_EVERY == 0 or i == len(todo):
            if batch:
                add = pd.concat(batch, ignore_index=True)
                old = add if old is None else pd.concat([old, add], ignore_index=True)
                old = old.drop_duplicates(["Date", "Product Code"], keep="last")
                old.to_parquet(CACHE, index=False)
                batch = []
            log(f"  {i}/{len(todo)} processed (cache now {old['Date'].nunique()} days)")

    if old is not None:
        log(f"DONE: {old['Date'].nunique()} days | {len(old):,} rows | "
            f"{old['Date'].min():%Y-%m-%d} -> {old['Date'].max():%Y-%m-%d}")
        log(f"saved -> {CACHE}")

if __name__ == "__main__":
    main()
