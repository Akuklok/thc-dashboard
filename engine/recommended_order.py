"""
RECOMMENDED WEEKLY ORDER  --  transfer-vs-buy, grounded in REAL per-store columns.
Runs PER DEPARTMENT (THC, Beer, Wine, Spirits, Other) from one daily Full Inventory
Sales Report. Each department gets its own order + transfer plan; same logic throughout.

Answers, per department: of everything that needs restocking, how much can we cover by
TRANSFERRING overstock between stores, and how much must we actually BUY from vendors?

Columns used (straight from Cloud Retailer):
  OH                          on hand at a store
  30D / 90D                   units sold in the last 30 / 90 days  (-> weekly velocity)
  PM                          the POS's per-store reorder need (units)
  Case Qty/Reorder Multiple   case pack
  Avg Cost / Supplier Cost    cost for the $ math

Outputs per department ("<Dept> Recommended Order.xlsx" + .txt):
  Recommended Order  - what to BUY, ranked closest-to-stockout first
  Transfer Plan      - what to MOVE between stores (to store, qty, from where)
(THC keeps the original "THC Recommended Order.*" names so the dashboard/assistant work.)

============================  TUNABLE RULES  ============================
"""
TARGET_WEEKS      = 4       # fallback target if the file has no POS suggestion
DONOR_KEEP_WEEKS  = 4       # a store keeps this many weeks for itself before donating the rest
MIN_TRANSFER      = 4       # don't suggest moving fewer than this many units (avoid trivial transfers)
WEEKLY_BUDGET     = None    # $ cap on the NET BUY; None = show the full buy
PRIORITY          = "stockout"   # stockout | margin | deals | balanced
TOP_TEXT          = 20      # lines in the text summary

# Which departments to build, and the exact Department values that map to each (excludes
# "- Open"/deposit/giftcard junk by using exact matches). Adjust "Other" as needed.
DEPARTMENTS = {
    "THC":     ["THC"],
    "Beer":    ["Beer"],
    "Wine":    ["Wine"],
    "Spirits": ["Spirits"],
    "Other":   ["Miscellaneous", "Tobacco"],
}
# ========================================================================

import os, glob, re, datetime
import pandas as pd
import numpy as np
from config_locations import drop_warehouses
from xlsx_helper import write_sheets
import daily_buying_brief as dbb

OUT_FOLDERS = [r"C:\Users\Anna K\Downloads",
               r"C:\Users\Anna K\OneDrive - Top Ten Liquors\THC Reports"]

FILE_PATTERNS = ["Full Inventory Sales Report*.xlsx", "*Full Inventory*Sales*.xlsx",
                 "*Inventory Sales Rep*.xlsx", "Master THC*Order*.xlsx"]


def _num(s):
    return pd.to_numeric(s, errors="coerce")


def _file_date(path):
    """Data date from the filename (e.g. '...2026-06-11' or '6.15.26'); else file mtime."""
    name = os.path.basename(path)
    m = re.search(r"(20\d{2})[-_.](\d{1,2})[-_.](\d{1,2})", name)
    if m:
        try: return datetime.date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError: pass
    m = re.search(r"\b(\d{1,2})[.](\d{1,2})[.](\d{2})\b", name)
    if m:
        try: return datetime.date(2000 + int(m[3]), int(m[1]), int(m[2]))
        except ValueError: pass
    return datetime.date.fromtimestamp(os.path.getmtime(path))


def find_inventory_file():
    for pat in FILE_PATTERNS:
        cands = []
        for folder in dbb.INPUT_FOLDERS:
            cands += glob.glob(os.path.join(folder, pat))
        cands = [c for c in cands if not os.path.basename(c).startswith("~$")]
        if cands:
            return max(set(cands), key=lambda c: (_file_date(c), os.path.getmtime(c)))
    return None


def pick_inventory_sheet(xl):
    best, best_score = None, -1
    for sh in xl.sheet_names:
        cols = set(map(str, xl.parse(sh, nrows=1).columns))
        if not ({"OH", "Product Description"} <= cols):
            continue
        score = sum(c in cols for c in ("TOH", "PM", "30D", "90D"))
        if "Location Name" in cols:
            score += 2
        if score > best_score:
            best, best_score = sh, score
    return best


def retail_price_map(xl):
    """Best-effort retail price by product code (for margin %)."""
    out = {}
    for sh in xl.sheet_names:
        cols = set(map(str, xl.parse(sh, nrows=1).columns))
        if "Product Code" in cols and ("Price" in cols or "Price A" in cols):
            d = xl.parse(sh)
            pcol = "Price" if "Price" in d.columns else "Price A"
            for _, r in d.iterrows():
                u = dbb.norm(r["Product Code"]); p = pd.to_numeric(r.get(pcol), errors="coerce")
                if u and pd.notna(p) and p > 0:
                    out.setdefault(u, float(p))
    return out


def plan_transfers(d, keep_weeks, min_transfer):
    """Per item, cover each short store's need from overstocked stores first; the rest is bought."""
    d = d.copy()
    d["surplus"] = (d["OH"] - d["vel"] * keep_weeks).round().clip(lower=0).astype(int)
    d["need"] = d["need"].round().clip(lower=0).astype(int)
    d["wos"] = np.where(d["vel"] > 0, d["OH"] / d["vel"], 1e9)
    summ, trows = [], []
    for item, g in d.groupby("Product Description"):
        gross = int(g["need"].sum())
        pool = {r["Location Name"]: int(r["surplus"]) for _, r in g.iterrows()
                if r["surplus"] >= min_transfer}
        xfer_total = 0
        for _, r in g[g["need"] > 0].sort_values("wos").iterrows():
            need = int(r["need"]); got = 0; src = []
            for loc in sorted(pool, key=pool.get, reverse=True):
                if need < min_transfer:
                    break
                if loc == r["Location Name"]:
                    continue
                avail = pool.get(loc, 0)
                take = min(need, avail)
                if take >= min_transfer:
                    src.append((loc, take)); pool[loc] = avail - take
                    need -= take; got += take
                    if pool[loc] <= 0:
                        pool.pop(loc, None)
            if got > 0:
                wos = round(min(r["wos"], 99), 1)
                priority = "STOCKOUT" if r["OH"] <= 0 else ("Low <2wk" if r["wos"] < 2 else "Top-up")
                trows.append({"Priority": priority, "To Store": r["Location Name"], "Item": item,
                              "To OH": int(r["OH"]), "To WOS": wos, "Transfer In": got,
                              "From": ", ".join(f"{l} ({t})" for l, t in src),
                              "Then Buy": int(r["need"]) - got})
            xfer_total += got
        summ.append({"Item": item, "Gross Need": gross, "Transfer": xfer_total,
                     "Net Buy": gross - xfer_total})
    return pd.DataFrame(summ), pd.DataFrame(trows)


def run_department(df, label, retail, buyers, today, fdate, stale_days, need_basis):
    """Build the order + transfer plan for one department's per-store rows. Writes files,
    returns a summary dict for the roll-up."""
    if df.empty:
        return None
    tsum, tplan = plan_transfers(df[["Product Description", "Location Name", "OH", "vel", "need"]],
                                 DONOR_KEEP_WEEKS, MIN_TRANSFER)
    have = lambda c: c in df.columns
    agg = {
        "upc": ("Product Code", "first"),
        "Category": ("Category", "first"),
        "Supplier": ("Supplier", "first") if have("Supplier") else ("Product Description", "first"),
        "OH": ("OH", "sum"), "u30": ("30D", "sum"), "u90": ("90D", "sum"),
        "cost": ("cost", "median"),
        "case": ("Case Qty/Reorder Multiple", "max") if have("Case Qty/Reorder Multiple") else ("OH", "max"),
    }
    g = df.groupby("Product Description").agg(**agg).reset_index().rename(columns={"Product Description": "Item"})
    g["case"] = _num(g["case"]).fillna(1).replace(0, 1)
    g["wk_vel"] = 0.6 * g["u30"] * 7 / 30 + 0.4 * g["u90"] * 7 / 90
    g["WOS"] = np.where(g["wk_vel"] > 0, g["OH"] / g["wk_vel"], np.nan).round(1)
    g = g.merge(tsum, on="Item", how="left")
    for c in ("Gross Need", "Transfer", "Net Buy"):
        g[c] = _num(g[c]).fillna(0)

    g["Gross $"]    = (g["Gross Need"] * g["cost"]).round(0)
    g["Transfer $"] = (g["Transfer"] * g["cost"]).round(0)
    g["Buy Cases"]  = (g["Net Buy"] / g["case"]).round(0)
    g["Buy Units"]  = (g["Buy Cases"] * g["case"]).astype(int)
    g["Net Buy $"]  = (g["Buy Units"] * g["cost"]).round(0)

    upc = g["upc"].map(dbb.norm)
    g["retail"] = upc.map(retail)
    g["GM %"] = np.where(g["retail"] > 0, (g["retail"] - g["cost"]) / g["retail"] * 100, np.nan).round(0)
    disc, deal = [], []
    for u in upc:
        dd, _m, t = buyers.get(u, (None, None, ""))
        disc.append(dd); deal.append(t)
    g["Discount %"] = disc; g["Deal Terms"] = deal
    on_deal = g["Deal Terms"].astype(str).str.len() > 0
    g["profit_protected"] = g["wk_vel"] * (g["retail"].fillna(g["cost"]) - g["cost"]).clip(lower=0)

    gross_total = float(g["Gross $"].sum()); xfer_total = float(g["Transfer $"].sum())
    net_total = float(g["Net Buy $"].sum())

    buy = g[g["Buy Units"] > 0].copy()
    if PRIORITY == "margin":
        buy = buy.sort_values(["GM %", "profit_protected"], ascending=[False, False]); basis = "highest margin first"
    elif PRIORITY == "deals":
        buy["_d"] = on_deal.loc[buy.index].astype(int)
        buy = buy.sort_values(["_d", "profit_protected"], ascending=[False, False]); basis = "active deals first, then profit"
    elif PRIORITY == "balanced":
        buy = buy.sort_values("profit_protected", ascending=False); basis = "profit protected (balanced)"
    else:
        buy = buy.sort_values(["WOS", "profit_protected"], ascending=[True, False]); basis = "lowest weeks-of-supply first (stockout risk)"

    buy["Cum Buy"] = buy["Net Buy $"].cumsum()
    if WEEKLY_BUDGET:
        within, deferred = buy[buy["Cum Buy"] <= WEEKLY_BUDGET], buy[buy["Cum Buy"] > WEEKLY_BUDGET]
    else:
        within, deferred = buy, buy.iloc[0:0]

    cols = ["Item", "Category", "Supplier", "OH", "WOS", "Gross Need", "Transfer", "Buy Units",
            "Buy Cases", "cost", "Net Buy $", "GM %", "Discount %", "Deal Terms"]
    ren = {"OH": "Chain OH (TOH)", "cost": "Unit Cost"}
    def fmt(d):
        d = d[[c for c in cols if c in d.columns]].rename(columns=ren)
        if "Unit Cost" in d: d["Unit Cost"] = d["Unit Cost"].round(2)
        return d
    out, defr = fmt(within), fmt(deferred)

    urgent = low = 0
    if len(tplan):
        tplan = tplan.merge(g[["Item", "cost"]], on="Item", how="left")
        tplan["Value $"] = (tplan["Transfer In"] * tplan["cost"]).round(0)
        rank = {"STOCKOUT": 0, "Low <2wk": 1, "Top-up": 2}
        tplan["_p"] = tplan["Priority"].map(rank).fillna(3)
        tplan = (tplan.sort_values(["_p", "Value $"], ascending=[True, False])
                 .drop(columns=["_p", "cost"]).reset_index(drop=True))
        tplan = tplan[["Priority", "To Store", "Item", "To OH", "To WOS",
                       "Transfer In", "Value $", "From", "Then Buy"]]
        urgent = int((tplan["Priority"] == "STOCKOUT").sum())
        low = int((tplan["Priority"] == "Low <2wk").sum())

    K = lambda v: f"${v/1000:,.0f}K"
    L = ["=" * 70,
         f"  {label.upper()}  -  WEEKLY BUYING PLAN  -  {today:%A, %b %d, %Y}",
         "=" * 70, ""]
    if stale_days >= 1:
        L += [f"Heads up: this data is {stale_days} day(s) old (from {fdate:%b %d}); "
              "a fresh report updates it automatically.", ""]
    L += ["THE BOTTOM LINE",
          f"  Buy from vendors:      ${net_total:>11,.0f}   <- this is your actual order",
          f"  Move between stores:   ${xfer_total:>11,.0f}   <- cover this by transferring, don't buy it",
          f"  If you bought it all:  ${gross_total:>11,.0f}", ""]
    if gross_total:
        L += [f"  In plain terms: stores need about {K(gross_total)} of product. {K(xfer_total)} of that is",
              f"  already sitting in other stores - just move it. You only need to BUY {K(net_total)}.", ""]
    L += ["TRANSFERS  (move stock between stores before buying)",
          f"  - {urgent + low} urgent moves: {urgent} store(s) are OUT, {low} have under 2 weeks left",
          f"  - {max(len(tplan) - urgent - low, 0):,} routine top-ups (these can wait)",
          "  - The full move-by-move list is on the Transfer Plan tab.", ""]
    cap = f"  (capped at ${WEEKLY_BUDGET:,.0f})" if WEEKLY_BUDGET else ""
    L += [f"WHAT TO BUY:  {len(within):,} products{cap}  -  most urgent first", ""]
    L += [f"  {'#':>2}  {'PRODUCT':<36} {'ORDER':<11} {'COST':>8}   SUPPLY LEFT",
          "  " + "-" * 66]
    for i, (_, r) in enumerate(out.head(TOP_TEXT).iterrows(), 1):
        name = str(r["Item"])[:35]
        cases = int(r["Buy Cases"]) if pd.notna(r["Buy Cases"]) else 0
        order = f"{cases} case" + ("s" if cases != 1 else "")
        cost = f"${r['Net Buy $']:,.0f}"
        wos = r["WOS"]
        supply = "OUT NOW" if (pd.isna(wos) or wos <= 0) else f"{wos:.1f} weeks"
        deal = f"   {r['Discount %']:.0f}% off" if pd.notna(r.get("Discount %")) else ""
        L.append(f"  {i:>2}  {name:<36} {order:<11} {cost:>8}   {supply}{deal}")
    if len(out) > TOP_TEXT:
        L.append(f"  ...and {len(out) - TOP_TEXT:,} more (see the full list above this summary).")
    text = "\n".join([x for x in L if x is not None])

    sheets = {"Recommended Order": out}
    if len(tplan): sheets["Transfer Plan"] = tplan
    if len(defr): sheets["Deferred"] = defr
    base = "THC" if label == "THC" else label   # THC keeps legacy filename
    for folder in OUT_FOLDERS:
        os.makedirs(folder, exist_ok=True)
        try:
            write_sheets(os.path.join(folder, f"{base} Recommended Order.xlsx"), sheets)
            with open(os.path.join(folder, f"{base} Recommended Order.txt"), "w", encoding="utf-8") as fh:
                fh.write(text)
        except PermissionError:
            print(f"  locked, skipped: {folder}")
    return {"Department": label, "Items": len(out), "Gross $": gross_total,
            "Transfer $": xfer_total, "Net Buy $": net_total}


def main():
    today = datetime.date.today()
    path = find_inventory_file()
    if not path:
        raise SystemExit("No inventory export found (looked for Full Inventory Sales Report).")
    xl = pd.ExcelFile(path)
    sheet = pick_inventory_sheet(xl)
    if not sheet:
        raise SystemExit(f"No inventory sheet with OH + Product Description in {os.path.basename(path)}.")
    df = xl.parse(sheet)
    fdate = _file_date(path); stale_days = (today - fdate).days
    print(f"Source: {os.path.basename(path)}  [sheet: {sheet}]  rows={len(df)}  data date: {fdate}\n")

    df = drop_warehouses(df)
    have = lambda c: c in df.columns
    for c in ("OH", "30D", "90D", "PM", "Case Qty/Reorder Multiple",
              "Avg Cost", "Supplier Cost", "Purchase Price"):
        if have(c):
            df[c] = _num(df[c]).fillna(0)
    df["cost"] = 0.0
    for c in ("Avg Cost", "Supplier Cost", "Purchase Price"):
        if have(c):
            df["cost"] = np.where(df["cost"] > 0, df["cost"], df[c])
    df["vel"] = 0.6 * df["30D"] * 7 / 30 + 0.4 * df["90D"] * 7 / 90
    if have("PM"):
        df["need"] = df["PM"].clip(lower=0); need_basis = "Cloud Retailer per-store reorder need (PM)"
    else:
        df["need"] = (df["vel"] * TARGET_WEEKS - df["OH"]).clip(lower=0)
        need_basis = f"per-store top-up to {TARGET_WEEKS} weeks (no POS suggestion in file)"

    retail = retail_price_map(xl)
    buyers, _ = dbb.buyer_lookup()
    dep = df["Department"].astype(str).str.strip().str.lower() if "Department" in df.columns else None

    summaries = []
    for label, matches in DEPARTMENTS.items():
        ddf = df if dep is None else df[dep.isin([m.lower() for m in matches])]
        s = run_department(ddf.copy(), label, retail, buyers, today, fdate, stale_days, need_basis)
        if s:
            summaries.append(s)
            print(f"{label:<9} net buy ${s['Net Buy $']:>11,.0f}  | gross ${s['Gross $']:>11,.0f}  "
                  f"| transfer ${s['Transfer $']:>11,.0f}  | {s['Items']:>4} items")

    if summaries:
        tot = pd.DataFrame(summaries)
        print("-" * 78)
        print(f"{'ALL DEPTS':<9} net buy ${tot['Net Buy $'].sum():>11,.0f}  | gross ${tot['Gross $'].sum():>11,.0f}  "
              f"| transfer ${tot['Transfer $'].sum():>11,.0f}")
        # roll-up file for the dashboard
        for folder in OUT_FOLDERS:
            try:
                write_sheets(os.path.join(folder, "All Dept Order Summary.xlsx"), {"Summary": tot})
            except PermissionError:
                pass


if __name__ == "__main__":
    main()
