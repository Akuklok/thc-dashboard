"""
BATCH DEAL EVALUATOR  --  runs every deal in the buyer sheet against current
velocity / stock / seasonality and ranks what's worth buying.

Reads the buyer 'THC' tab (Deal Description, our cost, Net/Unit deal cost, min Buy
Qty, free goods, GM%) and the current inventory export (weekly velocity, on hand),
then for each real deal computes: savings, margin at the deal, weeks-to-sell-through,
a suggested buy quantity, and a BUY / CAUTION / SKIP verdict.

Output: "THC Deal Evaluation.xlsx" (+ .txt summary), ranked by weekly $ saved.
Runs on existing data - no API/SFTP.
"""
import os, glob, datetime
import pandas as pd
import numpy as np
from filepick import read_newest
from config_locations import drop_warehouses
import daily_buying_brief as dbb

BUYER_FOLDERS = [r"C:\Users\Anna K\OneDrive - Top Ten Liquors\THC Reports",
                 r"C:\Users\Anna K\Downloads",
                 r"C:\Users\Anna K\OneDrive - Top Ten Liquors\Documents",
                 r"C:\Users\Anna K\OneDrive - Top Ten Liquors\Documents\Claude"]
OUT_FOLDERS = [r"C:\Users\Anna K\Downloads",
               r"C:\Users\Anna K\OneDrive - Top Ten Liquors\THC Reports"]
DEAL_TARGET_WEEKS = 8     # a deal buy can stock up to ~this many weeks
MAX_SELLTHROUGH   = 16    # beyond this = overstock risk
MIN_DISCOUNT_PCT  = 3     # below this off cost, not really a deal

# buyer 'THC' tab column indexes (header is row index 2)
C = dict(upc=8, desc=9, cat=12, units_case=15, deal=16, reg_cost=19,
         buy_qty=22-1, free_qty=22, net_unit=25, gm=29, retail=27)
C["buy_qty"] = 21

def find_buyer_sheet():
    cands = []
    for d in BUYER_FOLDERS:
        cands += glob.glob(os.path.join(d, "THC*.xlsx"))
    cands = [c for c in cands if not os.path.basename(c).startswith("~$")]
    for f in sorted(cands, key=os.path.getmtime, reverse=True):
        try:
            head = pd.read_excel(f, sheet_name="THC", header=None, nrows=3)
            if head.iloc[2].astype(str).str.contains("Deal Description").any():
                return f
        except Exception:
            continue
    return None

def inventory_by_upc():
    df, _ = read_newest("Full Inventory Sales Report*.xlsx", dbb.INPUT_FOLDERS,
                        lambda f: pd.read_excel(f, sheet_name=0))
    df = df[df["Department"].astype(str).str.contains("THC", case=False, na=False)].copy()
    df = drop_warehouses(df)
    for c in ("OH", "30D", "90D"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce").fillna(0)
    df["upc"] = df["Product Code"].map(dbb.norm)
    df["price"] = pd.to_numeric(df["Price"], errors="coerce")
    g = df.groupby("upc").agg(OH=("OH", "sum"), u30=("30D", "sum"), u90=("90D", "sum"),
                              price=("price", "median"),
                              cat=("Category", "first")).reset_index()
    g["wk_vel"] = 0.6*g["u30"]*7/30 + 0.4*g["u90"]*7/90
    return g.set_index("upc")

def main():
    today = datetime.date.today()
    bf = find_buyer_sheet()
    if not bf:
        raise SystemExit("No buyer THC sheet found.")
    print("buyer sheet:", os.path.basename(bf))
    raw = pd.read_excel(bf, sheet_name="THC", header=None).iloc[3:]
    inv = inventory_by_upc()
    ISF = dbb.item_seasonal_factors(today); SF = dbb.seasonal_factors(today)

    rows = []
    for _, r in raw.iterrows():
        upc = dbb.norm(r[C["upc"]])
        if not upc:
            continue
        units_case = pd.to_numeric(r[C["units_case"]], errors="coerce")
        units_case = int(units_case) if pd.notna(units_case) and units_case > 0 else 1
        reg_case = pd.to_numeric(r[C["reg_cost"]], errors="coerce")   # Top Ten Invoice = CASE cost
        deal_cost = pd.to_numeric(r[C["net_unit"]], errors="coerce")  # Net/Unit = per-unit deal cost
        if pd.isna(reg_case) or pd.isna(deal_cost) or reg_case <= 0 or deal_cost <= 0:
            continue
        reg_cost = reg_case / units_case                              # regular per-unit cost
        savings = reg_cost - deal_cost
        if savings <= 0.01:
            continue   # no meaningful per-unit cost break
        disc = savings / reg_cost * 100
        if disc > 80:
            continue   # implausible (bad/blank cost data) - skip rather than show garbage
        desc = str(r[C["desc"]]).strip()
        cat  = str(r[C["cat"]]).strip()
        retail = pd.to_numeric(r[C["retail"]], errors="coerce")
        buy_cases = pd.to_numeric(r[C["buy_qty"]], errors="coerce")
        min_units = int(buy_cases*units_case) if pd.notna(buy_cases) and buy_cases > 0 else 0

        iv = inv.loc[upc] if upc in inv.index else None
        OH = float(iv["OH"]) if iv is not None else 0.0
        wk_vel = float(iv["wk_vel"]) if iv is not None else 0.0
        seasonF = ISF.get(upc) or SF.get(cat) or 1.0
        eff_vel = wk_vel * seasonF
        WOS = round(OH/wk_vel, 1) if wk_vel > 0 else None

        # suggested buy: up to DEAL_TARGET_WEEKS of supply, at least the min buy
        target_units = max(0, eff_vel*DEAL_TARGET_WEEKS - OH)
        suggested = int(np.ceil(target_units/units_case)*units_case)
        suggested = max(suggested, min_units)
        weeks_to_sell = round(suggested/eff_vel, 1) if eff_vel > 0 else None
        margin_at = (retail-deal_cost)/retail*100 if pd.notna(retail) and retail else None
        weekly_savings = savings * eff_vel   # $/week the deal saves at current pace

        if eff_vel <= 0:
            verdict = "SKIP - not selling (any quantity would sit)"
        elif suggested <= 0:
            verdict = f"WELL-STOCKED ({WOS} wks on hand) - skip the deal for now"
        elif disc < MIN_DISCOUNT_PCT:
            verdict = f"MARGINAL - only {disc:.0f}% off cost"
        elif weeks_to_sell and weeks_to_sell > MAX_SELLTHROUGH:
            good = max(min_units, int(np.ceil(eff_vel*DEAL_TARGET_WEEKS/units_case)*units_case))
            verdict = f"CAUTION - {suggested}u = ~{weeks_to_sell:.0f} wks (overstock); consider ~{good}u"
        else:
            note = " (rising into season)" if seasonF >= 1.1 else " (cooling)" if seasonF <= 0.9 else ""
            verdict = f"BUY ~{suggested}u ({suggested//units_case}cs) - sells in ~{weeks_to_sell:.0f} wks{note}"

        rows.append({"Item": desc, "Category": cat, "Deal": str(r[C['deal']]).strip(),
                     "Reg Cost": round(reg_cost, 2), "Deal Cost": round(deal_cost, 2),
                     "Savings/Unit": round(savings, 2), "Disc %": round(disc, 0),
                     "Wk Velocity": round(eff_vel, 1), "WOS": WOS, "On Hand": int(OH),
                     "Min Buy (u)": min_units, "Suggested Buy (u)": suggested,
                     "Wks to Sell": weeks_to_sell,
                     "Margin @ Deal %": round(margin_at, 0) if margin_at is not None else None,
                     "Weekly $ Saved": round(weekly_savings, 0), "Verdict": verdict})

    res = pd.DataFrame(rows).sort_values("Weekly $ Saved", ascending=False)
    buys = res[res["Verdict"].str.startswith("BUY")]
    L = ["="*72, f"  THC DEAL EVALUATION  -  {today:%A %b %d, %Y}", "="*72, "",
         f"{len(res)} deals evaluated | {len(buys)} worth buying now (ranked by $ saved/week)", ""]
    for _, r in buys.head(20).iterrows():
        L.append(f"  ${r['Weekly $ Saved']:>5,.0f}/wk saved  {str(r['Item'])[:38]:38} "
                 f"{r['Disc %']:.0f}% off  -> {r['Verdict']}")
    text = "\n".join(L)
    print(text)
    from xlsx_helper import write_sheets
    for folder in OUT_FOLDERS:
        os.makedirs(folder, exist_ok=True)
        try:
            write_sheets(os.path.join(folder, "THC Deal Evaluation.xlsx"), {"Deals": res})
            with open(os.path.join(folder, "THC Deal Evaluation.txt"), "w", encoding="utf-8") as fh:
                fh.write(text)
        except PermissionError:
            print("  locked, skipped:", folder)

if __name__ == "__main__":
    main()
