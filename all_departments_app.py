"""
Store-wide Buying Intelligence (all departments) - its own Streamlit app.
Reads the live All Dept Summary from the repo. Deploy as a separate Streamlit app
pointing to this file; reuse the same secrets (github_token + [passwords]).
"""
import os, io, urllib.request, urllib.parse
import pandas as pd
import streamlit as st

REPO = "Akuklok/thc-dashboard"
BRANCH = "main"
LOW_MARGIN_PCT = 45
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
st.set_page_config(page_title="Store Buying Intelligence", layout="wide")

def get_users():
    try:
        return dict(st.secrets["passwords"])
    except Exception:
        return {"akuklok": "topten575corp"}

def require_login():
    if st.session_state.get("auth"):
        return
    st.title("Store Buying Intelligence")
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
        "Authorization": "Bearer " + tok, "User-Agent": "store-app"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.read()
    except Exception:
        return None

@st.cache_data(ttl=900)
def load_summary():
    data = fetch_bytes("data/All Dept Summary.xlsx")
    src = io.BytesIO(data) if data is not None else os.path.join(DATA, "All Dept Summary.xlsx")
    try:
        xl = pd.ExcelFile(src)
        if "Summary" in xl.sheet_names:
            return xl.parse("Summary")
    except Exception:
        return None
    return None

df = load_summary()

st.sidebar.header("View")
if st.sidebar.button("Refresh data"):
    st.cache_data.clear(); st.rerun()
if st.sidebar.button("Log out"):
    st.session_state["auth"] = False; st.rerun()

st.title("Store Buying Intelligence")
st.caption(f"Top Ten Liquors. Signed in as {st.session_state.get('who','')}. "
           "Velocity, stockouts, margin and profit for every department. "
           "Updates from the daily report; click Refresh data for the latest.")

if df is None or "Department" not in df.columns:
    st.info("Summary data not found yet (build_all_dept_summary needs to run / token not set).")
    st.stop()

depts = sorted(df["Department"].dropna().astype(str).unique())
default = depts.index("Spirits") if "Spirits" in depts else 0
dept = st.selectbox("Department", depts, index=default)
a = df[df["Department"].astype(str) == dept].copy()
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

if "Category" in a.columns:
    cats = ["(all)"] + sorted(a["Category"].dropna().astype(str).unique())
    pick = st.selectbox("Category", cats)
    if pick != "(all)":
        a = a[a["Category"].astype(str) == pick]
q = st.text_input("Search product")
if q:
    a = a[a["Product"].astype(str).str.contains(q, case=False, na=False)]
st.write(f"{len(a):,} items")
st.dataframe(a, use_container_width=True, height=420)

if "Monthly Profit $" in a.columns and len(a):
    st.subheader("Top 15 by monthly profit ($)")
    st.bar_chart(a.sort_values("Monthly Profit $", ascending=False).head(15)
                  .set_index("Product")["Monthly Profit $"])
if "Monthly Sales" in a.columns and len(a):
    st.subheader("Top 15 sellers by monthly units")
    st.bar_chart(a.sort_values("Monthly Sales", ascending=False).head(15)
                  .set_index("Product")["Monthly Sales"])
if "Monthly Sales" in a.columns and "Margin %" in a.columns and len(a):
    st.subheader("Volume vs margin (high-volume, low-margin sit lower-right)")
    st.scatter_chart(a, x="Monthly Sales", y="Margin %")
    lm = a[(a["Margin %"] < LOW_MARGIN_PCT) &
           (a["Monthly Sales"] >= a["Monthly Sales"].quantile(0.70))]
    if len(lm):
        st.subheader(f"Low-margin top sellers (margin under {LOW_MARGIN_PCT}%)")
        st.dataframe(lm.sort_values("Monthly Sales", ascending=False),
                     use_container_width=True, height=260)
