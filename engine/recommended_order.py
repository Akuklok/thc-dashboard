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
REVIEW_BUY_USD    = 4000    # a single-item buy at/above this gets flagged for human review

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

import os, glob, re, json, datetime, calendar
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


def load_remove_set():
    """UPCs flagged 'Remove' in the product file (Remove List.csv) - don't reorder these."""
    for folder in OUT_FOLDERS + list(getattr(dbb, "INPUT_FOLDERS", [])):
        p = os.path.join(folder, "Remove List.csv")
        if os.path.exists(p):
            try:
                d = pd.read_csv(p, dtype={"upc": str})
                return set(d["upc"].map(dbb.norm))
            except Exception:
                pass
    return frozenset()


def load_new_set():
    """UPCs on the product file's New Items tab (no sales history yet - flag for manual qty)."""
    for folder in OUT_FOLDERS + list(getattr(dbb, "INPUT_FOLDERS", [])):
        p = os.path.join(folder, "New Items.csv")
        if os.path.exists(p):
            try:
                d = pd.read_csv(p, dtype={"upc": str})
                return set(d["upc"].map(dbb.norm))
            except Exception:
                pass
    return frozenset()


def load_buy_months():
    """UPC -> set of month numbers it should be bought in (real months only), from Cost Reference.
    Items with no real month listed ('ALL', 'LTO', blank, etc.) are absent = buyable any time."""
    for folder in OUT_FOLDERS + list(getattr(dbb, "INPUT_FOLDERS", [])):
        p = os.path.join(folder, "Cost Reference.csv")
        if os.path.exists(p):
            try:
                d = pd.read_csv(p, dtype=str)
                if "Buy Months" not in d.columns:
                    return {}
                out = {}
                for _, r in d.iterrows():
                    bm = str(r.get("Buy Months", "")).strip()
                    if bm and bm.lower() != "nan":
                        out[dbb.norm(r["upc"])] = {int(x) for x in bm.split("|") if x.isdigit()}
                return out
            except Exception:
                pass
    return {}


def run_department(df, label, retail, buyers, today, fdate, stale_days, need_basis,
                   remove_set=frozenset(), buy_months=None, new_items=None):
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
    removed = buy.iloc[0:0]
    if remove_set:                              # pull items flagged for removal out of the buy
        is_rm = buy["upc"].map(dbb.norm).isin(remove_set)
        removed = buy[is_rm].copy()
        buy = buy[~is_rm].copy()

    # Buy-month timing: a deal item bought outside its buy month costs more. DEFER routine
    # off-month buys to their month; keep URGENT ones (stockout / under 2wk) but flag them.
    # Only items with a real month listed are constrained; everything else is buyable any time.
    cur_month = fdate.month
    bm_wait = buy.iloc[0:0]
    buy["Buy Month"] = ""
    if buy_months:
        notes, defer = [], []
        for _, r in buy.iterrows():
            months = buy_months.get(dbb.norm(r["upc"]))
            if not months:
                notes.append(""); defer.append(False); continue
            mtxt = ", ".join(calendar.month_abbr[m] for m in sorted(months))
            if cur_month in months:
                notes.append(f"In buy-month ({mtxt})"); defer.append(False)
            elif pd.notna(r["WOS"]) and r["WOS"] < 2:          # urgent -> buy anyway
                notes.append(f"Off-month, buy anyway - urgent (deal: {mtxt})"); defer.append(False)
            else:                                              # routine -> wait for the deal
                notes.append(f"Wait for buy-month ({mtxt})"); defer.append(True)
        buy["Buy Month"] = notes
        mask = pd.Series(defer, index=buy.index)
        bm_wait = buy[mask].copy()
        buy = buy[~mask].copy()
    net_total = float(buy["Net Buy $"].sum())   # the actual buy now (excludes discontinued + buy-month waits)

    # Human-review flags: surface anything the system isn't fully confident about so a buyer can
    # eyeball it before ordering. Flagged items STAY in the order - this is a check, not a block.
    nset = new_items or frozenset()
    def _review(r):
        out = []
        if not (r["cost"] > 0):
            out.append("No cost data - verify price")
        gm = r["GM %"]
        if pd.isna(gm) or gm < 0:
            out.append("Margin looks off - check retail/cost")
        elif gm > 95:
            out.append("Margin over 95% - check retail")
        if r["Net Buy $"] >= REVIEW_BUY_USD:
            out.append(f"Large buy (${r['Net Buy $']:,.0f}) - confirm")
        if dbb.norm(r["upc"]) in nset:
            out.append("New item - set quantity by hand")
        if str(r.get("Buy Month", "")).startswith("Off-month"):
            out.append("Off buy-month - confirm timing")
        return "; ".join(out)
    buy["Review"] = [_review(r) for _, r in buy.iterrows()]

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
            "Buy Cases", "cost", "Net Buy $", "GM %", "Discount %", "Deal Terms", "Buy Month", "Review"]
    ren = {"OH": "Chain OH (TOH)", "cost": "Unit Cost"}
    def fmt(d):
        d = d[[c for c in cols if c in d.columns]].rename(columns=ren)
        if "Unit Cost" in d: d["Unit Cost"] = d["Unit Cost"].round(2)
        return d
    out, defr = fmt(within), fmt(deferred)
    removed_out = fmt(removed) if len(removed) else None
    bm_wait_out = fmt(bm_wait) if len(bm_wait) else None
    rev = within[within["Review"].astype(str).str.len() > 0] if len(within) else within
    review_out = fmt(rev) if len(rev) else None

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

    pct = f"{xfer_total / gross_total * 100:.0f}%" if gross_total else "0%"
    L = ["=" * 74,
         f"  RECOMMENDED {label.upper()} ORDER          {today:%A, %b %d, %Y}",
         "=" * 74, ""]
    if stale_days >= 1:
        L += [f"  (data is {stale_days} day(s) old, from {fdate:%b %d} - refreshes automatically)", ""]
    L += [f"  Gross need (all stores) .....  ${gross_total:>11,.0f}",
          f"  Coverable by transfer .......  ${xfer_total:>11,.0f}   ({pct})",
          f"  NET BUY from vendors ........  ${net_total:>11,.0f}   <- the order", ""]
    cap = f"  (capped at ${WEEKLY_BUDGET:,.0f})" if WEEKLY_BUDGET else ""
    L += [f"  {len(within):,} items{cap}   |   {int(within['Buy Units'].sum()):,} units to buy   |   "
          f"{int(g['Transfer'].sum()):,} units to rebalance",
          f"  Transfers: {len(tplan):,} moves  -  {urgent + low} urgent "
          f"({urgent} stockouts, {low} under 2wk), {max(len(tplan) - urgent - low, 0):,} routine",
          f"  Ranked by {basis}.",
          (f"  >> {len(review_out)} item(s) flagged for your review before ordering (see NEEDS REVIEW below)."
           if review_out is not None and len(review_out) else None), "",
          f"  TOP {TOP_TEXT} TO BUY",
          f"  {'#':>2}  {'ITEM':<36} {'BUY':<12} {'NET $':>8} {'GROSS':>6} {'XFER':>5} {'WOS':>5}",
          "  " + "-" * 72]
    for i, (_, r) in enumerate(out.head(TOP_TEXT).iterrows(), 1):
        name = str(r["Item"])[:35]
        buy = f"{int(r['Buy Units'])}u/{int(r['Buy Cases'])}cs"
        netd = f"${r['Net Buy $']:,.0f}"
        wos = "OUT" if (pd.isna(r["WOS"]) or r["WOS"] <= 0) else f"{r['WOS']:.1f}"
        disc = f"  {r['Discount %']:.0f}% off" if pd.notna(r.get("Discount %")) else ""
        L.append(f"  {i:>2}  {name:<36} {buy:<12} {netd:>8} {int(r['Gross Need']):>6} "
                 f"{int(r['Transfer']):>5} {wos:>5}{disc}")
    if len(out) > TOP_TEXT:
        L.append(f"  ...and {len(out) - TOP_TEXT:,} more in the full list.")
    if review_out is not None and len(review_out):
        L += ["", f"  NEEDS REVIEW ({len(review_out)} items the system isn't fully sure about - check before ordering):"]
        for _, r in review_out.head(15).iterrows():
            L.append(f"    {str(r['Item'])[:40]:40}  {r['Review']}")
        if len(review_out) > 15:
            L.append(f"    ...and {len(review_out) - 15} more (see Needs Review tab).")
    if removed_out is not None and len(removed_out):
        L += ["", f"  BEING REMOVED - DO NOT REORDER ({len(removed_out)} discontinued items still selling/low - run down stock):"]
        for _, r in removed_out.head(15).iterrows():
            L.append(f"    {str(r['Item'])[:42]:42}  (would have bought {int(r['Buy Units'])}u)")
        if len(removed_out) > 15:
            L.append(f"    ...and {len(removed_out) - 15} more (see Being Removed tab).")
    if bm_wait_out is not None and len(bm_wait_out):
        wait_val = float(bm_wait["Net Buy $"].sum())
        L += ["", f"  WAIT FOR BUY-MONTH ({len(bm_wait_out)} routine deal items out of their buy month - "
                  f"${wait_val:,.0f} cheaper to buy in-month):"]
        for _, r in bm_wait_out.head(15).iterrows():
            L.append(f"    {str(r['Item'])[:42]:42}  {r.get('Buy Month', '')}")
        if len(bm_wait_out) > 15:
            L.append(f"    ...and {len(bm_wait_out) - 15} more (see Buy-Month Wait tab).")
    text = "\n".join([x for x in L if x is not None])

    sheets = {"Recommended Order": out}
    if review_out is not None and len(review_out): sheets["Needs Review"] = review_out
    if removed_out is not None and len(removed_out): sheets["Being Removed"] = removed_out
    if bm_wait_out is not None and len(bm_wait_out): sheets["Buy-Month Wait"] = bm_wait_out
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
    remove_set = load_remove_set()
    buy_months = load_buy_months()
    new_items = load_new_set()
    dep = df["Department"].astype(str).str.strip().str.lower() if "Department" in df.columns else None

    summaries = []
    for label, matches in DEPARTMENTS.items():
        ddf = df if dep is None else df[dep.isin([m.lower() for m in matches])]
        s = run_department(ddf.copy(), label, retail, buyers, today, fdate, stale_days, need_basis,
                           remove_set, buy_months, new_items)
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
        stamp = {"built_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "data_date": fdate.isoformat()}
        for folder in OUT_FOLDERS:
            try:
                write_sheets(os.path.join(folder, "All Dept Order Summary.xlsx"), {"Summary": tot})
                with open(os.path.join(folder, "status.json"), "w", encoding="utf-8") as fh:
                    json.dump(stamp, fh)
            except PermissionError:
                pass


if __name__ == "__main__":
    main()
