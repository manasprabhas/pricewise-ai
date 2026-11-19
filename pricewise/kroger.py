"""Optional Kroger price checks (free developer account at developer.kroger.com).

Works for Kroger-family stores (Kroger, Ralphs, Fred Meyer, King Soopers, Fry's, Smith's, QFC and others).
Records the shelf price of products you link, so the stock-up advisor learns sale rhythms faster.
"""
from __future__ import annotations

import time
from datetime import date

import requests

from . import db, units

BASE = "https://api.kroger.com/v1"
_token: dict = {}


class KrogerError(RuntimeError):
    pass


def credentials() -> tuple[str, str]:
    from . import credentials
    return credentials.get("kroger_client_id"), credentials.get("kroger_client_secret")


def enabled() -> bool:
    cid, sec = credentials()
    return db.get_setting("kroger_enabled") == "1" and bool(cid and sec)


def token() -> str:
    if _token.get("value") and _token.get("expires", 0) > time.time() + 30:
        return _token["value"]
    cid, sec = credentials()
    if not (cid and sec):
        raise KrogerError("Add your Kroger client ID and secret in Settings.")
    r = requests.post(f"{BASE}/connect/oauth2/token", auth=(cid, sec),
                      data={"grant_type": "client_credentials", "scope": "product.compact"}, timeout=15)
    if r.status_code >= 400:
        raise KrogerError(f"Kroger sign-in failed ({r.status_code}). Check the client ID and secret.")
    data = r.json()
    _token.update(value=data["access_token"], expires=time.time() + int(data.get("expires_in", 1800)))
    return _token["value"]


def _get(path: str, params: dict) -> dict:
    r = requests.get(f"{BASE}{path}", headers={"Authorization": f"Bearer {token()}", "Accept": "application/json"},
                     params=params, timeout=15)
    if r.status_code >= 400:
        raise KrogerError(f"Kroger returned {r.status_code}: {r.text[:200]}")
    return r.json()


def find_locations(zip_code: str) -> list[dict]:
    data = _get("/locations", {"filter.zipCode.near": zip_code, "filter.limit": 5})
    return [{"id": l["locationId"], "name": l.get("name"), "address": l.get("address", {}).get("addressLine1")}
            for l in data.get("data", [])]


def search(term: str, location_id: str) -> list[dict]:
    data = _get("/products", {"filter.term": term, "filter.locationId": location_id, "filter.limit": 10})
    out = []
    for p in data.get("data", []):
        item = (p.get("items") or [{}])[0]
        price = item.get("price") or {}
        out.append({"product_id": p["productId"], "description": p.get("description"), "brand": p.get("brand"),
                    "size": item.get("size"), "regular": price.get("regular"), "promo": price.get("promo") or None})
    return out


def check_prices() -> list[str]:
    """Record today's price for every linked product at the Kroger store you set."""
    store = db.one("SELECT * FROM stores WHERE kroger_location_id IS NOT NULL AND kroger_location_id != '' "
                   "AND kroger_location_id != 'demo-location' LIMIT 1")
    if not store:
        raise KrogerError("Pick your Kroger store in Settings first.")
    linked = db.q("SELECT * FROM products WHERE kroger_product_id IS NOT NULL AND kroger_product_id != ''")
    log = []
    for _, p in linked.iterrows():
        data = _get(f"/products/{p['kroger_product_id']}", {"filter.locationId": store["kroger_location_id"]})
        item = ((data.get("data") or {}).get("items") or [{}])[0]
        price = item.get("price") or {}
        value = price.get("promo") or price.get("regular")
        if not value:
            log.append(f"{p['name']}: no price today")
            continue
        per = "lb" if p["sold_by_weight"] else "package"
        db.run("INSERT INTO shelf_prices(product_id, store_id, observed_on, price, per, kind, note) VALUES (?,?,?,?,?,?,?)",
               (int(p["id"]), int(store["id"]), date.today().isoformat(), float(value), per, "kroger",
                "Kroger API check" + (" (promo)" if price.get("promo") else "")))
        log.append(f"{p['name']}: {units.fmt_money(float(value))}" + (" (promo)" if price.get("promo") else ""))
    return log


if __name__ == "__main__":     # run daily with Windows Task Scheduler: python -m pricewise.kroger
    db.init_db()
    for line in check_prices():
        print(line)
