"""
Non-retail locations (warehouses / distribution), kept separate from stores.

A warehouse must never be treated as a store (no stockout/need flags, not a
transfer point) and must never be merged with a similarly named store -- e.g.
the Blaine2 WAREHOUSE is a different location from the Blaine STORE.

If a warehouse ever appears in a future report, add its exact Location Name here.
"""
WAREHOUSES = ["Blaine2"]

def drop_warehouses(df, col="Location Name"):
    """Return df with warehouse/non-retail rows removed (store-level data only)."""
    if col in df.columns:
        return df[~df[col].astype(str).str.strip().isin(WAREHOUSES)].copy()
    return df
