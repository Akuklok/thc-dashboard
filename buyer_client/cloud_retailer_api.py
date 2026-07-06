"""
Cloud Retailer Web API connector.
Reads credentials/base URL from cloud_retailer_api.txt (LOCAL ONLY - never commit).

  get_token()                       -> bearer token (cache for 8h)
  fetch_flat(report, query)         -> list of rows (use for < 500 rows)
  fetch_all(report, query)          -> list of rows, follows paging (any size)

`report` is the report name as it appears in the report URL after /Reports/ViewReport/
`query`  is the filter/sort query string from that same URL (everything after the '?').
"""
import os, json, urllib.request, urllib.parse, urllib.error

CONF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloud_retailer_api.txt")

def _conf():
    d = {}
    if os.path.exists(CONF):
        with open(CONF, encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.split("=", 1)
                    d[k.strip()] = v.strip()
    # env vars override / fill in (so CI can supply creds via secrets, no committed file)
    for key, env in (("base", "CR_BASE"), ("username", "CR_USERNAME"),
                     ("password", "CR_PASSWORD"), ("token_url", "CR_TOKEN_URL")):
        if os.environ.get(env):
            d[key] = os.environ[env]
    if not d.get("base"):
        raise SystemExit("Set 'base' in cloud_retailer_api.txt or the CR_BASE env var")
    return d

def get_token():
    c = _conf()
    body = urllib.parse.urlencode({
        "username": c["username"], "password": c["password"], "grant_type": "password"
    }).encode()
    token_url = c.get("token_url") or (c["base"].rstrip("/") + "/token")   # prod URL may differ; override in config
    req = urllib.request.Request(token_url, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "Accept": "application/json",
                                          "User-Agent": "Mozilla/5.0 (compatible; TopTenBuyer/1.0)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["access_token"]

def _get(url, token):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token,
                                               "Accept": "application/json",
                                               "User-Agent": "Mozilla/5.0 (compatible; TopTenBuyer/1.0)"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)

def fetch_flat(report, query="", token=None):
    c = _conf(); token = token or get_token()
    url = c["base"].rstrip("/") + "/Api/Reports/ViewFlatReport/" + report
    if query:
        url += "?" + query
    return _get(url, token)

def _with_scheme(url, base):
    if url.startswith("http"):
        return url
    scheme = base.split("://", 1)[0] if "://" in base else "https"
    return scheme + "://" + url.lstrip("/")

def fetch_all(report, query="", token=None, page=500, max_pages=400, pause=0.25):
    """ViewReport with Skip/Take paging. Response is {estimatedTotal, items:[{item:{...}}]}.
    Pages at `page` rows with a small pause between calls (gentle on the server)."""
    import time, sys
    c = _conf(); token = token or get_token()
    base = c["base"].rstrip("/")
    rows, skip, total = [], 0, None
    t0 = time.time()
    for pno in range(max_pages):
        sep = "&" if query else ""
        url = f"{base}/Api/Reports/ViewReport/{report}?{query}{sep}Skip={skip}&Take={page}"
        data = _get(url, token)
        print(f"  page {pno+1}: {len(rows)} rows so far, {time.time()-t0:.0f}s elapsed", file=sys.stderr, flush=True)
        if isinstance(data, dict):
            items = data.get("items", []); total = data.get("estimatedTotal", total)
        elif isinstance(data, list):
            items = data
        else:
            items = []
        got = 0
        for el in items:
            it = el.get("item") if (isinstance(el, dict) and "item" in el) else (el if isinstance(el, dict) else None)
            if it:
                rows.append(it); got += 1
        if got == 0:
            break
        skip += page
        if total is not None and skip >= total:
            break
        time.sleep(pause)
    return rows

# ---- "Today's sales" summary (near-real-time), aggregated for the buying app ----
# Cloud Retailer department -> app department (Other = Miscellaneous + Tobacco), matches the engine.
DEPT_MAP = {"THC": "THC", "Beer": "Beer", "Wine": "Wine", "Spirits": "Spirits",
            "Miscellaneous": "Other", "Tobacco": "Other"}
# Fixed, known-good encoded filter blobs from the report URL John Foster supplied.
_SHIFT_BLOB = ("%0D%0A%20%20%20%20%20%20%20%20%7B%0D%0A%20%20%20%20%20%20%20%20type%3A%20%22RegisterShiftIdType%22%2C"
               "%0D%0A%20%20%20%20%20%20%20%20columns%3A%20%5B%0D%0A%20%20%20%20%20%20%20%20%7Bname%3A%20%22ShiftId%22%2C%20type%3A%20%22int%22%7D%2C"
               "%0D%0A%20%20%20%20%20%20%20%20%7Bname%3A%20%22DeviceId%22%2C%20type%3A%20%22int%22%7D%0D%0A%20%20%20%20%20%20%20%20%5D%0D%0A%20%20%20%20%20%20%20%20%7D%0D%0A%20%20%20%20%20%20")
_PRODTYPE = "Filter%20out%20vouchers%2Fgift%20cards%20and%20Not%20Contributing%20To%20Sales"


def _day_query(date_str):
    """SaleReport-Detailed query for one calendar day (date_str like '6/24/2026')."""
    fromv = urllib.parse.quote(f"{date_str} 12:00 AM")
    tov = urllib.parse.quote(f"{date_str} 11:59 PM")
    return ("Cols=LocationName~Department~Category~ProductCode~Description~Quantity~SoldPrice~Total"
            "&GroupIndex=0&SortBy=-TransactionTime"
            f"&Filters[0].IsCustom=True&Filters[0].PropertyName=%40fromDate&Filters[0].Value={fromv}"
            f"&Filters[1].IsCustom=True&Filters[1].OperatorJoin=BAnd&Filters[1].PropertyName=%40toDate&Filters[1].Value={tov}"
            "&Filters[2].IsCustom=True&Filters[2].OperatorJoin=BAnd&Filters[2].AdditionalData=" + _SHIFT_BLOB
            + "&Filters[2].PropertyName=%40shiftIds"
            "&Filters[3].IsCustom=True&Filters[3].OperatorJoin=BAnd&Filters[3].PropertyName=%40productType&Filters[3].Value=" + _PRODTYPE)


def sales_summary(date_str=None, max_pages=400):
    """Pull one day of detailed sales and roll it up per app-department: chain + per-store + top sellers.
    Returns a compact dict the app serves as 'Today's sales'."""
    import datetime, collections
    if not date_str:
        try:                                  # store-local (Central) day, so it doesn't roll over at UTC midnight
            from zoneinfo import ZoneInfo
            d = datetime.datetime.now(ZoneInfo("America/Chicago")).date()
        except Exception:
            d = datetime.date.today()
        date_str = f"{d.month}/{d.day}/{d.year}"
    rows = fetch_all("SaleReport-Detailed", _day_query(date_str), max_pages=max_pages)
    leaves = [r for r in rows if str(r.get("productCode") or "").strip()]
    store_agg = collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0.0]))   # dept->store->[units,$]
    prod_agg = collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0.0, ""]))  # dept->code->[units,$,name]
    chain_u = chain_s = 0.0
    for r in leaves:
        dep = DEPT_MAP.get(str(r.get("department") or "").strip(), "Other")
        u = float(r.get("quantity") or 0); s = float(r.get("total") or 0)
        store = str(r.get("locationName") or "").strip()
        code = str(r.get("productCode") or "").strip()
        chain_u += u; chain_s += s
        sv = store_agg[dep][store]; sv[0] += u; sv[1] += s
        pv = prod_agg[dep][code]; pv[0] += u; pv[1] += s; pv[2] = str(r.get("description") or "").strip()
    depts = {}
    for dep in set(list(store_agg) + list(prod_agg)):
        by_store = sorted(({"store": k, "units": round(v[0]), "sales": round(v[1], 2)}
                           for k, v in store_agg[dep].items()), key=lambda x: -x["sales"])
        top = sorted(({"item": v[2], "code": k, "units": round(v[0]), "sales": round(v[1], 2)}
                      for k, v in prod_agg[dep].items()), key=lambda x: -x["sales"])[:25]
        depts[dep] = {"units": round(sum(x["units"] for x in by_store)),
                      "sales": round(sum(x["sales"] for x in by_store), 2),
                      "by_store": by_store, "top": top}
    return {"as_of": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "date": date_str, "rows": len(leaves),
            "chain": {"units": round(chain_u), "sales": round(chain_s, 2)}, "depts": depts}


if __name__ == "__main__":
    import sys, json as _json
    if "--summary" in sys.argv:
        cap = 400
        for a in sys.argv:
            if a.startswith("--pages="):
                cap = int(a.split("=", 1)[1])
        try:
            summ = sales_summary(max_pages=cap)
        except Exception as e:
            # e.g. Cloud Retailer token blocked (400): skip quietly, don't fail the job (no failure emails)
            print("live sales pull skipped:", e)
            raise SystemExit(0)
        # write to <cwd>/data so the GitHub Action (cwd = repo root) updates the app's data/ folder
        out = os.environ.get("LIVE_OUT") or os.path.join(os.getcwd(), "data", "live_sales.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            _json.dump(summ, f)
        print(f"rows={summ['rows']}  chain=${summ['chain']['sales']:,.0f}  depts={list(summ['depts'])}")
        print("wrote", out)
    else:
        print("token OK:", bool(get_token()))

