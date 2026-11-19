"""Milestone 3: the evidence tools must catch their own blind spots and never peek at the future."""
from pathlib import Path

import pandas as pd

from pricewise import evidence as E, scanner

ROOT = Path(__file__).resolve().parent.parent


def test_blind_spot_matrix():
    m = E.run_blind_spots().set_index("case")
    # the naive scorer is fooled by exactly the cases it was built to be fooled by
    for case in ("Same-price variants", "Missing quantity", "Coupon on the wrong product"):
        assert m.loc[case, "naive evaluator"] == "missed"
        assert m.loc[case, "field-level evaluator"] == "caught"
        assert m.loc[case, "reconciliation"] == "missed"           # the totals still add up
    assert m.loc["Extra line", "reconciliation"] == "caught"
    assert m.loc["One digit misread", "field-level evaluator"] == "caught"
    assert m.loc["Right text, wrong product size", "identity check"] == "caught"
    # and the control raises no false alarm anywhere
    assert set(m.loc["Read perfectly", ["naive evaluator", "field-level evaluator", "reconciliation"]]) == {"missed"}


def test_fields_have_denominators_and_unlabelled_fields_are_not_counted():
    rec = scanner.ParsedReceipt(store="Aldi", purchased_on="2026-11-01", subtotal=5.0, total=5.0)
    rec.items = [scanner.ParsedItem("MILK 1 GAL", 3.0, qty=1), scanner.ParsedItem("EGGS 12CT", 2.0, qty=1)]
    label = {"store": "Aldi", "date": "2026-11-01", "subtotal": 5.0, "total": 5.0,
             "lines": [{"text": "MILK 1 GAL", "line_total": 3.0, "qty": 1, "size": "1 gal"},
                       {"text": "EGGS 12CT", "line_total": 2.0}]}
    s = E.score(rec, label)
    assert s["fields"]["line_total"] == [2, 2]
    assert s["fields"]["qty"] == [1, 1]          # only one line had a labelled quantity
    assert s["fields"]["size"] == [1, 1]
    assert s["fields"]["discount"] == [0, 0]     # nothing labelled: no evidence either way


def test_sample_evaluation_is_reproducible(fresh_db):
    a = E.evaluate_folder(ROOT / "eval" / "sample")
    b = E.evaluate_folder(ROOT / "eval" / "sample")
    assert a["manifest"]["dataset"] == b["manifest"]["dataset"]
    assert a["manifest"]["results"] == b["manifest"]["results"]
    f = a["fields"].set_index("field")
    assert f.loc["Quantity / weight", "labelled"] > 0 and f.loc["Item discount", "labelled"] > 0


def test_benchmark_never_uses_the_trip_or_later(demo):
    b = E.benchmark()
    assert b["n"] > 100
    lines = b["lines"]
    # a rule's prediction for a line can only equal a price observed BEFORE that line's date
    from pricewise import observations
    obs = observations.load()
    obs["date"] = obs["date"].astype(str).str[:10]
    sample = lines[lines["Last paid, any age"].notna()].head(60)
    for r in sample.to_dict("records"):
        earlier = obs[(obs["product_id"] == r["product_id"]) & (obs["store"] == r["store"])
                      & (obs["date"] < r["date"])]
        last = earlier.sort_values(["date", "obs_id"]).iloc[-1]["price_cents"]
        assert r["Last paid, any age"] == last
    rules = b["rules"]
    assert rules["chosen"].sum() == 1
    assert (rules["coverage"] <= 1).all() and (rules["coverage"] > 0).all()
