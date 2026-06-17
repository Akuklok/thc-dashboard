"""
BUYER CLIENT  --  the installable AI buying assistant (cross-platform web app).

This is the endgame client (not Streamlit): a custom app each buyer opens, that shows
their department's "what to do today" and answers "ask anything" with Claude.

  - Cross-platform: runs in any browser; installable as an app (PWA) on Windows + Mac.
  - Role-based: each login sees only their departments.
  - Reuses the brain: reads the per-department order/transfer files the pipeline produces.
  - Claude chat: the API key stays server-side (never in the browser).

Run locally:  python server.py   (then open http://localhost:8520)
Deploy later: same code goes on a small cloud host so all 3 buyers reach it from any computer.
"""
import os, io, re, glob, json, base64, time, urllib.request, urllib.parse, urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
DATA_DIRS = [r"C:\Users\Anna K\Downloads",
             r"C:\Users\Anna K\OneDrive - Top Ten Liquors\THC Reports"]
ANTHROPIC_KEYFILE = os.path.join(os.path.dirname(HERE), "anthropic_key.txt")
GEMINI_KEYFILE = os.path.join(os.path.dirname(HERE), "gemini_key.txt")
CLAUDE_MODEL = "claude-sonnet-4-6"
GEMINI_MODEL = "gemini-2.5-flash"   # cheap + fast (free tier covers low usage)
PORT = int(os.environ.get("PORT", 8520))
HOST = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
# Cloud data source (used when hosted): the same repo the pipeline pushes order files to.
GH_OWNER, GH_REPO, GH_BRANCH = "Akuklok", "thc-dashboard", "main"


def gh_token():
    return os.environ.get("GITHUB_TOKEN", "")


def get_bytes(name):
    """Return the bytes of a data file. Hosted: from the repo's data/ folder; local: newest match."""
    if gh_token():
        url = ("https://api.github.com/repos/%s/%s/contents/data/%s?ref=%s"
               % (GH_OWNER, GH_REPO, urllib.parse.quote(name), GH_BRANCH))
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + gh_token(),
                                                   "Accept": "application/vnd.github+json",
                                                   "User-Agent": "ttb"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return base64.b64decode(json.load(r)["content"])
        except Exception:
            return None
    f = newest(name)
    return open(f, "rb").read() if f else None

ALL_DEPTS = ["THC", "Wine", "Spirits", "Beer", "Other"]
USERS = {"akuklok": "topten575corp", "wine": "wine2026",
         "thc": "thc2026", "beerspirits": "bs2026"}
USER_DEPTS = {
    "akuklok": ALL_DEPTS,
    "wine": ["Wine"],
    "thc": ["THC", "Other", "Beer"],
    "beerspirits": ["Beer", "Spirits"],
}


def newest(pattern):
    hits = []
    for d in DATA_DIRS:
        hits += glob.glob(os.path.join(d, pattern))
    hits = [h for h in hits if not os.path.basename(h).startswith("~$")]
    return max(hits, key=os.path.getmtime) if hits else None


def read_order(dept):
    """Return (summary_text, buys_df, transfers_df) for a department."""
    base = "THC" if dept == "THC" else dept
    xb = get_bytes(f"{base} Recommended Order.xlsx")
    tb = get_bytes(f"{base} Recommended Order.txt")
    summary = tb.decode("utf-8", "replace") if tb else "No order yet for " + dept
    buys = trans = None
    if xb:
        try:
            xl = pd.ExcelFile(io.BytesIO(xb))
            if "Recommended Order" in xl.sheet_names:
                buys = xl.parse("Recommended Order")
            if "Transfer Plan" in xl.sheet_names:
                trans = xl.parse("Transfer Plan")
        except Exception:
            pass
    return summary, buys, trans


STOPWORDS = {"what", "does", "need", "needs", "this", "week", "that", "have", "much", "how", "the",
             "for", "and", "buy", "store", "item", "items", "should", "which", "with", "sell",
             "sells", "selling", "price", "cost", "costs", "margin", "margins", "stock", "many",
             "are", "our", "you", "can", "get", "right", "now", "most", "top", "best", "worst",
             "low", "high", "out", "from", "into", "per", "about", "tell", "give", "list", "show"}


def load_inventory(dept):
    """The full per-item snapshot for a department (read fresh so daily updates show)."""
    b = get_bytes(f"{dept} Inventory.csv")
    if not b:
        return None
    try:
        return pd.read_csv(io.BytesIO(b))
    except Exception:
        return None


def build_context(dept, focus=""):
    summary, buys, trans = read_order(dept)
    inv = load_inventory(dept)
    cost_map = (dict(zip(inv["Item"].astype(str), inv["Cost"]))
                if inv is not None and "Item" in inv.columns and "Cost" in inv.columns else {})
    parts = [f"=== {dept} RECOMMENDED ORDER (summary) ===", summary[:3000]]
    if buys is not None and len(buys):
        parts += [f"\n=== {dept} ITEMS TO BUY (chain-wide order) ===", buys.head(45).to_csv(index=False)]
    if trans is not None and len(trans) and "To Store" in trans.columns:
        try:   # per-store rollup so store-specific questions can be answered
            tv = pd.to_numeric(trans.get("Value $"), errors="coerce").fillna(0)
            roll = (trans.assign(_v=tv).groupby("To Store")
                    .agg(items=("Item", "count"), transfer_in_value=("_v", "sum"),
                         stockouts=("Priority", lambda s: int((s == "STOCKOUT").sum())))
                    .reset_index().sort_values("transfer_in_value", ascending=False))
            parts += ["\n=== PER-STORE NEEDS (what each store receives via transfer) ===",
                      roll.to_csv(index=False)]
        except Exception:
            pass
        named = [s for s in trans["To Store"].dropna().astype(str).unique()
                 if s.lower() in (focus or "").lower()]
        for s in named[:2]:
            sub = trans[trans["To Store"].astype(str) == s].copy()
            if cost_map:
                sub["Unit Cost"] = sub["Item"].astype(str).map(cost_map)
            parts += [f"\n=== {s.upper()}: DETAILED NEEDS (transfer in, then-buy, with unit cost) ===",
                      sub.head(60).to_csv(index=False)]
        if not named:
            parts += [f"\n=== {dept} TOP TRANSFERS (all stores) ===", trans.head(25).to_csv(index=False)]
    # full inventory snapshot - pull rows relevant to the question + key rankings
    if inv is not None and len(inv):
        words = [w for w in re.findall(r"[a-z0-9]{3,}", (focus or "").lower()) if w not in STOPWORDS]
        if words:
            mask = inv["Item"].astype(str).str.lower().apply(lambda s: any(w in s for w in words))
            hits = inv[mask]
            if len(hits):
                parts += [f"\n=== ITEMS MATCHING THE QUESTION ({len(hits)}) - full detail incl. by-store on-hand ===",
                          hits.head(30).to_csv(index=False)]
        compact = [c for c in ["Item", "Category", "Chain OH", "Wk Velocity", "WOS", "Cost",
                               "Retail", "Margin %", "30D Units"] if c in inv.columns]
        parts += ["\n=== TOP SELLERS (by weekly velocity) ===", inv[compact].head(12).to_csv(index=False)]
        wos = pd.to_numeric(inv["WOS"], errors="coerce")
        atrisk = inv[wos <= 2].sort_values("Wk Velocity", ascending=False)
        if len(atrisk):
            parts += ["\n=== LOW / AT-RISK (WOS 2 weeks or less) ===", atrisk[compact].head(15).to_csv(index=False)]
        if "Deal" in inv.columns:
            deals = inv[inv["Deal"].astype(str).str.strip().str.lower().replace("nan", "").str.len() > 0]
            if len(deals):
                dcols = [c for c in ["Item", "Deal", "Cost", "Retail", "Margin %", "Wk Velocity", "WOS"]
                         if c in inv.columns]
                parts += [f"\n=== ACTIVE DEALS ({len(deals)}) - sorted by weekly velocity ===",
                          deals.sort_values("Wk Velocity", ascending=False)[dcols].head(25).to_csv(index=False)]
    return "\n".join(parts)[:22000]


def _build_system(dept, focus=""):
    return (
        f"You are the buying assistant for Top Ten Liquors' {dept} buyer.\n"
        "ANSWER STYLE (important):\n"
        "- Lead with the answer. No preamble, no disclaimers, and NEVER explain what the data does "
        "or doesn't contain - just answer the question.\n"
        "- Be tight: a short bulleted list (about 8 items max unless they ask for the full list), "
        "each bullet = item + the key number(s): quantity (units/cases), WOS, on-hand, $, or margin.\n"
        "- For a STORE question, list what that store needs this week, most urgent (lowest WOS / "
        "out of stock) first; note 'transfer' vs 'buy' in a few words only if useful.\n"
        "- Use the buyer's terms (PM, WOS, cases, gross/net). Cite only real numbers from the data.\n"
        "- Prices: 'Cost'/'Unit Cost' = what Top Ten PAYS the vendor (used for order $); 'Retail' = the "
        "customer shelf price; 'Margin %' = (Retail - Cost) / Retail. Never report cost as retail or vice versa.\n"
        "- If a fact truly isn't present, say so in one short line.\n\n"
        "The data below has: the weekly ORDER (chain-wide buy), the TRANSFER plan + PER-STORE NEEDS "
        "(use for store questions), and a full INVENTORY snapshot of every item (on-hand chain + by "
        "store, velocity, WOS, cost, retail, margin, sales) plus TOP SELLERS and LOW/AT-RISK lists.\n\n"
        "DATA:\n" + build_context(dept, focus))


def gemini_chat(key, system, messages):
    contents = [{"role": "model" if m["role"] == "assistant" else "user",
                 "parts": [{"text": m["content"]}]} for m in messages[-6:]]
    body = {"system_instruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {"maxOutputTokens": 900, "temperature": 0.3}}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={key}"
    last = None
    for attempt in range(3):   # free tier occasionally returns 429/503 (busy) - retry briefly
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                out = json.load(r)
            cand = (out.get("candidates") or [{}])[0]
            return "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", [])) or "(no response)"
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 503) and attempt < 2:
                time.sleep(2 * (attempt + 1)); continue
            raise
    raise last


def claude_chat(key, system, messages):
    body = {"model": CLAUDE_MODEL, "max_tokens": 900, "system": system,
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages][-6:]}
    req = urllib.request.Request("https://api.anthropic.com/v1/messages",
                                 data=json.dumps(body).encode(),
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                          "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        out = json.load(r)
    return "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")


def _key(env_name, keyfile):
    """Secret from an env var (hosted) or a local file (dev)."""
    return os.environ.get(env_name) or (open(keyfile, encoding="utf-8").read().strip()
                                        if os.path.exists(keyfile) else "")


def ai_reply(dept, messages):
    """Provider-pluggable: Gemini if a Gemini key is present, else Claude."""
    # focus on the recent conversation (not just the last line) so follow-ups like
    # "how much would that cost" keep the item/store from earlier turns in context.
    user_msgs = [m.get("content", "") for m in messages if m.get("role") == "user"]
    focus = " ".join(user_msgs[-4:])
    system = _build_system(dept, focus)
    gk = _key("GEMINI_API_KEY", GEMINI_KEYFILE)
    if gk:
        return gemini_chat(gk, system, messages)
    ak = _key("ANTHROPIC_API_KEY", ANTHROPIC_KEYFILE)
    if ak:
        return claude_chat(ak, system, messages)
    return "No AI key configured (add gemini_key.txt or anthropic_key.txt)."


def dept_totals(dept):
    """True chain totals from the all-department summary (includes items fully covered
    by transfer, which aren't in the buy list)."""
    b = get_bytes("All Dept Order Summary.xlsx")
    if not b:
        return None
    try:
        s = pd.read_excel(io.BytesIO(b), sheet_name="Summary")
        row = s[s["Department"].astype(str) == dept]
        if len(row):
            r = row.iloc[0]
            return {"net_buy": float(r["Net Buy $"]), "gross": float(r["Gross $"]),
                    "transfer": float(r["Transfer $"]), "items": int(r["Items"])}
    except Exception:
        pass
    return None


def status_info():
    b = get_bytes("status.json")
    if b:
        try:
            return json.loads(b.decode("utf-8", "replace"))
        except Exception:
            pass
    return {}


def today_payload(dept):
    summary, buys, trans = read_order(dept)
    def rows(df, cols, n):
        if df is None or not len(df):
            return []
        keep = [c for c in cols if c in df.columns]
        return df[keep].head(n).fillna("").astype(object).values.tolist(), keep
    buy_rows, buy_cols = rows(buys, ["Item", "WOS", "Buy Units", "Buy Cases", "Net Buy $", "GM %", "Deal Terms"], 30) if buys is not None else ([], [])
    tr_rows, tr_cols = rows(trans, ["Priority", "To Store", "Item", "Transfer In", "Value $", "From"], 30) if trans is not None else ([], [])
    headline = dept_totals(dept) or {}
    if not headline and buys is not None and len(buys):
        unit_cost = pd.to_numeric(buys.get("Unit Cost"), errors="coerce")
        headline = {
            "net_buy": float(pd.to_numeric(buys.get("Net Buy $"), errors="coerce").sum()),
            "gross": float((pd.to_numeric(buys.get("Gross Need"), errors="coerce") * unit_cost).sum()),
            "transfer": float((pd.to_numeric(buys.get("Transfer"), errors="coerce") * unit_cost).sum()),
            "items": int(len(buys)),
        }
    if buys is not None and len(buys):
        headline["units"] = int(pd.to_numeric(buys.get("Buy Units"), errors="coerce").fillna(0).sum())
    if trans is not None and len(trans):
        headline["transfers"] = int(len(trans))
        headline["rebalance_units"] = int(pd.to_numeric(trans.get("Transfer In"), errors="coerce").fillna(0).sum())
        pr = trans.get("Priority")
        if pr is not None:
            so = int((pr == "STOCKOUT").sum()); lo = int((pr == "Low <2wk").sum())
            headline["stockouts"] = so; headline["low"] = lo; headline["routine"] = int(len(trans) - so - lo)
    return {"summary": summary, "headline": headline, "status": status_info(),
            "buy_cols": buy_cols, "buy_rows": buy_rows,
            "tr_cols": tr_cols, "tr_rows": tr_rows}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")   # allow the Chrome/Excel clients
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-cache")          # always revalidate; updates show fast
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain")

    def _serve_static(self, path):
        if path == "/" or path == "":
            path = "/index.html"
        fp = os.path.normpath(os.path.join(STATIC, path.lstrip("/")))
        if not fp.startswith(STATIC) or not os.path.isfile(fp):
            return self._send(404, b"not found", "text/plain")
        ext = os.path.splitext(fp)[1].lower()
        ctype = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
                 ".json": "application/manifest+json", ".webmanifest": "application/manifest+json",
                 ".svg": "image/svg+xml", ".png": "image/png"}.get(ext, "application/octet-stream")
        with open(fp, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/today":
            dept = (parse_qs(u.query).get("dept", ["THC"])[0])
            try:
                return self._send(200, today_payload(dept))
            except Exception as e:
                return self._send(500, {"error": str(e)})
        return self._serve_static(u.path)

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            payload = {}
        if u.path == "/api/login":
            user = str(payload.get("user", "")).strip().lower()
            pw = payload.get("pw", "")
            if user in USERS and pw == USERS[user]:
                return self._send(200, {"ok": True, "who": user,
                                        "depts": USER_DEPTS.get(user, ALL_DEPTS)})
            return self._send(200, {"ok": False})
        if u.path == "/api/chat":
            try:
                reply = ai_reply(payload.get("dept", "THC"), payload.get("messages", []))
                return self._send(200, {"reply": reply})
            except Exception as e:
                return self._send(200, {"reply": f"(Assistant error: {e})"})
        return self._send(404, {"error": "unknown endpoint"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"Buyer client running on {HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
