"""Smart stock-up advisor.

For each product you buy regularly it learns:
- how fast you use it (from how much you buy over time)
- what counts as a good price for you (your lowest 20% of observed prices)
- whether it goes on sale in a rhythm (e.g. every ~4 weeks), from receipts and recorded price checks
Then it says "stock up", "wait" or "normal price", and warns when buying extra could go to waste.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import analytics, db, units

FREEZER_WEEKS = 8


@dataclass
class Advice:
    product_id: int
    label: str
    action: str                 # "stock up" | "wait" | "normal"
    message: str
    current: str
    good_price: str
    usual: str
    weekly_use: float           # in display units
    display_unit: str
    cycle_days: float | None
    next_sale: pd.Timestamp | None
    max_weeks: float
    suggest_qty: float          # packages or lb
    qty_unit: str
    observed: int
    percentile: float
    expected_saving: float
    unit_cost_now: float


def _observations(product_id: int, pur: pd.DataFrame) -> pd.DataFrame:
    h = pur[pur["product_id"] == product_id][["purchased_on", "store", "unit_cost"]].copy()
    h["source"] = "receipt"
    shelf = db.q("""SELECT sp.observed_on AS purchased_on, s.name AS store, sp.price, sp.per, sp.kind,
                    p.base_qty, p.sold_by_weight FROM shelf_prices sp JOIN stores s ON s.id = sp.store_id
                    JOIN products p ON p.id = sp.product_id WHERE sp.product_id = ?""", (product_id,))
    if not shelf.empty:
        shelf["purchased_on"] = pd.to_datetime(shelf["purchased_on"])
        shelf["unit_cost"] = [analytics.shelf_unit_cost(r) for _, r in shelf.iterrows()]
        shelf["source"] = shelf["kind"]
        h = pd.concat([h, shelf[["purchased_on", "store", "unit_cost", "source"]]], ignore_index=True)
    return h.dropna(subset=["unit_cost"]).sort_values("purchased_on")


def sale_cycle(obs: pd.DataFrame) -> tuple[float | None, pd.Timestamp | None]:
    """Median days between distinct low-price episodes, and the date of the last one."""
    if len(obs) < 6:
        return None, None
    low = obs[obs["unit_cost"] <= obs["unit_cost"].median() * 0.9]["purchased_on"].sort_values()
    if low.empty:
        return None, None
    starts, prev = [], None
    for d in low:
        if prev is None or (d - prev).days > 5:   # a new sale episode
            starts.append(d)
        prev = d
    if len(starts) < 3:
        return None, starts[-1]
    gaps = np.diff([s.value for s in starts]) / 86400e9
    base = float(gaps.min())
    if base >= 7 and all(abs(g / base - round(g / base)) <= 0.2 for g in gaps):
        return base, starts[-1]       # longer gaps are whole multiples: you just missed some sales
    return float(np.median(gaps)), starts[-1]


def advise(today: pd.Timestamp | None = None, pur: pd.DataFrame | None = None) -> list[Advice]:
    pur = db.purchases() if pur is None else pur
    if pur.empty:
        return []
    today = today or pur["purchased_on"].max()
    out = []
    for pid, g in pur.groupby("product_id"):
        if len(g) < 3:
            continue
        p = db.product(int(pid))
        obs = _observations(int(pid), pur)
        if len(obs) < 4:
            continue
        du = p["display_unit"]
        kind_rows = pur[pur["kind"] == p["kind"]]     # how fast you use this kind of food, from any store
        span = max((kind_rows["purchased_on"].max() - kind_rows["purchased_on"].min()).days, 28)
        weekly_base = kind_rows["base_qty_total"].sum() / span * 7
        good = obs["unit_cost"].quantile(0.2)
        usual = obs["unit_cost"].median()
        recent = obs[obs["purchased_on"] >= today - pd.Timedelta(days=7)]
        now_row = (recent if not recent.empty else obs).iloc[-1]
        now = float(now_row["unit_cost"])
        pct = float((obs["unit_cost"] < now).mean())
        cycle, last_low = sale_cycle(obs[obs["store"] == now_row["store"]])   # rhythms are per store
        next_sale = last_low + pd.Timedelta(days=cycle) if cycle and last_low is not None else None
        shelf_weeks = (p["shelf_life_days"] or 14) / 7
        max_weeks = FREEZER_WEEKS if p["freezable"] else min(shelf_weeks, 12)
        weeks_to_buy = max(1.0, min(max_weeks, cycle / 7 if cycle else 4.0))
        need_base = weekly_base * weeks_to_buy
        if p["sold_by_weight"]:
            suggest, qunit = round(need_base / units.base_factor("lb"), 1), "lb"
        else:
            suggest, qunit = max(1, int(round(need_base / float(p["base_qty"] or 1)))), "pkg"
        label = db.product_label(p)
        fmt = lambda c: units.fmt_unit_price(c, du)
        saving = max(0.0, (usual - now) * need_base)
        if now <= good * 1.001 and now < usual * 0.97:
            action = "stock up"
            msg = (f"{fmt(now)} is in your best 20% of prices (usual {fmt(usual)}). Buying ~{suggest:g} {qunit} "
                   f"covers about {weeks_to_buy:.0f} weeks"
                   + (", freeze what you won't use within a few days." if p["freezable"] else "."))
        elif next_sale is not None and 0 <= (next_sale - today).days <= 14 and now > good * 1.05:
            action = "wait"
            msg = (f"It usually drops every ~{cycle:.0f} days; the next dip is expected around "
                   f"{units.day(next_sale)}. Buy only what you need until then.")
            saving = 0.0
        else:
            action = "normal"
            msg = f"Normal price for you. A good price is {fmt(good)} or less."
            saving = 0.0
        out.append(Advice(int(pid), label, action, msg, fmt(now), fmt(good), fmt(usual),
                          weekly_base / units.base_factor(du), du, cycle, next_sale, max_weeks,
                          suggest, qunit, len(obs), pct, saving, now))
    order = {"stock up": 0, "wait": 1, "normal": 2}
    return sorted(out, key=lambda a: (order[a.action], -a.expected_saving))


def waste_check(product_id: int, qty: float, pur: pd.DataFrame | None = None) -> dict:
    """Will you use `qty` (packages, or lb for by-weight items) before it spoils?"""
    pur = db.purchases() if pur is None else pur
    p = db.product(product_id)
    g = pur[pur["product_id"] == product_id]
    if g.empty:
        return {"ok": None, "message": "Not enough history to know how fast you use this."}
    span = max((g["purchased_on"].max() - g["purchased_on"].min()).days, 28)
    daily = g["base_qty_total"].sum() / span
    need = qty * (units.base_factor("lb") if p["sold_by_weight"] else float(p["base_qty"] or 1))
    days_to_use = need / daily if daily else float("inf")
    shelf = p["shelf_life_days"] or 14
    freezer = bool(p["freezable"])
    ok = bool(days_to_use <= shelf or (freezer and days_to_use <= FREEZER_WEEKS * 7))
    msg = f"At your pace this lasts about {days_to_use:.0f} days; it keeps about {shelf} days"
    if days_to_use > shelf and freezer:
        msg += ", so freeze some of it" + ("" if ok else ", though even frozen this is a lot")
    elif days_to_use > shelf:
        waste = (days_to_use - shelf) / days_to_use
        msg += f". Roughly {waste:.0%} could go to waste"
    return {"ok": ok, "days_to_use": days_to_use, "shelf_days": shelf, "message": msg + "."}
