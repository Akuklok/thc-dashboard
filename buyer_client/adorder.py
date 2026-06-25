"""
Weekly / Sunday ad order helper.

Given a list of ad items (product descriptions or UPCs), match each to the wine catalog
(Wine - Full List.csv) for UPC / case pack / price / buy months, pull current stock and
velocity from the snapshot (Wine Inventory.csv), and suggest the cases needed to hit the
weeks-of-supply target:
    Weekly Special  -> 4.5 weeks of supply   (Laura's rule: 4 to 4.5)
    Sunday Special  -> 8 weeks of supply     (8+ rule of thumb)

Returns paste-ready rows plus any items it could not match (so the buyer can eyeball them).
"""
import re, math

TARGETS = {"weekly": 4.5, "sunday": 8.0}

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


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
    """Pull month numbers out of a Buy Months string ('Jan/Feb', 'March, April', '1|3', ...)."""
    out = set()
    low = str(s or "").lower()
    for tok in re.split(r"[^a-z0-9]+", low):
        if not tok:
            continue
        if tok[:3] in _MONTHS:
            out.add(_MONTHS[tok[:3]])
        elif tok.isdigit() and 1 <= int(tok) <= 12:
            out.add(int(tok))
    return out


def _best_token(qtokens, pool):
    """Best Jaccard-overlap row in pool (list of (tokenset, row)). Returns (row, score)."""
    best, best_score = None, 0.0
    for toks, row in pool:
        if not toks:
            continue
        inter = len(qtokens & toks)
        if not inter:
            continue
        score = inter / len(qtokens | toks)
        if score > best_score:
            best, best_score = row, score
    return best, best_score


def suggest(items, kind, catalog_rows, inv_rows, month=None):
    target = TARGETS.get(str(kind or "").lower(), 4.5)

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

    rows, unmatched = [], []
    for raw in items:
        q = str(raw or "").strip()
        if not q:
            continue
        cat, conf = None, "exact"
        dig = _digits(q)
        if len(dig) >= 11:
            cat = cat_by_upc.get(dig) or cat_by_upc.get(dig.lstrip("0"))
            conf = "upc"
        if not cat:
            nd = _norm(q)
            cat = cat_by_desc.get(nd)
            if not cat:
                cand, sc = _best_token(set(nd.split()), cat_tokens)
                if cand and sc >= 0.5:
                    cat, conf = cand, "fuzzy"
        if not cat:
            unmatched.append(q)
            continue

        cd = _norm(cat.get("Product Description"))
        inv = inv_by_desc.get(cd)
        if not inv:
            inv, _ = _best_token(set(cd.split()), inv_tokens)
        oh = _num(inv.get("Chain OH")) if inv else 0.0
        vel30 = _num(inv.get("Wk Velocity")) if inv else 0.0
        vel90 = (_num(inv.get("90D Units")) * 7.0 / 90.0) if inv else 0.0
        vel = vel30 if vel30 > 0 else vel90              # fall back to the 90-day pace
        wos = round(oh / vel, 1) if vel > 0 else (_num(inv.get("WOS")) if inv else 0.0)

        cpk = _num(cat.get("Units/Case")) or 1.0
        price = _num(cat.get("Net Case")) or _num(cat.get("Case 1 (Retail)"))

        flags = []
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
            cases = None                                 # no sales history -> the buyer sets the quantity
            flags.append("no recent sales, set by hand")

        bm = str(cat.get("Buy Months (if appl.)") or "").strip()
        if bm and month:
            months = _parse_months(bm)
            if months and month not in months:
                flags.append("not buy month (%s)" % bm)

        rows.append({
            "UPC": _digits(cat.get("Product UPC")),
            "Item": cat.get("Product Description"),
            "On hand": int(round(oh)), "Wk vel": round(vel, 1), "WOS": round(wos, 1),
            "Target WOS": target, "Suggested cases": (cases if cases is not None else ""),
            "Case price": round(price, 2), "Order $": round((cases or 0) * price, 2),
            "Flag": "; ".join(flags),
        })

    return {"rows": rows, "unmatched": unmatched, "target": target, "kind": kind,
            "matched": len(rows), "missed": len(unmatched)}
