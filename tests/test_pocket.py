"""Milestone 4: Pocket works offline, carries no web addresses, and imports exactly once."""
import json
import re
import uuid

import pytest

from pricewise import db as _db, pocket


def _export(observations, export_id=None, changes=None, final=None):
    return {"format": pocket.FORMAT, "export_id": export_id or str(uuid.uuid4()), "device": "test phone",
            "exported_at": "2026-11-17T10:00:00", "list_name": "My usual week", "observations": observations,
            "list_changes": changes or [], "final_list": final or []}


def _obs(pid, cents, store="Kroger", u=None, seconds=6.2, multibuy=1, terms=""):
    return {"uuid": u or str(uuid.uuid4()), "product_id": pid, "store": store, "date": "2026-11-16",
            "price_cents": cents, "basis": "package", "multibuy": multibuy, "terms": terms, "seconds": seconds}


def test_pocket_file_is_self_contained_and_offline(demo):
    html = pocket.make_html(pocket.bundle())
    data = json.loads(re.search(r'<script id="pocket-data" type="application/json">(.*?)</script>', html, re.S)
                      .group(1).replace("<\\/", "</"))
    assert data["format"] == pocket.FORMAT and len(data["products"]) > 20
    assert "connect-src 'none'" in html and "default-src 'none'" in html
    # no web address anywhere: nothing to connect to (fonts are embedded as data: URIs)
    assert not re.search(r"https?://", html)
    assert "NaN" not in re.search(r'id="pocket-data"[^>]*>(.*?)</script>', html, re.S).group(1)


def test_price_book_separates_paid_from_seen(demo):
    book = pocket.price_book()
    pasta = next(p for p in book.values() if p["label"] == "Kroger Spaghetti (16 oz)")
    k = pasta["stores"]["Kroger"]
    assert k["last_source"] == "receipt"          # "last paid" is a receipt, not the weekly ad
    assert k["seen"]["source"] == "ad" and k["seen"]["cents"] == 100


def test_import_preview_then_once_only(demo):
    pid = int(demo.one("SELECT id FROM products WHERE name = 'Spaghetti' AND brand = 'Kroger'")["id"])
    u = str(uuid.uuid4())
    exp = _export([_obs(pid, 99, u=u), _obs(pid, 0)],
                  final=[{"product_id": pid, "qty": 2}], changes=[{"action": "add", "product_id": pid}])
    pv = pocket.preview(exp)
    assert pv["new"] == 1 and pv["skipped"] == 1                 # a $0 price is never imported
    r = pocket.apply_import(exp)
    assert r["added"] == 1 and r["list_id"] and not r["repeat"]
    row = demo.one("SELECT * FROM shelf_prices WHERE uuid = ?", (u,))
    assert row["price"] == 0.99 and row["source"] == "pocket:test phone"
    # the same file again: detected, and nothing changes
    again = pocket.preview(exp)
    assert again["already"] is not None and again["new"] == 0
    assert pocket.apply_import(exp)["added"] == 0
    # a later, cumulative export that repeats the same price adds only what's new
    exp2 = _export([_obs(pid, 99, u=u), _obs(pid, 105, store="Aldi")])
    assert pocket.apply_import(exp2)["added"] == 1
    assert demo.one("SELECT COUNT(*) n FROM shelf_prices WHERE uuid = ?", (u,))["n"] == 1


def test_multibuy_saves_the_per_package_price_with_terms(demo):
    pid = int(demo.one("SELECT id FROM products WHERE brand = 'Bluehill'")["id"])
    u = str(uuid.uuid4())
    pocket.apply_import(_export([_obs(pid, 450, u=u, multibuy=2, terms="buy 2; card")]))
    row = demo.one("SELECT * FROM shelf_prices WHERE uuid = ?", (u,))
    assert row["price"] == 4.50 and row["note"] == "2 for $9.00" and row["terms"] == "buy 2; card"


def test_bad_files_are_refused(fresh_db):
    with pytest.raises(pocket.PocketError):
        pocket.parse_export(b"not json")
    with pytest.raises(pocket.PocketError):
        pocket.parse_export(json.dumps({"format": "something else"}))
