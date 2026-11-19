"""Receipt reading: the parser, the totals check, and (if Tesseract is installed) real OCR on the sample."""
from pathlib import Path

import pytest

from pricewise import scanner

SAMPLE_TEXT = """WALMART
Save money. Live better.
11/07/2026 14:32
GV WHL MLK GAL 3.48 N
CHKN BRST BNLS
1.80 LB @ 3.99 /LB 7.18 N
BANANAS
2.31 LB @ 0.58 /LB 1.34 N
GV LG EGGS 12CT 3.58 N
BLUEHILL GRK YGT 32 5.98 N
COUPON YOGURT 1.00-
2 @ 1.12
GV SPAG 160Z 2.24 N
PEAK CRMY PB 16OZ 2.98 N
SUBTOTAL 25.78
TAX 1 0.00
TOTAL 25.78
VISA TEND 25.78
CHANGE DUE 0.00
"""


def test_parse_lines(fresh_db):
    rec = scanner.parse_text(SAMPLE_TEXT)
    assert rec.store == "Walmart"
    assert rec.purchased_on == "2026-11-07"
    names = [i.raw_text for i in rec.items]
    assert len(rec.items) == 7, names
    chicken = rec.items[1]
    assert chicken.raw_text.startswith("CHKN BRST BNLS") and chicken.qty == 1.80 and chicken.qty_unit == "lb"
    assert chicken.unit_price == 3.99
    spag = next(i for i in rec.items if "SPAG" in i.raw_text)
    assert spag.qty == 2 and spag.unit_price == 1.12 and "16OZ" in spag.raw_text   # OCR slip fixed
    yog = next(i for i in rec.items if "YGT" in i.raw_text)
    assert yog.discount == 1.00
    assert rec.subtotal == 25.78 and rec.total == 25.78
    assert rec.items_sum == 25.78
    assert rec.warnings == []


def test_total_mismatch_is_flagged(fresh_db):
    rec = scanner.parse_text(SAMPLE_TEXT.replace("SUBTOTAL 25.78", "SUBTOTAL 27.00"))
    assert any("subtotal" in w for w in rec.warnings)


def test_weight_maths_error_lowers_confidence(fresh_db):
    rec = scanner.parse_text(SAMPLE_TEXT.replace("2.31 LB @ 0.58 /LB 1.34 N", "2.31 LB @ 0.58 /LB 1.64 N"))
    bananas = next(i for i in rec.items if "BANANAS" in i.raw_text)
    assert bananas.confidence <= 0.55 and bananas.notes


def test_gemini_shape_is_checked(fresh_db):
    rec = scanner.from_gemini({"store": "Aldi", "date": "2026-11-01", "subtotal": 5.0, "items": [
        {"raw_text": "MILK", "line_total": 3.19, "qty": 1}, {"raw_text": "EGGS", "line_total": 2.65}]})
    assert rec.engine == "gemini" and len(rec.items) == 2
    assert any("subtotal" in w for w in rec.warnings)     # 5.84 vs 5.00


@pytest.mark.skipif(not scanner.tesseract_ready(), reason="Tesseract not installed")
def test_real_ocr_on_sample_image(fresh_db):
    img = (Path(__file__).resolve().parent.parent / "samples" / "sample_receipt.png").read_bytes()
    rec = scanner.scan(img, "image/png", engine="offline")
    assert len(rec.items) == 7
    assert rec.items_sum == pytest.approx(26.78)
    assert rec.warnings == []
