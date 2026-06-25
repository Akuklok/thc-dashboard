"""Pull the wine Open POs straight from Cloud Retailer and write a clean CSV (no manual run/filter).
  Receiving:    items still in Receiving (open POs we should not reorder).
  Discrepancy:  recently received POs with a case discrepancy (not received in full -> memos)."""
import cloud_retailer_api as cr
import csv, os, time, datetime, urllib.parse

REPORT = "Custom-PurchaseOrder-Details"

Q_RECEIVING = ("Cols=SupplierName~Location~DateCreated~ReferenceNumber~Department~Category~ProductCode~ProductDescription~"
               "CaseQuantity~SupplierReorderNumber~Notes~QuantityCases~CaseCost~ExtendedCaseCost~Quantity~"
               "QuantityCasesReceived~QuantityCaseDiscrepancy~Status~LineItemNotes&GroupIndex=0&SortBy=Category"
               "&Filters[0].PropertyName=Status&Filters[0].Value=Receiving"
               "&Filters[1].OperatorJoin=BAnd&Filters[1].PropertyName=SupplierNameText&Filters[1].Operator=ine"
               "&Filters[2].OperatorJoin=BAnd&Filters[2].PropertyName=BuyingTeam&Filters[2].Operator=ct&Filters[2].Value=wine")


def _disc_query(cut_quoted):
    return ("Cols=ProductCode~ProductDescription~SupplierName~SupplierReorderNumber~Location~DateCreated~"
            "ReferenceNumber~Notes~CaseCost~ExtendedCaseCost~Quantity~QuantityCases~QuantityCasesReceived~"
            "QuantityCaseDiscrepancy~LineItemNotes~CaseQuantity~Department~Category~Status&GroupIndex=0&SortBy=ReferenceNumber"
            "&Filters[0].PropertyName=Status&Filters[0].Value=Inventory%20Received"
            "&Filters[1].OperatorJoin=COr&Filters[1].PropertyName=Status&Filters[1].Value=Receiving"
            "&Filters[2].OperatorJoin=BAnd&Filters[2].PropertyName=SupplierNameText&Filters[2].Operator=ine"
            "&Filters[3].OperatorJoin=BAnd&Filters[3].PropertyName=QuantityCaseDiscrepancy&Filters[3].Operator=ne&Filters[3].Value=0"
            "&Filters[4].OperatorJoin=BAnd&Filters[4].PropertyName=BuyingTeam&Filters[4].Operator=ct&Filters[4].Value=wine"
            "&Filters[5].OperatorJoin=BAnd&Filters[5].PropertyName=DateCreated&Filters[5].Operator=gt&Filters[5].Value=" + cut_quoted)


COLS = [("source", "Source"), ("productCode", "Product Code"), ("productDescription", "Description"),
        ("supplierName", "Supplier"), ("location", "Location"), ("referenceNumber", "Ref #"),
        ("status", "Status"), ("quantityCases", "Cases Ordered"), ("quantityCasesReceived", "Cases Received"),
        ("quantityCaseDiscrepancy", "Case Discrepancy"), ("department", "Dept"), ("category", "Category"),
        ("notes", "Notes"), ("lineItemNotes", "Line Notes"), ("dateCreated", "Date Created")]

# Laura's cleanup: drop Beer/Spirits/THC; within Misc drop Mixes & Condiments (keep Wine + Acc/Misc Other)
DROP_DEPTS = {"spirits", "beer", "thc"}
DROP_MISC_CATS = {"mixes", "condiments"}


def _keep(r):
    dept = str(r.get("department") or "").strip().lower()
    cat = str(r.get("category") or "").strip().lower()
    if dept in DROP_DEPTS:
        return False
    if dept in ("misc", "miscellaneous") and cat in DROP_MISC_CATS:
        return False
    return True


def build(out_dir=None, lookback_days=21):
    cut = datetime.date.today() - datetime.timedelta(days=lookback_days)
    cutq = urllib.parse.quote("%d/%d/%d" % (cut.month, cut.day, cut.year))
    t0 = time.time()
    rows = []
    for src, q in (("Receiving", Q_RECEIVING), ("Discrepancy", _disc_query(cutq))):
        got = cr.fetch_all(REPORT, q, max_pages=30)
        kept = 0
        for r in got:
            if str(r.get("productCode") or "").strip() and _keep(r):
                r["source"] = src
                rows.append(r); kept += 1
        print("  open POs %-12s %d pulled, %d kept" % (src, len(got), kept))
    out_dir = out_dir or os.path.join(os.path.expanduser("~"), "Downloads")
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, "Wine Open POs.csv")
    with open(dest, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([h for _, h in COLS])
        for r in rows:
            w.writerow([r.get(k, "") for k, _ in COLS])
    print("  open POs: %d rows -> %s  (%.0fs)" % (len(rows), dest, time.time() - t0))
    return dest


if __name__ == "__main__":
    build()
