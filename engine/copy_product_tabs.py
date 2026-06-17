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
# Beer shares the Liquor workbook (Beer is maintained inside the Liquor file), so the app
# serves Beer's product tabs from the Spirits copy - no need to duplicate the files here.
DEPTS = {"THC": "THC [0-9]*.xlsx", "Wine": "Wine [0-9]*.xlsx",
         "Spirits": "Liquor [0-9]*.xlsx"}
# Carry over EVERY tab the buyers use, except these internal/huge/redundant ones:
#   - "*alias*"  : WMS SKU-alias mapping (not a buying reference)
#   - "*inventory*" / "*sales and inv*" : embedded inventory snapshot (the app already has live daily inventory)
#   - dated copies like "6.6.25" : stale working copies
SKIP_CONTAINS = ["alias", "inventory", "sales and inv"]
# Useful buyer references to keep even though they have no per-product UPC column.
KEEP_REFERENCE = {"markups", "retail pricing table"}
MAXROWS = 8000      # cover the full product lists (Spirits Full List ~5,400) so search finds everything

# Internal/helper columns to drop from every tab (match if the header CONTAINS any of these).
DROP_COLS = ["concat", "preferred", "supplier supported tasting", "remove?", "check",
             "vv vendor", "ns ", "cr vendor", "vendor code", "pnumber",
             "pitem", "internal id", "updated in", "alias", "wms", "# of rps",
             "eo ", "free case value", "free qty", "buy qty", "freight"]
# Useful columns pulled to the FRONT in this order (by header substring); the rest follow.
PREFERRED = ["Product Description", "Product UPC", "THC Mg", "Department", "Category",
             "Sub Category", "Size", "Units/Case", "Supplier", "Distributor",
             "Deal Description", "Buy Months", "Net/Unit", "Net Unit Cost", "Net Case",
             "Case 1 (Retail)", "Retail", "Club", "GM", "Top Ten Invoice", "Buy Month Invoice",
             "Effective Date", "Notes", "Average of Price", "Average of Sale", "Buy Month"]


def tidy_columns(df):
    """Drop internal/helper columns and put the useful ones first (keeps everything else)."""
    def junk(c):
        cl = str(c).strip().lower()
        if (not cl) or cl == "nan":
            return True
        if re.search(r"\(\d+\)$", cl):          # repeated header-block duplicates ("... (1)")
            return True
        return any(d in cl for d in DROP_COLS)
    kept = [c for c in df.columns if not junk(c)]
    ordered, used = [], set()
    for p in PREFERRED:
        for c in kept:
            if c not in used and p.lower() in str(c).lower():
                ordered.append(c); used.add(c); break
    ordered += [c for c in kept if c not in used]
    return df[ordered] if ordered else df


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
    # Carry over tabs connected to products (have a UPC / product identifier column),
    # plus a few useful references (Markups, Retail Pricing Table). Pure helper/lookup
    # tables (Buy Month Lookup, Vendor Reference, etc.) are skipped.
    low = " | ".join(c.lower() for c in header)
    is_product = any(k in low for k in ("upc", "product code", "product description"))
    if not is_product and sheet.strip().lower() not in KEEP_REFERENCE:
        return None
    keep = [j for j, c in enumerate(header) if c and c.lower() != "nan"]
    if not keep:
        return None
    names = dedupe([header[j] for j in keep])
    body = d.iloc[hdr + 1:, keep].copy()
    body.columns = names
    keycol = next((c for c in names if "Product Description" in c), names[0])
    body = body[body[keycol].astype(str).str.strip().str.lower().replace("nan", "").str.len() > 0]
    body = body.dropna(axis=1, how="all")
    body = tidy_columns(body)
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
