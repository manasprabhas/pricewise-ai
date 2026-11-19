"""The evaluation tool itself must catch misreadings (otherwise a 100% score means nothing)."""
import evaluate
from pricewise import scanner

LABEL = {"store": "Walmart", "date": "2026-11-07", "subtotal": 5.00, "total": 5.00,
         "lines": [{"text": "GV WHL MLK GAL", "line_total": 3.48}, {"text": "BANANAS", "line_total": 1.52}]}


def _rec(items):
    r = scanner.ParsedReceipt(store="Walmart", purchased_on="2026-11-07", subtotal=5.00, total=5.00)
    r.items = [scanner.ParsedItem(raw_text=t, line_total=p) for t, p in items]
    return r


def test_perfect_read_scores_100():
    s = evaluate.score(_rec([("GV WHL MLK GAL", 3.48), ("BANANAS", 1.52)]), LABEL)
    assert (s["recall"], s["precision"], s["reconciles"]) == (1.0, 1.0, True)


def test_wrong_price_missing_and_extra_lines_are_penalised():
    s = evaluate.score(_rec([("GV WHL MLK GAL", 3.43), ("BANANAS", 1.52), ("TOTAL SAVINGS", 0.50)]), LABEL)
    assert s["lines_correct"] == 1 and s["recall"] == 0.5 and s["precision"] == 1 / 3
    assert not s["reconciles"]


def test_one_predicted_line_cannot_match_two_labels():
    label = dict(LABEL, lines=[{"text": "BANANAS", "line_total": 1.52}, {"text": "BANANAS", "line_total": 1.52}])
    assert evaluate.score(_rec([("BANANAS", 1.52)]), label)["lines_correct"] == 1
