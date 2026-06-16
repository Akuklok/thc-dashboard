"""
BUILD INVENTORY SNAPSHOT  --  per-department, per-item facts the assistant can pull from.

For every item in each department it captures: chain on-hand + on-hand by store, weekly
velocity, weeks-of-supply (WOS), cost, retail, margin %, and recent unit sales. This is
what turns the assistant into a real agent - it can answer item lookups, "where is X
overstocked", top sellers, margins, etc. by pulling the relevant rows.

Writes "<Dept> Inventory.csv" to the output folders. Reuses recommended_order's readers.
"""
import os
import pandas as pd
import numpy as np
import recommended_order as ro
import daily_buying_brief as dbb


def per_store_oh(g):
    pairs = [f"{r['Location Name']}:{int(r['OH'])}" for _, r in g.iterrows() if r["OH"] > 0]
    return ";".join(pairs)


def main():
    path = ro.find_inventory_file()
    if not path:
        raise SystemExit("No inventory report found for snapshot.")
    xl = pd.ExcelFile(path)
    sheet = ro.pick_inventory_sheet(xl)
    df = xl.parse(sheet)
    df = ro.drop_warehouses(df)
    for c in ("OH", "30D", "90D", "Avg Cost", "Supplier Cost", "Purchase Price"):
        if c in df.columns:
            df[c] = ro._num(df[c]).fillna(0)
    df["cost"] = 0.0
    for c in ("Avg Cost", "Supplier Cost", "Purchase Price"):
        if c in df.columns:
            df["cost"] = np.where(df["cost"] > 0, df["cost"], df[c])
    retail = ro.retail_price_map(xl)
    dep = df["Department"].astype(str).str.strip().str.lower() if "Department" in df.columns else None

    for label, matches in ro.DEPARTMENTS.items():
        ddf = df if dep is None else df[dep.isin([m.lower() for m in matches])]
        if ddf.empty:
            continue
        rows = []
        for item, g in ddf.groupby("Product Description"):
            oh = g["OH"].sum()
            vel = 0.6 * g["30D"].sum() * 7 / 30 + 0.4 * g["90D"].sum() * 7 / 90
            cost = g["cost"].median()
            upc = dbb.norm(g["Product Code"].iloc[0]) if "Product Code" in g else ""
            ret = retail.get(upc)
            rows.append({
                "Item": item,
                "Category": g["Category"].iloc[0] if "Category" in g else "",
                "Supplier": g["Supplier"].iloc[0] if "Supplier" in g else "",
                "Chain OH": int(oh),
                "Wk Velocity": round(vel, 1),
                "WOS": round(oh / vel, 1) if vel > 0 else "",
                "Cost": round(float(cost), 2) if pd.notna(cost) else "",
                "Retail": round(float(ret), 2) if ret else "",
                "Margin %": round((ret - cost) / ret * 100) if (ret and pd.notna(cost) and ret > 0) else "",
                "30D Units": int(g["30D"].sum()),
                "90D Units": int(g["90D"].sum()),
                "By Store OH": per_store_oh(g),
            })
        snap = pd.DataFrame(rows).sort_values("Wk Velocity", ascending=False)
        for folder in ro.OUT_FOLDERS:
            os.makedirs(folder, exist_ok=True)
            try:
                snap.to_csv(os.path.join(folder, f"{label} Inventory.csv"), index=False)
            except PermissionError:
                pass
        print(f"{label}: {len(snap)} items")


if __name__ == "__main__":
    main()
