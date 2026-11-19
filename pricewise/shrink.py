"""Shrinkflation detector: same brand and product, smaller package, same or higher price."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import db, units


@dataclass
class ShrinkEvent:
    brand: str
    name: str
    old_size: str
    new_size: str
    size_change: float          # e.g. -0.144
    old_price: float            # typical package price before
    new_price: float            # typical package price after
    unit_change: float          # change in price per unit
    first_seen: pd.Timestamp
    display_unit: str
    old_unit: str
    new_unit: str
    yearly_extra: float         # extra you pay per year at your buying rate
    old_count: int = 0          # purchases seen at the old size
    new_count: int = 0
    old_first: pd.Timestamp | None = None
    old_last: pd.Timestamp | None = None
    new_first: pd.Timestamp | None = None
    new_last: pd.Timestamp | None = None
    old_priced_on: int = 0      # how many purchases the typical prices are based on
    new_priced_on: int = 0
    coexist: bool = False       # both sizes were still bought in the same period
    packs_per_year: float = 0.0
    new_base_qty: float = 0.0
    unit_cost_old: float = 0.0  # per base unit
    unit_cost_new: float = 0.0
    stores: str = ""

    def extra_for(self, packs_per_year: float) -> float:
        """Extra a year for the same amount of food, if you buy this many of the new packages."""
        return (self.unit_cost_new - self.unit_cost_old) * self.new_base_qty * packs_per_year

    @property
    def verdict(self) -> str:
        if self.new_price <= self.old_price * 1.01:
            return "Smaller package, same price"
        if self.new_price < self.old_price:
            return "Smaller package, slightly lower price, still costs more per unit"
        return "Smaller package and a higher price"


def detect(pur: pd.DataFrame | None = None) -> list[ShrinkEvent]:
    pur = db.purchases() if pur is None else pur
    if pur.empty:
        return []
    pk = pur[pur["sold_by_weight"] == 0].copy()
    pk["family"] = (pk["brand"].fillna("").str.lower().str.strip() + "|" + pk["name"].str.lower().str.strip())
    events = []
    for fam, g in pk.groupby("family"):
        sizes = g.groupby("base_qty")["purchased_on"].min().sort_values()
        if len(sizes) < 2:
            continue
        for old_q, new_q in zip(sizes.index, sizes.index[1:]):
            if new_q >= old_q:
                continue
            old = g[g["base_qty"] == old_q].sort_values("purchased_on")
            new = g[g["base_qty"] == new_q].sort_values("purchased_on")
            old_used, new_used = old.tail(3), new.head(3)
            op = (old_used["paid"] / old_used["qty"]).median()
            np_ = (new_used["paid"] / new_used["qty"]).median()
            ou, nu = op / old_q, np_ / new_q
            if nu <= ou * 1.03:
                continue
            row = new.iloc[0]
            du = row["display_unit"]
            span_days = max((g["purchased_on"].max() - g["purchased_on"].min()).days, 30)
            yearly_qty = g["base_qty_total"].sum() / span_days * 365
            packs = g["qty"].sum() / span_days * 365
            events.append(ShrinkEvent(
                brand=row["brand"], name=row["name"],
                old_size=units.fmt_size(old.iloc[0]["size_value"], old.iloc[0]["size_unit"]),
                new_size=units.fmt_size(row["size_value"], row["size_unit"]),
                size_change=new_q / old_q - 1, old_price=op, new_price=np_, unit_change=nu / ou - 1,
                first_seen=new["purchased_on"].min(), display_unit=du,
                old_unit=units.fmt_unit_price(ou, du), new_unit=units.fmt_unit_price(nu, du),
                yearly_extra=(nu - ou) * yearly_qty,
                old_count=len(old), new_count=len(new),
                old_first=old["purchased_on"].min(), old_last=old["purchased_on"].max(),
                new_first=new["purchased_on"].min(), new_last=new["purchased_on"].max(),
                old_priced_on=len(old_used), new_priced_on=len(new_used),
                coexist=bool(old["purchased_on"].max() > new["purchased_on"].min()),
                packs_per_year=float(packs), new_base_qty=float(new_q), unit_cost_old=float(ou),
                unit_cost_new=float(nu), stores=", ".join(sorted(g["store"].unique()))))
    return sorted(events, key=lambda e: e.first_seen, reverse=True)
