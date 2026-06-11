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
LOW_MARGIN_PCT = 45   # flag top sellers whose margin is below this

# ----------------------------- login gate (username + password) -----------------------------
def get_users():
    try:
        return dict(st.secrets["passwords"])
    except Exception:
        return {"akuklok": "topten575corp"}   # built-in login (private repo)

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

# ----------------------------- data loading (live from the repo) -----------------------------
# Read the data files straight from GitHub so the app always reflects the latest
# daily push - no redeploy needed. Falls back to the bundled copy if the fetch fails.
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
alldept = load("All Dept Summary.xlsx", "Summary")
brief   = load_text("THC Daily Buying Brief.txt")

sales_col = None
if db is not None:
    for c in ["Avg Monthly Sales", "Average Monthly Sales"]:
        if c in db.columns:
            sales_col = c
            break

# ----------------------------- brand / potency parsing -----------------------------
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

# build filter options from everything we have
def name_series():
    s = []
    if db is not None and "Product Name" in db.columns: s.append(db["Product Name"])
    if restock is not None and "Item" in restock.columns: s.append(restock["Item"])
    if pricing is not None and "Item" in pricing.columns: s.append(pricing["Item"])
    return pd.concat(s) if s else pd.Series(dtype=str)

allnames = name_series()
brand_opts = sorted([b for b in allnames.map(brand_of).dropna().unique() if b])
pot_opts = sorted([int(p) for p in allnames.map(potency_of).dropna().unique()])

# ----------------------------- sidebar filters -----------------------------
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

LOW_MARGIN_COLS = ["Product Name", "Brand", "Potency", "Category",
                   "Average Monthly Sales", "Margin %", "Monthly Revenue $", "Monthly Profit $"]

def low_margin_sellers(source):
    """High-volume items whose margin is under LOW_MARGIN_PCT - re-price/renegotiate
    candidates. 'High volume' = top ~30% of sellers. Respects the sidebar filters."""
    d = flt(source, "Product Name")
    if d is None or "Margin %" not in d.columns or "Average Monthly Sales" not in d.columns:
        return None
    d = d.copy()
    d["Margin %"] = pd.to_numeric(d["Margin %"], errors="coerce")
    d["Average Monthly Sales"] = pd.to_numeric(d["Average Monthly Sales"], errors="coerce")
    sells = d[d["Average Monthly Sales"] > 0]
    if sells.empty:
        return None
    vol_cut = sells["Average Monthly Sales"].quantile(0.70)
    flagged = sells[(sells["Average Monthly Sales"] >= vol_cut) & (sells["Margin %"] < LOW_MARGIN_PCT)]
    return flagged.sort_values("Average Monthly Sales", ascending=False)

# ----------------------------- header + metrics -----------------------------
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
                "Restock & Transfer", "Pricing Flags", "Seasonality", "All Departments"])

# ----------------------------- Buying Brief -----------------------------
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
    lm = low_margin_sellers(db)
    if lm is not None and len(lm):
        st.subheader("Low-margin top sellers")
        st.caption(f"High volume but margin under {LOW_MARGIN_PCT}% — re-price or renegotiate.")
        c = [x for x in LOW_MARGIN_COLS if x in lm.columns]
        st.dataframe(lm[c], use_container_width=True, height=260)
    if db is not None and sales_col and "Product Name" in db.columns:
        st.subheader("Top 15 sellers by monthly sales")
        top = db.sort_values(sales_col, ascending=False).head(15)
        st.bar_chart(top.set_index("Product Name")[sales_col])

# ----------------------------- Needs Attention -----------------------------
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
    lm = low_margin_sellers(db)
    if lm is not None and len(lm):
        st.markdown(f"**Low-margin top sellers — {len(lm):,}**  "
                    f"(high volume but margin under {LOW_MARGIN_PCT}% — re-price or renegotiate)")
        c = [x for x in LOW_MARGIN_COLS if x in lm.columns]
        st.dataframe(lm[c], use_container_width=True, height=300)

# ----------------------------- Product Database -----------------------------
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
            st.subheader("Top 15 sellers by monthly sales (units)")
            top = d.sort_values(sales_col, ascending=False).head(15)
            st.bar_chart(top.set_index("Product Name")[sales_col])
            if "Category" in d.columns:
                st.subheader("Sales by category")
                st.bar_chart(d.groupby("Category")[sales_col].sum())
        if "Monthly Profit $" in d.columns and "Product Name" in d.columns and len(d):
            st.subheader("Top 15 by monthly profit ($)")
            topp = d.sort_values("Monthly Profit $", ascending=False).head(15)
            st.bar_chart(topp.set_index("Product Name")["Monthly Profit $"])
        if "Average Monthly Sales" in d.columns and "Margin %" in d.columns and len(d):
            st.subheader("Volume vs margin (high-volume, low-margin items sit lower-right)")
            st.scatter_chart(d, x="Average Monthly Sales", y="Margin %",
                             color="Category" if "Category" in d.columns else None)

# ----------------------------- Restock & Transfer -----------------------------
with tabs[3]:
    r = flt(restock, "Item", "Discount %")
    if r is None:
        st.info("No restock data found.")
    else:
        if st.checkbox("Stockouts only"):
            r = r[r["Stockout?"] == "STOCKOUT"]
        st.write(f"{len(r):,} store-needs")
        st.dataframe(r, use_container_width=True, height=460)

# ----------------------------- Pricing Flags -----------------------------
with tabs[4]:
    p = flt(pricing, "Item")
    if p is None:
        st.info("No pricing data found.")
    else:
        st.dataframe(p, use_container_width=True, height=460)

# ----------------------------- Seasonality -----------------------------
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

# ----------------------------- All Departments (store-wide, generic) -----------------------------
with tabs[6]:
    if alldept is None or "Department" not in alldept.columns:
        st.info("All-department summary not found yet (build_all_dept_summary).")
    else:
        st.caption("Store-wide view from the daily file: velocity, stockouts, margin and profit "
                   "for every department. The same engine as THC, minus the THC-only history/competitor tuning.")
        depts = sorted(alldept["Department"].dropna().astype(str).unique())
        dept = st.selectbox("Department", depts,
                            index=depts.index("Spirits") if "Spirits" in depts else 0)
        a = alldept[alldept["Department"].astype(str) == dept].copy()
        for c in ["Monthly Revenue $", "Monthly Profit $", "Stores Out", "Margin %", "Monthly Sales"]:
            if c in a.columns:
                a[c] = pd.to_numeric(a[c], errors="coerce")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Items", f"{len(a):,}")
        if "Stores Out" in a.columns:
            m2.metric("Items short somewhere", int((a["Stores Out"] > 0).sum()))
        if "Monthly Revenue $" in a.columns:
            m3.metric("Monthly revenue", f"${a['Monthly Revenue $'].sum():,.0f}")
        if "Monthly Profit $" in a.columns:
            m4.metric("Monthly profit", f"${a['Monthly Profit $'].sum():,.0f}")

        q = st.text_input("Search product", key="alldept_q")
        if q:
            a = a[a["Product"].astype(str).str.contains(q, case=False, na=False)]
        st.dataframe(a, use_container_width=True, height=380)

        if "Monthly Profit $" in a.columns and len(a):
            st.subheader("Top 15 by monthly profit ($)")
            st.bar_chart(a.sort_values("Monthly Profit $", ascending=False).head(15)
                          .set_index("Product")["Monthly Profit $"])
        if "Monthly Sales" in a.columns and "Margin %" in a.columns and len(a):
            st.subheader("Volume vs margin (high-volume, low-margin sit lower-right)")
            st.scatter_chart(a, x="Monthly Sales", y="Margin %")
        if {"Monthly Sales", "Margin %"}.issubset(a.columns):
            lm = a[(a["Margin %"] < LOW_MARGIN_PCT) &
                   (a["Monthly Sales"] >= a["Monthly Sales"].quantile(0.70))]
            if len(lm):
                st.subheader(f"Low-margin top sellers (margin under {LOW_MARGIN_PCT}%)")
                st.dataframe(lm.sort_values("Monthly Sales", ascending=False),
                             use_container_width=True, height=260)
