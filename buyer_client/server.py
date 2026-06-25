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
# Beta feedback -> emailed straight to the buying lead via Resend (set these on the host).
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FEEDBACK_EMAIL = os.environ.get("FEEDBACK_EMAIL", "")             # where reports land (your inbox)
FEEDBACK_FROM  = os.environ.get("FEEDBACK_FROM", "onboarding@resend.dev")


def gh_token():
    return os.environ.get("GITHUB_TOKEN", "")


def _gh_url(path):
    return "https://api.github.com/repos/%s/%s/contents/%s" % (GH_OWNER, GH_REPO, urllib.parse.quote(path))


def _gh_hdr(accept="application/vnd.github+json"):
    return {"Authorization": "Bearer " + gh_token(), "Accept": accept, "User-Agent": "ttb"}


def gh_read(path):
    """Raw bytes of any repo file (None if missing)."""
    if not gh_token():
        return None
    try:
        with urllib.request.urlopen(urllib.request.Request(_gh_url(path) + "?ref=" + GH_BRANCH,
                                                           headers=_gh_hdr("application/vnd.github.raw")), timeout=30) as r:
            return r.read()
    except Exception:
        return None


def gh_get_sha(path):
    if not gh_token():
        return None
    try:
        with urllib.request.urlopen(urllib.request.Request(_gh_url(path) + "?ref=" + GH_BRANCH, headers=_gh_hdr()), timeout=20) as r:
            return json.load(r).get("sha")
    except Exception:
        return None


def gh_write(path, data_bytes, message, sha=None):
    """PUT a file; returns HTTP status (409 = sha conflict, retry). Fetches sha if not given."""
    if not gh_token():
        return 0
    if sha is None:
        sha = gh_get_sha(path)
    body = {"message": message, "content": base64.b64encode(data_bytes).decode(), "branch": GH_BRANCH}
    if sha:
        body["sha"] = sha
    try:
        req = urllib.request.Request(_gh_url(path), data=json.dumps(body).encode(), headers=_gh_hdr(), method="PUT")
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


# Feedback store: a single JSON list in the repo (or a local file when running without a token).
LOCAL_INDEX = os.path.join(HERE, "local_feedback_index.json")
INDEX_PATH = "feedback/index.json"


def read_index():
    if gh_token():
        raw = gh_read(INDEX_PATH)
        try:
            return json.loads(raw) if raw else []
        except Exception:
            return []
    try:
        with open(LOCAL_INDEX, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def update_index(mutate):
    """Read-modify-write the feedback index. Optimistic-concurrency retry when hosted."""
    if not gh_token():
        data = read_index(); mutate(data)
        try:
            with open(LOCAL_INDEX, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return True
        except Exception:
            return False
    for _ in range(4):
        sha = gh_get_sha(INDEX_PATH)
        raw = gh_read(INDEX_PATH)
        try:
            data = json.loads(raw) if raw else []
        except Exception:
            data = []
        mutate(data)
        status = gh_write(INDEX_PATH, json.dumps(data, indent=2).encode(), "feedback update", sha)
        if status in (200, 201):
            return True
        if status != 409:
            return False
    return False


def admin_ok(key):
    """Inbox auth. Accepts the host ADMIN_KEY env var (if set) OR a key whose
    sha256 matches feedback/admin_key.sha256 in the repo — so the inbox works
    without host config and the lead can rotate the key via set_inbox_key.py."""
    if not key:
        return False
    env = os.environ.get("ADMIN_KEY", "")
    if env and key == env:
        return True
    import hashlib
    try:
        if gh_token():
            stored = gh_read("feedback/admin_key.sha256")        # bytes (or None)
        else:
            p = os.path.join(HERE, "local_admin_key.sha256")
            stored = open(p, "rb").read() if os.path.exists(p) else b""
        if isinstance(stored, bytes):
            stored = stored.decode("utf-8", "replace")
        stored = (stored or "").strip()
        if stored and hashlib.sha256(key.encode("utf-8")).hexdigest() == stored:
            return True
    except Exception:
        pass
    return False


DEC_PATH = "data/buyer_decisions.json"          # buyer review decisions (applied by the order engine)
LOCAL_DEC = os.path.join(HERE, "local_decisions.json")


def read_decisions():
    if gh_token():
        raw = gh_read(DEC_PATH)
        try:
            return json.loads(raw) if raw else []
        except Exception:
            return []
    try:
        with open(LOCAL_DEC, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def write_decisions(items):
    if not gh_token():
        try:
            with open(LOCAL_DEC, "w", encoding="utf-8") as f:
                json.dump(items, f, indent=2)
            return True
        except Exception:
            return False
    for _ in range(4):
        sha = gh_get_sha(DEC_PATH)
        status = gh_write(DEC_PATH, json.dumps(items, indent=2).encode(), "buyer review decisions", sha)
        if status in (200, 201):
            return True
        if status != 409:
            return False
    return False


# Append-only change log = the backup that powers Undo.
CHG_PATH = "data/buyer_changes_log.json"
LOCAL_CHG = os.path.join(HERE, "local_changes.json")


def read_changes():
    if gh_token():
        raw = gh_read(CHG_PATH)
        try:
            return json.loads(raw) if raw else []
        except Exception:
            return []
    try:
        with open(LOCAL_CHG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def write_changes(items):
    if not gh_token():
        try:
            with open(LOCAL_CHG, "w", encoding="utf-8") as f:
                json.dump(items, f, indent=2)
            return True
        except Exception:
            return False
    for _ in range(4):
        sha = gh_get_sha(CHG_PATH)
        status = gh_write(CHG_PATH, json.dumps(items, indent=2).encode(), "buyer change log", sha)
        if status in (200, 201):
            return True
        if status != 409:
            return False
    return False


def _dec_key(dept, item):
    return (str(dept).strip().lower(), str(item).strip().lower())


def norm_val(s):
    """Normalize a cell value for de-duping filter options (ignore case + spacing)."""
    return re.sub(r"\s+", "", str(s).strip().lower())


def send_feedback_email(subject, html, img_b64):
    """Email a buyer report (with screenshot) via Resend. Returns (ok, detail)."""
    key = os.environ.get("RESEND_API_KEY", "")          # read at call time (picks up host config)
    to = os.environ.get("FEEDBACK_EMAIL", "")
    frm = os.environ.get("FEEDBACK_FROM", "onboarding@resend.dev")
    if not key:
        return False, "RESEND_API_KEY not set on the server"
    if not to:
        return False, "FEEDBACK_EMAIL not set on the server"
    body = {"from": frm, "to": [to], "subject": subject, "html": html}
    if img_b64:
        body["attachments"] = [{"filename": "screenshot.png", "content": img_b64}]
    try:
        req = urllib.request.Request("https://api.resend.com/emails", data=json.dumps(body).encode(),
                                     headers={"Authorization": "Bearer " + key,
                                              "Content-Type": "application/json",
                                              "Accept": "application/json",
                                              # Cloudflare in front of Resend 403s the default
                                              # python-urllib UA (error 1010); send a real one.
                                              "User-Agent": "Mozilla/5.0 (compatible; TopTenBuyer/1.0)"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            return (200 <= r.status < 300), f"resend {r.status}"
    except urllib.error.HTTPError as e:
        msg = ""
        try: msg = e.read().decode("utf-8", "replace")[:400]
        except Exception: pass
        return False, f"resend HTTP {e.code}: {msg}"
    except Exception as e:
        return False, f"send error: {e}"


def get_bytes(name):
    """Return the bytes of a data file. Hosted: from the repo's data/ folder; local: newest match."""
    if gh_token():
        url = ("https://api.github.com/repos/%s/%s/contents/data/%s?ref=%s"
               % (GH_OWNER, GH_REPO, urllib.parse.quote(name), GH_BRANCH))
        # raw media type returns the file bytes directly (handles files >1MB, which the
        # default base64-JSON response truncates to empty - e.g. the 2.5MB Full Lists).
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + gh_token(),
                                                   "Accept": "application/vnd.github.raw",
                                                   "User-Agent": "ttb"})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read()
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
    """Return (summary_text, buys_df, transfers_df, buy_month_wait_df, needs_review_df)."""
    base = "THC" if dept == "THC" else dept
    xb = get_bytes(f"{base} Recommended Order.xlsx")
    tb = get_bytes(f"{base} Recommended Order.txt")
    summary = tb.decode("utf-8", "replace") if tb else "No order yet for " + dept
    buys = trans = wait = review = None
    if xb:
        try:
            xl = pd.ExcelFile(io.BytesIO(xb))
            if "Recommended Order" in xl.sheet_names:
                buys = xl.parse("Recommended Order")
            if "Transfer Plan" in xl.sheet_names:
                trans = xl.parse("Transfer Plan")
            if "Buy-Month Wait" in xl.sheet_names:
                wait = xl.parse("Buy-Month Wait")
            if "Needs Review" in xl.sheet_names:
                review = xl.parse("Needs Review")
        except Exception:
            pass
    return summary, buys, trans, wait, review


STOPWORDS = {"what", "does", "need", "needs", "this", "week", "that", "have", "much", "how", "the",
             "for", "and", "buy", "store", "item", "items", "should", "which", "with", "sell",
             "sells", "selling", "price", "cost", "costs", "margin", "margins", "stock", "many",
             "are", "our", "you", "can", "get", "right", "now", "most", "top", "best", "worst",
             "low", "high", "out", "from", "into", "per", "about", "tell", "give", "list", "show",
             "club", "customer", "one", "case", "cases", "unit", "units", "each", "make", "retail"}


def load_inventory(dept):
    """The full per-item snapshot for a department (read fresh so daily updates show)."""
    b = get_bytes(f"{dept} Inventory.csv")
    if not b:
        return None
    try:
        return pd.read_csv(io.BytesIO(b))
    except Exception:
        return None


def load_list(name):
    """A product-form section list (Remove List.csv / New Items.csv)."""
    b = get_bytes(name)
    if not b:
        return None
    try:
        return pd.read_csv(io.BytesIO(b))
    except Exception:
        return None


def load_store_orders(dept):
    """Per-store order table ('<Dept> Store Orders.csv'): each store's own order, by distributor."""
    b = get_bytes(f"{dept} Store Orders.csv")
    if not b:
        return None
    try:
        return pd.read_csv(io.BytesIO(b), dtype={"Product Code": str})
    except Exception:
        return None


# Product-list tabs, in the order buyers expect them (extras appended alphabetically).
TAB_ORDER = ["Full List", "New Items", "Remove", "Upcoming Price Changes", "Price Level",
             "Retail Pricing Table", "Markups"]


def list_data_files():
    """Every data file name (repo data/ when hosted, else the local data dirs)."""
    if gh_token():
        url = ("https://api.github.com/repos/%s/%s/contents/data?ref=%s"
               % (GH_OWNER, GH_REPO, GH_BRANCH))
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + gh_token(),
                                                   "Accept": "application/vnd.github+json",
                                                   "User-Agent": "ttb"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return [it.get("name", "") for it in json.load(r)]
        except Exception:
            return []
    names = set()
    for d in DATA_DIRS:
        for fp in glob.glob(os.path.join(d, "*.csv")):
            names.add(os.path.basename(fp))
    return sorted(names)


def prod_dept(dept):
    """Beer shares the Liquor (Spirits) product file, so its product tabs come from Spirits."""
    return "Spirits" if dept == "Beer" else dept


def list_tabs(dept):
    """The product-list tabs that actually exist for a department (e.g. 'THC - Remove.csv')."""
    prefix = prod_dept(dept) + " - "
    found = [n[len(prefix):-4] for n in list_data_files()
             if n.startswith(prefix) and n.lower().endswith(".csv")]
    return [t for t in TAB_ORDER if t in found] + sorted(t for t in found if t not in TAB_ORDER)


def build_parts(dept, focus=""):
    """Returns (stable, focus_text): the per-department data (same every question -> cacheable),
    and the small per-question block (relevant item/store) that rides with the message."""
    summary, buys, trans, wait, review = read_order(dept)
    inv = load_inventory(dept)
    cost_map = (dict(zip(inv["Item"].astype(str), inv["Cost"]))
                if inv is not None and "Item" in inv.columns and "Cost" in inv.columns else {})
    S = [f"=== {dept} RECOMMENDED ORDER (summary) ===", summary[:3000]]
    if buys is not None and len(buys):
        S += [f"\n=== {dept} ITEMS TO BUY (chain-wide order) ===", buys.head(45).to_csv(index=False)]
    if wait is not None and len(wait):
        S += [f"\n=== {dept} WAITING FOR BUY-MONTH (routine deal items deferred until their cheaper buy month; "
              f"not in this week's buy) ===", wait.head(40).to_csv(index=False)]
    if review is not None and len(review):
        S += [f"\n=== {dept} NEEDS HUMAN REVIEW (in the order, but flagged - the system isn't fully sure; "
              f"check before ordering) ===", review.head(40).to_csv(index=False)]
    if trans is not None and len(trans) and "To Store" in trans.columns:
        try:   # per-store rollup so store-specific questions can be answered
            tv = pd.to_numeric(trans.get("Value $"), errors="coerce").fillna(0)
            roll = (trans.assign(_v=tv).groupby("To Store")
                    .agg(items=("Item", "count"), transfer_in_value=("_v", "sum"),
                         stockouts=("Priority", lambda s: int((s == "STOCKOUT").sum())))
                    .reset_index().sort_values("transfer_in_value", ascending=False))
            S += ["\n=== PER-STORE NEEDS (what each store receives via transfer) ===", roll.to_csv(index=False)]
        except Exception:
            pass
        S += [f"\n=== {dept} TOP TRANSFERS (all stores) ===", trans.head(25).to_csv(index=False)]
    if inv is not None and len(inv):
        compact = [c for c in ["Item", "Category", "Chain OH", "Wk Velocity", "WOS", "Cost",
                               "Retail", "Margin %", "30D Units"] if c in inv.columns]
        S += ["\n=== TOP SELLERS (by weekly velocity) ===", inv[compact].head(12).to_csv(index=False)]
        wos = pd.to_numeric(inv["WOS"], errors="coerce")
        atrisk = inv[wos <= 2].sort_values("Wk Velocity", ascending=False)
        if len(atrisk):
            S += ["\n=== LOW / AT-RISK (WOS 2 weeks or less) ===", atrisk[compact].head(15).to_csv(index=False)]
        if "Deal" in inv.columns:
            deals = inv[inv["Deal"].astype(str).str.strip().str.lower().replace("nan", "").str.len() > 0]
            if len(deals):
                dcols = [c for c in ["Item", "Deal", "Cost", "Retail", "Margin %", "Wk Velocity", "WOS"]
                         if c in inv.columns]
                S += [f"\n=== ACTIVE DEALS ({len(deals)}) - sorted by weekly velocity ===",
                      deals.sort_values("Wk Velocity", ascending=False)[dcols].head(25).to_csv(index=False)]
    rem = load_list("Remove List.csv")
    if rem is not None and "Item" in rem.columns:
        if "Department" in rem.columns:
            rem = rem[rem["Department"] == dept]
        if len(rem):
            S += [f"\n=== BEING REMOVED ({len(rem)} discontinued items - DO NOT reorder) ===",
                  rem["Item"].head(40).to_csv(index=False)]
    newi = load_list("New Items.csv")
    if newi is not None and "Item" in newi.columns:
        if "Department" in newi.columns:
            newi = newi[newi["Department"] == dept]
        if len(newi):
            S += [f"\n=== NEW ITEMS ({len(newi)} being added) ===", newi["Item"].head(40).to_csv(index=False)]
    # memory: recent actions the buyer already took in the app (so the assistant doesn't re-suggest them)
    try:
        log = sorted(read_changes(), key=lambda c: str(c.get("id", "")), reverse=True)
        ch = [c for c in log if str(c.get("dept", "")).lower() == dept.lower() and c.get("action") != "undo"][:12]
        if ch:
            lines = []
            for c in ch:
                act = c.get("action")
                w = f"set quantity to {c.get('qty')}" if act == "qty" else ("marked don't-buy" if act == "skip" else (act or ""))
                note = f" - {c.get('note')}" if c.get("note") else ""
                lines.append(f"{c.get('item')}: {w}{note} [{c.get('ts')}, {c.get('who')}]")
            S += ["\n=== RECENT ACTIONS THE BUYER ALREADY TOOK (remember these; don't re-suggest changing what they just changed) ===",
                  "\n".join(lines)]
    except Exception:
        pass

    # ---- per-question (focus) block: named store detail + items matching the question ----
    F = []
    if trans is not None and len(trans) and "To Store" in trans.columns:
        named = [s for s in trans["To Store"].dropna().astype(str).unique()
                 if s.lower() in (focus or "").lower()]
        for s in named[:2]:
            sub = trans[trans["To Store"].astype(str) == s].copy()
            if cost_map:
                sub["Unit Cost"] = sub["Item"].astype(str).map(cost_map)
            F += [f"\n=== {s.upper()}: DETAILED NEEDS (transfer in, then-buy, with unit cost) ===",
                  sub.head(60).to_csv(index=False)]
    if inv is not None and len(inv):
        words = [w for w in re.findall(r"[a-z0-9]{3,}", (focus or "").lower()) if w not in STOPWORDS]
        if words:
            names = inv["Item"].astype(str).str.lower()
            score = names.apply(lambda s: sum(w in s for w in words))
            hits = inv[score > 0].copy()
            hits["_s"] = score[score > 0]
            hits = hits.sort_values("_s", ascending=False).drop(columns="_s")
            if len(hits):
                F += ["\n=== ITEMS MATCHING THE QUESTION (best match first) - full detail: cost, retail, club price, margin, on-hand by store ===",
                      hits.head(15).to_csv(index=False)]
    return "\n".join(S)[:24000], "\n".join(F)[:6000]


def build_context(dept, focus=""):
    stable, foc = build_parts(dept, focus)
    return (stable + "\n" + foc)[:26000]


def _build_system(dept, data=""):
    return (
        f"You are Claude, the AI assistant for Top Ten Liquors' {dept} buyer — like having Claude right on their "
        "computer. Be genuinely helpful and conversational: answer buying questions, but also help with whatever else "
        "they ask — draft an email to a distributor, explain something, think a decision through, or read what's on "
        "their screen. You have their live buying data below; use it whenever it's relevant, and just be a smart, "
        "useful colleague otherwise.\n"
        "STYLE:\n"
        "- Be natural and direct. For a quick factual buying question, lead with the answer as a tight bulleted list "
        "(item + the key numbers: quantity in units/cases, WOS, on-hand, $, margin) — no filler. For broader or "
        "open-ended requests, reply like a sharp colleague would.\n"
        "- For a STORE question, list what that store needs this week, most urgent (lowest WOS / out of stock) first; "
        "note 'transfer' vs 'buy' briefly if useful.\n"
        "- Use the buyer's terms (PM, WOS, cases, gross/net). Cite only real numbers from the data.\n"
        "- Prices: 'Cost'/'Unit Cost' = what Top Ten PAYS the vendor (used for order $); 'Retail' = the regular "
        "customer shelf price; 'Club Price' = the member/club customer price; 'Margin %' = (Retail - Cost)/Retail. "
        "Never report cost as retail or vice versa.\n"
        "- If a buying fact isn't in the data, say so briefly — then still help however you can.\n"
        "- BE PROACTIVE AND RESOURCEFUL. Find a useful angle on whatever they show you, tie it back to their "
        "buying when there's any connection (like linking a store's club rate to its stockout urgency), and finish "
        "with a concrete next step or a useful offer — don't just describe what you see.\n"
        "- ASK A QUICK QUESTION when it would let you help better. If their intent is unclear or you're missing one "
        "thing, ask one short clarifying question (e.g., 'What are you trying to decide here?' or 'Want me to compare "
        "this against your current order?') instead of guessing or stopping short. It's good to be curious and dig in.\n"
        "- If a SCREENSHOT is attached, it's the buyer's current screen (Cloud Retailer, a distributor "
        "portal, Excel, etc.). Read what's on it, answer their question about it, and tie it to the buying "
        "data when useful (e.g., compare an on-screen price/qty to the recommended buy or the cost/retail).\n\n"
        "ABOUT THIS APP (guide the buyer to the right button when they ask how to do something):\n"
        "- EXPORT TO EXCEL: green 'Export to Excel' buttons sit on the Today tab (on 'What to buy' and 'Transfer plan'), "
        "on Product Lists (exports the whole tab), and on 'Order by distributor'. There's also an 'Export all' button in "
        "the top bar that downloads one Excel workbook with every tab as a sheet. So YES, the app can export to Excel - "
        "point them to those buttons (never say it can't).\n"
        "- TODAY tab: the week's order + transfers, and a yellow 'Needs your review' cell - tapping it opens a panel to "
        "change quantity / mark don't-buy / approve, all backed up and undoable.\n"
        "- ASK tab: this chat; the 'Screen' button lets them share their screen so you can see and answer about it.\n"
        "- PRODUCT LISTS tab: each department's workbook tabs with search, column sort, per-column filters, and Export.\n"
        "- ORDER BY DISTRIBUTOR tab: pick a distributor to get that order ready to Copy or Export.\n"
        "- CUSTOMIZE (top bar): show/hide and rename sections, add a note, choose columns. REPORT: send feedback. "
        "REFRESH: reload the latest data. The department picker is top-left.\n\n"
        "The data below has: the weekly ORDER (chain-wide buy), the TRANSFER plan + PER-STORE NEEDS "
        "(use for store questions), and a full INVENTORY snapshot of every item (on-hand chain + by "
        "store, velocity, WOS, cost, retail, margin, sales) plus TOP SELLERS, LOW/AT-RISK, ACTIVE DEALS, "
        "items BEING REMOVED (discontinued - do not reorder), and NEW ITEMS lists.\n\n"
        "DATA:\n" + data)


def _img_parts(img):
    """A Gemini inline_data part from a data URL (screenshot the buyer attached)."""
    mime = "image/jpeg" if "jpeg" in img.split(",", 1)[0] else "image/png"
    b64 = img.split(",", 1)[1] if "," in img else img
    return {"inline_data": {"mime_type": mime, "data": b64}}


def gemini_chat(key, system, messages):
    contents = []
    for m in messages[-6:]:
        parts = [{"text": m.get("content", "")}]
        if m.get("image"):
            parts.append(_img_parts(m["image"]))
        contents.append({"role": "model" if m["role"] == "assistant" else "user", "parts": parts})
    body = {"system_instruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {"maxOutputTokens": 1100, "temperature": 0.3}}
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
    msgs = []
    for m in messages[-6:]:
        if m.get("image") and m["role"] == "user":
            mime = "image/jpeg" if "jpeg" in m["image"].split(",", 1)[0] else "image/png"
            b64 = m["image"].split(",", 1)[1] if "," in m["image"] else m["image"]
            msgs.append({"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                {"type": "text", "text": m.get("content", "")}]})
        else:
            msgs.append({"role": m["role"], "content": m.get("content", "")})
    # cache the big system/data block so repeat questions in a department are ~cheap (5-min TTL)
    body = {"model": CLAUDE_MODEL, "max_tokens": 1100,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": msgs}
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request("https://api.anthropic.com/v1/messages",
                                         data=json.dumps(body).encode(),
                                         headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                                  "content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                out = json.load(r)
            return "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 529) and attempt < 2:
                time.sleep(2 * (attempt + 1)); continue
            raise
    raise last


def _key(env_name, keyfile):
    """Secret from an env var (hosted) or a local file (dev)."""
    return os.environ.get(env_name) or (open(keyfile, encoding="utf-8").read().strip()
                                        if os.path.exists(keyfile) else "")


def ai_reply(dept, messages):
    """Claude-powered (Anthropic) when a key is present; falls back to Gemini if Claude errors."""
    # focus on the recent conversation (both sides) so follow-ups like "what's the club price"
    # keep the item the assistant just named in context, not only what the user typed.
    focus = " ".join(m.get("content", "") for m in messages[-6:])
    stable, foc = build_parts(dept, focus)
    system = _build_system(dept, stable)            # stable per-dept data -> cached for Claude
    msgs = [dict(m) for m in messages]
    if foc.strip():                                 # per-question detail rides with the latest user message
        for m in reversed(msgs):
            if m.get("role") == "user":
                m["content"] = (m.get("content", "") + "\n\n[Most relevant to this question]\n" + foc)
                break
    ak = _key("ANTHROPIC_API_KEY", ANTHROPIC_KEYFILE)
    gk = _key("GEMINI_API_KEY", GEMINI_KEYFILE)
    if ak:
        try:
            return claude_chat(ak, system, msgs)
        except Exception as e:
            if not gk:
                raise
            print("Claude failed, falling back to Gemini:", e)
    if gk:
        return gemini_chat(gk, system, msgs)
    return "No AI key configured (add anthropic_key.txt or gemini_key.txt)."


# ---- Excel agent: turn a plain request + the sheet's shape into a concrete, approvable plan ----
EXCEL_AGENT_SYSTEM = (
    "You help a wine buyer edit their Excel workbook. The buyer gives a plain-English request and the "
    "current sheet's shape; you return a JSON plan of concrete operations they will review and approve "
    "before anything runs.\n\n"
    "Use ONLY these operations:\n"
    '- {"op":"setValues","sheet":S,"address":A,"values":[[...]]}  write a block; A is the top-left cell.\n'
    '- {"op":"clearValues","sheet":S,"address":A}  clear the cells in range A (e.g. "B2:B50").\n'
    '- {"op":"deleteRowsWhere","sheet":S,"column":C,"test":T,"value":V}  delete data rows where column C '
    '(a column letter or header name) meets test T. T is one of "zero","negative","blank","equals","contains"; '
    'value only for equals/contains.\n'
    '- {"op":"highlight","sheet":S,"address":A,"color":H}  fill cells A with hex color H (e.g. "#FFFF00").\n'
    '- {"op":"copyValues","fromSheet":S1,"fromAddress":A1,"toSheet":S2,"toAddress":A2}  copy values from A1 to anchor A2.\n\n'
    "Rules:\n"
    "- Use ONLY the real sheet names, column letters/headers, and addresses in the CONTEXT. Never invent columns or sheets.\n"
    "- If the request names no sheet, act on the active sheet. 'sheet' may be omitted to mean the active sheet.\n"
    "- If the request is ambiguous, or you would be guessing which column or range, DO NOT guess: return an empty "
    "actions array and put a short clarifying question in 'summary'.\n"
    "- Keep it to the fewest operations that satisfy the request.\n"
    "- Output ONLY a JSON object, no prose and no code fences: "
    '{"summary":"<one line of what you will do, or a question>","actions":[...]}.'
)


def _parse_json_obj(text):
    t = str(text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t.strip())
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        t = t[i:j + 1]
    try:
        return json.loads(t)
    except Exception:
        return None


def _excel_ctx_text(ctx):
    lines = ["Active sheet: %s" % ctx.get("activeSheet", "?")]
    if ctx.get("sheets"):
        lines.append("All sheets: %s" % ", ".join(map(str, ctx["sheets"])))
    if ctx.get("selection"):
        lines.append("Current selection: %s" % ctx["selection"])
    hdrs = ctx.get("headers") or []
    if hdrs:
        lines.append("Columns (letter = header):")
        lines.append("  " + ", ".join("%s=%s" % (h.get("col"), h.get("name")) for h in hdrs))
    rows = ctx.get("sampleRows") or []
    if rows:
        lines.append("Sample data rows:")
        for r in rows[:5]:
            lines.append("  " + " | ".join("" if c is None else str(c) for c in r))
    return "\n".join(lines)


def excel_plan(request_text, ctx):
    msgs = [{"role": "user", "content": "REQUEST:\n%s\n\nCONTEXT:\n%s" % (request_text, _excel_ctx_text(ctx))}]
    ak = _key("ANTHROPIC_API_KEY", ANTHROPIC_KEYFILE)
    gk = _key("GEMINI_API_KEY", GEMINI_KEYFILE)
    raw = ""
    if ak:
        try:
            raw = claude_chat(ak, EXCEL_AGENT_SYSTEM, msgs)
        except Exception:
            if gk:
                raw = gemini_chat(gk, EXCEL_AGENT_SYSTEM, msgs)
            else:
                raise
    elif gk:
        raw = gemini_chat(gk, EXCEL_AGENT_SYSTEM, msgs)
    else:
        return {"summary": "No AI key configured.", "actions": []}
    plan = _parse_json_obj(raw)
    if not isinstance(plan, dict):
        return {"summary": "I couldn't form a clear plan, try rephrasing.", "actions": []}
    plan.setdefault("summary", "")
    if not isinstance(plan.get("actions"), list):
        plan["actions"] = []
    return plan


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
    summary, buys, trans, wait, review = read_order(dept)
    def rows(df, cols, n):
        if df is None or not len(df):
            return []
        keep = [c for c in cols if c in df.columns]
        return df[keep].head(n).fillna("").astype(object).values.tolist(), keep
    buy_rows, buy_cols = rows(buys, ["Item", "Product Code", "Review", "WOS", "Buy Units", "Buy Cases", "Net Buy $", "GM %", "Deal Terms", "Buy Month"], 600) if buys is not None else ([], [])
    tr_rows, tr_cols = rows(trans, ["Priority", "To Store", "Item", "Transfer In", "Value $", "From"], 600) if trans is not None else ([], [])
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
    if wait is not None and len(wait):
        headline["wait_items"] = int(len(wait))
        headline["wait_buy"] = float(pd.to_numeric(wait.get("Net Buy $"), errors="coerce").fillna(0).sum())
    if review is not None and len(review):
        headline["review_items"] = int(len(review))
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
        if u.path == "/api/myreports":
            dev = parse_qs(u.query).get("device", [""])[0]
            mine = [{k: r.get(k) for k in ("id", "ts", "dept", "page", "text", "status", "reply", "resolved_ts")}
                    for r in read_index() if dev and r.get("device") == dev]
            mine.sort(key=lambda r: int(r.get("id", "0") or 0), reverse=True)
            return self._send(200, {"reports": mine})
        if u.path == "/api/decisions":
            dept = parse_qs(u.query).get("dept", [""])[0]
            mine = [c for c in read_decisions() if str(c.get("dept", "")).lower() == dept.lower()]
            return self._send(200, {"decisions": mine})
        if u.path == "/api/changes":
            dept = parse_qs(u.query).get("dept", [""])[0]
            log = [c for c in read_changes() if not dept or str(c.get("dept", "")).lower() == dept.lower()]
            log.sort(key=lambda c: str(c.get("id", "")), reverse=True)
            return self._send(200, {"changes": log[:200]})
        if u.path == "/api/health":
            ak = _key("ANTHROPIC_API_KEY", ANTHROPIC_KEYFILE); gk = _key("GEMINI_API_KEY", GEMINI_KEYFILE)
            info = {"anthropic_set": bool(ak), "gemini_set": bool(gk), "model": CLAUDE_MODEL}
            if ak:
                try:
                    claude_chat(ak, "Reply with exactly: ok", [{"role": "user", "content": "ok"}])
                    info["provider"] = "claude"
                except Exception as e:
                    info["provider"] = "gemini (claude key set but failing)"; info["claude_error"] = str(e)[:200]
            else:
                info["provider"] = "gemini" if gk else "none"
            return self._send(200, info)
        if u.path == "/api/vendors":
            dept = parse_qs(u.query).get("dept", ["THC"])[0]
            _, buys, _, _, _ = read_order(dept)
            if buys is None or "Supplier" not in buys.columns:
                return self._send(200, {"vendors": []})
            vendors = []
            for name, x in buys.groupby("Supplier"):
                vendors.append({"name": str(name), "items": int(len(x)),
                                "units": int(pd.to_numeric(x.get("Buy Units"), errors="coerce").fillna(0).sum()),
                                "dollars": float(pd.to_numeric(x.get("Net Buy $"), errors="coerce").fillna(0).sum())})
            vendors.sort(key=lambda v: -v["dollars"])
            return self._send(200, {"vendors": vendors})
        if u.path == "/api/vendororder":
            qs = parse_qs(u.query)
            dept = qs.get("dept", ["THC"])[0]; vendor = qs.get("vendor", [""])[0]
            _, buys, _, _, _ = read_order(dept)
            if buys is None or "Supplier" not in buys.columns:
                return self._send(200, {"cols": [], "rows": [], "text": "", "total": 0, "units": 0, "by_store": []})
            x = buys[buys["Supplier"].astype(str) == vendor]
            cols = [c for c in ["Item", "Buy Units", "Buy Cases", "Net Buy $", "Deal Terms", "Buy Month", "Review"] if c in x.columns]
            disp = x[cols].fillna("").astype(object)
            units = int(pd.to_numeric(x.get("Buy Units"), errors="coerce").fillna(0).sum())
            total = float(pd.to_numeric(x.get("Net Buy $"), errors="coerce").fillna(0).sum())
            txt = "Item\tUnits\tCases\n" + "\n".join(
                f"{r.get('Item','')}\t{int(pd.to_numeric(r.get('Buy Units'),errors='coerce') or 0)}\t{int(pd.to_numeric(r.get('Buy Cases'),errors='coerce') or 0)}"
                for _, r in x.iterrows())
            # per-store on-hand for each item (stores order their own, so each needs its number)
            inv = {}
            try:
                import csv as _csv, io as _io
                b = get_bytes("%s Inventory.csv" % dept)
                if b:
                    for row in _csv.DictReader(_io.StringIO(b.decode("utf-8", "replace"))):
                        try: coh = int(float(row.get("Chain OH") or 0))
                        except Exception: coh = 0
                        inv[str(row.get("Item", "")).strip().lower()] = (row.get("By Store OH", ""), coh)
            except Exception:
                pass
            def _bs(s):
                out = []
                for part in str(s or "").split(";"):
                    if ":" in part:
                        st, oh = part.rsplit(":", 1)
                        try: out.append([st.strip(), int(float(oh))])
                        except Exception: pass
                return out
            by_store, oos = [], []
            for _, r in x.iterrows():
                bsv, coh = inv.get(str(r.get("Item", "")).strip().lower(), ("", 0))
                lst = _bs(bsv)
                by_store.append(lst)
                oos.append((not lst) and coh <= 0)
            return self._send(200, {"cols": cols, "rows": disp.values.tolist(), "text": txt,
                                    "total": total, "units": units, "by_store": by_store, "oos": oos})
        if u.path == "/api/stores":
            # the stores that have anything to order this week (for the per-store order picker)
            dept = parse_qs(u.query).get("dept", ["THC"])[0]
            so = load_store_orders(dept)
            stores = (sorted(so["Store"].dropna().astype(str).str.strip().unique().tolist())
                      if so is not None and "Store" in so.columns else [])
            return self._send(200, {"stores": stores})
        if u.path == "/api/storevendors":
            # distributors a given store needs to order from, with item/case/$ counts
            qs = parse_qs(u.query)
            dept = qs.get("dept", ["THC"])[0]; store = qs.get("store", [""])[0]
            so = load_store_orders(dept)
            out = []
            if so is not None and "Store" in so.columns and "Supplier" in so.columns:
                x = so[so["Store"].astype(str).str.strip() == store]
                for sup, g in x.groupby(x["Supplier"].astype(str)):
                    out.append({"name": sup, "items": int(len(g)),
                                "cases": int(pd.to_numeric(g.get("Order Cases"), errors="coerce").fillna(0).sum()),
                                "dollars": float(pd.to_numeric(g.get("Order $"), errors="coerce").fillna(0).sum())})
                out.sort(key=lambda v: -v["dollars"])
            return self._send(200, {"vendors": out})
        if u.path == "/api/storeorder":
            # one store's order from one distributor (full cases per store)
            qs = parse_qs(u.query)
            dept = qs.get("dept", ["THC"])[0]; store = qs.get("store", [""])[0]; vendor = qs.get("vendor", [""])[0]
            so = load_store_orders(dept)
            if so is None or "Store" not in so.columns:
                return self._send(200, {"cols": [], "rows": [], "text": "", "total": 0,
                                        "units": 0, "cases": 0, "mode": "store"})
            x = so[so["Store"].astype(str).str.strip() == store]
            if vendor and "Supplier" in x.columns:
                x = x[x["Supplier"].astype(str) == vendor]
            cols = [c for c in ["Item", "Product Code", "Store OH", "WOS", "Order Cases", "Order Units", "Order $"]
                    if c in x.columns]
            disp = x[cols].fillna("").astype(object)
            units = int(pd.to_numeric(x.get("Order Units"), errors="coerce").fillna(0).sum())
            cases = int(pd.to_numeric(x.get("Order Cases"), errors="coerce").fillna(0).sum())
            total = float(pd.to_numeric(x.get("Order $"), errors="coerce").fillna(0).sum())
            txt = "Item\tCode\tCases\tUnits\n" + "\n".join(
                f"{r.get('Item','')}\t{r.get('Product Code','')}\t"
                f"{int(pd.to_numeric(r.get('Order Cases'),errors='coerce') or 0)}\t"
                f"{int(pd.to_numeric(r.get('Order Units'),errors='coerce') or 0)}"
                for _, r in x.iterrows())
            return self._send(200, {"cols": cols, "rows": disp.values.tolist(), "text": txt,
                                    "total": total, "units": units, "cases": cases, "mode": "store"})
        if u.path == "/api/livesales":
            # near-real-time "today's sales" for one department, from data/live_sales.json
            dept = parse_qs(u.query).get("dept", ["THC"])[0]
            b = get_bytes("live_sales.json")
            if not b:
                return self._send(200, {"available": False})
            try:
                d = json.loads(b.decode("utf-8", "replace"))
            except Exception:
                return self._send(200, {"available": False})
            dv = (d.get("depts") or {}).get(dept) or {"units": 0, "sales": 0, "by_store": [], "top": []}
            return self._send(200, {"available": True, "as_of": d.get("as_of"), "date": d.get("date"),
                                    "dept": dept, "chain": d.get("chain") or {},
                                    "units": dv.get("units", 0), "sales": dv.get("sales", 0),
                                    "by_store": dv.get("by_store", []), "top": dv.get("top", [])})
        if u.path == "/api/tabs":
            dept = parse_qs(u.query).get("dept", ["THC"])[0]
            return self._send(200, {"tabs": list_tabs(dept)})
        if u.path == "/api/list":
            qs = parse_qs(u.query)
            dept = qs.get("dept", ["THC"])[0]; tab = qs.get("tab", ["Remove"])[0]
            q = qs.get("q", [""])[0].strip().lower()
            sort = qs.get("sort", [""])[0]
            desc = qs.get("dir", ["asc"])[0] == "desc"
            try: offset = max(0, int(qs.get("offset", ["0"])[0]))
            except Exception: offset = 0
            try: limit = min(50000, max(1, int(qs.get("limit", ["400"])[0])))   # high cap allows full-tab export
            except Exception: limit = 400
            df = load_list(f"{prod_dept(dept)} - {tab}.csv")
            if df is None or not len(df):
                return self._send(200, {"cols": [], "rows": [], "total": 0, "matched": 0, "offset": 0, "limit": limit})
            try: fmap = json.loads(qs.get("f", ["{}"])[0]) or {}
            except Exception: fmap = {}
            try: fx = set(json.loads(qs.get("fx", ["[]"])[0]) or [])   # columns to match exactly
            except Exception: fx = set()
            want_facets = qs.get("facets", ["0"])[0] == "1"
            df = df.fillna("").astype(object)
            total = len(df)
            if q:                                   # search every column across the FULL tab
                hay = df.astype(str).agg(" ".join, axis=1).str.lower()
                df = df[hay.str.contains(q, regex=False)]
            facets = {}
            if want_facets:                         # dropdown values per low-cardinality column (deduped by case/spacing)
                for c in df.columns:
                    s = df[c].astype(str).str.strip()
                    s = s[(s != "") & (s.str.lower() != "nan")]
                    if s.empty:
                        continue
                    groups = {}                     # value_counts is desc -> first per key = dominant spelling
                    for orig in s.value_counts().index:
                        groups.setdefault(norm_val(orig), orig)
                    labels = sorted(set(groups.values()), key=lambda x: x.lower())
                    if 1 < len(labels) <= 50:
                        facets[c] = labels
            for c, val in fmap.items():             # per-column filters (dropdowns match all case/spacing variants)
                if c in df.columns and str(val) != "":
                    col = df[c].astype(str)
                    if c in fx:
                        df = df[col.str.strip().map(norm_val) == norm_val(val)]
                    else:
                        df = df[col.str.lower().str.contains(str(val).lower(), regex=False)]
            matched = len(df)
            if sort and sort in df.columns:         # sort the WHOLE (filtered) set, then page
                col = df[sort].astype(str)
                nums = pd.to_numeric(col.str.replace(r"[$,%\s]", "", regex=True), errors="coerce")
                if nums.notna().mean() >= 0.6:      # mostly numeric -> numeric sort
                    df = df.assign(_k=nums).sort_values("_k", ascending=not desc, na_position="last").drop(columns="_k")
                else:
                    df = df.assign(_k=col.str.lower()).sort_values("_k", ascending=not desc).drop(columns="_k")
            page = df.iloc[offset:offset + limit]
            resp = {"cols": list(map(str, page.columns)), "rows": page.values.tolist(),
                    "total": total, "matched": matched, "offset": offset, "limit": limit}
            if want_facets:
                resp["facets"] = facets
            return self._send(200, resp)
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
        if u.path == "/api/excel-actions":
            # turn a plain request + the sheet's shape into an approvable plan of cell operations
            try:
                req_text = str(payload.get("request", "")).strip()
                if not req_text:
                    return self._send(200, {"summary": "What would you like me to do?", "actions": []})
                return self._send(200, excel_plan(req_text, payload.get("context", {}) or {}))
            except Exception as e:
                return self._send(200, {"summary": f"Error planning that: {e}", "actions": []})
        if u.path == "/api/compare":
            try:
                import base64, io, csv, compare as cmp
                dept = str(payload.get("dept", "THC")).strip() or "THC"
                fname = str(payload.get("filename", "upload"))
                raw = str(payload.get("data", ""))
                if raw.startswith("data:") and "," in raw:
                    raw = raw.split(",", 1)[1]
                fb = base64.b64decode(raw)

                def rows_of(name):
                    b = get_bytes(name)
                    return list(csv.DictReader(io.StringIO(b.decode("utf-8", "replace")))) if b else []
                catalog = rows_of("%s - Full List.csv" % dept)
                if not catalog and dept == "Beer":           # Beer product tabs live under Spirits
                    catalog = rows_of("Spirits - Full List.csv")
                if not catalog:
                    return self._send(200, {"error": "No product catalog (Full List) found for %s yet." % dept})
                stock = rows_of("%s Inventory.csv" % dept)
                return self._send(200, cmp.compare(fb, fname, catalog, stock))
            except Exception as e:
                return self._send(200, {"error": "Couldn't compare that file: %s" % e})
        if u.path == "/api/feedback":
            text = str(payload.get("text", "")).strip()
            who = str(payload.get("who", "")).strip() or "(no name)"
            dept = str(payload.get("dept", "")).strip()
            page = str(payload.get("page", "")).strip()
            img = str(payload.get("image", ""))
            img_b64 = img.split(",", 1)[1] if "," in img else ""
            ts = time.strftime("%Y-%m-%d %H:%M")
            html = ("<b>From:</b> %s &nbsp; <b>Dept:</b> %s &nbsp; <b>Screen:</b> %s<br><b>When:</b> %s<br><br>%s"
                    % (who, dept, page, ts, (text or "(no description)").replace("\n", "<br>")))
            device = str(payload.get("device", "")).strip()
            emailed, detail = send_feedback_email(f"Buyer feedback - {who} ({dept})", html, img_b64)
            rid = re.sub(r"\D", "", str(time.time()))
            rec = {"id": rid, "ts": ts, "who": who, "dept": dept, "page": page,
                   "text": text, "device": device, "status": "open", "reply": "", "emailed": emailed}
            if img:
                rec["image"] = img        # full data URL, shown in the inbox card
            try:
                def add(data):
                    data.append(rec)
                update_index(add)
            except Exception:
                pass
            return self._send(200, {"ok": True, "emailed": emailed, "detail": detail, "id": rid})
        if u.path == "/api/admin/list":
            if not admin_ok(payload.get("key", "")):
                return self._send(200, {"ok": False})
            data = read_index()
            data.sort(key=lambda r: (r.get("status") == "fixed", -int(r.get("id", "0") or 0)))
            return self._send(200, {"ok": True, "reports": data})
        if u.path == "/api/admin/resolve":
            if not admin_ok(payload.get("key", "")):
                return self._send(200, {"ok": False})
            rid = str(payload.get("id", "")); reply = str(payload.get("reply", "")).strip()
            status = str(payload.get("status", "fixed"))

            def upd(data):
                for r in data:
                    if str(r.get("id")) == rid:
                        r["status"] = status; r["reply"] = reply
                        r["resolved_ts"] = time.strftime("%Y-%m-%d %H:%M")
            ok = update_index(upd)
            return self._send(200, {"ok": ok})
        if u.path == "/api/decisions":
            dept = str(payload.get("dept", "")).strip()
            who = str(payload.get("who", "")).strip() or "(no name)"
            decs = payload.get("decisions", []) or []
            ts = time.strftime("%Y-%m-%d %H:%M")
            curmap = {_dec_key(c.get("dept"), c.get("item")): c for c in read_decisions()}
            log = read_changes()
            base = re.sub(r"\D", "", str(time.time()))
            applied = 0
            for i, d in enumerate(decs):
                item = str(d.get("item", "")).strip()
                action = str(d.get("action", "")).strip()
                if not item or action not in ("approve", "qty", "skip", "fix"):
                    continue
                key = _dec_key(dept, item)
                prev = curmap.get(key)
                curmap[key] = {"dept": dept, "item": item, "action": action,
                               "qty": d.get("qty"), "note": str(d.get("note", "")).strip(), "ts": ts, "who": who}
                log.append({"id": f"{base}{i:03d}", "ts": ts, "who": who, "dept": dept, "item": item,
                            "action": action, "qty": d.get("qty"), "note": str(d.get("note", "")).strip(),
                            "prev": ({k: prev.get(k) for k in ("action", "qty", "note")} if prev else None),
                            "undone": False})
                applied += 1
            write_decisions(list(curmap.values()))
            write_changes(log)
            rows = "".join("<li><b>%s</b>: %s%s%s</li>" % (
                d.get("item"), d.get("action"),
                (" &rarr; %s units" % d.get("qty")) if d.get("action") == "qty" else "",
                (" - %s" % d.get("note")) if d.get("note") else "")
                for d in decs if str(d.get("action", "")) in ("approve", "qty", "skip", "fix"))
            if rows:
                send_feedback_email(f"Buyer order changes - {dept} ({who})",
                                    f"<b>{dept}</b> order changes ({ts}):<ul>{rows}</ul>", "")
            return self._send(200, {"ok": True, "count": applied})
        if u.path == "/api/undo":
            cid = str(payload.get("id", ""))
            who = str(payload.get("who", "")).strip() or "(no name)"
            ts = time.strftime("%Y-%m-%d %H:%M")
            log = read_changes()
            entry = next((c for c in log if str(c.get("id")) == cid and not c.get("undone")), None)
            if not entry:
                return self._send(200, {"ok": False})
            curmap = {_dec_key(c.get("dept"), c.get("item")): c for c in read_decisions()}
            key = _dec_key(entry.get("dept"), entry.get("item"))
            prev = entry.get("prev")
            if prev and prev.get("action"):
                curmap[key] = {"dept": entry["dept"], "item": entry["item"], "action": prev.get("action"),
                               "qty": prev.get("qty"), "note": prev.get("note", ""), "ts": ts, "who": who}
            else:
                curmap.pop(key, None)
            entry["undone"] = True
            base = re.sub(r"\D", "", str(time.time()))
            log.append({"id": f"{base}u", "ts": ts, "who": who, "dept": entry["dept"], "item": entry["item"],
                        "action": "undo", "qty": None, "note": f"undid '{entry.get('action')}'",
                        "prev": None, "undone": True})
            write_decisions(list(curmap.values()))
            write_changes(log)
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "unknown endpoint"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"Buyer client running on {HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
