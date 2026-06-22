"""
flag_failure.py  --  runs in the cloud ONLY when the daily build fails.

A failed build normally commits nothing, so the app would keep showing the last
numbers with no hint anything went wrong. This stamps data/status.json with
health="failed" (keeping the existing data_date) so the buyer app shows a clear
"last good numbers" warning right away. The next successful build overwrites
status.json with a fresh stamp, which clears the warning automatically.

(In the repo this lives at engine/flag_failure.py and is called by
 .github/workflows/daily_build.yml on failure.)
"""
import json, os, datetime

PATH = "data/status.json"
try:
    st = json.load(open(PATH, encoding="utf-8"))
except Exception:
    st = {}

st["health"] = "failed"
st["built_utc"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
st["warnings"] = ["The automatic update did not finish, so the app is showing the last good numbers."]

os.makedirs("data", exist_ok=True)
with open(PATH, "w", encoding="utf-8") as fh:
    json.dump(st, fh)
print("Flagged status.json health=failed (kept data_date:", st.get("data_date"), ")")
