"""Milestone 6: upgrades, restores, privacy and the launcher checks."""
import socket
import sqlite3
import uuid
from pathlib import Path

import pytest

OLD_SCHEMA = """
CREATE TABLE stores (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, chain TEXT, distance_miles REAL DEFAULT 3,
                     kroger_location_id TEXT);
CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL, brand TEXT DEFAULT '', kind TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Other', size_value REAL, size_unit TEXT, dimension TEXT NOT NULL, base_qty REAL,
    sold_by_weight INTEGER DEFAULT 0, display_unit TEXT, upc TEXT, grams_per_each REAL, density_g_ml REAL DEFAULT 1.0,
    shelf_life_days INTEGER, freezable INTEGER DEFAULT 0, kroger_product_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE receipts (id INTEGER PRIMARY KEY, store_id INTEGER NOT NULL, purchased_on TEXT NOT NULL,
    subtotal REAL, tax REAL DEFAULT 0, total REAL, source TEXT DEFAULT 'manual', notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE items (id INTEGER PRIMARY KEY, receipt_id INTEGER NOT NULL, product_id INTEGER NOT NULL, raw_text TEXT,
    qty REAL NOT NULL DEFAULT 1, qty_unit TEXT NOT NULL DEFAULT 'each', unit_price REAL, line_total REAL NOT NULL,
    discount REAL DEFAULT 0, paid REAL NOT NULL, base_qty_total REAL, unit_cost REAL, confidence REAL DEFAULT 1.0);
CREATE TABLE shelf_prices (id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL, store_id INTEGER NOT NULL,
    observed_on TEXT NOT NULL, price REAL NOT NULL, per TEXT DEFAULT 'package', kind TEXT DEFAULT 'shelf', note TEXT);
CREATE TABLE savings (id INTEGER PRIMARY KEY, logged_on TEXT, kind TEXT, amount REAL, description TEXT,
    receipt_id INTEGER);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
"""


def _old_db(path: Path) -> None:
    c = sqlite3.connect(path)
    c.executescript(OLD_SCHEMA)
    c.execute("INSERT INTO stores(id, name) VALUES (1, 'Walmart'), (2, 'Aldi')")
    c.execute("INSERT INTO products(id, name, kind, category, size_value, size_unit, dimension, base_qty, display_unit)"
              " VALUES (1, 'Whole Milk', 'milk', 'Dairy', 1, 'gal', 'volume', 3785.41, 'gal')")
    for rid, store, day, price in ((1, 1, "2026-09-01", 3.48), (2, 2, "2026-09-08", 2.95), (3, 1, "2026-09-15", 3.52)):
        c.execute("INSERT INTO receipts(id, store_id, purchased_on, subtotal, tax, total) VALUES (?,?,?,?,0,?)",
                  (rid, store, day, price, price))
        c.execute("INSERT INTO items(receipt_id, product_id, raw_text, qty, line_total, discount, paid, base_qty_total,"
                  " unit_cost) VALUES (?,1,'MILK GAL',1,?,0,?,3785.41,?)", (rid, price, price, price / 3785.41))
    c.execute("INSERT INTO shelf_prices(product_id, store_id, observed_on, price, kind, note) "
              "VALUES (1, 2, '2026-09-20', 2.89, 'ad', 'Aldi ad')")
    c.commit()
    c.close()


def test_a_database_from_an_earlier_version_upgrades_in_place(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    _old_db(path)
    monkeypatch.setenv("PRICEWISE_DB", str(path))
    from pricewise import audit, db, decision as D
    db.init_db()
    db.init_db()                                                  # running it twice is harmless
    items = db.q("SELECT line_cents, paid_cents, match_method FROM items ORDER BY id")
    assert items["line_cents"].tolist() == [348, 295, 352]       # dollars became exact cents
    assert set(items["match_method"]) == {"legacy"}
    r = db.one("SELECT total_cents, tax_cents FROM receipts WHERE id = 1")
    assert (r["total_cents"], r["tax_cents"]) == (348, 0)
    assert db.one("SELECT COUNT(*) n FROM decisions")["n"] == 0    # new tables exist
    assert db.product(1)["variant"] in ("", None)
    # the upgraded data works everywhere
    res = D.compute(D.build_inputs("2026-10-01", [{"product_id": 1, "qty": 1}]))
    assert res["winner"] is not None
    assert isinstance(audit.audit(), list)


def test_backup_and_restore_round_trip(fresh_db):
    from pricewise import backup, demo
    db = fresh_db
    demo.load_demo()
    before = backup.counts()
    data = backup.make_backup()
    db.run("DELETE FROM items WHERE id IN (SELECT id FROM items LIMIT 5)")
    assert backup.counts()["items"] == before["items"] - 5
    result = backup.restore(data)
    assert backup.counts() == before
    assert any(Path(db.db_path()).parent.joinpath("backups").glob("before-restore-*.db"))
    assert result


def test_restore_refuses_a_file_that_is_not_a_pricewise_backup(fresh_db, tmp_path):
    from pricewise import backup
    with pytest.raises(backup.RestoreError):
        backup.restore(b"this is not sqlite")
    with pytest.raises(backup.RestoreError):                     # a valid SQLite file, but not PriceWise's
        other = tmp_path / "other.db"
        c = sqlite3.connect(other)
        c.execute("CREATE TABLE t(x)")
        c.commit()
        c.close()
        backup.restore(other.read_bytes())


def test_launcher_dependency_check():
    from pricewise import doctor
    checks = doctor.run()
    assert doctor.ok(checks)
    names = {c.name for c in checks}
    assert {"Python", "streamlit", "pandas", "Data folder"} <= names
    assert all(c.required is False for c in checks if c.name in ("Tesseract OCR", "keyring"))


def test_core_flows_work_with_the_network_switched_off(tmp_path, monkeypatch):
    """Privacy you can test: nothing below may open a network connection."""
    attempts = []

    def refuse(self, address, *a, **k):
        attempts.append(address)
        raise OSError("network disabled by the test")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: refuse(None, a[0] if a else None))
    monkeypatch.setenv("PRICEWISE_DB", str(tmp_path / "offline.db"))
    from pricewise import audit, decision as D, demo, digital, evidence, identity, pocket, shrink
    demo.load_demo()
    cut = D.today_cutoff()
    lst = D.lists().iloc[0]
    inputs = D.build_inputs(cut, D.load_list(int(lst["id"])))
    did = D.save(inputs, D.compute(inputs), "offline", "current")
    assert D.reproduce(did)["same"]
    audit.audit(); shrink.detect(); identity.review_queue(); evidence.benchmark()
    digital.parse((Path(__file__).resolve().parent.parent / "samples" / "sample_email_receipt.eml")
                  .read_text(errors="ignore"))
    html = pocket.make_html(pocket.bundle())
    pid = int(next(iter(pocket.bundle()["products"])))
    pocket.apply_import({"format": pocket.FORMAT, "export_id": str(uuid.uuid4()), "device": "t", "observations": [
        {"uuid": str(uuid.uuid4()), "product_id": pid, "store": "Aldi", "date": "2026-11-16", "price_cents": 199,
         "basis": "package"}], "list_changes": []})
    assert html and attempts == []
