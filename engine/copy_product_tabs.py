"""
COPY PRODUCT TABS  --  mirror the tabs buyers already use in the Product Lists workbook.

Copies whole tabs (all columns) from each product file so they show up as familiar sections
in the new system - an easy transfer. Handles the messy headers (finds the real header row,
drops blank columns, de-duplicates repeated column names).

Output: "<Dept> - <Tab>.csv" for each tab. cloud_build/local pipeline run this; the dashboard
shows them under a "Product Lists" view.
"""
import os, glob, re
import pandas as pd

FOLDERS = [r"C:\Users\Anna K\OneDrive - Top Ten Liquors\Top Ten OneDrive - Product Lists",
           r"C:\Users\Anna K\OneDrive - Top Ten Liquors\Product Lists",
           r"C:\Users\Anna K\OneDrive - Top Ten Liquors",
           r"C:\Users\Anna K\OneDrive - Top Ten Liquors\THC Reports",
           r"C:\Users\Anna K\Downloads"]
OUT = [r"C:\Users\Anna K\Downloads", r"C:\Users\Anna K\OneDrive - Top Ten Liquors\THC Reports"]
# Beer shares the Liquor workbook (Beer is maintained inside the Liquor file).
DEPTS = {"THC": "THC [0-9]*.xlsx", "Wine": "Wine [0-9]*.xlsx",
         "Spirits": "Liquor [0-9]*.xlsx", "Beer": "Liquor [0-9]*.xlsx"}
# Carry over EVERY tab the buyers use, except these internal/huge/redundant ones:
#   - "*alias*"  : WMS SKU-alias mapping (not a buying reference)
#   - "*inventory*" / "*sales and inv*" : embedded inventory snapshot (the app already has live daily inventory)
#   - dated copies like "6.6.25" : stale working copies
SKIP_CONTAINS = ["alias", "inventory", "sales and inv"]
MAXROWS = 3000


def skip_sheet(name):
    low = (name or "").strip().lower()
    if any(s in low for s in SKIP_CONTAINS):
        return True
    return re.fullmatch(r"\d{1,2}\.\d{1,2}(\.\d{2,4})?", low) is not None   # dated tab


def readable(p):
    try:
        with open(p, "rb") as fh:
            fh.read(1); return True
    except Exception:
        return False


def newest(pat):
    h = []
    for d in FOLDERS:
        h += glob.glob(os.path.join(d, pat))
    h = [x for x in set(h) if not os.path.basename(x).startswith("~$") and readable(x)]
    return max(h, key=os.path.getmtime) if h else None


def _read(f, sh):
    for e in ("calamine", None):
        try:
            return pd.read_excel(f, sheet_name=sh, header=None, engine=e) if e \
                else pd.read_excel(f, sheet_name=sh, header=None)
        except Exception:
            continue
    return None


def dedupe(names):
    seen, out = {}, []
    for n in names:
        n = (n or "col").strip() or "col"
        if n in seen:
            seen[n] += 1; out.append(f"{n} ({seen[n]})")
        else:
            seen[n] = 0; out.append(n)
    return out


def best_header(d):
    """Find the header row: prefer one naming Product UPC/Description; else the row (in the
    first 8) with the most non-empty text cells. Works for plain lookup tabs too."""
    for i in range(min(8, len(d))):
        row = [str(x) for x in d.iloc[i].tolist()]
        if any("Product UPC" in x or "Product Description" in x for x in row):
            return i
    best, bi = -1, 0
    for i in range(min(8, len(d))):
        score = sum(1 for x in d.iloc[i].tolist()
                    if str(x).strip() and str(x).strip().lower() != "nan")
        if score > best:
            best, bi = score, i
    return bi


def copy_tab(f, sheet):
    d = _read(f, sheet)
    if d is None or d.empty:
        return None
    hdr = best_header(d)
    header = [str(x).strip() for x in d.iloc[hdr].tolist()]
    keep = [j for j, c in enumerate(header) if c and c.lower() != "nan"]
    if not keep:
        return None
    names = dedupe([header[j] for j in keep])
    body = d.iloc[hdr + 1:, keep].copy()
    body.columns = names
    keycol = next((c for c in names if "Product Description" in c), names[0])
    body = body[body[keycol].astype(str).str.strip().str.lower().replace("nan", "").str.len() > 0]
    body = body.dropna(axis=1, how="all")
    return body.head(MAXROWS)


def main():
    for dept, pat in DEPTS.items():
        f = newest(pat)
        if not f:
            continue
        try:
            names = pd.ExcelFile(f, engine="calamine").sheet_names
        except Exception:
            try:
                names = pd.ExcelFile(f).sheet_names
            except Exception:
                continue
        # the main working sheet is named after the department (Liquor's is "Spirits")
        main_sheet = "spirits" if dept in ("Spirits", "Beer") else dept.lower()
        for sh in names:
            if skip_sheet(sh):
                continue
            label = "Full List" if sh.strip().lower() == main_sheet else sh.strip()
            t = copy_tab(f, sh)
            if t is None or t.empty:
                print(f"{dept} / {label}: (empty - skipped)"); continue
            for o in OUT:
                os.makedirs(o, exist_ok=True)
                try:
                    t.to_csv(os.path.join(o, f"{dept} - {label}.csv"), index=False)
                except PermissionError:
                    pass
            print(f"{dept} / {label}: {len(t)} rows x {t.shape[1]} cols")


if __name__ == "__main__":
    main()
