import os
import re
import io
import datetime
import urllib.request
import urllib.parse
import pandas as pd
import streamlit as st

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")  # bundled fallback
REPO = "Akuklok/thc-dashboard"
BRANCH = "main"
st.set_page_config(page_title="THC Buying Intelligence", layout="wide")

MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

def get_users():
    try:
        return dict(st.secrets["passwords"])
    except Exception:
        return {"akuklok": "topten575corp"}

def require_login():
    if st.session_state.get("auth"):
        return
    st.title("THC Buying Intelligence")
    users = get_users()
    st.text_input("Username", key="user")
    st.text_input("Password", type="password", key="pw")
    if st.button("Log in"):
        u = str(st.session_state.get("user", "")).strip()
        p = st.session_state.get("pw", "")
        if u in users and p == users[u]:
            st.session_state["auth"] = True
            st.session_state["who"] = u
            st.rerun()
        else:
            st.error("Incorrect username or password.")
    st.stop()

require_login()

def gh_token():
    try:
        return st.secrets["github_token"]
    except Exception:
        return None

@st.cache_data(ttl=900)
def fetch_bytes(repo_path):
    tok = gh_token()
    if not tok:
        return None
    url = "https://api.github.com/repos/{}/contents/{}?ref={}".format(
        REPO, urllib.parse.quote(repo_path), BRANCH)
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github.raw",
        "Authorization": "Bearer " + tok,
        "User-Agent": "thc-dashboard"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.read()
    except Exception:
        return None

@st.cache_data(ttl=900)
def load(name, sheet):
    data = fetch_bytes("data/" + name)
    src = io.BytesIO(data) if data is not None else os.path.join(DATA, name)
    try:
        xl = pd.ExcelFile(src)
        if sheet in xl.sheet_names:
            return xl.parse(sheet)
    except Exception:
        return None
    return None

@st.cache_data(ttl=900)
def load_text(name):
    data = fetch_bytes("data/" + name)
    if data is not None:
        return data.decode("utf-8", "replace")
    f = os.path.join(DATA, name)
    return open(f, encoding="utf-8").read() if os.path.exists(f) else "No brief file."

db      = load("THC_Mock_Database.xlsx", "THC Mock DB")
restock = load("THC Daily Buying Brief.xlsx", "Restock & Transfer")
pricing = load("THC Daily Buying Brief.xlsx", "Pricing Flags")
cat_s   = load("THC History Insights.xlsx", "Category Seasonality")
item_s  = load("THC History Insights.xlsx", "Item Seasonality")
events  = load("THC History Insights.xlsx", "Event Lifts")
yoy     = load("THC History Insights.xlsx", "YoY Growth")
brief   = load_text("THC Daily Buying Brief.txt")

sales_col = None
if db is not None:
    for c in ["Avg Monthly Sales", "Average Monthly Sales"]:
        if c in db.columns:
            sales_col = c
            break

TWO_WORD = {"uncle","minny","hop","sweet","earl","bent","green","old"}

def brand_of(name):
    toks = str(name).split()
    if not toks:
        return ""
    if toks[0].lower().strip("'s") in TWO_WORD and len(toks) > 1:
        return toks[0] + " " + toks[1]
    return toks[0]

def potency_of(name):
    m = re.search(r"(\d+)\s?mg", str(name), re.I)
    return int(m.group(1)) if m else None

def add_dims(df, namecol):
    if df is None or namecol not in df.columns:
        return df
    df = df.copy()
    df.insert(1, "Brand", df[namecol].map(brand_of))
    df.insert(2, "Potency", df[namecol].map(potency_of))
    return df

def name_series():
    s = []
    if db is not None and "Product Name" in db.columns: s.append(db["Product Name"])
    if restock is not None and "Item" in restock.columns: s.append(restock["Item"])
    if pricing is not None and "Item" in pricing.columns: s.append(pricing["Item"])
    return pd.concat(s) if s else pd.Series(dtype=str)

allnames = name_series()
brand_opts = sorted([b for b in allnames.map(brand_of).dropna().unique() if b])
pot_opts = sorted([int(p) for p in allnames.map(potency_of).dropna().unique()])

st.sidebar.header("Filters")
f_brand = st.sidebar.multiselect("Brand", brand_opts)
f_pot   = st.sidebar.multiselect("Potency (mg)", pot_opts)
f_disc  = st.sidebar.slider("Min discount %", 0, 50, 0,
                            help="Applies where a Discount % is available (buys).")
if st.sidebar.button("Refresh data"):
    st.cache_data.clear()
    st.rerun()
if st.sidebar.button("Log out"):
    st.session_state["auth"] = False
    st.rerun()

def flt(df, namecol, disccol=None):
    df = add_dims(df, namecol)
    if df is None:
        return None
    if f_brand: df = df[df["Brand"].isin(f_brand)]
    if f_pot:   df = df[df["Potency"].isin(f_pot)]
    if f_disc > 0 and disccol and disccol in df.columns:
        df = df[pd.to_numeric(df[disccol], errors="coerce").fillna(0) >= f_disc]
    return df

as_of = ""
for line in brief.splitlines():
    if "DAILY BUYING BRIEF" in line and " - " in line:
        as_of = line.split(" - ", 1)[1].strip()
        break

st.title("THC Buying Intelligence")
st.caption(f"Top Ten Liquors. Signed in as {st.session_state.get('who','')}. "
           f"Data as of {as_of}. Filters in the left sidebar apply to the data pages.")
with st.expander("How to use this dashboard"):
    st.markdown("""
**Tabs**
- **Buying Brief** - the morning brief plus a sortable table of top buy / transfer actions.
- **Needs Attention** - stockouts on sellers and items priced above market, most urgent first.
- **Product Database** - the full THC catalog; filter by category or search by name.
- **Restock & Transfer** - every store-level action (transfer or buy), with a "Stockouts only" toggle.
- **Pricing Flags** - items above or below the market benchmark.
- **Seasonality** - buy-ahead items, year-over-year growth, event lifts, and category trends.

**Filters (left sidebar)** apply to all data pages: Brand, Potency (mg), Min discount %.
**Refresh data** pulls the latest immediately; **Log out** ends your session.

**Data freshness** - the header shows "Data as of <date>" and updates itself within about 15 minutes
of each morning's run. If it looks old, click **Refresh data** in the sidebar.

**Key terms**
- **Wk Velocity** - units sold per week (recent 30 days weighted over the 90-day baseline).
- **Wk $ at Risk** - weekly sales lost if the item stays out; ranks urgency.
- **Trend** - rising / falling / steady, from recent vs longer-run sales.
- **Season x** - seasonal multiplier on the buy target (above 1 heading into a peak, below 1 into a trough).
- **Gap %** - how far our price sits above or below the benchmark.
- **Vs** - which benchmark was used: a named competitor, or "Market" (the Buncha blend).
""")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Products tracked", f"{len(db):,}" if db is not None else "-")
if restock is not None:
    c2.metric("Stockouts", int((restock["Stockout?"] == "STOCKOUT").sum()))
    c3.metric("Need a buy", int((restock["Buy Qty"] > 0).sum()))
if pricing is not None:
    c4.metric("Above market", int(pricing["Flag"].str.startswith("ABOVE").sum()))

tabs = st.tabs(["Buying Brief", "Needs Attention", "Product Database",
                "Restock & Transfer", "Pricing Flags", "Seasonality"])

with tabs[0]:
    st.subheader("Today's brief")
    st.text(brief)
    if restock is not None:
        st.subheader("Top buy / transfer actions")
        r = flt(restock, "Item", "Discount %")
        r = r[r["Action"].astype(str).str.len() > 0] if r is not None else r
        cols = [c for c in ["Item","Brand","Potency","Store","Trend","Season x",
                            "Buy Qty","Transfer Qty","Wk $ at Risk","Discount %","GM %","Action"]
                if r is not None and c in r.columns]
        if r is not None:
            st.dataframe(r.sort_values("Wk $ at Risk", ascending=False)[cols],
                         use_container_width=True, height=380)
    if db is not None and sales_col and "Product Name" in db.columns:
        st.subheader("Top 15 sellers by monthly sales")
        top = db.sort_values(sales_col, ascending=False).head(15)
        st.bar_chart(top.set_index("Product Name")[sales_col])

with tabs[1]:
    st.subheader("Needs attention")
    so = flt(restock, "Item", "Discount %")
    if so is not None:
        so = so[so["Stockout?"] == "STOCKOUT"].sort_values("Wk $ at Risk", ascending=False)
        st.markdown(f"**Stockouts on sellers — {len(so):,}**")
        cols = [c for c in ["Item","Brand","Potency","Store","On Hand","Wk Velocity",
                            "Trend","Wk $ at Risk","Action"] if c in so.columns]
        st.dataframe(so[cols], use_container_width=True, height=300)
    am = flt(pricing, "Item")
    if am is not None:
        am = am[am["Flag"].astype(str).str.startswith("ABOVE")].sort_values("Gap %", ascending=False)
        st.markdown(f"**Priced above market — {len(am):,}**")
        cols = [c for c in ["Item","Brand","Potency","Our Price","Market Price","Vs","Gap %"]
                if c in am.columns]
        st.dataframe(am[cols], use_container_width=True, height=300)

with tabs[2]:
    d = flt(db, "Product Name")
    if d is None:
        st.info("Mock database not found.")
    else:
        if "Category" in d.columns:
            cats = ["(all)"] + sorted(d["Category"].dropna().astype(str).unique())
            pick = st.selectbox("Category", cats)
            if pick != "(all)":
                d = d[d["Category"].astype(str) == pick]
        q = st.text_input("Search product name")
        if q and "Product Name" in d.columns:
            d = d[d["Product Name"].astype(str).str.contains(q, case=False, na=False)]
        st.write(f"{len(d):,} items")
        st.dataframe(d, use_container_width=True, height=460)
        if sales_col and "Product Name" in d.columns and len(d):
            st.subheader("Top 15 sellers by monthly sales")
            top = d.sort_values(sales_col, ascending=False).head(15)
            st.bar_chart(top.set_index("Product Name")[sales_col])
            if "Category" in d.columns:
                st.subheader("Sales by category")
                st.bar_chart(d.groupby("Category")[sales_col].sum())

with tabs[3]:
    r = flt(restock, "Item", "Discount %")
    if r is None:
        st.info("No restock data found.")
    else:
        if st.checkbox("Stockouts only"):
            r = r[r["Stockout?"] == "STOCKOUT"]
        st.write(f"{len(r):,} store-needs")
        st.dataframe(r, use_container_width=True, height=460)

with tabs[4]:
    p = flt(pricing, "Item")
    if p is None:
        st.info("No pricing data found.")
    else:
        st.dataframe(p, use_container_width=True, height=460)

with tabs[5]:
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
            st.line_chart(big.set_index("Category")[MONTHS].T)
