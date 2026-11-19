"""Every feature finds the stories planted in the demo data, and its numbers check out."""
import pandas as pd
import pytest

from pricewise import analytics, assistant, audit, decision, pvm, savings, shrink, stockup, swaps


def test_trip_explainer_adds_up_exactly(demo):
    pur = demo.purchases()
    for a, b in [("2026-09", "2026-10"), ("2026-05", "2026-06"), ("2026-07", "2026-08")]:
        ex = pvm.explain(pur[pur["month"] == a], pur[pur["month"] == b])
        assert abs(ex.check()) < 1e-6
    ex = pvm.explain(pur[pur["month"] == "2026-09"], pur[pur["month"] == "2026-10"])
    assert ex.effects["new"] > 0               # spinach, tofu, lentils first appear in October
    assert ex.effects["volume"] > 0            # more chicken in October


def test_trip_explainer_same_trip_is_zero(demo):
    pur = demo.purchases()
    r = pur[pur["receipt_id"] == pur["receipt_id"].max()]
    ex = pvm.explain(r, r)
    assert all(abs(v) < 1e-9 for v in ex.effects.values())


def test_overcharges_found(demo):
    issues = audit.audit()
    kinds = {i.kind: i for i in issues}
    assert kinds["weight_math"].amount == pytest.approx(0.30)
    assert kinds["double_scan"].amount == pytest.approx(6.29)
    assert kinds["missed_sale"].amount == pytest.approx(0.90)
    assert audit.refund_note([kinds["weight_math"], kinds["double_scan"]]) == ""   # not reviewed yet
    for k in ("weight_math", "double_scan"):
        audit.set_status(kinds[k], "reviewed")
    kinds = {i.kind: i for i in audit.audit()}
    note = audit.refund_note([kinds["weight_math"], kinds["double_scan"]])
    assert "$6.59" in note
    for k in ("weight_math", "double_scan"):
        audit.set_status(kinds[k], "open")


def test_shrinkflation_found(demo):
    ev = shrink.detect()
    assert len(ev) == 1
    e = ev[0]
    assert e.brand == "Sunny Crunch" and e.old_size == "18 oz" and e.new_size == "15.4 oz"
    assert e.unit_change == pytest.approx(18 / 15.4 - 1, abs=0.01)


def test_alerts_and_store_comparison(demo):
    pur = demo.purchases()
    alerts = analytics.alerts_for(pur, [int(pur["receipt_id"].max())])
    assert any("Bananas" in a.label and a.direction == "high" for a in alerts)
    score = analytics.store_scorecard()
    assert score.index[0] == "Aldi"


def test_personal_inflation_is_chained(demo):
    pi = analytics.personal_inflation()
    assert pi["index"].iloc[0] == 100
    chained = 100 * (1 + pi["change"].fillna(0)).prod()
    assert pi["index"].iloc[-1] == pytest.approx(chained)


def test_planner_ranks_complete_plans_and_adds_up(demo):
    basket = decision.infer_basket("2026-11-08", 2)
    assert len(basket) >= 10
    for b in basket:
        b["approved_substitutes"] = [c["product_id"] for c in decision.substitute_candidates(b["product_id"])]
    r = decision.compute(decision.build_inputs("2026-11-08", basket, {}))
    assert r["complete"] == sorted(r["complete"], key=lambda p: (p["decision_cents"], len(p["stores"]), p["name"]))
    for p in r["complete"] + r["partial"]:
        assert p["checkout_cents"] == sum(l["cost_cents"] for l in p["lines"])
        assert p["decision_cents"] == p["checkout_cents"] + p["tax_cents"] + p["trip_cents"]
        assert p["complete"] == (p["covered"] == p["total_items"])

def test_stockup_finds_deal_and_rhythm(demo):
    adv = stockup.advise()
    up = [a for a in adv if a.action == "stock up"]
    wait = [a for a in adv if a.action == "wait"]
    assert any("Kirkwood" in a.label for a in up)
    assert any("Kroger Whole Milk" in a.label and 24 <= a.cycle_days <= 32 for a in wait)


def test_waste_check(demo):
    bananas = demo.one("SELECT id FROM products WHERE kind = 'bananas'")["id"]
    assert stockup.waste_check(bananas, 10)["ok"] is False
    rice = demo.one("SELECT id FROM products WHERE kind = 'white rice' LIMIT 1")["id"]
    assert stockup.waste_check(rice, 1)["ok"] is True


def test_swaps_only_cheaper_same_kind(demo):
    found = swaps.find()
    assert found
    for s in found:
        assert s.saving_pct > 0 and s.yearly_saving > 0
        assert demo.product(s.from_id)["kind"] == demo.product(s.to_id)["kind"]


def test_savings_kept_separate(demo):
    savings.evaluate()
    b = savings.scoreboard()
    assert b["refund"] == pytest.approx(2.98)
    assert b["documented"] >= 0 and b["estimated"] == pytest.approx(6.50)


@pytest.mark.parametrize("q,tool", [
    ("Why did I spend more last month?", "spend_change"),
    ("Where have I paid the lowest price for chicken?", "cheapest_store"),
    ("Which products went up in price recently?", "price_increases"),
    ("What gives me the most protein per dollar?", "protein_per_dollar"),
    ("Can I keep a week of groceries under $35?", "plan_budget"),
    ("Is a second store worth the trip?", "trip_worth_it"),
    ("Did any receipt overcharge me?", "overcharges"),
    ("What should I stock up on?", "stock_up"),
])
def test_assistant_routes_and_uses_database_numbers(demo, q, tool):
    ans = assistant.ask(q)
    assert ans.tool == tool
    assert ans.text and "couldn't" not in ans.text.lower()


def test_assistant_numbers_match_database(demo):
    ans = assistant.cheapest_store("chicken")
    m = analytics.store_matrix()
    best = m.loc["chicken breast"].drop("unit").astype(float).min()
    assert f"${best:.2f}/lb" in ans.text
