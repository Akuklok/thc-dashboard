import os
import pandas as pd
import streamlit as st

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
st.set_page_config(page_title="THC Buying Intelligence", layout="wide")

@st.cache_data
def load(name, sheet):
    f = os.path.join(DATA, name)
    try:
        xl = pd.ExcelFile(f)
        if sheet in xl.sheet_names:
            return xl.parse(sheet)
    except Exception:
        return None
    return None

db      = load("THC_Mock_Database.xlsx", "THC Mock DB")
restock = load("THC Daily Buying Brief.xlsx", "Restock & Transfer")
pricing = load("THC Daily Buying Brief.xlsx", "Pricing Flags")
btf = os.path.join(DATA, "THC Daily Buying Brief.txt")
brief = open(btf, encoding="utf-8").read() if os.path.exists(btf) else "No brief file."

sales_col = None
if db is not None:
    for c in ["Avg Monthly Sales", "Average Monthly Sales"]:
        if c in db.columns:
            sales_col = c
            break

st.title("THC Buying Intelligence")
st.caption("Prototype dashboard - Top Ten Liquors.")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Products tracked", f"{len(db):,}" if db is not None else "-")
if restock is not None:
    c2.metric("Stockouts", int((restock["Stockout?"] == "STOCKOUT").sum()))
    c3.metric("Need a buy", int((restock["Buy Qty"] > 0).sum()))
if pricing is not None:
    c4.metric("Above market", int(pricing["Flag"].str.startswith("ABOVE").sum()))

tabs = st.tabs(["Daily Brief", "Product Database", "Restock & Transfer", "Pricing Flags"])
with tabs[0]:
    st.text(brief)
with tabs[1]:
    if db is None:
        st.info("Mock database not found.")
    else:
        if "Category" in db.columns:
            cats = ["(all)"] + sorted(db["Category"].dropna().astype(str).unique())
            pick = st.selectbox("Category", cats)
        else:
            pick = "(all)"
        q = st.text_input("Search product name")
        v = db.copy()
        if pick != "(all)" and "Category" in v.columns:
            v = v[v["Category"].astype(str) == pick]
        if q and "Product Name" in v.columns:
            v = v[v["Product Name"].astype(str).str.contains(q, case=False, na=False)]
        st.write(f"{len(v):,} items")
        st.dataframe(v, use_container_width=True, height=460)
with tabs[2]:
    if restock is None:
        st.info("No restock data found.")
    else:
        st.dataframe(restock, use_container_width=True, height=460)
with tabs[3]:
    if pricing is None:
        st.info("No pricing data found.")
    else:
        st.dataframe(pricing, use_container_width=True, height=460)

st.divider()
if db is not None and sales_col and "Product Name" in db.columns:
    st.subheader("Top 15 sellers by monthly sales")
    top = db.sort_values(sales_col, ascending=False).head(15)
    st.bar_chart(top.set_index("Product Name")[sales_col])
    if "Category" in db.columns:
        st.subheader("Sales by category")
        st.bar_chart(db.groupby("Category")[sales_col].sum())
