"""Product identity: catching wrong matches, fixing them, and undoing fixes.

- size_mismatch / variant_mismatch compare what the RECEIPT says with the product a line was saved as.
- reassign_item moves a line to another product, recomputes its unit price, and logs the change (undoable).
- merge_products folds a duplicate product into another, recording every moved row so the merge can be undone.
- review_queue lists lines worth a second look: unconfirmed fuzzy matches and lines whose receipt text disagrees
  with their product's size or variant.
"""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd

from . import db, matcher, money, units


def size_mismatch(raw_text: str | None, prod: dict) -> str | None:
    """'Receipt says 18 oz; product is 15.4 oz' when the printed package size disagrees (2% tolerance)."""
    if not raw_text or prod.get("sold_by_weight") or not prod.get("base_qty"):
        return None
    if "@" in raw_text and units.parse_size(raw_text.split("@")[0]) and "LB" in raw_text.upper():
        return None                                  # a weighed line, not a package size
    found = units.parse_size(raw_text)
    if not found:
        return None
    try:
        dim, base = units.to_base(*found)
    except ValueError:
        return None
    if dim != prod["dimension"]:
        return None
    if abs(base - float(prod["base_qty"])) / float(prod["base_qty"]) <= 0.02:
        return None
    return (f"Receipt says {units.fmt_size(*found)}; product is "
            f"{units.fmt_size(prod.get('size_value'), prod.get('size_unit'))}")


# Words that change what a product IS for a shopper with requirements. Only checked when spelled out in full
# on the receipt, because abbreviations are ambiguous (“LF” is lactose free at one store, L'oven Fresh at another).
REQUIREMENT_WORDS = ["LACTOSE FREE", "ORGANIC", "GLUTEN FREE", "DECAF", "SUGAR FREE", "UNSWEETENED", "NONFAT",
                     "LOW SODIUM", "UNSALTED", "DIET"]


def variant_mismatch(raw_text: str | None, prod: dict) -> str | None:
    if not raw_text:
        return None
    up = f" {' '.join(raw_text.upper().replace('-', ' ').split())} "
    prod_text = f" {' '.join(str(prod.get(k) or '') for k in ('brand', 'name', 'variant')).upper()} "
    for w in REQUIREMENT_WORDS:
        if f" {w} " in up and f" {w} " not in prod_text:
            return f"Receipt says “{w.lower()}”; the product doesn't"
    return None


def line_flags(raw_text: str | None, prod: dict) -> list[str]:
    return [m for m in (size_mismatch(raw_text, prod), variant_mismatch(raw_text, prod)) if m]


def _recompute(c, item_id: int) -> None:
    it = dict(c.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())
    prod = dict(c.execute("SELECT * FROM products WHERE id = ?", (it["product_id"],)).fetchone())
    calc = db.compute_item(prod, float(it["qty"] or 1), it["qty_unit"] or "each", it["line_cents"] / 100,
                           (it["discount_cents"] or 0) / 100)
    c.execute("UPDATE items SET base_qty_total = ?, unit_cost = ? WHERE id = ?",
              (calc["base_qty_total"], calc["unit_cost"], item_id))


def unit_price_text(item_id: int) -> str:
    r = db.one("""SELECT i.unit_cost, p.display_unit FROM items i JOIN products p ON p.id = i.product_id
                  WHERE i.id = ?""", (item_id,))
    return units.fmt_unit_price(r["unit_cost"], r["display_unit"])


def reassign_item(item_id: int, new_pid: int, reason: str = "", actor: str = "you", relearn: bool = True) -> int:
    """Move one receipt line to another product. Logged in item_changes, so it can be undone."""
    with db.tx() as c:
        it = dict(c.execute("SELECT i.*, r.store_id FROM items i JOIN receipts r ON r.id = i.receipt_id "
                            "WHERE i.id = ?", (item_id,)).fetchone())
        cid = c.execute("INSERT INTO item_changes(item_id, field, old_value, new_value, actor, reason) "
                        "VALUES (?,?,?,?,?,?)", (item_id, "product_id", str(it["product_id"]), str(new_pid),
                                                 actor, reason)).lastrowid
        c.execute("UPDATE items SET product_id = ?, confirmed = 1, match_method = 'you' WHERE id = ?",
                  (new_pid, item_id))
        _recompute(c, item_id)
    if relearn and it.get("raw_text"):
        matcher.learn(it["raw_text"], it["store_id"], new_pid)
    return cid


def confirm_item(item_id: int) -> None:
    with db.tx() as c:
        c.execute("UPDATE items SET confirmed = 1 WHERE id = ?", (item_id,))
        c.execute("INSERT INTO item_changes(item_id, field, old_value, new_value, actor, reason) "
                  "VALUES (?,?,?,?,?,?)", (item_id, "confirmed", "0", "1", "you", "checked the match"))


def undo_change(change_id: int) -> None:
    with db.tx() as c:
        ch = dict(c.execute("SELECT * FROM item_changes WHERE id = ?", (change_id,)).fetchone())
        if ch["undone"]:
            return
        if ch["field"] == "product_id":
            c.execute("UPDATE items SET product_id = ? WHERE id = ?", (int(ch["old_value"]), ch["item_id"]))
            _recompute(c, ch["item_id"])
        elif ch["field"] == "confirmed":
            c.execute("UPDATE items SET confirmed = ? WHERE id = ?", (int(ch["old_value"]), ch["item_id"]))
        c.execute("UPDATE item_changes SET undone = 1 WHERE id = ?", (change_id,))


def changes(limit: int = 50) -> pd.DataFrame:
    return db.q("""SELECT ch.*, i.raw_text FROM item_changes ch LEFT JOIN items i ON i.id = ch.item_id
                   ORDER BY ch.id DESC LIMIT ?""", (limit,))


def merge_products(from_pid: int, into_pid: int) -> int:
    """Fold `from_pid` into `into_pid`. Every moved row is recorded, so undo_merge restores exactly."""
    if from_pid == into_pid:
        raise ValueError("Pick two different products.")
    with db.tx() as c:
        items = [r[0] for r in c.execute("SELECT id FROM items WHERE product_id = ?", (from_pid,))]
        aliases = [r[0] for r in c.execute("SELECT id FROM aliases WHERE product_id = ?", (from_pid,))]
        shelf = [r[0] for r in c.execute("SELECT id FROM shelf_prices WHERE product_id = ?", (from_pid,))]
        mid = c.execute("INSERT INTO product_merges(from_product_id, into_product_id, moved, merged_on) "
                        "VALUES (?,?,?,?)", (from_pid, into_pid, json.dumps({"items": items, "aliases": aliases,
                                                                              "shelf": shelf}),
                                            datetime.now().isoformat(timespec="seconds"))).lastrowid
        c.execute("UPDATE items SET product_id = ? WHERE product_id = ?", (into_pid, from_pid))
        c.execute("UPDATE OR IGNORE aliases SET product_id = ? WHERE product_id = ?", (into_pid, from_pid))
        c.execute("UPDATE shelf_prices SET product_id = ? WHERE product_id = ?", (into_pid, from_pid))
        c.execute("UPDATE products SET merged_into = ? WHERE id = ?", (into_pid, from_pid))
        for iid in items:
            _recompute(c, iid)
    return mid


def undo_merge(merge_id: int) -> None:
    with db.tx() as c:
        m = dict(c.execute("SELECT * FROM product_merges WHERE id = ?", (merge_id,)).fetchone())
        if m["undone_on"]:
            return
        moved = json.loads(m["moved"])
        for iid in moved["items"]:
            c.execute("UPDATE items SET product_id = ? WHERE id = ?", (m["from_product_id"], iid))
            _recompute(c, iid)
        for aid in moved["aliases"]:
            c.execute("UPDATE aliases SET product_id = ? WHERE id = ?", (m["from_product_id"], aid))
        for sid in moved["shelf"]:
            c.execute("UPDATE shelf_prices SET product_id = ? WHERE id = ?", (m["from_product_id"], sid))
        c.execute("UPDATE products SET merged_into = NULL WHERE id = ?", (m["from_product_id"],))
        c.execute("UPDATE product_merges SET undone_on = ? WHERE id = ?",
                  (datetime.now().isoformat(timespec="seconds"), merge_id))


def merge_warnings(from_pid: int, into_pid: int) -> list[str]:
    a, b = db.product(from_pid), db.product(into_pid)
    out = []
    if (a["base_qty"] or 0) and (b["base_qty"] or 0) and abs(a["base_qty"] - b["base_qty"]) / b["base_qty"] > 0.02:
        out.append(f"Different package sizes ({units.fmt_size(a['size_value'], a['size_unit'])} vs "
                   f"{units.fmt_size(b['size_value'], b['size_unit'])}): prices per package will mix.")
    if (a.get("variant") or "") != (b.get("variant") or ""):
        out.append(f"Different variants (“{a.get('variant') or 'regular'}” vs “{b.get('variant') or 'regular'}”).")
    if a["kind"] != b["kind"]:
        out.append(f"Different kinds of food ({a['kind']} vs {b['kind']}).")
    if bool(a["sold_by_weight"]) != bool(b["sold_by_weight"]):
        out.append("One is sold by weight and the other by the package.")
    return out


def review_queue() -> pd.DataFrame:
    """Lines worth a second look, most important first."""
    df = db.q("""SELECT i.id AS item_id, i.receipt_id, r.purchased_on, s.name AS store, i.raw_text, i.product_id,
                        i.match_method, i.match_score, i.confirmed, i.paid_cents, i.qty, i.qty_unit,
                        p.name, p.brand, p.variant, p.size_value, p.size_unit, p.base_qty, p.dimension,
                        p.sold_by_weight, p.display_unit, i.unit_cost
                 FROM items i JOIN receipts r ON r.id = i.receipt_id JOIN stores s ON s.id = r.store_id
                 JOIN products p ON p.id = i.product_id ORDER BY r.purchased_on DESC, i.id""")
    rows = []
    for r in df.to_dict("records"):
        flags = line_flags(r["raw_text"], r)
        weak = (r["match_method"] in ("fuzzy", "alias", "import") and not r["confirmed"]
                and (r["match_score"] or 0) < 95)
        if not flags and not weak:
            continue
        reason = "; ".join(flags) if flags else f"Matched automatically ({(r['match_score'] or 0):.0f}%), not yet checked"
        rows.append({**r, "label": db.product_label(r), "reason": reason, "severity": 0 if flags else 1,
                     "unit_price": units.fmt_unit_price(r["unit_cost"], r["display_unit"])})
    out = pd.DataFrame(rows)
    return out.sort_values(["severity", "purchased_on"], ascending=[True, False]) if not out.empty else out
