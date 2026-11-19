"""Milestone 2: trust and correctness.

Money in integer cents, receipts that reconcile line by line, product identity that can be fixed and undone, the
three duplicate cases, shrinkflation detail and checkout-problem review."""
from decimal import Decimal

import numpy as np
import pytest

from pricewise import audit, db as _db, digital, failure_demo, identity, money, reconcile, scanner, shrink


# ---------------------------------------------------------------- money ------------------------------------------
def test_money_is_exact_in_cents():
    assert money.cents("3.48") == 348
    assert money.cents(0.1 + 0.2) == 30              # float noise never leaks into cents
    assert money.cents(np.float64(3.48)) == 348      # pandas/numpy values convert too
    assert money.cents(np.int64(2)) == 200
    assert money.cents(Decimal("2.345")) == 235      # HALF_UP, stated policy
    assert money.cents("$1,234.50") == 123450
    with pytest.raises(ValueError):
        money.cents(float("nan"))


def test_weighted_items_round_once_half_up():
    assert money.line_cents(2.31, 0.58) == 134       # 1.3398 -> 1.34
    assert money.line_cents(1.005, 1.00) == 101      # 1.005 -> 1.01 (HALF_UP, not banker's)
    assert money.line_cents(0.80, 4.67) == 374       # 3.736 -> 3.74


def test_multipack_and_whole_packages():
    assert money.packages_needed(32, 16) == 2
    assert money.packages_needed(33, 16) == 3
    assert money.packages_needed(16.0000000001, 16) == 1   # float noise doesn't buy an extra package


def _rec(lines, adj, sub, tax, tot):
    return reconcile.run([(money.cents(l), money.cents(d)) for l, d in lines], adj,
                         None if sub is None else money.cents(sub), money.cents(tax),
                         None if tot is None else money.cents(tot))


def test_deposits_returns_store_coupons_and_item_offers_reconcile():
    A = reconcile.Adjustment
    lines = [(4.99, 0.50),            # item offer: belongs to the item
             (6.00, 0.00), (1.29, 0.00)]
    adj = [A("deposit", 60, "CRV 12 x 0.05"),                    # before subtotal
           A("return", 129, "RETURN PEAK PB"),                    # a returned item, not a negative price
           A("store_coupon", 500, "$5 OFF $50", after_subtotal=True),   # store-wide: attributable to nothing
           A("fee", 10, "BAG FEE", after_subtotal=True)]
    sub = 4.99 - 0.50 + 6.00 + 1.29 + 0.60 - 1.29
    r = _rec(lines, adj, round(sub, 2), 0.42, round(sub + 0.42 - 5.00 + 0.10, 2))
    assert r.status == "reconciled", [c.explain for c in r.checks]
    assert r.item_discounts_cents == 50
    assert r.adjustments_before_cents == 60 - 129
    assert r.adjustments_after_cents == -500 + 10


def test_unattributable_coupon_is_kept_separately_not_spread_on_items(fresh_db):
    db = fresh_db
    p = db.add_product("Milk", "milk", "Dairy", 1, "gal")
    s = db.get_or_create_store("Kroger")
    A = reconcile.Adjustment
    rid = db.save_receipt(s, "2026-10-01", [dict(product_id=p, qty=1, line_total=4.00)], subtotal=4.00, tax=0,
                          total=3.00, adjustments=[A("store_coupon", 100, "$1 OFF ORDER", after_subtotal=True)])
    item = db.one("SELECT * FROM items WHERE receipt_id = ?", (rid,))
    assert item["paid_cents"] == 400                     # the item's price is untouched
    adj = db.one("SELECT * FROM receipt_adjustments WHERE receipt_id = ?", (rid,))
    assert adj["kind"] == "store_coupon" and adj["cents"] == -100
    assert db.one("SELECT status FROM receipts WHERE id = ?", (rid,))["status"] == "reconciled"


def test_a_25_line_receipt_reconciles_and_a_one_digit_misread_is_pinpointed():
    lines = [(round(0.79 + i * 0.37, 2), 0.25 if i % 6 == 0 else 0.0) for i in range(25)]
    lines[11] = (2.49, 0.0)
    sub = round(sum(l - d for l, d in lines), 2)
    good = _rec(lines, [], sub, 1.17, round(sub + 1.17, 2))
    assert good.status == "reconciled"
    # the reader takes 2.49 for 2.79: Check A fails by exactly 30 cents and the culprit line is named
    bad_lines = list(lines)
    bad_lines[11] = (2.79, 0.0)
    bad = _rec(bad_lines, [], sub, 1.17, round(sub + 1.17, 2))
    assert bad.status == "unverified"
    c = bad.failed[0]
    assert c.actual_cents - c.expected_cents == 30
    culprits = reconcile.likely_misreads([(money.cents(l), money.cents(d)) for l, d in bad_lines], 30)
    assert 11 in culprits


def test_parser_keeps_deposits_coupons_and_returns_apart(fresh_db):
    text = """KROGER
11/02/2026
KRO WHOLE MILK 1 GAL      3.49
BOTTLE DEPOSIT            0.60
PEAK CRMY PB 16OZ         2.99
RETURN PEAK CRMY PB      -2.99
SUBTOTAL                  4.09
TAX                       0.00
$5 OFF STORE COUPON      -0.50
TOTAL                     3.59
"""
    rec = digital.parse(text)
    kinds = sorted(a.kind for a in rec.adjustments)
    assert kinds == ["deposit", "return", "store_coupon"], kinds
    assert [i.raw_text for i in rec.items] == ["KRO WHOLE MILK 1 GAL", "PEAK CRMY PB 16OZ"]
    assert rec.reconciliation.status == "reconciled", [c.explain for c in rec.reconciliation.checks]


# ------------------------------------------------------- the three duplicate cases ----------------------------------
def _milk(db):
    return db.add_product("Milk", "milk", "Dairy", 1, "gal"), db.get_or_create_store("Walmart")


def test_duplicate_case_1_same_file_twice(fresh_db):
    data = b"Walmart\n2026-10-05\nGV MILK 1 GAL 3.48\nSUBTOTAL 3.48\nTOTAL 3.48\n"
    h = digital.file_hash(data)
    assert not digital.already_imported(h)
    digital.mark_imported(h, "r.txt", 1)
    assert digital.already_imported(h)                    # the same bytes are recognised before parsing


def test_duplicate_case_2_same_purchase_other_source(fresh_db):
    db = fresh_db
    p, s = _milk(db)
    db.save_receipt(s, "2026-10-05", [dict(product_id=p, qty=1, line_total=3.48, raw_text="GV MILK")],
                    subtotal=3.48, total=3.48, source="digital")
    with pytest.raises(db.DuplicateReceipt):              # same store, day, products, amounts: e.g. the paper copy
        db.save_receipt(s, "2026-10-05", [dict(product_id=p, qty=1, line_total=3.48, raw_text="GV MLK 1G")],
                        subtotal=3.48, total=3.48, source="scan-offline")


def test_duplicate_case_3_really_bought_twice_is_kept_when_you_say_so(fresh_db):
    db = fresh_db
    p, s = _milk(db)
    a = db.save_receipt(s, "2026-10-05", [dict(product_id=p, qty=1, line_total=3.48)], subtotal=3.48, total=3.48)
    b = db.save_receipt(s, "2026-10-05", [dict(product_id=p, qty=1, line_total=3.48)], subtotal=3.48, total=3.48,
                        allow_duplicate=True)
    assert a != b and db.one("SELECT COUNT(*) n FROM receipts")["n"] == 2
    # and a different purchase the same day is never mistaken for a duplicate
    db.save_receipt(s, "2026-10-05", [dict(product_id=p, qty=2, line_total=6.96)], subtotal=6.96, total=6.96)
    assert db.one("SELECT COUNT(*) n FROM receipts")["n"] == 3


# -------------------------------------------------------------- identity ---------------------------------------------
def _cereals(db):
    big = db.add_product("Honey Oat Cereal", "cereal", "Pantry", 18, "oz", brand="Sunny Crunch")
    small = db.add_product("Honey Oat Cereal", "cereal", "Pantry", 15.4, "oz", brand="Sunny Crunch")
    return big, small


def test_brand_variant_and_size_are_separate_fields(fresh_db):
    db = fresh_db
    reg = db.add_product("Whole Milk", "milk", "Dairy", 0.5, "gal", brand="Fairlife")
    lf = db.add_product("Whole Milk", "milk", "Dairy", 0.5, "gal", brand="Fairlife", variant="lactose free")
    assert reg != lf
    assert db.product(lf)["variant"] == "lactose free" and not db.product(reg)["variant"]
    assert identity.variant_mismatch("FAIRLIFE WHOLE MILK LACTOSE FREE 52OZ", db.product(reg))
    assert not identity.variant_mismatch("FAIRLIFE WHOLE MILK LACTOSE FREE 52OZ", db.product(lf))
    assert not identity.variant_mismatch("LF WHOLE WHEAT BRD", db.product(reg))   # abbreviations never flag


def test_size_mismatch_is_caught_and_the_fix_changes_the_unit_price_and_can_be_undone(fresh_db):
    db = fresh_db
    big, small = _cereals(db)
    s = db.get_or_create_store("Walmart")
    rid = db.save_receipt(s, "2026-10-05", [dict(product_id=small, qty=1, line_total=4.48,
                                                 raw_text="SUNNY CRNCH HNY OAT 18OZ", match_method="import",
                                                 confirmed=False)], subtotal=4.48, total=4.48)
    item = db.one("SELECT * FROM items WHERE receipt_id = ?", (rid,))
    assert db.one("SELECT status FROM receipts WHERE id = ?", (rid,))["status"] == "reconciled"   # arithmetic is fine
    assert identity.size_mismatch(item["raw_text"], db.product(small)) == "Receipt says 18 oz; product is 15.4 oz"
    q = identity.review_queue()
    assert int(item["id"]) in q["item_id"].tolist()
    before = db.one("SELECT unit_cost FROM items WHERE id = ?", (item["id"],))["unit_cost"]
    cid = identity.reassign_item(int(item["id"]), big, reason="size")
    after = db.one("SELECT unit_cost, product_id, confirmed FROM items WHERE id = ?", (item["id"],))
    assert after["product_id"] == big and after["confirmed"] == 1
    assert round(before * 28.3495, 3) == round(4.48 / 15.4, 3) and round(after["unit_cost"] * 28.3495, 3) == round(
        4.48 / 18, 3)
    assert identity.review_queue().empty
    identity.undo_change(cid)
    undone = db.one("SELECT unit_cost, product_id FROM items WHERE id = ?", (item["id"],))
    assert undone["product_id"] == small and abs(undone["unit_cost"] - before) < 1e-12


def test_aliases_are_scoped_to_the_retailer(fresh_db):
    from pricewise import matcher
    db = fresh_db
    big, small = _cereals(db)
    k, w = db.get_or_create_store("Kroger"), db.get_or_create_store("Walmart")
    matcher.learn("SUNNY CRUNCH CRL", k, big)
    matcher.learn("SUNNY CRUNCH CRL", w, small)
    assert matcher.match("SUNNY CRUNCH CRL", k)[0].product_id == big
    assert matcher.match("SUNNY CRUNCH CRL", w)[0].product_id == small


def test_merge_is_reversible_exactly(fresh_db):
    db = fresh_db
    a = db.add_product("Greek Yogurt", "yogurt", "Dairy", 32, "oz", brand="Bluehill")
    b = db.add_product("Plain Greek Yogurt", "yogurt", "Dairy", 32, "oz", brand="Bluehill")
    s = db.get_or_create_store("Aldi")
    db.save_receipt(s, "2026-10-01", [dict(product_id=a, qty=1, line_total=5.29, raw_text="BH GRK YOG")], total=5.29)
    db.save_receipt(s, "2026-10-08", [dict(product_id=b, qty=1, line_total=5.49, raw_text="BLUEHILL GREEK")],
                    total=5.49)
    db.run("INSERT INTO shelf_prices(product_id, store_id, observed_on, price) VALUES (?,?,?,?)", (a, s, "2026-10-02",
                                                                                                  4.99))
    assert identity.merge_warnings(a, b) == []
    mid = identity.merge_products(a, b)
    assert db.one("SELECT COUNT(*) n FROM items WHERE product_id = ?", (b,))["n"] == 2
    assert a not in db.products()["id"].tolist()                 # hidden while merged
    identity.undo_merge(mid)
    assert db.one("SELECT COUNT(*) n FROM items WHERE product_id = ?", (a,))["n"] == 1
    assert db.one("SELECT product_id FROM shelf_prices")["product_id"] == a
    assert a in db.products()["id"].tolist()


def test_merge_warns_about_different_sizes(fresh_db):
    big, small = _cereals(fresh_db)
    assert any("package sizes" in w for w in identity.merge_warnings(small, big))


def test_provenance_records_parsed_corrected_and_saved_values_with_who(fresh_db):
    db = fresh_db
    p = db.add_product("Bananas", "bananas", "Produce", None, "lb", sold_by_weight=True)
    s = db.get_or_create_store("Kroger")
    rid = db.save_receipt(s, "2026-10-01", [dict(product_id=p, qty=2.31, qty_unit="lb", line_total=1.34,
                                                 raw_text="BANANAS", parsed={"qty": 2.13, "line_total": 1.34,
                                                                             "product_id": p},
                                                 actor="parser:offline", match_method="fuzzy", confirmed=True)],
                          total=1.34)
    prov = db.q("SELECT field, parsed_value, saved_value, actor FROM item_provenance").set_index("field")
    assert prov.loc["qty", "parsed_value"] == "2.13" and prov.loc["qty", "saved_value"] == "2.31"
    assert prov.loc["qty", "actor"] == "you"                       # a correction is credited to you
    assert prov.loc["line_total", "actor"] == "parser:offline"     # an untouched value to the reader
    assert prov.loc["product_id", "actor"] == "you"                # you confirmed the product


# ----------------------------------------------------- deliberate failure, end to end ---------------------------------
def test_deliberate_failure_walkthrough(demo):
    failure_demo.remove()
    assert failure_demo.state()["step"] == 0
    failure_demo.plant()
    s = failure_demo.state()
    assert s["step"] == 1 and s["in_queue"] and s["flag"] == "Receipt says 18 oz; product is 15.4 oz"
    assert s["unit_now"] == s["as_small"] != s["as_big"]
    cid = identity.reassign_item(int(s["item"]["id"]), int(s["big"]["id"]))
    s2 = failure_demo.state()
    assert s2["step"] == 2 and s2["unit_now"] == s["as_big"]
    identity.undo_change(cid)
    assert failure_demo.state()["step"] == 1
    failure_demo.remove()
    assert failure_demo.state()["step"] == 0


# --------------------------------------------------------------- shrinkflation ---------------------------------------
def test_shrinkflation_detail(demo):
    e = next(e for e in shrink.detect() if e.name == "Honey Oat Cereal")
    assert e.old_size == "18 oz" and e.new_size == "15.4 oz"
    assert e.old_count >= 2 and e.new_count >= 2
    assert e.old_first <= e.old_last < e.new_first <= e.new_last
    assert not e.coexist
    assert e.extra_for(26) == pytest.approx(2 * e.extra_for(13))
    assert e.extra_for(12) > 0


# ------------------------------------------------------------ checkout problems --------------------------------------
def test_checkout_problems_are_split_into_three_kinds(demo):
    cats = {i.category for i in audit.audit()}
    assert {"Calculation mismatch", "Possible double scan", "Price-tag or promotion mismatch"} <= cats


def test_refund_note_only_includes_reviewed_flags(demo):
    issues = [i for i in audit.audit() if i.claimable]
    for i in issues:
        audit.set_status(i, "open")
    assert audit.refund_note(audit.audit()) == ""                 # nothing reviewed: no note at all
    target = next(i for i in audit.audit() if i.kind == "weight_math")
    audit.set_status(target, "reviewed")
    same_receipt = [i for i in audit.audit() if i.receipt_id == target.receipt_id]
    note = audit.refund_note(same_receipt)
    assert target.label in note
    for other in same_receipt:
        if other.key != target.key:
            assert f"{other.label}: {other.title.lower()}" not in note
    audit.set_status(target, "open")


def test_missed_offer_needs_recorded_terms(fresh_db):
    db = fresh_db
    p = db.add_product("Spaghetti", "pasta", "Pantry", 16, "oz", brand="Kroger")
    s = db.get_or_create_store("Kroger")
    db.save_receipt(s, "2026-11-07", [dict(product_id=p, qty=2, line_total=2.90, unit_price=1.45)], total=2.90)
    sid = db.run("INSERT INTO shelf_prices(product_id, store_id, observed_on, price, per, kind, note) "
                 "VALUES (?,?,?,?,?,?,?)", (p, s, "2026-11-07", 1.00, "package", "ad", "2 for $2"))
    i = next(i for i in audit.audit() if i.kind == "missed_sale")
    assert i.needs_terms and not i.claimable
    audit.set_status(i, "reviewed")
    assert audit.refund_note([next(x for x in audit.audit() if x.kind == "missed_sale")]) == ""
    audit.set_terms(sid, "buy 2; card required; limit 4")
    i = next(i for i in audit.audit() if i.kind == "missed_sale")
    assert i.claimable and any("limit 4" in e for e in i.evidence)
    assert "limit 4" in audit.refund_note([i])


def test_total_check_counts_adjustments_so_deposits_are_not_overcharges(fresh_db):
    db = fresh_db
    p = db.add_product("Sparkling Water", "water", "Drinks", 144, "fl oz")
    s = db.get_or_create_store("Aldi")
    db.save_receipt(s, "2026-11-01", [dict(product_id=p, qty=1, line_total=3.99)], subtotal=4.59, tax=0,
                    total=4.59, adjustments=[reconcile.Adjustment("deposit", 60, "CRV")])
    assert not [i for i in audit.audit() if i.kind == "total_mismatch"]
