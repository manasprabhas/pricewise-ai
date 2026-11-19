"""Decision engine: price a basket at every store combination, using only what was known at the cutoff.

`compute(inputs)` is a PURE function of a JSON-serializable `inputs` dict:
    {calc_version, cutoff, basket, settings, products, stores, observations}
It never reads the database. That is what makes a saved decision reproducible: store the inputs, and
`compute(stored_inputs)` gives the identical result next week, after new receipts or product edits.

`build_inputs(...)` is the only step that reads the database, through observations.load(cutoff).

Cost model, kept deliberately simple and visible:
  checkout      = sum of item costs (whole packages for packaged goods, measured weight for weighed goods)
  tax           = per store, the tax rate seen on that store's past receipts (unknown if never seen)
  trip cost     = (stores visited - 1) x "second-stop cost"; a trip to one store is the baseline you'd make anyway
  decision cost = checkout + known tax + trip cost
The cash budget is compared with checkout + known tax only. The trip trade-off is shown separately.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from itertools import combinations

import pandas as pd

from . import db, money, observations, units

CALC_VERSION = "decision-v1"
LB = units.base_factor("lb")
DEFAULT_SETTINGS = {"method": "median", "max_age_days": 120, "extra_stop_cents": 300, "budget_cents": 3500,
                    "max_stores": 3, "stores": None, "exclude_obs": []}


def today_cutoff() -> str:
    """The cutoff for a plan made 'now': every price seen up to and including today.
    The demo has its own fixed clock (the day after its last receipt), so it behaves the same whenever it's run."""
    clock = db.get_setting("demo_clock")
    if clock:
        newest = db.one("SELECT MAX(d) AS d FROM (SELECT MAX(purchased_on) AS d FROM receipts "
                        "UNION ALL SELECT MAX(observed_on) FROM shelf_prices)")["d"]
        if newest:
            clock = max(clock, (date.fromisoformat(newest) + timedelta(days=1)).isoformat())
        return clock
    return (date.today() + timedelta(days=1)).isoformat()


def today_label() -> str:
    if db.get_setting("demo_clock"):
        return (f"Demo clock: every price seen before {today_cutoff()} (the day after the demo's newest price, "
                f"so the demo works the same whenever you open it)")
    return f"Using every price seen up to and including today ({date.today().isoformat()})"


# ---------------------------------------------------------------- inputs
def product_facts(pid: int) -> dict:
    p = db.product(pid)
    return {"label": db.product_label(p), "brand": p["brand"] or "", "name": p["name"], "variant": p.get("variant") or "",
            "kind": p["kind"], "sold_by_weight": bool(p["sold_by_weight"]),
            "base_qty": float(p["base_qty"]) if p["base_qty"] else None,
            "size": units.fmt_size(p["size_value"], p["size_unit"]) if not p["sold_by_weight"] else "by the lb",
            "display_unit": p["display_unit"]}


def build_inputs(cutoff, basket: list[dict], settings: dict | None = None) -> dict:
    """Snapshot everything the decision may use: only observations dated before `cutoff`."""
    s = {**DEFAULT_SETTINGS, **(settings or {})}
    pids = set()
    for b in basket:
        pids.add(int(b["product_id"]))
        pids.update(int(x) for x in b.get("approved_substitutes", []))
    obs = observations.load(cutoff, product_ids=pids, exclude=set(s.get("exclude_obs") or []))
    if s.get("stores"):
        obs = obs[obs["store"].isin(s["stores"])]
    obs_records = []
    for r in obs.to_dict("records"):
        obs_records.append({k: (None if (isinstance(v, float) and pd.isna(v)) else v) for k, v in r.items()})
    rates = observations.store_tax_rates(cutoff)
    stores = sorted(set(obs["store"])) if not obs.empty else []
    return {
        "calc_version": CALC_VERSION,
        "cutoff": observations.as_day(cutoff).isoformat(),
        "basket": [{"product_id": int(b["product_id"]), "qty": float(b["qty"]),
                    "approved_substitutes": [int(x) for x in b.get("approved_substitutes", [])],
                    "requirements": b.get("requirements") or {}} for b in basket],
        "settings": s,
        "products": {str(pid): product_facts(pid) for pid in sorted(pids)},
        "stores": {st: {"tax_rate": rates.get(st)} for st in stores},
        "observations": obs_records,
    }


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def digest(obj) -> str:
    return hashlib.sha256(canonical(obj).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- estimates
def estimate(inputs: dict, pid: int, store: str, _cache: dict | None = None) -> dict:
    """The price we'd expect for one product at one store, from eligible observations only."""
    key = (pid, store)
    if _cache is not None and key in _cache:
        return _cache[key]
    s = inputs["settings"]
    cut = date.fromisoformat(inputs["cutoff"])
    mine = [o for o in inputs["observations"] if o["product_id"] == pid and o["store"] == store]
    fresh = [o for o in mine if (cut - date.fromisoformat(str(o["date"])[:10])).days <= int(s["max_age_days"])]
    if not fresh:
        out = {"ok": False, "reason": "stale" if mine else "none",
               "last_seen": max((str(o["date"])[:10] for o in mine), default=None)}
    else:
        fresh.sort(key=lambda o: (str(o["date"]), o["obs_id"]))
        if s["method"] == "last":
            used = [fresh[-1]]
            price = used[0]["price_cents"]
        else:
            used = fresh
            price = money.median_cents([o["price_cents"] for o in fresh])
        newest = str(fresh[-1]["date"])[:10]
        out = {"ok": True, "price_cents": int(price), "basis": fresh[-1]["basis"], "method": s["method"],
               "n": len(used), "n_eligible": len(fresh), "newest": newest, "oldest": str(used[0]["date"])[:10],
               "age_days": (cut - date.fromisoformat(newest)).days, "obs_ids": [o["obs_id"] for o in used],
               "limited": len(fresh) == 1 or all(o.get("promo") for o in used),
               "limited_why": ("one purchase only" if len(fresh) == 1 else
                               "only sale or promotion prices" if all(o.get("promo") for o in used) else "")}
    if _cache is not None:
        _cache[key] = out
    return out


def _variant_ok(req: dict, sub: dict, requirements: dict) -> tuple[bool, str]:
    need = {v.strip() for v in (requirements.get("variant") or req.get("variant") or "").split(",") if v.strip()}
    have = {v.strip() for v in (sub.get("variant") or "").split(",") if v.strip()}
    if not need <= have:
        return False, f"must be {', '.join(sorted(need - have))}"
    mp = requirements.get("min_pack_base")
    if mp and not sub["sold_by_weight"] and (sub["base_qty"] or 0) < float(mp) * 0.999:
        return False, "package smaller than your minimum"
    return True, ""


def needed_base(item: dict, prod: dict) -> float:
    """How much food the shopper asked for, in base units (grams, millilitres or a count)."""
    if prod["sold_by_weight"]:
        return float(item["qty"]) * LB
    return float(item["qty"]) * float(prod["base_qty"] or 0)


def option(inputs: dict, item: dict, pid: int, store: str, cache: dict) -> dict:
    """Cost of satisfying one basket item with product `pid` at `store` (exact product or a substitute)."""
    req = inputs["products"][str(item["product_id"])]
    prod = inputs["products"][str(pid)]
    exact = pid == item["product_id"]
    if not prod["sold_by_weight"] and not prod["base_qty"]:
        return {"ok": False, "reason": "missing package size"}
    est = estimate(inputs, pid, store, cache)
    if not est["ok"]:
        return {"ok": False, "reason": est["reason"], "last_seen": est.get("last_seen")}
    if exact and not prod["sold_by_weight"]:
        packages = max(1, money.packages_needed(float(item["qty"]), 1.0))   # whole packages
        qty, unit, cost = packages, "pkg", packages * est["price_cents"]
    elif prod["sold_by_weight"]:
        lbs = round(needed_base(item, req) / LB, 2)
        qty, unit, cost = lbs, "lb", money.line_cents(lbs, est["price_cents"] / 100)
    else:
        packages = money.packages_needed(needed_base(item, req), float(prod["base_qty"]))
        qty, unit, cost = packages, "pkg", packages * est["price_cents"]
    return {"ok": True, "store": store, "product_id": pid, "label": prod["label"], "exact": exact, "qty": qty,
            "unit": unit, "price_cents": est["price_cents"], "basis": est["basis"], "cost_cents": int(cost),
            "estimate": est}


def _best_for_item(inputs: dict, idx: int, stores: tuple[str, ...], cache: dict) -> tuple[dict | None, list[str]]:
    item = inputs["basket"][idx]
    req = inputs["products"][str(item["product_id"])]
    cands, reasons = [], []
    for store in stores:
        o = option(inputs, item, item["product_id"], store, cache)
        if o["ok"]:
            cands.append(o)
        else:
            reasons.append(_why(o, store, inputs))
        for sid in item.get("approved_substitutes", []):
            sub = inputs["products"].get(str(sid))
            if not sub:
                continue
            ok, why = _variant_ok(req, sub, item.get("requirements") or {})
            if not ok:
                continue
            so = option(inputs, item, sid, store, cache)
            if so["ok"]:
                cands.append(so)
    if not cands:
        return None, reasons
    cands.sort(key=lambda o: (o["cost_cents"], not o["exact"], o["store"]))
    return cands[0], reasons


def _why(o: dict, store: str, inputs: dict) -> str:
    if o["reason"] == "none":
        return f"no observation at {store}"
    if o["reason"] == "stale":
        return (f"only stale history at {store} (last seen {o.get('last_seen')}, older than "
                f"{inputs['settings']['max_age_days']} days)")
    return f"{o['reason']} at {store}"


# ---------------------------------------------------------------- plans
def _plan(inputs: dict, stores: tuple[str, ...], cache: dict) -> dict | None:
    lines, missing = [], []
    for i in range(len(inputs["basket"])):
        best, reasons = _best_for_item(inputs, i, stores, cache)
        req_label = inputs["products"][str(inputs["basket"][i]["product_id"])]["label"]
        if best is None:
            missing.append({"item_index": i, "item": req_label, "reasons": reasons})
        else:
            lines.append({"item_index": i, "item": req_label, **best})
    used = tuple(sorted({l["store"] for l in lines}))
    if used != tuple(sorted(stores)):
        return None                      # a store you'd buy nothing at isn't a real plan
    checkout = sum(l["cost_cents"] for l in lines)
    tax, tax_known = 0, True
    for st in used:
        rate = inputs["stores"].get(st, {}).get("tax_rate")
        sub = sum(l["cost_cents"] for l in lines if l["store"] == st)
        if rate is None:
            tax_known = False
        else:
            tax += money.cents(money.to_decimal(sub) * money.to_decimal(repr(rate)) / 100)
    extra = max(0, len(used) - 1)
    trip = extra * int(inputs["settings"]["extra_stop_cents"])
    dates = [l["estimate"]["newest"] for l in lines]
    budget = inputs["settings"].get("budget_cents")
    return {
        "name": " + ".join(used), "stores": list(used), "lines": lines, "missing": missing,
        "covered": len(lines), "total_items": len(inputs["basket"]), "complete": not missing,
        "checkout_cents": checkout, "tax_cents": tax, "tax_known": tax_known,
        "cash_cents": checkout + tax, "extra_stops": extra, "trip_cents": trip,
        "decision_cents": checkout + tax + trip,
        "fits_budget": None if budget is None else checkout + tax <= int(budget),
        "evidence_newest": max(dates) if dates else None, "evidence_oldest": min(dates) if dates else None,
        "limited_lines": sum(1 for l in lines if l["estimate"]["limited"]),
        "substitutions": sum(1 for l in lines if not l["exact"]),
    }


def compute(inputs: dict) -> dict:
    """Every store combination, ranked. Complete plans compete; partial plans are listed apart."""
    stores = sorted(inputs["stores"])
    cache: dict = {}
    plans = []
    for k in range(1, min(int(inputs["settings"].get("max_stores", 3)), len(stores)) + 1):
        for combo in combinations(stores, k):
            p = _plan(inputs, combo, cache)
            if p:
                plans.append(p)
    complete = sorted([p for p in plans if p["complete"]],
                      key=lambda p: (p["decision_cents"], len(p["stores"]), p["name"]))
    partial = sorted([p for p in plans if not p["complete"]],
                     key=lambda p: (-p["covered"], p["checkout_cents"], p["name"]))
    winner = complete[0]["name"] if complete else None
    return {"calc_version": inputs["calc_version"], "cutoff": inputs["cutoff"], "winner": winner,
            "complete": complete, "partial": partial, "n_items": len(inputs["basket"]),
            "n_observations": len(inputs["observations"])}


def plan_by_name(result: dict, name: str) -> dict | None:
    return next((p for p in result["complete"] + result["partial"] if p["name"] == name), None)


# ---------------------------------------------------------------- what changes the decision?
def stop_cost_threshold(result: dict) -> dict | None:
    """The second-stop cost at which the best one-store plan and the best multi-store plan tie."""
    singles = [p for p in result["complete"] if len(p["stores"]) == 1]
    multis = [p for p in result["complete"] if len(p["stores"]) > 1]
    if not singles or not multis:
        return None
    s = min(singles, key=lambda p: (p["cash_cents"], p["name"]))
    m = min(multis, key=lambda p: (p["cash_cents"] + 0, p["name"]))
    advantage = s["cash_cents"] - m["cash_cents"]
    by_item_s = {l["item_index"]: l for l in s["lines"]}
    rows = []
    for l in m["lines"]:
        a = by_item_s.get(l["item_index"])
        if a:
            rows.append({"item": l["item"], "single_store": a["store"], "single_cents": a["cost_cents"],
                         "multi_store": l["store"], "multi_cents": l["cost_cents"],
                         "diff_cents": a["cost_cents"] - l["cost_cents"]})
    rows.sort(key=lambda r: -r["diff_cents"])
    carriers, acc = [], 0
    for r in rows:
        if r["diff_cents"] <= 0 or acc >= advantage:
            break
        carriers.append(r)
        acc += r["diff_cents"]
    per_stop = advantage / max(1, m["extra_stops"])
    return {"single": s["name"], "multi": m["name"], "advantage_cents": advantage,
            "tie_at_cents": int(round(per_stop)) if advantage > 0 else None,
            "extra_stops": m["extra_stops"], "items": rows, "carriers": carriers}


def sweep(inputs: dict, key: str, values: list) -> list[dict]:
    """Recompute with one setting changed; report the winner at each value."""
    out = []
    for v in values:
        alt = json.loads(canonical(inputs))
        alt["settings"][key] = v
        r = compute(alt)
        w = r["complete"][0] if r["complete"] else None
        out.append({"value": v, "winner": r["winner"], "decision_cents": w["decision_cents"] if w else None,
                    "best_partial": (f"{r['partial'][0]['covered']} of {r['n_items']} items"
                                     if not w and r["partial"] else None)})
    return out


def change_points(rows: list[dict]) -> list[tuple]:
    pts = []
    for a, b in zip(rows, rows[1:]):
        if a["winner"] != b["winner"]:
            pts.append((a["value"], b["value"], a["winner"], b["winner"]))
    return pts


# ---------------------------------------------------------------- baskets
def infer_basket(cutoff, weeks: int = 1) -> list[dict]:
    """Your usual basket from trips BEFORE the cutoff (12 weeks of history), scaled to `weeks`."""
    cut = observations.as_day(cutoff)
    pur = db.purchases()
    if pur.empty:
        return []
    pur = pur[(pur["purchased_on"] < pd.Timestamp(cut)) & (pur["purchased_on"] >= pd.Timestamp(cut - timedelta(days=84)))]
    out = []
    for kind, g in pur.groupby("kind"):
        if g["receipt_id"].nunique() < 3:
            continue
        pid = int(g["product_id"].mode().iloc[0])
        p = db.product(pid)
        need = g["base_qty_total"].sum() / 12 * weeks
        if p["sold_by_weight"]:
            qty = round(need / LB, 1)
        else:
            qty = max(1, round(need / float(p["base_qty"] or 1)))
        if qty > 0:
            out.append({"product_id": pid, "qty": float(qty), "approved_substitutes": [], "requirements": {}})
    return out


def receipt_qty(row) -> float:
    """A receipt line's amount in the plan's units: pounds for food sold by weight (whatever unit the receipt
    weighed it in), packages otherwise. The same conversion observations.load uses for prices per lb."""
    qty = float(row["qty"] or 0)
    if not row["sold_by_weight"]:
        return qty
    unit = units.normalize_unit(row.get("qty_unit") if hasattr(row, "get") else row["qty_unit"])
    if unit not in ("lb", "oz", "kg", "g"):
        unit = "lb"                                  # a weight with no unit is read as pounds, as everywhere else
    return qty * units.base_factor(unit) / LB


def basket_from_receipt(receipt_id: int) -> list[dict]:
    """The items actually bought on a trip (labelled 'reconstructed after the trip' wherever shown)."""
    pur = db.purchases(include_unverified=True)
    rows = pur[pur["receipt_id"] == receipt_id]
    out: dict[int, float] = {}
    for _, r in rows.iterrows():
        out[int(r["product_id"])] = out.get(int(r["product_id"]), 0) + receipt_qty(r)
    return [{"product_id": pid, "qty": round(q, 2), "approved_substitutes": [], "requirements": {}}
            for pid, q in out.items()]


def substitute_candidates(pid: int) -> list[dict]:
    """Same kind of food, other products (other brands or sizes), with their facts, for opt-in approval."""
    p = db.product(pid)
    df = db.products()
    df = df[(df["kind"] == p["kind"]) & (df["id"] != pid)]
    return [{"product_id": int(r["id"]), **product_facts(int(r["id"]))} for _, r in df.iterrows()]


# ---------------------------------------------------------------- saving and replaying decisions
def save(inputs: dict, result: dict, name: str, mode: str, trip_receipt_id: int | None = None) -> int:
    return db.run("""INSERT INTO decisions(created_at, name, mode, cutoff, trip_receipt_id, calc_version, inputs, result,
                     input_hash, result_hash) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                  (datetime.now().isoformat(timespec="seconds"), name, mode, inputs["cutoff"], trip_receipt_id,
                   inputs["calc_version"], canonical(inputs), canonical(result), digest(inputs), digest(result)))


def load_decision(did: int) -> dict:
    row = db.one("SELECT * FROM decisions WHERE id = ?", (did,))
    row["inputs"] = json.loads(row["inputs"])
    row["result"] = json.loads(row["result"])
    return row


def reproduce(did: int) -> dict:
    """Recompute from the frozen inputs. Must give the identical result (same hash)."""
    d = load_decision(did)
    again = compute(d["inputs"])
    return {"same": digest(again) == d["result_hash"], "result": again, "stored": d["result"]}


def replay_live(did: int) -> dict:
    """Rebuild the inputs from TODAY's database with the SAME cutoff, basket and settings, and compare.
    Receipts imported later but dated after the cutoff can't change it. If it differs, the history before the
    cutoff was edited; the differences are listed."""
    d = load_decision(did)
    inp = d["inputs"]
    fresh = build_inputs(inp["cutoff"], inp["basket"], inp["settings"])
    res = compute(fresh)
    old_ids = {o["obs_id"] for o in inp["observations"]}
    new_ids = {o["obs_id"] for o in fresh["observations"]}
    old_obs = {o["obs_id"]: o for o in inp["observations"]}
    changed = [o["obs_id"] for o in fresh["observations"]
               if o["obs_id"] in old_obs and o["price_cents"] != old_obs[o["obs_id"]]["price_cents"]]
    return {"same_result": digest(res) == d["result_hash"], "same_winner": res["winner"] == d["result"]["winner"],
            "result": res, "added": sorted(new_ids - old_ids), "removed": sorted(old_ids - new_ids),
            "changed": changed}


# ---------------------------------------------------------------- prediction vs. receipt
def compare_with_receipt(inputs: dict, result: dict, receipt_id: int) -> dict:
    """Reveal: what was predicted for the store you actually visited vs. what the receipt says."""
    rec = db.one("SELECT r.*, s.name AS store FROM receipts r JOIN stores s ON s.id = r.store_id WHERE r.id = ?",
                 (receipt_id,))
    store = rec["store"]
    pur = db.purchases(include_unverified=True)
    lines = pur[pur["receipt_id"] == receipt_id].copy()
    cache: dict = {}
    used_items: set[int] = set()
    rows = []
    for idx, item in enumerate(inputs["basket"]):
        req = inputs["products"][str(item["product_id"])]
        est = estimate(inputs, item["product_id"], store, cache)
        same = lines[(lines["product_id"] == item["product_id"]) & (~lines["item_id"].isin(used_items))]
        kind_rows = lines[(lines["kind"] == req["kind"]) & (lines["product_id"] != item["product_id"])
                          & (~lines["item_id"].isin(used_items))]
        if not same.empty:
            for _, l in same.iterrows():
                used_items.add(int(l["item_id"]))
                qty = receipt_qty(l)
                actual_unit = int(round(l["paid_cents"] / qty)) if qty else int(l["paid_cents"])
                pred = (money.line_cents(qty, est["price_cents"] / 100) if req["sold_by_weight"]
                        else int(round(qty * est["price_cents"]))) if est["ok"] else None
                rows.append({"type": "compared", "item": req["label"], "bought": req["label"], "qty": qty,
                             "unit": "lb" if req["sold_by_weight"] else "pkg",
                             "predicted_unit_cents": est.get("price_cents"), "actual_unit_cents": actual_unit,
                             "predicted_cents": pred, "actual_cents": int(l["paid_cents"]),
                             "error_cents": None if pred is None else int(l["paid_cents"]) - pred,
                             "evidence": est.get("obs_ids", []), "item_id": int(l["item_id"]),
                             "note": "" if est["ok"] else f"no eligible price at {store} before the trip"})
        elif not kind_rows.empty:
            l = kind_rows.iloc[0]
            used_items.add(int(l["item_id"]))
            rows.append({"type": "substituted", "item": req["label"], "bought": db.product_label(l),
                         "qty": receipt_qty(l), "unit": "lb" if l["sold_by_weight"] else "pkg",
                         "predicted_unit_cents": est.get("price_cents"), "actual_unit_cents": None,
                         "predicted_cents": None, "actual_cents": int(l["paid_cents"]), "error_cents": None,
                         "evidence": est.get("obs_ids", []), "item_id": int(l["item_id"]),
                         "note": "you bought a different product of the same kind; not counted as an error"})
        else:
            rows.append({"type": "missing", "item": req["label"], "bought": "", "qty": item["qty"],
                         "unit": "lb" if req["sold_by_weight"] else "pkg",
                         "predicted_unit_cents": est.get("price_cents"), "actual_unit_cents": None,
                         "predicted_cents": None, "actual_cents": None, "error_cents": None,
                         "evidence": est.get("obs_ids", []), "item_id": None, "note": "planned, not on the receipt"})
    for _, l in lines[~lines["item_id"].isin(used_items)].iterrows():
        rows.append({"type": "extra", "item": "", "bought": db.product_label(l), "qty": receipt_qty(l),
                     "unit": "lb" if l["sold_by_weight"] else "pkg", "predicted_unit_cents": None,
                     "actual_unit_cents": None, "predicted_cents": None, "actual_cents": int(l["paid_cents"]),
                     "error_cents": None, "evidence": [], "item_id": int(l["item_id"]),
                     "note": "bought, not in the planned basket"})
    adj = db.q("SELECT kind, cents, description FROM receipt_adjustments WHERE receipt_id = ?", (receipt_id,))
    lines_cents = sum(r["actual_cents"] or 0 for r in rows)
    adj_cents = int(adj["cents"].sum()) if not adj.empty else 0
    tax = int(rec["tax_cents"] or 0)
    accounted = lines_cents + adj_cents + tax
    compared = [r for r in rows if r["type"] == "compared" and r["error_cents"] is not None]
    observed = observed_savings(inputs, store, compared)
    winner = plan_by_name(result, result["winner"]) if result.get("winner") else None
    return {
        "store": store, "date": rec["purchased_on"], "status": rec["status"], "rows": rows,
        "total_cents": int(rec["total_cents"]), "accounted_cents": accounted,
        "unreconciled_cents": int(rec["total_cents"]) - accounted, "adjustments": adj.to_dict("records"),
        "tax_cents": tax, "n_compared": len(compared),
        "abs_error_cents": sum(abs(r["error_cents"]) for r in compared),
        "net_error_cents": sum(r["error_cents"] for r in compared),
        "followed_plan": bool(winner and winner["stores"] == [store]),
        "plan_relation": ("followed" if winner and winner["stores"] == [store] else
                          "partly" if winner and store in winner["stores"] else "other"),
        "plan_stores": winner["stores"] if winner else [],
        "observed": observed,
    }


def observed_savings(inputs: dict, visited: str, compared: list[dict], window_days: int = 7) -> list[dict]:
    """Only where a comparable alternative was actually SEEN: the same product at another store, observed in
    the 7 days before the trip. Everything else about unvisited stores is an estimate, never a saving."""
    cut = date.fromisoformat(inputs["cutoff"])
    out = []
    for r in compared:
        pid = next((int(k) for k, v in inputs["products"].items() if v["label"] == r["item"]), None)
        alts = [o for o in inputs["observations"] if o["product_id"] == pid and o["store"] != visited
                and (cut - date.fromisoformat(str(o["date"])[:10])).days <= window_days]
        if not alts:
            continue
        best = min(alts, key=lambda o: o["price_cents"])
        diff = (best["price_cents"] - r["actual_unit_cents"]) * (r["qty"] if r["unit"] == "pkg" else 1)
        if r["unit"] == "lb":
            diff = money.line_cents(r["qty"], (best["price_cents"] - r["actual_unit_cents"]) / 100)
        out.append({"item": r["item"], "other_store": best["store"], "other_price_cents": best["price_cents"],
                    "other_seen": str(best["date"])[:10], "other_obs": best["obs_id"],
                    "paid_unit_cents": r["actual_unit_cents"], "qty": r["qty"], "unit": r["unit"],
                    "saving_cents": int(diff)})
    return out


# ---------------------------------------------------------------- saved lists
def save_list(name: str, basket: list[dict]) -> int:
    return db.run("INSERT INTO saved_lists(name, created_at, items) VALUES (?,?,?)",
                  (name, datetime.now().isoformat(timespec="seconds"), canonical(basket)))


def lists() -> pd.DataFrame:
    return db.q("SELECT * FROM saved_lists ORDER BY id DESC")


def load_list(list_id: int) -> list[dict]:
    row = db.one("SELECT items FROM saved_lists WHERE id = ?", (list_id,))
    return json.loads(row["items"]) if row else []
