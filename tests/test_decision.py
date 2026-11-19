"""Decision Replay: time-correct inputs, frozen decisions, honest coverage and outcomes."""
import json

import pytest

from pricewise import catalog, db as _db, decision as D, money, observations, summary

CUT = "2026-11-08"


def _test_basket(db):
    lst = D.lists()
    return D.load_list(int(lst[lst["name"].str.startswith("Test basket")].iloc[0]["id"]))


def _receipt(db, store, day, product_id, qty, line_total, unit="each"):
    sid = db.get_or_create_store(store)
    return db.save_receipt(sid, day, [dict(product_id=product_id, qty=qty, qty_unit=unit, line_total=line_total,
                                           raw_text="TEST")], subtotal=line_total, total=line_total)


# ---------------------------------------------------------------- the engine
def test_compute_is_pure_and_deterministic(demo):
    inp = D.build_inputs(CUT, _test_basket(demo), {})
    assert D.digest(D.compute(inp)) == D.digest(D.compute(json.loads(D.canonical(inp))))


def test_brief_test_case_ties_at_64_cents(demo):
    inp = D.build_inputs(CUT, _test_basket(demo), {"extra_stop_cents": 0})
    r = D.compute(inp)
    th = D.stop_cost_threshold(r)
    assert (th["single"], th["multi"], th["advantage_cents"], th["tie_at_cents"]) == ("Walmart", "Aldi + Walmart", 64, 64)
    assert sorted(c["item"] for c in th["carriers"]) == ["Broccoli Crowns (by the lb)", "Gala Apples (3 lb)"]
    rows = D.sweep(inp, "extra_stop_cents", [63, 64, 65])
    assert [r["winner"] for r in rows] == ["Aldi + Walmart", "Walmart", "Walmart"]   # a tie goes to fewer stops


def test_removing_an_observation_changes_coverage(demo):
    basket = _test_basket(demo)
    inp = D.build_inputs(CUT, basket, {})
    walmart = D.plan_by_name(D.compute(inp), "Walmart")
    spinach = next(l for l in walmart["lines"] if "Spinach" in l["item"])
    eligible = [o["obs_id"] for o in inp["observations"]
                if o["product_id"] == spinach["product_id"] and o["store"] == "Walmart"]
    r = D.compute(D.build_inputs(CUT, basket, {"exclude_obs": eligible}))
    w = D.plan_by_name(r, "Walmart")
    assert w["covered"] == 4 and not w["complete"]
    assert "no observation at Walmart" in w["missing"][0]["reasons"][0]
    assert all(p["complete"] for p in r["complete"])       # partial plans never enter the ranking


def test_stale_history_is_explained(demo):
    basket = _test_basket(demo)
    r = D.compute(D.build_inputs(CUT, basket, {"max_age_days": 14}))
    reasons = [x for p in r["partial"] for m in p["missing"] for x in m["reasons"]]
    assert any("only stale history" in x for x in reasons)


def test_estimate_methods_and_limited_history(demo):
    basket = _test_basket(demo)
    for method in ("last", "median"):
        inp = D.build_inputs(CUT, basket, {"method": method})
        e = D.estimate(inp, basket[0]["product_id"], "Aldi")
        assert e["ok"] and e["method"] == method and e["newest"] < CUT
        assert e["n"] == (1 if method == "last" else e["n_eligible"])


def test_whole_packages_for_substitutes_and_requirements(fresh_db):
    db = fresh_db
    big = catalog.create_product("Greek Yogurt", "greek yogurt", "Dairy & eggs", 32, "oz", brand="A")
    small = catalog.create_product("Greek Yogurt", "greek yogurt", "Dairy & eggs", 16, "oz", brand="B")
    lf_milk = catalog.create_product("Lactose Free Whole Milk", "whole milk", "Dairy & eggs", 0.5, "gal", brand="C")
    milk = catalog.create_product("Whole Milk", "whole milk", "Dairy & eggs", 0.5, "gal", brand="D")
    _receipt(db, "Store1", "2026-11-01", big, 1, 5.00)
    _receipt(db, "Store2", "2026-11-02", small, 1, 2.20)
    _receipt(db, "Store1", "2026-11-03", lf_milk, 1, 4.00)
    _receipt(db, "Store2", "2026-11-04", milk, 1, 2.00)
    basket = [{"product_id": big, "qty": 1, "approved_substitutes": [small]},
              {"product_id": lf_milk, "qty": 1, "approved_substitutes": [milk]}]
    r = D.compute(D.build_inputs("2026-11-10", basket, {"extra_stop_cents": 0}))
    plan = D.plan_by_name(r, "Store2")
    yog = next(l for l in plan["lines"] if "Yogurt" in l["item"])
    assert (yog["qty"], yog["cost_cents"], yog["exact"]) == (2, 440, False)      # two 16 oz tubs cover 32 oz
    assert not plan["complete"]          # regular milk can't stand in for lactose-free, even when approved


def test_cash_budget_is_separate_from_trip_cost(demo):
    r = D.compute(D.build_inputs(CUT, _test_basket(demo), {"budget_cents": 1450, "extra_stop_cents": 100}))
    two = D.plan_by_name(r, "Aldi + Walmart")
    assert two["fits_budget"] and two["decision_cents"] > 1450 and two["cash_cents"] <= 1450


# ---------------------------------------------------------------- look-ahead bias: the acceptance test
def test_saved_decision_survives_a_later_cheaper_receipt(demo):
    db = demo
    basket = _test_basket(db)
    settings = {"extra_stop_cents": 50, "method": "last"}
    inp = D.build_inputs(CUT, basket, settings)
    res = D.compute(inp)
    assert res["winner"] == "Aldi + Walmart"            # Aldi saves $0.64, more than the $0.50 second stop
    did = D.save(inp, res, "acceptance", "current")
    apples = basket[0]["product_id"]
    # a much cheaper Walmart receipt for apples, dated AFTER the cutoff
    rid = _receipt(db, "Walmart", "2026-11-09", apples, 1, 0.99)
    try:
        assert D.reproduce(did)["same"]                     # frozen inputs: identical result
        live = D.replay_live(did)
        assert live["same_result"] and not live["added"]     # today's database, same cutoff: identical
        newer = D.compute(D.build_inputs("2026-11-10", basket, settings))
        line = next(l for l in D.plan_by_name(newer, "Walmart")["lines"] if l["product_id"] == apples)
        assert f"r{db.one('SELECT id FROM items WHERE receipt_id = ?', (rid,))['id']}" in \
            [o["obs_id"] for o in D.build_inputs("2026-11-10", basket, {})["observations"]]
        assert line["estimate"]["newest"] == "2026-11-09"     # the new receipt is eligible in a new scenario
        assert newer["winner"] == "Walmart"                   # and it changes the decision
        assert D.load_decision(did)["result"]["winner"] == "Aldi + Walmart"   # the saved one doesn't
    finally:
        db.delete_receipt(rid)


def test_product_edit_doesnt_change_saved_decision(demo):
    db = demo
    basket = _test_basket(db)
    inp = D.build_inputs(CUT, basket, {})
    did = D.save(inp, D.compute(inp), "edit test", "current")
    old = db.product(basket[0]["product_id"])["name"]
    db.run("UPDATE products SET name = 'Renamed Apples' WHERE id = ?", (basket[0]["product_id"],))
    try:
        rep = D.reproduce(did)
        assert rep["same"] and "Gala Apples" in rep["result"]["complete"][0]["lines"][0]["item"]
    finally:
        db.run("UPDATE products SET name = ? WHERE id = ?", (old, basket[0]["product_id"]))


def test_replay_never_sees_the_trip_itself(demo):
    db = demo
    trip = db.one("SELECT id, purchased_on FROM receipts WHERE purchased_on = '2026-10-31'")
    obs = observations.load(trip["purchased_on"])
    assert obs.empty or obs["date"].max() < trip["purchased_on"]
    assert not (obs["receipt_id"] == trip["id"]).any()
    assert observations.hidden_after(trip["purchased_on"]) > 0


# ---------------------------------------------------------------- the reveal
def test_prediction_vs_receipt_accounts_for_every_cent(demo):
    db = demo
    trip = db.one("SELECT id, purchased_on FROM receipts WHERE purchased_on = '2026-10-31'")
    basket = D.infer_basket(trip["purchased_on"], 1)
    inp = D.build_inputs(trip["purchased_on"], basket, {})
    out = D.compare_with_receipt(inp, D.compute(inp), trip["id"])
    types = {r["type"] for r in out["rows"]}
    assert "compared" in types and {"missing", "extra"} & types
    assert out["unreconciled_cents"] == 0                       # every cent of a reconciled receipt is placed
    for r in out["rows"]:
        if r["type"] == "compared" and r["error_cents"] is not None:
            assert r["error_cents"] == r["actual_cents"] - r["predicted_cents"]
    for o in out["observed"]:
        assert o["other_store"] != out["store"] and o["other_seen"] < trip["purchased_on"]


def test_summary_is_redacted_by_default(demo):
    basket = _test_basket(demo)
    inp = D.build_inputs(CUT, basket, {})
    did = D.save(inp, D.compute(inp), "share test", "current")
    d = D.load_decision(did)
    red = summary.render(d)
    full = summary.render(d, redact=False)
    some_obs = d["result"]["complete"][0]["lines"][0]["estimate"]["obs_ids"][0]
    assert some_obs not in red and some_obs in full
    assert "distance" not in red.lower() and "TEST" not in red


# --------------------------------------------- regressions from the Nov 18 review ------------------------------------
def _chicken(db):
    p = db.add_product("Chicken Thighs", "chicken", "Meat", None, "lb", sold_by_weight=True)
    s = db.get_or_create_store("Walmart")
    # history: $4.00 a pound
    db.save_receipt(s, "2026-10-01", [dict(product_id=p, qty=2, qty_unit="lb", line_total=8.00)],
                    subtotal=8.00, tax=0, total=8.00)
    return p, s


@pytest.mark.parametrize("qty,unit,lbs", [(16, "oz", 1.0), (0.5, "kg", 1.1023), (453.59237, "g", 1.0),
                                          (1, "lb", 1.0)])
def test_weighed_receipt_lines_are_converted_to_pounds(fresh_db, qty, unit, lbs):
    from pricewise import decision as D
    db = fresh_db
    p, s = _chicken(db)
    paid = round(4.00 * lbs, 2)
    trip = db.save_receipt(s, "2026-10-10", [dict(product_id=p, qty=qty, qty_unit=unit, line_total=paid)],
                           subtotal=paid, tax=0, total=paid)
    basket = D.basket_from_receipt(trip)
    assert basket[0]["qty"] == pytest.approx(round(lbs, 2))          # 16 oz is 1 lb, not 16 lb
    inputs = D.build_inputs("2026-10-10", basket, {"method": "last"})
    out = D.compare_with_receipt(inputs, D.compute(inputs), trip)
    row = next(r for r in out["rows"] if r["type"] == "compared")
    assert row["qty"] == pytest.approx(lbs, rel=1e-4) and row["unit"] == "lb"
    assert row["predicted_cents"] == pytest.approx(round(400 * lbs), abs=1)   # $4.00 for 16 oz, never $64.00
    assert abs(row["error_cents"]) <= 1
    assert row["actual_unit_cents"] == pytest.approx(400, abs=1)


def test_unverified_receipts_do_not_move_the_tax_estimate(fresh_db):
    from pricewise import observations
    db = fresh_db
    p = db.add_product("Paper Towels", "paper towels", "Household", 6, "each")
    s = db.get_or_create_store("Kroger")
    db.save_receipt(s, "2026-10-01", [dict(product_id=p, qty=1, line_total=10.00)], subtotal=10.00, tax=0.70,
                    total=10.70)
    before = observations.store_tax_rates("2026-11-01")["Kroger"]
    assert before == pytest.approx(0.07)
    # a misread receipt: $9.00 tax on $10.00. It doesn't reconcile, so it is saved as unverified
    db.save_receipt(s, "2026-10-08", [dict(product_id=p, qty=1, line_total=10.00)], subtotal=10.00, tax=9.00,
                    total=10.90, status="unverified", allow_duplicate=True)
    assert db.one("SELECT status FROM receipts WHERE tax_cents = 900")["status"] == "unverified"
    assert observations.store_tax_rates("2026-11-01")["Kroger"] == pytest.approx(before)
