"""
THC Buying Dashboard -- Streamlit Cloud version.
Reads bundled files from the ./data folder (so it works when hosted online).
"""
import os, glob
import pandas as pd
import streamlit as st

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

st.set_page_config(page_title="THC Buying Intelligence", page_icon="🌿", layout="wide")

def find(pattern):
    """Newest file in data/ matching the pattern (tolerant of spaces, _, dates)."""
    hits = glob.glob(os.path.join(DATA, pattern))
    return max(hits, key=os.path.getmtime) if hits else None

@st.cache_data
def load(pattern, sheet=0):
    f = find(pattern)
    try:
        return pd.read_excel(f, sheet_name=sheet) if f else None
    except Exception:
        return None

db      = load("*Mock*Database*.xlsx", "THC Mock DB")
restock = load("*Buying Brief*.xlsx", "Restock & Transfer")
pricing = load("*Buying Brief*.xlsx", "Pricing Flags")
bt = find("*Buying Brief*.txt")
brief = open(bt, encoding="utf-8").read() if bt else "No brief file bundled."

st.title("🌿 THC Buying Intelligence")
st.caption("Prototype dashboard — Top Ten Liquors. Data refreshed when the data files are updated.")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Products tracked", f"{len(db):,}" if db is not None else "—")
if restock is not None:
    c2.metric("Stockouts on sellers", int((restock["Stockout?"] == "STOCKOUT").sum()))
    c3.metric("Need a buy", int((restock["Buy Qty"] > 0).sum()))
if pricing is not None:
    c4.metric("Priced above market", int(pricing["Flag"].str.startswith("ABOVE").sum()))

tabs = st.tabs(["Daily Brief", "Product Database", "Restock & Transfer", "Pricing Flags"])

with tabs[0]:
    st.subheader("Today's buying brief")
    st.text(brief)

with tabs[1]:
    if db is None:
        st.info("Mock database not bundled.")
    else:
        st.subheader("Product database")
        cats = ["(all)"] + sorted(db["Category"].dropna().astype(str).unique())
        pick = st.selectbox("Category", cats)
        q = st.text_input("Search product name")
        v = db.copy()
        if pick != "(all)": v = v[v["Category"].astype(str) == pick]
        if q: v = v[v["Product Name"].astype(str).str.contains(q, case=False, na=False)]
        st.write(f"{len(v):,} items")
        st.dataframe(v, use_container_width=True, height=460)

with tabs[2]:
    if restock is None:
        st.info("No restock data bundled.")
    else:
        st.subheader("Restock / transfer / buy actions")
        stores = ["(all)"] + sorted(restock["Store"].dropna().astype(str).unique())
        s = st.selectbox("Store", stores)
        if st.checkbox("Stockouts only"): restock = restock[restock["Stockout?"] == "STOCKOUT"]
        v = restock if s == "(all)" else restock[restock["Store"].astype(str) == s]
        st.dataframe(v, use_container_width=True, height=460)

with tabs[3]:
    if pricing is None:
        st.info("No pricing data bundled.")
    else:
        st.subheader("Pricing vs market")
        st.dataframe(pricing, use_container_width=True, height=460)

st.divider()
st.subheader("Top 15 sellers by monthly sales")
if db is not None:
    top = db.sort_values("Avg Monthly Sales", ascending=False).head(15)
    st.bar_chart(top.set_index("Product Name")["Avg Monthly Sales"])

st.subheader("Sales by category")
if db is not None:
    st.bar_chart(db.groupby("Category")["Avg Monthly Sales"].sum())
