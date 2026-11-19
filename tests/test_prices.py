"""Unit prices, discounts and weights must be exactly right (target: 100%)."""
import pytest

from pricewise import units


@pytest.mark.parametrize("text,expected", [
    ("18 oz", (18.0, "oz")), ("1 gal", (1.0, "gal")), ("12 ct", (12.0, "each")), ("64 FL OZ", (64.0, "fl oz")),
    ("6 x 12 fl oz", (72.0, "fl oz")), ("2.5 lbs", (2.5, "lb")), ("500 g", (500.0, "g")), ("1 dozen", (1.0, "dozen")),
    ("GV LG EGGS 12CT", (12.0, "each")), ("no size here", None),
])
def test_parse_size(text, expected):
    assert units.parse_size(text) == expected


def test_unit_price_table_from_blueprint():
    # The blueprint's example: the 3 lb pack is cheapest per pound even though it costs most.
    packs = {"2 lb": (8.00, 2), "3 lb": (10.50, 3), "1.5 lb": (6.75, 1.5)}
    per_lb = {k: units.unit_price(price, units.to_base(lb, "lb")[1], "lb") for k, (price, lb) in packs.items()}
    assert per_lb["2 lb"] == pytest.approx(4.00)
    assert per_lb["3 lb"] == pytest.approx(3.50)
    assert per_lb["1.5 lb"] == pytest.approx(4.50)
    assert min(per_lb, key=per_lb.get) == "3 lb"


def test_conversions_between_units():
    assert units.to_base(1, "lb")[1] == pytest.approx(16 * units.to_base(1, "oz")[1])
    assert units.to_base(1, "gal")[1] == pytest.approx(128 * units.to_base(1, "fl oz")[1])
    assert units.to_base(1, "dozen") == ("count", 12.0)
    # $3.48 per gallon is 2.72 cents per fl oz
    per_floz = units.unit_price(3.48, units.to_base(1, "gal")[1], "fl oz")
    assert per_floz == pytest.approx(3.48 / 128)


def test_formatting():
    assert units.fmt_money(-1.5) == "−$1.50"
    assert units.fmt_money(2, signed=True) == "+$2.00"
    assert units.fmt_unit_price(3.48 / units.to_base(1, "gal")[1], "gal") == "$3.48/gal"
    assert units.fmt_unit_price(0.25, "each") == "$0.25/ea"


def test_receipt_line_maths(fresh_db):
    db = fresh_db
    milk = db.add_product("Whole Milk", "whole milk", "Dairy & eggs", 1, "gal", brand="GV")
    chicken = db.add_product("Chicken Breast", "chicken breast", "Meat & fish", None, "lb", sold_by_weight=True)
    s = db.get_or_create_store("Walmart")
    rid = db.save_receipt(s, "2026-11-07", [
        dict(product_id=milk, qty=2, qty_unit="each", unit_price=3.48, line_total=6.96, discount=0.50),
        dict(product_id=chicken, qty=1.80, qty_unit="lb", unit_price=3.99, line_total=7.18),
    ], tax=0.0)
    pur = db.purchases()
    m = pur[pur["product_id"] == milk].iloc[0]
    assert m["paid"] == pytest.approx(6.46)                       # discount kept separate, then applied
    assert units.per_unit(m["unit_cost"], "gal") == pytest.approx(3.23)
    c = pur[pur["product_id"] == chicken].iloc[0]
    assert units.per_unit(c["unit_cost"], "lb") == pytest.approx(7.18 / 1.80)
    r = db.receipts_df().iloc[0]
    assert r["id"] == rid and r["total"] == pytest.approx(13.64)


def test_tax_kept_out_of_unit_prices(fresh_db):
    db = fresh_db
    p = db.add_product("Coffee", "coffee", "Drinks", 30.5, "oz")
    s = db.get_or_create_store("Aldi")
    db.save_receipt(s, "2026-11-01", [dict(product_id=p, qty=1, line_total=7.49)], tax=0.52)
    row = db.purchases().iloc[0]
    assert row["paid"] == pytest.approx(7.49)
    assert db.receipts_df().iloc[0]["total"] == pytest.approx(8.01)
