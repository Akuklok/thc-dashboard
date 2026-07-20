"""
Weekly / Sunday ad order helper.

Given ad items (which come as marketing shorthand: slash pairs, whole brands, abbreviations),
match each to the wine catalog for UPC / case pack / price / buy months, pull stock and velocity
from the snapshot, and suggest cases to hit the weeks-of-supply target:
    Weekly Special -> 4.5 weeks     Sunday Special -> 8 weeks

Handles:
  - "A/B"                    -> split, match each side
  - "All 90+ Cellars"       -> expand to every UPC in that brand
  - "Josh Cab"              -> prefix-fuzzy to "Josh Cellars Cabernet"
Returns paste-ready rows plus anything it could not match.
"""
import re, math

TARGETS = {"weekly": 4.5, "sunday": 8.0}          # legacy default (THC/Spirits shape)
# Ad stock targets PER DEPARTMENT, grounded in measured ad lift (Phase 0 daily analysis,
# 2026-07-20). Sunday specials only out-lift weekly ads for THC (1.54x vs 1.21x) and Spirits
# (1.50x vs 1.31x), so only those earn the deeper Sunday target. Wine's Sunday lift (1.26x) is
# BELOW its weekly (1.52x) and Beer's is flat (1.14x vs 1.20x), so their Sunday ads get the
# same target as a weekly ad - buying 8 weeks there just built dead stock.
DEPT_TARGETS = {"thc":     {"weekly": 4.5, "sunday": 8.0},   # keys lowercase: "THC".title() is "Thc"
                "spirits": {"weekly": 4.5, "sunday": 8.0},
                "wine":    {"weekly": 4.5, "sunday": 4.5},
                "beer":    {"weekly": 4.5, "sunday": 4.5}}

def targets_for(dept):
    """Ad weeks-of-supply targets for a department. Unknown depts get no deeper Sunday."""
    return DEPT_TARGETS.get(str(dept or "").strip().lower(), {"weekly": 4.5, "sunday": 4.5})

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

_BRAND_SIGNAL = re.compile(r"\ball\b|\(all|all types|all varietals|all flavors|all varieties", re.I)
BRAND_CAP = 40


def _norm(s):
    s = re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def _digits(s):
    return re.sub(r"\D", "", str(s or ""))


def _num(s):
    try:
        return float(str(s).replace(",", "").replace("$", "").strip())
    except Exception:
        return 0.0


def _parse_months(s):
    out = set()
    for tok in re.split(r"[^a-z0-9]+", str(s or "").lower()):
        if not tok:
            continue
        if tok[:3] in _MONTHS:
            out.add(_MONTHS[tok[:3]])
        elif tok.isdigit() and 1 <= int(tok) <= 12:
            out.add(int(tok))
    return out


def _expand_queries(raw):
    """A raw ad line -> list of (query, is_brand). Splits slash pairs and flags whole-brand items."""
    out = []
    for part in re.split(r"\s*/\s*", str(raw or "")):
        p = part.strip()
        if not p:
            continue
        is_brand = bool(_BRAND_SIGNAL.search(p))
        q = re.sub(r"\(all[^)]*\)", " ", p, flags=re.I)
        q = re.sub(r"\ball (types|varietals|flavors|varieties)\b", " ", q, flags=re.I)
        q = re.sub(r"\ball\b", " ", q, flags=re.I)
        q = re.sub(r"\s+", " ", q).strip()
        out.append((q or p, is_brand))
    return out


def _prefix_hit(qt, ct):
    # exact, or a catalog word that extends a short query abbreviation (cab -> cabernet).
    # Deliberately NOT the other direction, so a brand like "whitehaven" doesn't match "white".
    return qt == ct or (3 <= len(qt) < len(ct) and ct.startswith(qt))


# Wine varietal initialisms that prefix-matching can't catch (SB -> Sauvignon Blanc, etc.).
_VARIETAL_ABBR = {
    "sb": "sauvignon blanc", "cs": "cabernet sauvignon", "pn": "pinot noir",
    "pg": "pinot grigio", "pgr": "pinot gris", "gsm": "grenache syrah mourvedre",
    "cdp": "chateauneuf du pape",
}


def _expand_abbr(norm_query):
    """Expand standalone varietal initialisms in a normalized query (Horologist SB -> ... sauvignon blanc)."""
    return " ".join(_VARIETAL_ABBR.get(t, t) for t in norm_query.split())


def suggest(items, kind, catalog_rows, inv_rows, month=None, targets=None):
    target = (targets or TARGETS).get(str(kind or "").lower(), 4.5)

    cat_by_upc, cat_by_desc, cat_tokens, desc_upcs = {}, {}, [], {}
    for r in catalog_rows:
        u = _digits(r.get("Product UPC"))
        if u:
            cat_by_upc[u] = r
        d = _norm(r.get("Product Description"))
        if d:
            cat_by_desc.setdefault(d, r)
            if u:
                desc_upcs.setdefault(d, set()).add(u)
            cat_tokens.append((set(d.split()), r))

    inv_by_desc, inv_tokens = {}, []
    for r in inv_rows:
        d = _norm(r.get("Item"))
        if d:
            inv_by_desc.setdefault(d, r)
            inv_tokens.append((set(d.split()), r))

    def best_token(qtokens, pool):
        best, best_score = None, 0.0
        for toks, row in pool:
            if not toks:
                continue
            hit = sum(1 for qt in qtokens if any(_prefix_hit(qt, ct) for ct in toks))
            if not hit:
                continue
            score = hit / max(len(qtokens), 1)
            if score > best_score:
                best, best_score = row, score
        return best, best_score

    def match_one(q):
        dig = _digits(q)
        if len(dig) >= 11:
            c = cat_by_upc.get(dig) or cat_by_upc.get(dig.lstrip("0"))
            if c:
                return c, "upc"
        nd = _norm(q)
        if nd in cat_by_desc:
            return cat_by_desc[nd], "exact"
        cand, sc = best_token(set(_expand_abbr(nd).split()), cat_tokens)
        if cand and sc >= 0.6:
            return cand, "fuzzy"
        return None, None

    def brand_matches(brand_tokens):
        return [row for toks, row in cat_tokens if brand_tokens and brand_tokens <= toks]

    def build_row(cat, conf, extra_flags):
        cd = _norm(cat.get("Product Description"))
        inv = inv_by_desc.get(cd)
        if not inv:
            inv, _ = best_token(set(cd.split()), inv_tokens)
        oh = _num(inv.get("Chain OH")) if inv else 0.0
        vel30 = _num(inv.get("Wk Velocity")) if inv else 0.0
        vel90 = (_num(inv.get("90D Units")) * 7.0 / 90.0) if inv else 0.0
        vel = vel30 if vel30 > 0 else vel90
        wos = round(oh / vel, 1) if vel > 0 else (_num(inv.get("WOS")) if inv else 0.0)
        cpk = _num(cat.get("Units/Case")) or 1.0
        price = _num(cat.get("Net Case")) or _num(cat.get("Case 1 (Retail)"))

        flags = list(extra_flags)
        if conf == "fuzzy":
            flags.append("check match")
        if conf != "upc" and len(desc_upcs.get(cd, ())) > 1:
            flags.append("multiple UPCs, confirm")
        if not inv:
            flags.append("no stock data")
        if vel > 0:
            need_u = max(0.0, target * vel - oh)
            cases = int(math.ceil(need_u / cpk)) if need_u > 0 else 0
            if vel30 <= 0 and vel90 > 0:
                flags.append("based on 90-day pace")
        else:
            cases = None
            flags.append("no recent sales, set by hand")
        bm = str(cat.get("Buy Months (if appl.)") or "").strip()
        if bm and month:
            months = _parse_months(bm)
            if months and month not in months:
                flags.append("not buy month (%s)" % bm)

        return {"UPC": _digits(cat.get("Product UPC")), "Item": re.sub(r"\s+", " ", str(cat.get("Product Description") or "")).strip(),
                "On hand": int(round(oh)), "Wk vel": round(vel, 1), "WOS": round(wos, 1),
                "Target WOS": target, "Suggested cases": (cases if cases is not None else ""),
                "Case price": round(price, 2), "Order $": round((cases or 0) * price, 2),
                "Flag": "; ".join(flags)}

    rows, unmatched, seen = [], [], set()
    for raw in items:
        for q, is_brand in _expand_queries(raw):
            if is_brand:
                bts = {t for t in set(_norm(q).split()) if len(t) >= 2}
                ms = brand_matches(bts)
                if ms:
                    capped = len(ms) > BRAND_CAP
                    for cat in ms[:BRAND_CAP]:
                        u = _digits(cat.get("Product UPC"))
                        if u and u in seen:
                            continue
                        if u:
                            seen.add(u)
                        rows.append(build_row(cat, "brand", ["whole brand"] + (["many, review"] if capped else [])))
                else:
                    unmatched.append(raw)
            else:
                cat, conf = match_one(q)
                if cat:
                    u = _digits(cat.get("Product UPC"))
                    if u and u in seen:
                        continue
                    if u:
                        seen.add(u)
                    rows.append(build_row(cat, conf, []))
                else:
                    unmatched.append(q)

    return {"rows": rows, "unmatched": unmatched, "target": target, "kind": kind,
            "matched": len(rows), "missed": len(unmatched)}


def parse_ad_file(xlsx_bytes, kind="weekly", target_date=None):
    """Read the 'Weekly and Sunday Specials' workbook and pull one ad week's item lines.
    The sheets are one block per week (a header with the ad date, then Type/Item/Size/price rows).
    Returns {items, week (chosen ad date), weeks (all ad dates)}."""
    import io, datetime, openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
    want = "sunday" if str(kind).lower().startswith("sun") else "weekly"
    sheet = None
    for s in wb.sheetnames:
        sl = s.lower()
        if want == "sunday" and "sunday" in sl and "special" in sl:
            sheet = s; break
        if want == "weekly" and "weekly" in sl and "ad" in sl:
            sheet = s; break
    if not sheet:
        wb.close()
        return {"items": [], "week": None, "weeks": []}
    rows = [list(r) for r in wb[sheet].iter_rows(values_only=True)]
    wb.close()

    target = target_date or datetime.date.today()
    blocks = []                                            # (row index, ad date) one per week
    for i, r in enumerate(rows):
        if not r:
            continue
        text = " ".join(str(c).lower() for c in r[:8] if c is not None)
        if "in-store ad" in text or "content due" in text:
            dt = None
            for c in r[:10]:
                if isinstance(c, datetime.datetime):
                    dt = c.date(); break
            if dt:
                blocks.append((i, dt))
    if not blocks:
        return {"items": [], "week": None, "weeks": []}
    past = [b for b in blocks if b[1] <= target]
    chosen = max(past, key=lambda b: b[1]) if past else min(blocks, key=lambda b: b[1])
    ci = blocks.index(chosen)
    start, end = chosen[0], (blocks[ci + 1][0] if ci + 1 < len(blocks) else len(rows))

    items = []
    for r in rows[start:end]:
        if not r or len(r) < 3:
            continue
        c2 = r[2]
        if c2 is None or isinstance(c2, datetime.datetime):
            continue
        it = re.sub(r"\s+", " ", str(c2)).strip()
        if not it or it.lower() == "item":
            continue
        rt = " ".join(str(x).lower() for x in r[:8] if x is not None)
        if "in-store ad" in rt or "content due" in rt:
            continue
        if len(r) > 1 and str(r[1]).strip().lower() == "type":
            continue
        items.append(it)
    return {"items": items, "week": chosen[1].isoformat(), "weeks": [b[1].isoformat() for b in blocks]}
