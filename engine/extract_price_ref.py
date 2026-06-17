"""
EXTRACT COST REFERENCE  --  the BUYER's price (and deal terms) from the product files.

The product workbooks (THC <date>, Liquor <date>, Wine <date>, Beer <date> in the Product
Lists folder) hold what Top Ten PAYS the vendor:
  Net/Unit (col 25)        deal-adjusted per-unit cost  <- the buyer's price
  Top Ten Invoice (col 19) case cost  (fallback: / Units/Case)
  Deal Description (col 16)
(Customer/retail price comes from the daily report's Price column, handled in build_snapshot.)

Output: Cost Reference.csv (upc, Department, Buyer Cost, Deal) -> used by the snapshot/order.
Fast (calamine, targeted folders, data sheet only). cloud_build overrides FOLDERS/OUT.
"""
import os, glob, re
import pandas as pd

FOLDERS = [r"C:\Users\Anna K\OneDrive - Top Ten Liquors\Top Ten OneDrive - Product Lists",
           r"C:\Users\Anna K\OneDrive - Top Ten Liquors\Product Lists",
           r"C:\Users\Anna K\OneDrive - Top Ten Liquors",
           r"C:\Users\Anna K\OneDrive - Top Ten Liquors\THC Reports",
           r"C:\Users\Anna K\Downloads"]
OUT = [r"C:\Users\Anna K\Downloads", r"C:\Users\Anna K\OneDrive - Top Ten Liquors\THC Reports"]
# Beer's cost lives in the Liquor file (covers Spirits + Beer + Wine by UPC).
DEPTS = {"THC": "THC [0-9]*.xlsx", "Wine": "Wine [0-9]*.xlsx",
         "Spirits": "Liquor [0-9]*.xlsx"}
# Each department's workbook is laid out differently, so locate columns by HEADER NAME
# (first occurrence; the header block repeats across the sheet), not by fixed position.
ALIASES = {
    "upc":      ["product upc"],
    "units":    ["units/case", "units / case"],
    "deal":     ["deal description"],
    "netunit":  ["net/unit", "net unit cost", "net / unit"],          # buyer's per-unit cost
    "casecost": ["net case cost", "top ten invoice cost", "top ten invoice"],  # fallback / units
    "buymonths": ["buy months (if appl.)", "buy months"],            # seasonal buy timing
}


def find_cols(d, hdr):
    """Map each needed field to its column index by matching the header text."""
    row = [str(x).strip().lower() for x in d.iloc[hdr].tolist()]
    cols = {}
    for j, v in enumerate(row):
        for key, names in ALIASES.items():
            if key not in cols and v in names:
                cols[key] = j
    return cols


def norm(u):
    s = re.sub(r"\D", "", str(u)); return s.lstrip("0") or s


# A product has a buy month ONLY if a real month is listed. "ALL", "LTO", pack sizes,
# "n/a", a product name, or blank = not enough info -> no buy-month constraint.
_MONTH_STEMS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def parse_months(val):
    """Return the set of month numbers named in the cell (empty if none are real months)."""
    s = str(val).strip().lower()
    if not s or s == "nan":
        return set()
    found = {n for stem, n in _MONTH_STEMS.items() if stem in s}
    if "sond" in s:                       # buyer shorthand: Sept/Oct/Nov/Dec
        found |= {9, 10, 11, 12}
    return found


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


def data_sheet(f, dept):
    try:
        names = pd.ExcelFile(f, engine="calamine").sheet_names
    except Exception:
        try:
            names = pd.ExcelFile(f).sheet_names
        except Exception:
            return None, None
    # Spirits/Beer both live in the Liquor workbook's "Spirits" sheet.
    want = "spirits" if dept.lower() in ("spirits", "beer") else dept.lower()
    order = [s for s in names if want in s.lower()] + [s for s in names if want not in s.lower()]
    for sh in order:
        d = _read(f, sh)
        if d is None or d.shape[1] < 12:
            continue
        for i in range(min(4, len(d))):
            if any("Product UPC" in str(x) for x in d.iloc[i].tolist()):
                return d, i
    return None, None


def tab_items(f, sheet):
    """Generic: pull {upc, Item} from a product-file tab (Remove / New Items) by locating
    the 'Product UPC' and 'Product Description' columns (their positions differ per tab)."""
    d = _read(f, sheet)
    if d is None:
        return []
    hdr = upc_c = desc_c = None
    for i in range(min(6, len(d))):
        row = [str(x) for x in d.iloc[i].tolist()]
        for j, v in enumerate(row):
            if upc_c is None and "Product UPC" in v:      # first occurrence (header repeats across the sheet)
                upc_c = j
            if desc_c is None and "Product Description" in v:
                desc_c = j
        if upc_c is not None and desc_c is not None:
            hdr = i; break
    if hdr is None:
        return []
    out = []
    for _, r in d.iloc[hdr + 1:].iterrows():
        upc = norm(r[upc_c]); item = str(r[desc_c]).strip()
        if upc and item and item.lower() != "nan":
            out.append({"upc": upc, "Item": item})
    return out


def write_sections():
    """Carry the product-form tabs (Remove, New Items) over as their own reference lists."""
    for sheet, outname in [("Remove", "Remove List.csv"), ("New Items", "New Items.csv")]:
        items = []
        for dept, pat in DEPTS.items():
            f = newest(pat)
            if f:
                for it in tab_items(f, sheet):
                    items.append({**it, "Department": dept})
        if items:
            df = pd.DataFrame(items).drop_duplicates("upc")
            for o in OUT:
                try:
                    df.to_csv(os.path.join(o, outname), index=False)
                except PermissionError:
                    pass
            print(f"{sheet}: {len(df)} items -> {outname}")


def main():
    rows = []
    for dept, pat in DEPTS.items():
        f = newest(pat)
        if not f:
            print(f"{dept}: no product file"); continue
        d, hdr = data_sheet(f, dept)
        if d is None:
            print(f"{dept}: no data sheet in {os.path.basename(f)}"); continue
        cols = find_cols(d, hdr)
        if "upc" not in cols or not ({"netunit", "casecost"} & set(cols)):
            print(f"{dept}: couldn't locate cost columns in {os.path.basename(f)} (found {sorted(cols)})"); continue
        n = 0
        for _, r in d.iloc[hdr + 1:].iterrows():
            upc = norm(r[cols["upc"]])
            if not upc:
                continue
            g = lambda k: pd.to_numeric(r[cols[k]], errors="coerce") if k in cols else float("nan")
            netunit, casecost, units = g("netunit"), g("casecost"), g("units")
            cost = netunit if pd.notna(netunit) else \
                (casecost / units if (pd.notna(casecost) and pd.notna(units) and units) else None)
            deal = str(r[cols["deal"]]).strip() if "deal" in cols else ""
            deal = "" if deal.lower() == "nan" else deal
            months = parse_months(r[cols["buymonths"]]) if "buymonths" in cols else set()
            bmstr = "|".join(str(m) for m in sorted(months))
            if (cost is None or pd.isna(cost)) and not deal and not bmstr:
                continue
            rows.append({"upc": upc, "Department": dept,
                         "Buyer Cost": round(float(cost), 2) if (cost is not None and pd.notna(cost)) else "",
                         "Deal": deal, "Buy Months": bmstr})
            n += 1
        print(f"{dept}: {n} items from {os.path.basename(f)}")
    if rows:
        df = pd.DataFrame(rows).drop_duplicates("upc")
        for o in OUT:
            os.makedirs(o, exist_ok=True)
            try:
                df.to_csv(os.path.join(o, "Cost Reference.csv"), index=False)
            except PermissionError:
                pass
        print("Cost Reference written:", len(df), "items")
    else:
        print("No product files found - Cost Reference not written (keeping the last one).")
    write_sections()


if __name__ == "__main__":
    main()
