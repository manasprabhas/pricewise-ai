"""Why did this trip / month cost more?  Price-volume-mix analysis for groceries.

For every kind of food (milk, eggs, chicken breast...) bought in both periods:
    volume effect = (quantity B - quantity A) x average unit cost A           -> "you bought more / less"
    rate effect   = (average unit cost B - average unit cost A) x quantity B  -> split into:
        price effect = sum over the same products bought both times of (cost B - cost A) x quantity B
        mix effect   = rate effect - price effect                             -> "pricier / cheaper choices"
Kinds bought only in B are "new items"; only in A are "stopped buying".
The pieces always add up exactly to (spend B - spend A).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import db, units

LABELS = {
    "price": "Prices changed",
    "volume": "Bought more or less",
    "mix": "Pricier or cheaper choices (brand, size or store)",
    "new": "New items",
    "dropped": "Items you stopped buying",
}


@dataclass
class Explanation:
    spend_a: float
    spend_b: float
    effects: dict[str, float]
    by_kind: pd.DataFrame

    @property
    def change(self) -> float:
        return self.spend_b - self.spend_a

    def check(self) -> float:
        """Difference between the effects and the actual change (should be ~0)."""
        return round(sum(self.effects.values()) - self.change, 6)

    def headline(self) -> str:
        if abs(self.change) < 0.01:
            return "You spent the same in both periods."
        verb = "more" if self.change > 0 else "less"
        top = max(self.effects.items(), key=lambda kv: abs(kv[1]))
        return (f"You spent {units.fmt_money(abs(self.change))} {verb}. The biggest reason: "
                f"{LABELS[top[0]].lower()} ({units.fmt_money(top[1], signed=True)}).")


def explain(a: pd.DataFrame, b: pd.DataFrame) -> Explanation:
    """a and b are purchase rows (from db.purchases()) for the two periods."""
    effects = {k: 0.0 for k in LABELS}
    rows = []
    kinds = sorted(set(a["kind"]) | set(b["kind"]))
    for kind in kinds:
        ka, kb = a[a["kind"] == kind], b[b["kind"] == kind]
        sa, sb = float(ka["paid"].sum()), float(kb["paid"].sum())
        if ka.empty:
            effects["new"] += sb
            rows.append({"kind": kind, "spend_a": 0.0, "spend_b": sb, "new": sb})
            continue
        if kb.empty:
            effects["dropped"] -= sa
            rows.append({"kind": kind, "spend_a": sa, "spend_b": 0.0, "dropped": -sa})
            continue
        qa, qb = float(ka["base_qty_total"].sum()), float(kb["base_qty_total"].sum())
        ua, ub = sa / qa, sb / qb
        volume = (qb - qa) * ua
        rate = (ub - ua) * qb
        price = 0.0
        pa = ka.groupby("product_id").agg(s=("paid", "sum"), q=("base_qty_total", "sum"))
        pb = kb.groupby("product_id").agg(s=("paid", "sum"), q=("base_qty_total", "sum"))
        for pid in pa.index.intersection(pb.index):
            ca, cb = pa.loc[pid, "s"] / pa.loc[pid, "q"], pb.loc[pid, "s"] / pb.loc[pid, "q"]
            price += (cb - ca) * pb.loc[pid, "q"]
        mix = rate - price
        effects["volume"] += volume
        effects["price"] += price
        effects["mix"] += mix
        du = kb["display_unit"].mode().iloc[0]
        rows.append({"kind": kind, "spend_a": sa, "spend_b": sb, "volume": volume, "price": price, "mix": mix,
                     "qty_a": qa / units.base_factor(du), "qty_b": qb / units.base_factor(du), "unit": du,
                     "unit_cost_a": units.per_unit(ua, du), "unit_cost_b": units.per_unit(ub, du)})
    cols = ["kind", "spend_a", "spend_b", "volume", "price", "mix", "new", "dropped", "qty_a", "qty_b", "unit",
            "unit_cost_a", "unit_cost_b"]
    by_kind = pd.DataFrame(rows).reindex(columns=cols) if rows else pd.DataFrame(columns=cols)
    num = [c for c in cols if c not in ("kind", "unit")]
    by_kind[num] = by_kind[num].astype(float).fillna(0.0)
    by_kind["unit"] = by_kind["unit"].fillna("")
    if not by_kind.empty:
        by_kind["change"] = by_kind["spend_b"] - by_kind["spend_a"]
        by_kind = by_kind.reindex(by_kind["change"].abs().sort_values(ascending=False).index)
    return Explanation(float(a["paid"].sum()), float(b["paid"].sum()), effects, by_kind)


def kind_sentence(r: pd.Series) -> str:
    parts = []
    if r.get("new"):
        return f"{r['kind'].title()}: new this period ({units.fmt_money(r['new'])})."
    if r.get("dropped"):
        return f"{r['kind'].title()}: not bought this period ({units.fmt_money(r['dropped'], signed=True)})."
    u = "ct" if r["unit"] == "each" else r["unit"]
    if abs(r["volume"]) >= 0.5:
        parts.append(f"bought {r['qty_a']:.3g} → {r['qty_b']:.3g} {u} ({units.fmt_money(r['volume'], signed=True)})")
    if abs(r["price"]) >= 0.5:
        parts.append(f"same products cost {units.fmt_money(r['price'], signed=True)} more"
                     if r["price"] > 0 else f"same products cost {units.fmt_money(-r['price'])} less")
    if abs(r["mix"]) >= 0.5:
        parts.append(("pricier picks " if r["mix"] > 0 else "cheaper picks ") + units.fmt_money(r["mix"], signed=True))
    if not parts:
        return f"{r['kind'].title()}: about the same."
    return f"{r['kind'].title()}: " + "; ".join(parts) + "."


def periods(pur: pd.DataFrame, mode: str, a: str | int | None = None, b: str | int | None = None):
    """Helper to slice two periods: mode 'month' (YYYY-MM) or 'receipt' (receipt ids)."""
    if mode == "month":
        return pur[pur["month"] == a], pur[pur["month"] == b]
    return pur[pur["receipt_id"] == a], pur[pur["receipt_id"] == b]
