"""Pocket: a one-file, offline price checker for your phone, and the laptop side of syncing it back.

make_html() writes a single HTML file with your price book inside it (no server, no network, no account). Open it
on the phone from Downloads or Files; it keeps working in airplane mode. On the phone you look up a product, type
the shelf price on a keypad, and see it against what you last paid and usually pay, with dates. Prices you note
and changes to your shopping list are kept on the phone until you export them as a small .json file, which the
laptop imports here with a preview, skipping anything it has already seen.
"""
from __future__ import annotations

import base64
import json
import uuid
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from . import db, decision as D, money, observations, units

FORMAT = "pricewise-pocket/1"
STALE_DAYS = 120
TEMPLATE = Path(__file__).with_name("pocket_template.html")
FONTS = Path(__file__).resolve().parent.parent / "static" / "fonts"


def _label(p: dict) -> str:
    return db.product_label(p)


def price_book(cutoff: date | None = None) -> dict:
    """Every product with, per store: the last price, the usual (median of the last 120 days) and dates."""
    obs = observations.load(cutoff)
    prods = db.products()
    out = {}
    if obs.empty:
        grouped = {}
    else:
        obs = obs.copy()
        obs["date"] = obs["date"].astype(str).str[:10]
        grouped = {k: g.sort_values(["date", "obs_id"]) for k, g in obs.groupby(["product_id", "store"])}
    today = (cutoff or date.today())
    for p in prods.to_dict("records"):
        pid = int(p["id"])
        stores = {}
        for (gp, store), g in grouped.items():
            if gp != pid:
                continue
            paid = g[g["source"] == "receipt"]
            seen = g[g["source"] != "receipt"]
            base = paid if len(paid) else g                # "last paid" means a receipt whenever there is one
            last = base.iloc[-1]
            cutoff_day = pd.Timestamp(today) - pd.Timedelta(days=STALE_DAYS)
            recent = base[pd.to_datetime(base["date"]) >= cutoff_day]
            usual = money.median_cents(recent["price_cents"].astype(int).tolist()) if len(recent) else None
            row = {"last": int(last["price_cents"]), "last_date": last["date"],
                   "last_source": last["source"], "usual": usual, "n": int(len(base)),
                   "n_recent": int(len(recent)), "low": int(base["price_cents"].min()),
                   "first_date": base.iloc[0]["date"]}
            if len(seen) and len(paid) and seen.iloc[-1]["date"] >= last["date"]:
                tag = seen.iloc[-1]
                row["seen"] = {"cents": int(tag["price_cents"]), "date": tag["date"], "source": tag["source"],
                               "note": None if pd.isna(tag.get("note")) else str(tag.get("note"))}
            stores[store] = row
        out[str(pid)] = {
            "id": pid, "label": _label(p), "brand": p.get("brand") or "", "name": p["name"],
            "variant": p.get("variant") or "", "kind": p["kind"], "category": p["category"],
            "size": "" if p["sold_by_weight"] else units.fmt_size(p.get("size_value"), p.get("size_unit")),
            "weighed": bool(p["sold_by_weight"]),
            "base_qty": float(p["base_qty"]) if pd.notna(p["base_qty"]) and p["base_qty"] else 1.0,
            "dimension": p["dimension"], "display_unit": p["display_unit"],
            "unit_factor": units.base_factor(p["display_unit"]) if p["display_unit"] else 1.0,
            "stores": stores,
        }
    return out


def bundle(list_id: int | None = None, budget_cents: int = 3500, device: str = "phone",
           cutoff: date | None = None) -> dict:
    cut = cutoff or observations.as_day(D.today_cutoff())
    if list_id:
        items = D.load_list(list_id)
        list_name = db.one("SELECT name FROM saved_lists WHERE id = ?", (list_id,))["name"]
    else:
        items = D.infer_basket(D.today_cutoff())
        list_name = "My usual week"
    stores = db.stores()["name"].tolist()
    return {
        "format": FORMAT, "export_id": str(uuid.uuid4()), "device": device,
        "created_at": datetime.now().isoformat(timespec="seconds"), "prices_as_of": cut.isoformat(),
        "stale_days": STALE_DAYS, "budget_cents": int(budget_cents), "stores": stores,
        "list": {"id": list_id, "name": list_name,
                 "items": [{"product_id": int(i["product_id"]), "qty": float(i["qty"])} for i in items]},
        "products": price_book(cut),
    }


def _font(name: str) -> str:
    path = FONTS / name
    return base64.b64encode(path.read_bytes()).decode() if path.exists() else ""


def make_html(b: dict) -> str:
    html = TEMPLATE.read_text(encoding="utf-8")
    data = json.dumps(b, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    return (html.replace("__POCKET_DATA__", data)
                .replace("__FONT_DISPLAY__", _font("BigShouldersDisplay.ttf"))
                .replace("__FONT_BODY__", _font("AtkinsonHyperlegible-Regular.ttf"))
                .replace("__FONT_BODY_BOLD__", _font("AtkinsonHyperlegible-Bold.ttf")))


# ---------------------------------------------------------------- import ------------------------------------------
class PocketError(ValueError):
    pass


def parse_export(raw: bytes | str) -> dict:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise PocketError(f"That isn't a Pocket export (not JSON: {e}).") from e
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise PocketError("That isn't a Pocket export from this version (format "
                          f"{data.get('format') if isinstance(data, dict) else '?'}).")
    for key in ("export_id", "observations", "list_changes"):
        if key not in data:
            raise PocketError(f"The export is missing “{key}”.")
    return data


def preview(data: dict) -> dict:
    """What an import would do, without doing it."""
    seen = db.one("SELECT * FROM pocket_imports WHERE export_id = ?", (data["export_id"],))
    uuids = [o["uuid"] for o in data["observations"]]
    known = set()
    if uuids:
        marks = ",".join("?" * len(uuids))
        known = set(db.q(f"SELECT uuid FROM shelf_prices WHERE uuid IN ({marks})", uuids)["uuid"])
    prods = {int(r["id"]): r for r in db.products(include_merged=True).to_dict("records")}
    rows = []
    for o in data["observations"]:
        p = prods.get(int(o["product_id"]))
        problem = ("unknown product" if p is None else "price must be above $0" if o["price_cents"] <= 0 else "")
        rows.append({"uuid": o["uuid"], "date": o["date"], "store": o["store"],
                     "product": _label(p) if p else f"#{o['product_id']}", "price": o["price_cents"] / 100,
                     "basis": o["basis"] + (f" ({o['multibuy']} for)" if o.get("multibuy", 1) > 1 else ""),
                     "terms": o.get("terms") or "", "seconds": o.get("seconds"),
                     "status": "already imported" if o["uuid"] in known else (problem or "new")})
    return {"already": seen, "observations": pd.DataFrame(rows),
            "new": sum(r["status"] == "new" for r in rows), "skipped": sum(r["status"] != "new" for r in rows),
            "list_changes": data["list_changes"], "final_list": data.get("final_list") or []}


def apply_import(data: dict) -> dict:
    """Save new observations and the trip's final list. Repeat imports and repeat observations change nothing."""
    pv = preview(data)
    new = pv["observations"][pv["observations"]["status"] == "new"] if pv["new"] else pd.DataFrame()
    by_uuid = {o["uuid"]: o for o in data["observations"]}
    added = 0
    with db.tx() as c:
        for u in new["uuid"] if not new.empty else []:
            o = by_uuid[u]
            store_id = c.execute("SELECT id FROM stores WHERE lower(name) = lower(?)", (o["store"],)).fetchone()
            store_id = store_id[0] if store_id else c.execute("INSERT INTO stores(name) VALUES (?)",
                                                              (o["store"].strip(),)).lastrowid
            n = max(int(o.get("multibuy") or 1), 1)
            price = round(o["price_cents"] / 100, 2)
            note = o.get("note") or (f"{n} for ${o['price_cents'] * n / 100:.2f}" if n > 1 else "Shelf tag")
            cur = c.execute("""INSERT OR IGNORE INTO shelf_prices(product_id, store_id, observed_on, price, per, kind,
                               note, terms, uuid, source) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                            (int(o["product_id"]), store_id, o["date"], price,
                             "lb" if o["basis"] == "lb" else "package", "shelf", note, o.get("terms") or None, u,
                             f"pocket:{data.get('device') or 'phone'}"))
            added += cur.rowcount
        list_id = None
        if pv["already"] is None:
            if data.get("final_list"):
                list_id = c.execute("INSERT INTO saved_lists(name, created_at, items) VALUES (?,?,?)",
                                    (f"{data.get('list_name') or 'Pocket list'} (from phone, "
                                     f"{data.get('exported_at', '')[:10]})", datetime.now().isoformat(timespec="seconds"),
                                     D.canonical([{"product_id": int(i["product_id"]), "qty": float(i["qty"])}
                                                  for i in data["final_list"]]))).lastrowid
            c.execute("""INSERT INTO pocket_imports(export_id, device, created_at, imported_on, observations,
                         list_changes, timings) VALUES (?,?,?,?,?,?,?)""",
                      (data["export_id"], data.get("device"), data.get("exported_at") or data.get("created_at"),
                       datetime.now().isoformat(timespec="seconds"), added, len(data["list_changes"]),
                       json.dumps([o.get("seconds") for o in data["observations"]])))
    return {"added": added, "skipped": len(data["observations"]) - added, "list_id": list_id,
            "repeat": pv["already"] is not None}
