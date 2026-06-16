"""
Resilient newest-file picking. A file that is open in Excel, mid-sync, or a bad
manual copy can be the 'newest' match and crash a script. These helpers try the
newest candidate and fall back to the next one that actually reads.
"""
import glob, os

def candidates(pattern, folders):
    hits = []
    for d in folders:
        hits += glob.glob(os.path.join(d, pattern))
    hits = [h for h in hits if not os.path.basename(h).startswith("~$")]  # skip Excel lock files
    return sorted(hits, key=os.path.getmtime, reverse=True)

def newest_readable(pattern, folders):
    """Newest file that can at least be opened (skips locked/inaccessible)."""
    for f in candidates(pattern, folders):
        try:
            with open(f, "rb") as fh:
                fh.read(1)
            return f
        except Exception:
            continue
    return None

def read_newest(pattern, folders, reader):
    """Try the newest matching file with `reader`; on ANY failure (locked, corrupt,
    bad copy) fall back to the next newest. Returns (result, path) or (None, None)."""
    for f in candidates(pattern, folders):
        try:
            return reader(f), f
        except Exception:
            continue
    return None, None
