import os
import datetime
import pandas as pd
import streamlit as st

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
st.set_page_config(page_title="THC Buying Intelligence", layout="wide")

MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

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
cat_s   = load("THC History Insights.xlsx", "Category Seasonality")
item_s  = load("THC History Insights.xlsx", "Item Seasonality")
events  = load("THC History Insights.xlsx", "Event Lifts")
yoy     = load("THC History Insights.xlsx", "YoY Growth")
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

tabs = st.tabs(["Daily Brief", "Product Database", "Restock & Transfer",
                "Pricing Flags", "Seasonality"])

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

with tabs[4]:
    if item_s is None and cat_s is None:
        st.info("History insights not found yet.")
    else:
        st.subheader("Buy ahead this month")
        this_month = MONTHS[datetime.date.today().month - 1]
        if item_s is not None and "Buy-Ahead Month" in item_s.columns:
            m = st.selectbox("Buy-ahead month", MONTHS, index=MONTHS.index(this_month))
            ba = item_s[item_s["Buy-Ahead Month"] == m].sort_values("Annual Units", ascending=False)
            st.write(f"{len(ba):,} items peak the month after {m} - order extra now")
            st.dataframe(ba, use_container_width=True, height=300)

        if yoy is not None:
            st.subheader("Year-over-year growth (shared full months)")
            st.dataframe(yoy, use_container_width=True)

        if events is not None:
            st.subheader("Event demand lifts (x a normal day)")
            st.dataframe(events, use_container_width=True)
            if "Lift x" in events.columns and "Event" in events.columns:
                st.bar_chart(events.set_index("Event")["Lift x"])

        if cat_s is not None and all(mo in cat_s.columns for mo in MONTHS):
            st.subheader("Category seasonality (1.00 = average month)")
            big = cat_s[pd.to_numeric(cat_s["Avg Units/Mo"], errors="coerce") >= 100]
            chart = big.set_index("Category")[MONTHS].T
            st.line_chart(chart)
            st.dataframe(cat_s, use_container_width=True)

st.divider()
if db is not None and sales_col and "Product Name" in db.columns:
    st.subheader("Top 15 sellers by monthly sales")
    top = db.sort_values(sales_col, ascending=False).head(15)
    st.bar_chart(top.set_index("Product Name")[sales_col])
    if "Category" in db.columns:
        st.subheader("Sales by category")
        st.bar_chart(db.groupby("Category")[sales_col].sum())
