"""Substitution finder: cheaper versions of things you already buy, from products you have bought."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import db, units


@dataclass
class Swap:
    from_id: int
    to_id: int
    from_label: str
    to_label: str
    kind: str
    from_unit: str
    to_unit: str
    saving_pct: float
    yearly_saving: float
    where: str
    protein_note: str


def find(days: int = 180, min_saving: float = 0.05, pur: pd.DataFrame | None = None) -> list[Swap]:
    pur = db.purchases() if pur is None else pur
    if pur.empty:
        return []
    recent = pur[pur["purchased_on"] >= pur["purchased_on"].max() - pd.Timedelta(days=days)]
    span = max((recent["purchased_on"].max() - recent["purchased_on"].min()).days, 30)
    prods = db.products().set_index("id")
    stats = recent.groupby("product_id").agg(cost=("unit_cost", "median"), qty=("base_qty_total", "sum"),
                                             n=("item_id", "count"), kind=("kind", "first"),
                                             store=("store", lambda s: s.mode().iloc[0]))
    out = []
    for pid, s in stats.iterrows():
        if s["n"] < 2:
            continue
        alts = stats[(stats["kind"] == s["kind"]) & (stats.index != pid)]
        alts = alts[alts["cost"] <= s["cost"] * (1 - min_saving)]
        if alts.empty:
            continue
        best_id = alts["cost"].idxmin()
        best = alts.loc[best_id]
        yearly_qty = s["qty"] / span * 365
        pf, pt = prods.loc[pid], prods.loc[best_id]
        du = pf["display_unit"]
        note = ""
        if pd.notna(pf["protein_g"]) and pd.notna(pt["protein_g"]) and max(pf["protein_g"], pt["protein_g"]) >= 1:
            d = pt["protein_g"] - pf["protein_g"]
            note = "Same protein per 100 g" if abs(d) < 0.5 else (
                f"{abs(d):.1f} g {'more' if d > 0 else 'less'} protein per 100 g")
        out.append(Swap(int(pid), int(best_id), db.product_label(pf), db.product_label(pt), s["kind"],
                        units.fmt_unit_price(s["cost"], du), units.fmt_unit_price(best["cost"], du),
                        1 - best["cost"] / s["cost"], (s["cost"] - best["cost"]) * yearly_qty, best["store"], note))
    # one suggestion per kind: the most valuable
    best_per_kind: dict[str, Swap] = {}
    for sw in sorted(out, key=lambda x: x.yearly_saving, reverse=True):
        best_per_kind.setdefault(sw.kind, sw)
    return list(best_per_kind.values())
