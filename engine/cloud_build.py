"""
CLOUD BUILD  --  the PC-free daily backend (runs on GitHub Actions).

Steps (no computer needed):
  1. Download the newest inventory report from IT's SFTP server (credentials from env/secrets).
  2. Run the same order engine, per department, writing into the repo's data/ folder.
  3. (the workflow then commits data/ so the hosted app + dashboard read the fresh numbers.)

Locally you can dry-run it too: set the SFTP_* env vars and run `python engine/cloud_build.py`.
"""
import os, paramiko

HERE = os.path.dirname(os.path.abspath(__file__))     # repo/engine
ROOT = os.path.dirname(HERE)                           # repo root
WORK = os.path.join(ROOT, "work")
DATA = os.path.join(ROOT, "data")


def _env(name, default=""):
    return (os.environ.get(name) or default).strip()

def sftp_download():
    host = _env("SFTP_HOST"); user = _env("SFTP_USER"); pw = _env("SFTP_PASS")
    port = int(_env("SFTP_PORT", "22"))                 # optional - defaults to 22
    path = _env("SFTP_PATH", "/Inventory-Sales/")       # optional - defaults to the known folder
    missing = [n for n, v in [("SFTP_HOST", host), ("SFTP_USER", user), ("SFTP_PASS", pw)] if not v]
    if missing:
        raise SystemExit("Missing required secret(s): " + ", ".join(missing))
    t = paramiko.Transport((host, port)); t.connect(username=user, password=pw)
    sftp = paramiko.SFTPClient.from_transport(t)
    files = [f for f in sftp.listdir_attr(path)
             if "full inventory sales report" in f.filename.lower() and not f.filename.startswith(".")]
    if not files:
        raise SystemExit("No inventory report found on the SFTP server.")
    newest = max(files, key=lambda f: f.st_mtime)
    os.makedirs(WORK, exist_ok=True)
    dest = os.path.join(WORK, newest.filename)
    print(f"Downloading {newest.filename} ({newest.st_size:,} bytes) from SFTP...")
    sftp.get(os.path.join(path, newest.filename), dest)
    sftp.close(); t.close()
    return dest


def main():
    sftp_download()
    os.makedirs(DATA, exist_ok=True)
    import daily_buying_brief as dbb
    import recommended_order as ro
    import build_snapshot as bs
    dbb.INPUT_FOLDERS = [WORK]      # engine reads the report from here
    ro.OUT_FOLDERS = [DATA]         # engine writes per-department orders + snapshots here
    ro.main()
    bs.main()                       # per-item inventory snapshots (for the assistant)
    print("Cloud build complete - orders + snapshots written to", DATA)


if __name__ == "__main__":
    main()
