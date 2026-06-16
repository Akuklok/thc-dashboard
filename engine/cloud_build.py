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


def sftp_download():
    host = os.environ["SFTP_HOST"]; port = int(os.environ.get("SFTP_PORT", "22"))
    user = os.environ["SFTP_USER"]; pw = os.environ["SFTP_PASS"]
    path = os.environ.get("SFTP_PATH", "/")
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
    dbb.INPUT_FOLDERS = [WORK]      # engine reads the report from here
    ro.OUT_FOLDERS = [DATA]         # engine writes per-department orders here
    ro.main()
    print("Cloud build complete - orders written to", DATA)


if __name__ == "__main__":
    main()
