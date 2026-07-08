"""Split this week's wine POs into one file per vendor (for emailing).
Pulls the PO Export-LT report (Status=Receiving, wine) from Cloud Retailer, keeps the recent
(this week's) POs, groups by supplier, and writes <vendor>.csv into a folder. Needs CR access."""
import cloud_retailer_api as cr
import csv, os, re, datetime, urllib.parse
from collections import defaultdict

REPORT = "Custom-PurchaseOrder-Details"

VENDOR_COLS = [("location", "Location"), ("referenceNumber", "PO #"), ("productCode", "UPC"),
               ("productDescription", "Description"), ("supplierReorderNumber", "Vendor Item #"),
               ("caseQuantity", "Units/Case"), ("quantityCases", "Cases"),
               ("caseCost", "Case Cost"), ("extendedCaseCost", "Ext Cost"), ("notes", "Notes")]


def _query(cut_quoted):
    return ("Cols=SupplierName~Location~DateCreated~ReferenceNumber~Department~Category~ProductCode~ProductDescription~"
            "CaseQuantity~SupplierReorderNumber~Notes~QuantityCases~CaseCost~ExtendedCaseCost~Status"
            "&GroupIndex=0&SortBy=SupplierName"
            "&Filters[0].PropertyName=Status&Filters[0].Value=Receiving"
            "&Filters[1].OperatorJoin=BAnd&Filters[1].PropertyName=SupplierNameText&Filters[1].Operator=ine"
            "&Filters[2].OperatorJoin=BAnd&Filters[2].PropertyName=BuyingTeam&Filters[2].Operator=ct&Filters[2].Value=wine"
            "&Filters[3].OperatorJoin=BAnd&Filters[3].PropertyName=DateCreated&Filters[3].Operator=gt&Filters[3].Value=" + cut_quoted)


def _safe(name):
    return re.sub(r"[^\w .-]", "_", str(name)).strip()[:60] or "Unknown"


def group_rows(rows):
    """Group PO line-item dicts by supplier -> {vendor: [rows]}."""
    byv = defaultdict(list)
    for r in rows:
        if str(r.get("productCode") or "").strip():
            byv[str(r.get("supplierName") or "Unknown").strip()].append(r)
    return byv


def build(out_dir=None, lookback_days=4):
    cut = datetime.date.today() - datetime.timedelta(days=lookback_days)
    cutq = urllib.parse.quote("%d/%d/%d" % (cut.month, cut.day, cut.year))
    rows = cr.fetch_all(REPORT, _query(cutq), max_pages=30)
    byv = group_rows(rows)
    out_dir = out_dir or os.path.join(os.path.expanduser("~"), "Downloads", "Wine Vendor POs")
    os.makedirs(out_dir, exist_ok=True)
    made = []
    for v, rs in byv.items():
        fname = "%s.csv" % _safe(v)
        with open(os.path.join(out_dir, fname), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow([h for _, h in VENDOR_COLS])
            for r in rs:
                w.writerow([r.get(k, "") for k, _ in VENDOR_COLS])
        made.append((v, fname, len(rs)))
    # manifest the download page reads (vendors + line counts + week)
    import json as _json
    with open(os.path.join(out_dir, "_index.json"), "w", encoding="utf-8") as f:
        _json.dump({"generated": datetime.date.today().isoformat(), "since": cut.isoformat(),
                    "vendors": [{"name": v, "file": fn, "lines": n} for v, fn, n in sorted(made)]}, f)
    print("wrote %d vendor files to %s" % (len(made), out_dir))
    for v, fn, n in sorted(made):
        print("  %-40s %d lines" % (v, n))
    return out_dir


if __name__ == "__main__":
    try:
        build()
    except Exception as e:
        print("vendor PO split skipped:", e)
