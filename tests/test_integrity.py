"""Regression tests for the defects found in the implementation review.

Each test reproduces a reviewer's finding and must keep passing."""
import pandas as pd
import pytest

from pricewise import audit, db as _db, digital, matcher, savings

CSV_COLS = {"date": "date", "store": "store", "item": "item", "total": "total", "qty": None, "price": None,
            "unit": None}


def _cereals(db):
    big = db.add_product("Honey Oat Cereal", "cereal", "Pantry", 18, "oz", brand="Sunny Crunch")
    small = db.add_product("Honey Oat Cereal", "cereal", "Pantry", 15.4, "oz", brand="Sunny Crunch")
    return big, small


# ---- 1. product identity -------------------------------------------------------------------------------
def test_learned_18oz_alias_never_matches_15_4oz(fresh_db):
    db = fresh_db
    big, small = _cereals(db)
    store = db.get_or_create_store("Walmart")
    matcher.learn("SUNNY CRNCH HNY OAT 18OZ", store, big)
    assert matcher.alias_key("SUNNY CRNCH HNY OAT 18OZ") != matcher.alias_key("SUNNY CRNCH HNY OAT 15.4OZ")
    top = matcher.match("SUNNY CRNCH HNY OAT 15.4OZ", store)
    assert all(c.product_id != big or c.score < matcher.AUTO_ACCEPT for c in top)
    assert top[0].product_id == small
    # the learned 18 oz text still matches its own product
    assert matcher.match("SUNNY CRNCH HNY OAT 18OZ", store)[0].product_id == big


def test_alias_key_ignores_what_changes_each_trip():
    k = matcher.alias_key
    assert k("BANANAS 2.31 LB @ 0.58/LB") == k("BANANAS 1.07 LB @ 0.65/LB")
    assert k("2 @ KRO SPAGHETTI") == k("KRO SPAGHETTI")
    assert k("GV SPAG 160Z") == k("GV SPAG 16OZ")           # OCR slip
    assert k("GV OJ 52OZ 3.28") == k("GV OJ 52OZ")           # price on the line


# ---- 2. duplicate imports --------------------------------------------------------------------------------
def test_same_csv_imported_twice_is_saved_once(fresh_db):
    df = pd.DataFrame({"date": ["2026-11-01", "2026-11-01", "2026-11-03"],
                       "store": ["Walmart", "Walmart", "Aldi"],
                       "item": ["WHOLE MILK 1 GAL", "LARGE EGGS 12 CT", "BANANAS"],
                       "total": [3.48, 2.98, 1.34]})
    first = digital.import_csv(df, CSV_COLS)
    second = digital.import_csv(df, CSV_COLS)
    assert first["receipts"] == 2 and second["receipts"] == 0 and second["skipped_duplicates"] == 2
    assert len(fresh_db.receipts_df()) == 2
    assert fresh_db.purchases()["paid"].sum() == pytest.approx(3.48 + 2.98 + 1.34)


def test_same_purchase_from_a_different_source_is_caught(fresh_db):
    db = fresh_db
    milk = db.add_product("Whole Milk", "whole milk", "Dairy & eggs", 1, "gal")
    s = db.get_or_create_store("Walmart")
    line = [dict(product_id=milk, qty=1, line_total=3.48, raw_text="GV WHL MLK GAL")]
    db.save_receipt(s, "2026-11-01", line, subtotal=3.48, source="scan-offline")
    with pytest.raises(db.DuplicateReceipt):
        db.save_receipt(s, "2026-11-01", [dict(line[0], raw_text="Great Value Whole Milk")], subtotal=3.48,
                        source="digital")
    # a genuine second identical trip is allowed only when asked for explicitly
    db.save_receipt(s, "2026-11-01", line, subtotal=3.48, source="manual", allow_duplicate=True)
    assert len(db.receipts_df()) == 2


# ---- 3. refunds recorded exactly once ---------------------------------------------------------------------
def test_refund_recorded_exactly_once(fresh_db):
    db = fresh_db
    bananas = db.add_product("Bananas", "bananas", "Produce", None, "lb", sold_by_weight=True)
    rid = db.save_receipt(db.get_or_create_store("Kroger"), "2026-11-07",
                          [dict(product_id=bananas, qty=2.31, qty_unit="lb", unit_price=0.58, line_total=1.64)])
    issue = audit.Issue(f"weight_math:{rid}:1", "weight_math", rid, "Kroger", pd.Timestamp("2026-11-07"),
                        "Bananas", 0.30)
    for _ in range(3):
        audit.set_status(issue, "refunded")
    assert savings.scoreboard()["refund"] == pytest.approx(0.30)
    audit.set_status(issue, "dismissed")             # changed your mind: the refund is removed
    assert savings.scoreboard()["refund"] == 0
    audit.set_status(issue, "refunded")
    assert len(fresh_db.q("SELECT * FROM savings")) == 1


def test_documented_savings_not_doubled_by_reevaluating(demo):
    savings.evaluate()
    first = savings.scoreboard()["documented"]
    savings.evaluate()
    savings.evaluate()
    assert savings.scoreboard()["documented"] == pytest.approx(first)


# ---- 4. reconciliation status ------------------------------------------------------------------------------
def test_unreconciled_receipt_is_kept_out_of_advice(fresh_db):
    db = fresh_db
    milk = db.add_product("Whole Milk", "whole milk", "Dairy & eggs", 1, "gal")
    s = db.get_or_create_store("Walmart")
    good = db.save_receipt(s, "2026-11-01", [dict(product_id=milk, qty=1, line_total=3.48)], subtotal=3.48)
    bad = db.save_receipt(s, "2026-11-02", [dict(product_id=milk, qty=1, line_total=9.99)], subtotal=3.48)
    typed = db.save_receipt(s, "2026-11-03", [dict(product_id=milk, qty=1, line_total=3.52)])
    status = dict(zip(db.receipts_df()["id"], db.receipts_df()["status"]))
    assert (status[good], status[bad], status[typed]) == ("reconciled", "unverified", "manual")
    assert bad not in set(db.purchases()["receipt_id"])
    assert bad in set(db.purchases(include_unverified=True)["receipt_id"])


# ---- 5. delete everything --------------------------------------------------------------------------------
def test_delete_all_removes_import_history(fresh_db):
    digital.mark_imported("abc123", "receipt.eml", None)
    assert digital.already_imported("abc123")
    fresh_db.wipe_all()
    assert not digital.already_imported("abc123")
    for t in ["receipts", "items", "products", "stores", "savings", "aliases", "imports", "shelf_prices"]:
        assert fresh_db.one(f"SELECT COUNT(*) AS n FROM {t}")["n"] == 0, t
