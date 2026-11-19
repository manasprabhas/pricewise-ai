"""The deliberate-failure walkthrough: an 18 oz cereal saved as the 15.4 oz product.

The mistake is planted the way it happens in real life: a CSV export from a store app maps its own product code
to the wrong size, so the line arrives already "matched" (method `import`, not confirmed by you). The receipt's
arithmetic is perfect, which is the point: reconciling proves amounts, not product identity.
"""
from __future__ import annotations

from . import db, identity, units

RAW = "SUNNY CRNCH HNY OAT 18OZ"
DATE = "2026-05-16"            # older than the planner's default 120-day window: it can't disturb today's plans
PRICE = 4.48


def products() -> tuple[dict | None, dict | None]:
    small = db.one("SELECT * FROM products WHERE brand = 'Sunny Crunch' AND size_value = 15.4 AND merged_into IS NULL")
    big = db.one("SELECT * FROM products WHERE brand = 'Sunny Crunch' AND size_value = 18 AND merged_into IS NULL")
    return small, big


def planted_item() -> dict | None:
    return db.one("""SELECT i.*, r.purchased_on, p.size_value, p.display_unit FROM items i
                     JOIN receipts r ON r.id = i.receipt_id JOIN products p ON p.id = i.product_id
                     WHERE i.raw_text = ? AND r.purchased_on = ? AND r.source = 'csv-import'""", (RAW, DATE))


def plant() -> int:
    small, _ = products()
    if small is None:
        raise RuntimeError("The demo cereal products aren't here. Open the demo first.")
    existing = planted_item()
    if existing:
        return int(existing["receipt_id"])
    store = db.get_or_create_store("Walmart")
    return db.save_receipt(store, DATE, [dict(product_id=int(small["id"]), qty=1, line_total=PRICE, raw_text=RAW,
                                              match_method="import", match_score=None, confirmed=False)],
                           subtotal=PRICE, tax=0.0, total=PRICE, source="csv-import",
                           notes="Planted for the deliberate-failure walkthrough", allow_duplicate=True,
                           source_ref="walmart-export.csv, row 14 (product code 078742-15)")


def state() -> dict:
    """Where the walkthrough stands, with the numbers each step shows."""
    small, big = products()
    it = planted_item()
    if not it:
        return {"step": 0, "small": small, "big": big}
    prod = db.product(int(it["product_id"]))
    unit_now = units.fmt_unit_price(it["unit_cost"], small["display_unit"] if small else prod["display_unit"])
    as_small = units.fmt_unit_price(PRICE / float(small["base_qty"]), small["display_unit"]) if small else "—"
    as_big = units.fmt_unit_price(PRICE / float(big["base_qty"]), small["display_unit"]) if big and small else "—"
    flag = identity.size_mismatch(it["raw_text"], prod)
    in_queue = not identity.review_queue().empty and int(it["id"]) in identity.review_queue()["item_id"].tolist()
    fixed = big is not None and int(it["product_id"]) == int(big["id"])
    return {"step": 2 if fixed else 1, "item": it, "small": small, "big": big, "flag": flag, "in_queue": in_queue,
            "unit_now": unit_now, "as_small": as_small, "as_big": as_big}


def remove() -> None:
    it = planted_item()
    if it:
        db.delete_receipt(int(it["receipt_id"]))
