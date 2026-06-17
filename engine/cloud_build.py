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
    import build_snapshot as bs
    dbb.INPUT_FOLDERS = [WORK]      # engine reads the report from here
    ro.OUT_FOLDERS = [DATA]
    ro.main()                       # orders + transfer plans
    bs.main()                       # per-item snapshots (retail/cost/margin from the report itself)
    print("Cloud build complete -> orders + snapshots in", DATA)


if __name__ == "__main__":
    main()
