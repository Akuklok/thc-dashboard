"""
EXTRACT PRICE REFERENCE  --  pull retail / margin / deal / cost from the buyer files.

The per-department buyer workbooks (THC 6.x, Wine 5.x, Liquor 6.x ...) carry better
pricing than the inventory report: unit Retail, GM%, Deal Description, deal unit cost,
and invoice cost. This extracts a compact "Price Reference.csv" (one row per UPC) that
the snapshot + order use to enrich retail/margin/deals across departments.

Runs locally (the buyer files live on OneDrive). The result is pushed to the repo so the
cloud runner can use it too.
"""
import os, glob, re
import pandas as pd

FOLDERS = [r"C:\Users\Anna K\OneDrive - Top Ten Liquors",
           r"C:\Users\Anna K\OneDrive - Top Ten Liquors\THC Reports",
           r"C:\Users\Anna K\Downloads",
           r"C:\Users\Anna K\OneDrive - Top Ten Liquors\Documents"]
OUT = [r"C:\Users\Anna K\Downloads",
       r"C:\Users\Anna K\OneDrive - Top Ten Liquors\THC Reports"]
# department label -> filename glob for its buyer workbook (newest, readable wins)
PATTERNS = {"THC": "THC [0-9]*.xlsx", "Wine": "Wine [0-9]*.xlsx",
            "Spirits": "Liquor [0-9]*.xlsx", "Beer": "Beer [0-9]*.xlsx"}
# column positions in the buyer sheet (0-based), confirmed from the file layout
C = {"upc": 8, "units": 15, "deal": 16, "casecost": 19, "netunit": 25, "retail": 27, "gm": 29}


def norm(u):
    s = re.sub(r"\D", "", str(u)); return s.lstrip("0") or s


def newest_readable(pattern):
    hits = []
    for d in FOLDERS:
        hits += glob.glob(os.path.join(d, pattern))
    hits = [h for h in hits if not os.path.basename(h).startswith("~$")]
    for h in sorted(hits, key=os.path.getmtime, reverse=True):
        try:
            with open(h, "rb") as fh: fh.read(1)
            return h
        except Exception:
            continue
    return None


def find_sheet(xl):
    """Find the data sheet + header row (has 'Product UPC' and a 'Retail' column)."""
    for sh in xl.sheet_names:
        try:
            head = xl.parse(sh, header=None, nrows=4)
        except Exception:
            continue
        for i in range(min(4, len(head))):
            row = [str(x).strip() for x in head.iloc[i].tolist()]
            if any("Product UPC" in c for c in row) and any(c == "Retail" for c in row):
                return sh, i
    return None, None


def main():
    rows = []
    for dept, pat in PATTERNS.items():
        f = newest_readable(pat)
        if not f:
            continue
        try:
            xl = pd.ExcelFile(f)
        except Exception:
            continue
        sh, hdr = find_sheet(xl)
        if sh is None:
            print(f"{dept}: no usable sheet in {os.path.basename(f)}"); continue
        d = xl.parse(sh, header=None).iloc[hdr + 1:]
        n = 0
        for _, r in d.iterrows():
            upc = norm(r[C["upc"]])
            if not upc:
                continue
            g = lambda k: pd.to_numeric(r[C[k]], errors="coerce")
            retail, gm, units, casecost, netunit = g("retail"), g("gm"), g("units"), g("casecost"), g("netunit")
            deal = str(r[C["deal"]]).strip()
            deal = "" if deal.lower() == "nan" else deal
            unit_cost = (casecost / units) if (pd.notna(casecost) and pd.notna(units) and units) else None
            rows.append({
                "upc": upc, "Department": dept,
                "Retail": round(float(retail), 2) if pd.notna(retail) else "",
                "GM %": round(float(gm) * 100) if pd.notna(gm) else "",
                "Deal": deal,
                "Deal Unit Cost": round(float(netunit), 2) if pd.notna(netunit) else "",
                "Unit Cost": round(float(unit_cost), 2) if unit_cost else "",
            })
            n += 1
        print(f"{dept}: {n} priced items from {os.path.basename(f)}")
    if rows:
        df = pd.DataFrame(rows).drop_duplicates("upc")
        for o in OUT:
            os.makedirs(o, exist_ok=True)
            try:
                df.to_csv(os.path.join(o, "Price Reference.csv"), index=False)
            except PermissionError:
                pass
        print("Price Reference written:", len(df), "items")
    else:
        print("No buyer files found - Price Reference not written.")


if __name__ == "__main__":
    main()
