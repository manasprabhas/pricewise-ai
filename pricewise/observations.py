"""Every price PriceWise has ever seen, as one list of dated observations, and nothing from the future.

An observation is one price for one product at one store on one day, from:
  receipt  a line you paid (trusted receipts only: reconciled or typed in, never unverified)
  shelf    a shelf tag you recorded (laptop or Pocket)
  ad       a weekly-ad or promotion price you recorded
  kroger   an optional Kroger price check

`load(cutoff)` returns only observations dated strictly BEFORE the cutoff day. A decision made on the morning of
a trip can't know that trip's receipt, so the receipt dated on the cutoff day is excluded too. This is the single
gate that prevents look-ahead bias: every planner and every replay gets its prices through it.

Prices are integer cents per *basis*: per package for packaged goods, per lb for weighed goods. The receipt
basis is the net amount after item discounts, before any store-wide coupon (which is kept separately).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd

from . import db, money, units

LB = units.base_factor("lb")
COLUMNS = ["obs_id", "source", "date", "store", "store_id", "product_id", "basis", "price_cents", "promo",
           "receipt_id", "item_id", "raw_text", "qty", "line_cents", "discount_cents", "note", "terms"]


def as_day(x) -> date:
    if x is None:
        return date.today() + timedelta(days=1)
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    return date.fromisoformat(str(x)[:10])


def _per_basis_cents(amount_cents: int, qty: float) -> int:
    if not qty:
        return amount_cents
    return int((Decimal(amount_cents) / Decimal(repr(float(qty)))).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def load(cutoff=None, product_ids: set[int] | None = None, exclude: set[str] | None = None) -> pd.DataFrame:
    """Observations dated before `cutoff` (a date; None = everything up to and including today)."""
    cut = as_day(cutoff).isoformat()
    rows = []
    pur = db.q("""
        SELECT i.id AS item_id, i.receipt_id, r.purchased_on AS d, s.name AS store, s.id AS store_id, i.product_id,
               p.sold_by_weight, p.base_qty, i.qty, i.qty_unit, i.raw_text, i.line_cents, i.discount_cents, i.paid_cents
        FROM items i JOIN receipts r ON r.id = i.receipt_id JOIN stores s ON s.id = r.store_id
        JOIN products p ON p.id = i.product_id
        WHERE r.purchased_on < ? AND COALESCE(r.status, 'manual') != 'unverified' AND i.qty > 0 AND i.paid_cents > 0""",
                 (cut,))
    for r in pur.itertuples():
        if r.sold_by_weight:
            lbs = float(r.qty) * (units.base_factor(r.qty_unit) / LB if units.normalize_unit(r.qty_unit) in
                                  ("lb", "oz", "kg", "g") else 1)
            basis, price = "lb", _per_basis_cents(int(r.paid_cents), lbs)
        else:
            basis, price = "package", _per_basis_cents(int(r.paid_cents), float(r.qty))
        rows.append({"obs_id": f"r{r.item_id}", "source": "receipt", "date": r.d, "store": r.store,
                     "store_id": r.store_id, "product_id": int(r.product_id), "basis": basis, "price_cents": price,
                     "promo": bool(r.discount_cents), "receipt_id": int(r.receipt_id), "item_id": int(r.item_id),
                     "raw_text": r.raw_text, "qty": float(r.qty), "line_cents": int(r.line_cents),
                     "discount_cents": int(r.discount_cents), "note": None, "terms": None})
    shelf = db.q("""
        SELECT sp.id, sp.observed_on AS d, s.name AS store, s.id AS store_id, sp.product_id, sp.price, sp.per,
               sp.kind, sp.note, sp.terms, p.sold_by_weight, p.base_qty
        FROM shelf_prices sp JOIN stores s ON s.id = sp.store_id JOIN products p ON p.id = sp.product_id
        WHERE sp.observed_on < ?""", (cut,))
    for r in shelf.itertuples():
        price = money.cents(r.price)
        if r.sold_by_weight:
            basis = "lb"
            if r.per == "package":
                continue                      # a package price for a by-the-lb product can't be placed
        else:
            basis = "package"
            if r.per in ("lb", "kg", "oz") and r.base_qty:
                price = money.cents(money.to_decimal(r.price) / Decimal(repr(units.base_factor(r.per)))
                                    * Decimal(repr(float(r.base_qty))))
        note = r.note or ""
        rows.append({"obs_id": f"s{r.id}", "source": r.kind or "shelf", "date": r.d, "store": r.store,
                     "store_id": r.store_id, "product_id": int(r.product_id), "basis": basis, "price_cents": price,
                     "promo": r.kind == "ad" or "promo" in note.lower(), "receipt_id": None, "item_id": None,
                     "raw_text": None, "qty": None, "line_cents": None, "discount_cents": None, "note": r.note,
                     "terms": r.terms})
    df = pd.DataFrame(rows, columns=COLUMNS)
    if product_ids is not None:
        df = df[df["product_id"].isin(product_ids)]
    if exclude:
        df = df[~df["obs_id"].isin(exclude)]
    return df.sort_values(["date", "obs_id"]).reset_index(drop=True)


def hidden_after(cutoff) -> int:
    """How many observations exist on or after the cutoff (what a replay hides)."""
    cut = as_day(cutoff).isoformat()
    a = db.one("SELECT COUNT(*) AS n FROM items i JOIN receipts r ON r.id = i.receipt_id WHERE r.purchased_on >= ?",
               (cut,))["n"]
    b = db.one("SELECT COUNT(*) AS n FROM shelf_prices WHERE observed_on >= ?", (cut,))["n"]
    return int(a + b)


def store_tax_rates(cutoff=None) -> dict[str, float | None]:
    """Each store's typical tax as a share of the subtotal, from receipts before the cutoff (None = never seen).
    Unverified receipts are left out, as they are from every price: a misread tax line mustn't move the estimate."""
    df = db.q("""SELECT s.name AS store, r.tax_cents, r.subtotal_cents FROM receipts r JOIN stores s ON s.id = r.store_id
                 WHERE r.purchased_on < ? AND r.subtotal_cents > 0
                   AND COALESCE(r.status, 'manual') != 'unverified'""", (as_day(cutoff).isoformat(),))
    out: dict[str, float | None] = {s: None for s in db.stores()["name"]}
    for store, g in df.groupby("store"):
        out[store] = float((g["tax_cents"].fillna(0) / g["subtotal_cents"]).median())
    return out
