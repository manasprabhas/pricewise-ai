"""Price memory, alerts, store comparison, spending and personal inflation."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import requests

from . import db, units

CPI_SERIES = "CUUR0000SAF11"  # BLS CPI-U, food at home, US city average


def display_cost(unit_cost: float, display_unit: str) -> float:
    return units.per_unit(unit_cost, display_unit)


# ---------------- per-product memory ----------------
def product_stats(pur: pd.DataFrame | None = None) -> pd.DataFrame:
    pur = db.purchases() if pur is None else pur
    if pur.empty:
        return pd.DataFrame()
    pur = pur.sort_values("purchased_on")
    g = pur.groupby("product_id")
    stats = pd.DataFrame({
        "times_bought": g.size(),
        "last_date": g["purchased_on"].max(),
        "first_date": g["purchased_on"].min(),
        "last_unit_cost": g["unit_cost"].last(),
        "median_unit_cost": g["unit_cost"].median(),
        "min_unit_cost": g["unit_cost"].min(),
        "max_unit_cost": g["unit_cost"].max(),
        "last_store": g["store"].last(),
        "total_spent": g["paid"].sum(),
    })
    prods = db.products().set_index("id")
    stats = stats.join(prods[["name", "brand", "kind", "category", "display_unit", "sold_by_weight", "size_value",
                              "size_unit"]])
    stats["label"] = [db.product_label(r) for _, r in stats.reset_index().iterrows()]
    stats["change_vs_median"] = stats["last_unit_cost"] / stats["median_unit_cost"] - 1
    return stats.reset_index().rename(columns={"index": "product_id"})


def product_history(product_id: int) -> pd.DataFrame:
    pur = db.purchases()
    h = pur[pur["product_id"] == product_id].copy()
    h["source"] = "Receipt"
    shelf = db.q("""SELECT sp.observed_on AS purchased_on, s.name AS store, sp.price, sp.per, sp.kind, sp.note,
                    p.base_qty, p.sold_by_weight FROM shelf_prices sp JOIN stores s ON s.id = sp.store_id
                    JOIN products p ON p.id = sp.product_id WHERE sp.product_id = ?""", (product_id,))
    if not shelf.empty:
        shelf["purchased_on"] = pd.to_datetime(shelf["purchased_on"])
        shelf["unit_cost"] = [shelf_unit_cost(r) for _, r in shelf.iterrows()]
        shelf["source"] = shelf["kind"].map({"kroger": "Kroger price check", "ad": "Weekly ad",
                                             "shelf": "Shelf tag"}).fillna("Price check")
        h = pd.concat([h, shelf[["purchased_on", "store", "unit_cost", "source", "note"]]], ignore_index=True)
    return h.sort_values("purchased_on")


def shelf_unit_cost(r) -> float | None:
    if r["per"] in ("lb", "kg", "oz"):
        return r["price"] / units.base_factor(r["per"])
    if r["base_qty"]:
        return r["price"] / r["base_qty"]
    return None


# ---------------- alerts ----------------
@dataclass
class Alert:
    item_id: int
    product_id: int
    label: str
    store: str
    date: pd.Timestamp
    direction: str        # "high" | "low"
    pct: float
    paid_unit: str
    usual_unit: str
    n_history: int
    reason: str


def alerts_for(pur: pd.DataFrame, receipt_ids: list[int] | None = None, threshold: float | None = None,
               min_history: int | None = None) -> list[Alert]:
    threshold = (db.get_float("alert_threshold_pct", 15) / 100) if threshold is None else threshold
    min_history = int(db.get_float("min_history", 3)) if min_history is None else min_history
    if pur.empty:
        return []
    target = pur if receipt_ids is None else pur[pur["receipt_id"].isin(receipt_ids)]
    out: list[Alert] = []
    for _, row in target.iterrows():
        prior = pur[(pur["product_id"] == row["product_id"]) & (pur["purchased_on"] < row["purchased_on"])]
        if len(prior) < min_history or not row["unit_cost"]:
            continue
        usual = prior["unit_cost"].median()
        pct = row["unit_cost"] / usual - 1
        if abs(pct) < threshold:
            continue
        du = row["display_unit"]
        same_store = prior[prior["store"] == row["store"]]
        why = []
        if pct > 0:
            if len(same_store) and row["unit_cost"] > same_store["unit_cost"].median() * (1 + threshold):
                why.append(f"higher than you usually pay at {row['store']} too")
            elif len(same_store):
                why.append(f"normal for {row['store']}; your usual price comes from cheaper stores")
            if prior["unit_cost"].tail(3).median() < usual * 0.95:
                why.append("you recently caught it on sale")
        else:
            why.append("possibly a sale or a cheaper store")
        out.append(Alert(int(row["item_id"]), int(row["product_id"]), db.product_label(row), row["store"],
                         row["purchased_on"], "high" if pct > 0 else "low", pct,
                         units.fmt_unit_price(row["unit_cost"], du), units.fmt_unit_price(usual, du),
                         len(prior), "; ".join(why)))
    return out


# ---------------- store comparison ----------------
def store_matrix(days: int = 120, pur: pd.DataFrame | None = None) -> pd.DataFrame:
    """Median unit cost per kind and store, in the kind's most common display unit."""
    pur = db.purchases() if pur is None else pur
    if pur.empty:
        return pd.DataFrame()
    recent = pur[pur["purchased_on"] >= pur["purchased_on"].max() - pd.Timedelta(days=days)]
    du = recent.groupby("kind")["display_unit"].agg(lambda s: s.mode().iloc[0])
    m = recent.groupby(["kind", "store"])["unit_cost"].median().unstack("store")
    for kind in m.index:
        m.loc[kind] = m.loc[kind] * units.base_factor(du[kind])
    m["unit"] = du
    return m


def cheapest_by_kind(days: int = 120) -> pd.DataFrame:
    m = store_matrix(days)
    if m.empty:
        return m
    stores = [c for c in m.columns if c != "unit"]
    rows = []
    for kind, r in m.iterrows():
        vals = r[stores].dropna().astype(float)
        if vals.empty:
            continue
        best, worst = vals.idxmin(), vals.idxmax()
        rows.append({"kind": kind, "unit": r["unit"], "cheapest": best, "cheapest_price": vals[best],
                     "priciest": worst, "priciest_price": vals[worst],
                     "gap_pct": vals[worst] / vals[best] - 1 if len(vals) > 1 else 0.0,
                     "stores_seen": len(vals)})
    return pd.DataFrame(rows).sort_values("gap_pct", ascending=False)


def store_scorecard(days: int = 120) -> pd.DataFrame:
    """How often each store was cheapest, and its average premium over the cheapest."""
    m = store_matrix(days)
    if m.empty:
        return m
    stores = [c for c in m.columns if c != "unit"]
    vals = m[stores].astype(float)
    best = vals.min(axis=1)
    premium = vals.div(best, axis=0) - 1
    wins = vals.eq(best, axis=0).sum()
    return pd.DataFrame({"times_cheapest": wins, "avg_premium": premium.mean(), "kinds_compared": vals.notna().sum()}
                        ).sort_values("avg_premium")


# ---------------- spending ----------------
def spend_by(pur: pd.DataFrame, col: str) -> pd.DataFrame:
    if pur.empty:
        return pd.DataFrame()
    return pur.groupby(["month", col])["paid"].sum().reset_index()


def monthly_spend(pur: pd.DataFrame) -> pd.Series:
    return pur.groupby("month")["paid"].sum() if not pur.empty else pd.Series(dtype=float)


# ---------------- personal inflation ----------------
def personal_inflation(pur: pd.DataFrame | None = None) -> pd.DataFrame:
    """Chained month-to-month index from products you bought in both months, weighted by your spending."""
    pur = db.purchases() if pur is None else pur
    if pur.empty:
        return pd.DataFrame()
    months = sorted(pur["month"].unique())
    # same product at the same store, so switching stores doesn't look like inflation
    agg = pur.groupby(["month", "product_id", "store"]).agg(cost=("unit_cost", "median"), spend=("paid", "sum"))
    index, rows = 100.0, [{"month": months[0], "index": 100.0, "change": None, "products_compared": None}]
    for prev, cur in zip(months, months[1:]):
        a, b = agg.loc[prev], agg.loc[cur]
        common = a.index.intersection(b.index)
        if len(common) < 5:
            rows.append({"month": cur, "index": index, "change": None, "products_compared": len(common)})
            continue
        w = a.loc[common, "spend"] / a.loc[common, "spend"].sum()
        rel = b.loc[common, "cost"] / a.loc[common, "cost"]
        change = float((w * rel).sum() - 1)
        index *= 1 + change
        rows.append({"month": cur, "index": index, "change": change, "products_compared": len(common)})
    return pd.DataFrame(rows)


def official_cpi(start_year: int, end_year: int, refresh: bool = False) -> pd.DataFrame:
    """Food-at-home CPI from the US Bureau of Labor Statistics (free, no key). Cached in the database."""
    cached = db.q("SELECT * FROM cpi_cache WHERE series_id = ? AND year BETWEEN ? AND ?",
                  (CPI_SERIES, start_year, end_year))
    if cached.empty or refresh:
        r = requests.post("https://api.bls.gov/publicAPI/v1/timeseries/data/",
                          json={"seriesid": [CPI_SERIES], "startyear": str(start_year), "endyear": str(end_year)},
                          timeout=15)
        r.raise_for_status()
        payload = r.json()
        if payload.get("status") != "REQUEST_SUCCEEDED":
            raise RuntimeError("; ".join(payload.get("message", [])) or "BLS request failed")
        for s in payload["Results"]["series"]:
            for d in s["data"]:
                if not d["period"].startswith("M") or d["period"] == "M13" or d["value"] in ("-", ""):
                    continue
                db.run("INSERT OR REPLACE INTO cpi_cache(series_id, year, period, value) VALUES (?,?,?,?)",
                       (CPI_SERIES, int(d["year"]), d["period"], float(d["value"])))
        cached = db.q("SELECT * FROM cpi_cache WHERE series_id = ? AND year BETWEEN ? AND ?",
                      (CPI_SERIES, start_year, end_year))
    if cached.empty:
        return cached
    cached["month"] = cached["year"].astype(str) + "-" + cached["period"].str[1:]
    cached = cached.sort_values("month")
    cached["change"] = cached["value"].pct_change()
    return cached[["month", "value", "change"]]
