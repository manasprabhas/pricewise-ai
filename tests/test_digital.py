"""Digital receipt import, end to end: file -> parsed lines -> reconciled -> saved once."""
from pathlib import Path

import pytest

from pricewise import catalog, digital

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def _save(db, rec, source):
    """Save a parsed receipt the way the review screen does, creating a product per line."""
    store = db.get_or_create_store(rec.store)
    lines = []
    for it in rec.items:
        by_weight = it.qty_unit == "lb"
        pid = catalog.create_product(it.raw_text.title(), it.raw_text.lower(), "Other", None if by_weight else 1,
                                     "lb" if by_weight else "each", sold_by_weight=by_weight)
        lines.append(dict(product_id=pid, qty=it.qty, qty_unit=it.qty_unit, unit_price=it.unit_price,
                          line_total=it.line_total, discount=it.discount, raw_text=it.raw_text))
    return db.save_receipt(store, rec.purchased_on, lines, subtotal=rec.subtotal, tax=rec.tax or 0,
                           total=rec.total, source=source), lines, store


@pytest.mark.parametrize("name,store,n_items,total", [
    ("sample_email_receipt.eml", "Walmart", 5, 17.22),
    ("sample_receipt.pdf", "Kroger", 4, 9.91),
])
def test_digital_receipt_parses_and_reconciles(fresh_db, name, store, n_items, total):
    data = (SAMPLES / name).read_bytes()
    text, hints = digital.extract(name, data)
    rec = digital.parse(text, hints)
    assert rec.store == store and rec.purchased_on == "2026-11-07"
    assert len(rec.items) == n_items
    assert rec.total == pytest.approx(total)
    assert rec.warnings == []                       # lines add up to the printed subtotal


def test_discount_attached_to_the_right_item(fresh_db):
    text, hints = digital.extract("r.pdf", (SAMPLES / "sample_receipt.pdf").read_bytes())
    rec = digital.parse(text, hints)
    eggs = next(i for i in rec.items if "EGG" in i.raw_text)
    assert eggs.discount == pytest.approx(0.50)
    weighed = next(i for i in rec.items if "BANANA" in i.raw_text)
    assert (weighed.qty, weighed.qty_unit, weighed.unit_price) == (1.81, "lb", 0.68)


def test_saved_digital_receipt_is_reconciled_and_cannot_be_saved_twice(fresh_db):
    db = fresh_db
    text, hints = digital.extract("r.eml", (SAMPLES / "sample_email_receipt.eml").read_bytes())
    rec = digital.parse(text, hints)
    rid, lines, store = _save(db, rec, "digital")
    assert db.one("SELECT status FROM receipts WHERE id = ?", (rid,))["status"] == "reconciled"
    with pytest.raises(db.DuplicateReceipt):
        db.save_receipt(store, rec.purchased_on, lines, subtotal=rec.subtotal, total=rec.total, source="digital")


def test_inbox_skips_files_already_imported(fresh_db, tmp_path, monkeypatch):
    monkeypatch.setattr(digital, "inbox_dir", lambda: tmp_path)
    data = (SAMPLES / "sample_receipt.pdf").read_bytes()
    (tmp_path / "a.pdf").write_bytes(data)
    (tmp_path / "copy-of-a.pdf").write_bytes(data)         # same file saved twice under another name
    assert len(digital.inbox_files()) == 2
    digital.mark_imported(digital.file_hash(data), "a.pdf", None)
    assert digital.inbox_files() == []
