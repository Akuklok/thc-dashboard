"""Produce Laura's 'wine report' from the daily Full Inventory Sales Report the app already pulls.
Replaces her manual steps: sort, filter to Wine, delete Accessories/Box Wine/Blanks, drop Blaine2.
Output 'Wine Inventory Calc.csv' is paste-ready into the Inventory Calc tab (columns C onward)."""
import pandas as pd, glob, os

EXCLUDE_CATS = {"accessories", "box wine", "blanks"}


def _default_folders():
    home = os.path.expanduser("~")
    return [os.path.join(home, "Downloads"), os.path.join(home, "OneDrive - Top Ten Liquors", "THC Reports")]


PATTERNS = ["Full Inventory Sales Report*.xlsx", "*Full Inventory*Sales*.xlsx",
            "*Inventory Sales Rep*.xlsx", "*Inventory*Sales*Report*.xlsx"]


def latest_report(folders=None):
    cands = []
    for folder in (folders or _default_folders()):
        for pat in PATTERNS:
            cands += glob.glob(os.path.join(folder, pat))
    cands = [c for c in set(cands) if not os.path.basename(c).startswith("~$")]
    return max(cands, key=os.path.getmtime) if cands else None


def pick_sheet(xl):
    for sh in xl.sheet_names:
        cols = set(map(str, xl.parse(sh, nrows=0).columns))
        if {"OH", "Product Description", "Department"} <= cols:
            return sh
    return xl.sheet_names[0]


def build(folders=None, out_dir=None):
    path = latest_report(folders)
    if not path:
        print("  wine report prep: no Full Inventory Sales Report found")
        return None
    xl = pd.ExcelFile(path)
    df = xl.parse(pick_sheet(xl))
    dept = df["Department"].astype(str).str.strip().str.lower()
    cat = df["Category"].astype(str).str.strip().str.lower()
    loc = df["Location Name"].astype(str).str.strip()
    keep = (dept == "wine") & (~cat.isin(EXCLUDE_CATS)) & (loc.str.lower() != "blaine2")
    out = df[keep].sort_values(["Location Name", "Product Description"])
    out_dir = out_dir or os.path.join(os.path.expanduser("~"), "Downloads")
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, "Wine Inventory Calc.csv")
    out.to_csv(dest, index=False, encoding="utf-8-sig")
    print("  wine report: %d wine rows (from %d total) -> %s" % (len(out), len(df), dest))
    return dest


if __name__ == "__main__":
    build()
