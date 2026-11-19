"""SQLite storage. One file on your laptop: data/pricewise.db"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from . import money, units

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "pricewise.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    chain TEXT,
    distance_miles REAL DEFAULT 3,
    kroger_location_id TEXT
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    brand TEXT DEFAULT '',
    kind TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Other',
    size_value REAL,
    size_unit TEXT,
    dimension TEXT NOT NULL,
    base_qty REAL,
    sold_by_weight INTEGER DEFAULT 0,
    display_unit TEXT,
    upc TEXT,
    grams_per_each REAL,
    density_g_ml REAL DEFAULT 1.0,
    shelf_life_days INTEGER,
    freezable INTEGER DEFAULT 0,
    kroger_product_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS nutrition (
    product_id INTEGER PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    protein_g REAL, kcal REAL, fiber_g REAL,
    source TEXT
);
CREATE TABLE IF NOT EXISTS aliases (
    id INTEGER PRIMARY KEY,
    raw_norm TEXT NOT NULL,
    store_id INTEGER REFERENCES stores(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    times_seen INTEGER DEFAULT 1,
    UNIQUE(raw_norm, store_id)
);
CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY,
    store_id INTEGER NOT NULL REFERENCES stores(id),
    purchased_on TEXT NOT NULL,
    subtotal REAL, tax REAL DEFAULT 0, total REAL,
    source TEXT DEFAULT 'manual',
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    raw_text TEXT,
    qty REAL NOT NULL DEFAULT 1,
    qty_unit TEXT NOT NULL DEFAULT 'each',
    unit_price REAL,
    line_total REAL NOT NULL,
    discount REAL DEFAULT 0,
    paid REAL NOT NULL,
    base_qty_total REAL,
    unit_cost REAL,
    confidence REAL DEFAULT 1.0
);
CREATE TABLE IF NOT EXISTS shelf_prices (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    observed_on TEXT NOT NULL,
    price REAL NOT NULL,
    per TEXT DEFAULT 'package',
    kind TEXT DEFAULT 'shelf',
    note TEXT
);
CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY,
    created_on TEXT NOT NULL,
    kind TEXT NOT NULL,
    product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,
    store_id INTEGER REFERENCES stores(id),
    message TEXT,
    ref_unit_cost REAL,
    expected_saving REAL,
    status TEXT DEFAULT 'open',
    decided_on TEXT,
    outcome_saving REAL,
    evaluated_on TEXT
);
CREATE TABLE IF NOT EXISTS savings (
    id INTEGER PRIMARY KEY,
    logged_on TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('refund','documented','estimated')),
    amount REAL NOT NULL,
    description TEXT,
    receipt_id INTEGER REFERENCES receipts(id) ON DELETE SET NULL,
    recommendation_id INTEGER REFERENCES recommendations(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS audit_status (
    issue_key TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    updated_on TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS cpi_cache (
    series_id TEXT, year INTEGER, period TEXT, value REAL,
    PRIMARY KEY (series_id, year, period)
);
CREATE TABLE IF NOT EXISTS imports (hash TEXT PRIMARY KEY, filename TEXT, receipt_id INTEGER, imported_on TEXT);
CREATE TABLE IF NOT EXISTS receipt_adjustments (
    id INTEGER PRIMARY KEY,
    receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,                 -- deposit | fee | store_coupon | return
    cents INTEGER NOT NULL,             -- signed: + adds to the bill, - reduces it
    description TEXT,
    after_subtotal INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS item_provenance (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    field TEXT NOT NULL,
    parsed_value TEXT,
    saved_value TEXT,
    actor TEXT NOT NULL,                -- parser:offline | parser:digital | gemini | matcher:alias | matcher:fuzzy | you
    at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS item_changes (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    field TEXT NOT NULL, old_value TEXT, new_value TEXT, actor TEXT, reason TEXT,
    at TEXT DEFAULT CURRENT_TIMESTAMP, undone INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS product_merges (
    id INTEGER PRIMARY KEY,
    from_product_id INTEGER NOT NULL, into_product_id INTEGER NOT NULL,
    moved TEXT NOT NULL,                -- JSON: item, alias and shelf-price ids moved
    merged_on TEXT, undone_on TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    name TEXT,
    mode TEXT NOT NULL,                 -- current | replay
    cutoff TEXT NOT NULL,               -- only observations dated BEFORE this day were eligible
    trip_receipt_id INTEGER,
    calc_version TEXT NOT NULL,
    inputs TEXT NOT NULL,               -- JSON: basket, settings, eligible observations, product and store facts
    result TEXT NOT NULL,               -- JSON: every plan, the winner, coverage
    input_hash TEXT NOT NULL,
    result_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_sessions (
    id INTEGER PRIMARY KEY,
    receipt_id INTEGER REFERENCES receipts(id) ON DELETE SET NULL,
    source TEXT, started_at TEXT, saved_at TEXT, seconds REAL,
    rows INTEGER, rows_flagged INTEGER, fields_corrected INTEGER
);
CREATE TABLE IF NOT EXISTS pocket_imports (
    export_id TEXT PRIMARY KEY, device TEXT, created_at TEXT, imported_on TEXT,
    observations INTEGER, list_changes INTEGER, timings TEXT
);
CREATE TABLE IF NOT EXISTS saved_lists (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, created_at TEXT, items TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_product ON items(product_id);
CREATE INDEX IF NOT EXISTS idx_items_receipt ON items(receipt_id);
CREATE INDEX IF NOT EXISTS idx_receipts_date ON receipts(purchased_on);
"""

DEFAULT_SETTINGS = {
    "currency": "USD",
    "alert_threshold_pct": "15",
    "min_history": "3",
    "cost_per_mile": "0.25",
    "value_of_time_per_hour": "0",
    "minutes_per_stop": "15",
    "avg_speed_mph": "25",
    "gemini_enabled": "0",
    "gemini_model": "gemini-flash-latest",
    "kroger_enabled": "0",
    "household_size": "2",
}


DEMO_DB = ROOT / "data" / "demo.db"
PROFILE_FILE = ROOT / "data" / "active_profile.txt"


def profile() -> str:
    """'mine' (your data, data/pricewise.db) or 'demo' (fictional, data/demo.db). The demo never touches yours."""
    if os.environ.get("PRICEWISE_DB"):
        return "test"
    try:
        return "demo" if PROFILE_FILE.read_text().strip() == "demo" else "mine"
    except OSError:
        return "mine"


def set_profile(name: str) -> None:
    PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_FILE.write_text("demo" if name == "demo" else "mine")


def db_path() -> Path:
    if os.environ.get("PRICEWISE_DB"):
        return Path(os.environ["PRICEWISE_DB"])
    return DEMO_DB if profile() == "demo" else DEFAULT_DB


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, detect_types=0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _add_column(c, table: str, column: str, decl: str) -> None:
    if column not in {r[1] for r in c.execute(f"PRAGMA table_info({table})")}:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init_db() -> None:
    with tx() as c:
        c.executescript(SCHEMA)
        # upgrades for databases made by earlier versions
        _add_column(c, "receipts", "fingerprint", "TEXT")
        _add_column(c, "receipts", "status", "TEXT DEFAULT 'manual'")
        _add_column(c, "savings", "source_key", "TEXT")
        for col in ("subtotal_cents", "tax_cents", "total_cents"):
            _add_column(c, "receipts", col, "INTEGER")
        for col, decl in (("source_ref", "TEXT"), ("source_text", "TEXT"), ("source_file", "TEXT"),
                          ("reconciliation", "TEXT")):
            _add_column(c, "receipts", col, decl)
        for col in ("line_cents", "discount_cents", "paid_cents"):
            _add_column(c, "items", col, "INTEGER")
        for col, decl in (("match_method", "TEXT"), ("match_score", "REAL"), ("confirmed", "INTEGER DEFAULT 0")):
            _add_column(c, "items", col, decl)
        _add_column(c, "products", "variant", "TEXT DEFAULT ''")
        _add_column(c, "products", "merged_into", "INTEGER")
        for col, decl in (("uuid", "TEXT"), ("terms", "TEXT"), ("source", "TEXT")):
            _add_column(c, "shelf_prices", col, decl)
        # money in integer cents (older databases stored dollars only)
        c.execute("UPDATE receipts SET subtotal_cents = CAST(ROUND(subtotal * 100) AS INTEGER) "
                  "WHERE subtotal_cents IS NULL AND subtotal IS NOT NULL")
        c.execute("UPDATE receipts SET tax_cents = CAST(ROUND(COALESCE(tax, 0) * 100) AS INTEGER) WHERE tax_cents IS NULL")
        c.execute("UPDATE receipts SET total_cents = CAST(ROUND(total * 100) AS INTEGER) "
                  "WHERE total_cents IS NULL AND total IS NOT NULL")
        c.execute("UPDATE items SET line_cents = CAST(ROUND(line_total * 100) AS INTEGER), "
                  "discount_cents = CAST(ROUND(COALESCE(discount, 0) * 100) AS INTEGER), "
                  "paid_cents = CAST(ROUND(paid * 100) AS INTEGER) WHERE line_cents IS NULL")
        c.execute("UPDATE items SET match_method = 'legacy' WHERE match_method IS NULL")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_shelf_uuid ON shelf_prices(uuid)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_receipts_fp ON receipts(fingerprint)")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_savings_source ON savings(source_key)")
        for k, v in DEFAULT_SETTINGS.items():
            c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))


def q(sql: str, params: tuple | list = ()) -> pd.DataFrame:
    conn = connect()
    try:
        return pd.read_sql_query(sql, conn, params=list(params))
    finally:
        conn.close()


def one(sql: str, params: tuple | list = ()):
    conn = connect()
    try:
        row = conn.execute(sql, list(params)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def run(sql: str, params: tuple | list = ()) -> int:
    with tx() as c:
        cur = c.execute(sql, list(params))
        return cur.lastrowid


# ---------- settings ----------
def get_setting(key: str, default: str | None = None) -> str | None:
    try:
        row = one("SELECT value FROM settings WHERE key = ?", (key,))
    except sqlite3.OperationalError:      # database not created yet
        row = None
    if row is None:
        return DEFAULT_SETTINGS.get(key, default)
    return row["value"]


def get_float(key: str, default: float = 0.0) -> float:
    try:
        return float(get_setting(key, str(default)))
    except (TypeError, ValueError):
        return default


def set_setting(key: str, value) -> None:
    run("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)))


# ---------- stores ----------
def get_or_create_store(name: str, distance_miles: float | None = None) -> int:
    name = name.strip()
    row = one("SELECT id FROM stores WHERE lower(name) = lower(?)", (name,))
    if row:
        return row["id"]
    return run("INSERT INTO stores(name, chain, distance_miles) VALUES (?, ?, ?)",
               (name, name.split()[0], distance_miles if distance_miles is not None else 3.0))


def stores() -> pd.DataFrame:
    return q("SELECT * FROM stores ORDER BY name")


# ---------- products ----------
def add_product(name: str, kind: str, category: str, size_value: float | None, size_unit: str | None,
                brand: str = "", sold_by_weight: bool = False, upc: str | None = None,
                grams_per_each: float | None = None, density_g_ml: float = 1.0,
                shelf_life_days: int | None = None, freezable: bool = False,
                display_unit: str | None = None, variant: str | None = None) -> int:
    """Brand, name, variant (lactose free, organic, 2%...) and package size are separate fields.
    If no variant is given it is read from the name's distinguishing words."""
    if variant is None:
        variant = detect_variant(f"{brand} {name}")
    if sold_by_weight:
        dimension, base_qty = "weight", None
        unit_key = units.normalize_unit(size_unit or "lb") or "lb"
        size_value, size_unit = None, unit_key
    else:
        unit_key = units.normalize_unit(size_unit or "each") or "each"
        dimension, base_qty = units.to_base(size_value or 1, unit_key)
        size_unit = unit_key
    if display_unit is None:
        display_unit = units.default_display_unit(dimension, base_qty, sold_by_weight)
    return run(
        """INSERT INTO products(name, brand, kind, category, size_value, size_unit, dimension, base_qty,
           sold_by_weight, display_unit, upc, grams_per_each, density_g_ml, shelf_life_days, freezable, variant)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (name.strip(), (brand or "").strip(), kind.strip().lower(), category, size_value, size_unit, dimension,
         base_qty, int(sold_by_weight), display_unit, upc, grams_per_each, density_g_ml, shelf_life_days,
         int(freezable), (variant or "").strip().lower()),
    )


def detect_variant(text: str) -> str:
    from .matcher import ATTRIBUTES, normalize
    t = f" {normalize(text)} "
    found = [a.lower() for a in ATTRIBUTES if f" {a} " in t and a not in ("HALF GALLON", "BONELESS", "SKINLESS")]
    return ", ".join(found)


def products(include_merged: bool = False) -> pd.DataFrame:
    """Products you can pick. Products merged into another are hidden (but kept, so merges can be undone)."""
    return q(f"""SELECT p.*, n.protein_g, n.kcal, n.fiber_g, n.source AS nutrition_source
                FROM products p LEFT JOIN nutrition n ON n.product_id = p.id
                {'' if include_merged else 'WHERE p.merged_into IS NULL'} ORDER BY p.category, p.name""")


def product(pid: int) -> dict | None:
    return one("SELECT * FROM products WHERE id = ?", (pid,))


def product_label(p: dict | pd.Series) -> str:
    brand = (p.get("brand") or "").strip()
    size = "by the lb" if p.get("sold_by_weight") else units.fmt_size(p.get("size_value"), p.get("size_unit"))
    head = f"{brand} {p.get('name') or '?'}".strip()
    return f"{head} ({size})" if size else head


def set_nutrition(pid: int, protein_g: float, kcal: float, fiber_g: float, source: str) -> None:
    run("""INSERT INTO nutrition(product_id, protein_g, kcal, fiber_g, source) VALUES (?,?,?,?,?)
           ON CONFLICT(product_id) DO UPDATE SET protein_g=excluded.protein_g, kcal=excluded.kcal,
           fiber_g=excluded.fiber_g, source=excluded.source""", (pid, protein_g, kcal, fiber_g, source))


# ---------- receipts ----------
def compute_item(prod: dict, qty: float, qty_unit: str, line_total: float, discount: float = 0.0) -> dict:
    """Work out base quantity and unit cost for one receipt line."""
    paid = round(float(line_total) - float(discount or 0), 2)
    if prod["sold_by_weight"]:
        unit = units.normalize_unit(qty_unit) or "lb"
        if units.dimension_of(unit) != "weight":
            unit = "lb"
        _, base_total = units.to_base(qty, unit)
    else:
        base_total = float(qty) * float(prod["base_qty"] or 1)
    unit_cost = paid / base_total if base_total else None
    return {"paid": paid, "base_qty_total": base_total, "unit_cost": unit_cost}


class DuplicateReceipt(Exception):
    """The same purchase is already saved (same store, date, products, amounts and total)."""

    def __init__(self, existing_id: int):
        super().__init__(f"This receipt is already saved (receipt #{existing_id}).")
        self.existing_id = existing_id


RECEIPT_STATUSES = {
    "reconciled": "Lines match the printed total",
    "manual": "Typed in by you (no printed total to check)",
    "unverified": "Lines don't match the printed total; kept out of advice",
}


def receipt_fingerprint(store_id: int, purchased_on: str, lines: list[dict], total_cents: int,
                        adjustments: list | None = None) -> str:
    """One identity for a purchase, whatever it was imported from (photo, email, PDF, CSV, typed)."""
    parts = sorted(f"{int(l['product_id'])}:{money.cents(l['line_total'])}:{money.cents(l.get('discount') or 0)}"
                   for l in lines)
    adj = sorted(f"{a.kind}:{a.signed}" for a in (adjustments or []))
    raw = f"{int(store_id)}|{purchased_on}|{int(total_cents)}|" + ",".join(parts) + "|" + ",".join(adj)
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def find_duplicate(store_id: int, purchased_on: str, lines: list[dict], total: float) -> int | None:
    fp = receipt_fingerprint(store_id, purchased_on, lines, money.cents(total))
    row = one("SELECT id FROM receipts WHERE fingerprint = ? ORDER BY id LIMIT 1", (fp,))
    return row["id"] if row else None


def similar_receipts(store_id: int, purchased_on: str, total: float) -> pd.DataFrame:
    """Receipts from the same store and day with the same total: likely the same trip read differently."""
    return q("SELECT id, source, total FROM receipts WHERE store_id = ? AND purchased_on = ? AND total_cents = ?",
             (store_id, purchased_on, money.cents(total)))


PROVENANCE_FIELDS = ("product_id", "qty", "qty_unit", "unit_price", "line_total", "discount")


def save_receipt(store_id: int, purchased_on: str | date, lines: list[dict], subtotal: float | None = None,
                 tax: float = 0.0, total: float | None = None, source: str = "manual",
                 notes: str | None = None, allow_duplicate: bool = False, status: str | None = None,
                 adjustments: list | None = None, source_ref: str | None = None, source_text: str | None = None,
                 source_file: str | None = None) -> int:
    """Save one purchase. Every amount is stored in integer cents (and mirrored in dollars for display).

    lines: dicts with product_id, qty, qty_unit, unit_price, line_total, discount, raw_text, confidence and,
      optionally, match_method, match_score, confirmed, and `parsed` (the values the reader produced, for
      provenance) and `actor` (who supplied the line: parser:offline, parser:digital, gemini, you).
    adjustments: reconcile.Adjustment list (deposits, fees, store-wide coupons, returns).
    Pass the PRINTED subtotal/total when there is one: the receipt is then marked reconciled or unverified.
    Raises DuplicateReceipt if the identical purchase is already saved (unless allow_duplicate)."""
    from . import reconcile
    if isinstance(purchased_on, (date, datetime)):
        purchased_on = purchased_on.isoformat()[:10]
    adjustments = adjustments or []
    item_cents = [(money.cents(l["line_total"]), money.cents(l.get("discount") or 0)) for l in lines]
    sub_c = money.cents(subtotal) if subtotal is not None else None
    tax_c = money.cents(tax or 0)
    tot_c = money.cents(total) if total is not None else None
    rec = reconcile.run(item_cents, adjustments, sub_c, tax_c, tot_c)
    if status is None:
        status = {"reconciled": "reconciled", "unverified": "unverified", "no_total": "manual"}[rec.status]
    if sub_c is None:
        sub_c = rec.computed_subtotal_cents
    if tot_c is None:
        tot_c = sub_c + tax_c + rec.adjustments_after_cents
    fp = receipt_fingerprint(store_id, purchased_on, lines, tot_c, adjustments)
    with tx() as c:
        existing = c.execute("SELECT id FROM receipts WHERE fingerprint = ? ORDER BY id LIMIT 1", (fp,)).fetchone()
        if existing and not allow_duplicate:
            raise DuplicateReceipt(existing["id"])
        rid = c.execute(
            """INSERT INTO receipts(store_id, purchased_on, subtotal, tax, total, source, notes, fingerprint, status,
               subtotal_cents, tax_cents, total_cents, source_ref, source_text, source_file, reconciliation)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (store_id, purchased_on, money.dollars(sub_c), money.dollars(tax_c), money.dollars(tot_c), source, notes,
             fp, status, sub_c, tax_c, tot_c, source_ref, source_text, source_file,
             json.dumps(rec.to_dict()))).lastrowid
        for a in adjustments:
            c.execute("INSERT INTO receipt_adjustments(receipt_id, kind, cents, description, after_subtotal) "
                      "VALUES (?,?,?,?,?)", (rid, a.kind, a.signed, a.description, int(a.after_subtotal)))
        for l, (lc, dc) in zip(lines, item_cents):
            prod = dict(c.execute("SELECT * FROM products WHERE id = ?", (int(l["product_id"]),)).fetchone())
            calc = compute_item(prod, float(l.get("qty") or 1), l.get("qty_unit") or "each", lc / 100, dc / 100)
            iid = c.execute(
                """INSERT INTO items(receipt_id, product_id, raw_text, qty, qty_unit, unit_price, line_total, discount,
                   paid, base_qty_total, unit_cost, confidence, line_cents, discount_cents, paid_cents,
                   match_method, match_score, confirmed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (rid, int(l["product_id"]), l.get("raw_text"), float(l.get("qty") or 1), l.get("qty_unit") or "each",
                 l.get("unit_price"), lc / 100, dc / 100, (lc - dc) / 100, calc["base_qty_total"], calc["unit_cost"],
                 float(l.get("confidence", 1.0)), lc, dc, lc - dc, l.get("match_method") or "manual",
                 l.get("match_score"), int(bool(l.get("confirmed", l.get("match_method") in (None, "manual", "you")))))
            ).lastrowid
            parsed = l.get("parsed")
            if parsed is not None:
                actor = l.get("actor") or "parser"
                for f in PROVENANCE_FIELDS:
                    pv, sv = parsed.get(f), l.get(f)
                    if f == "product_id":
                        who = "you" if l.get("confirmed") else f"matcher:{l.get('match_method') or 'fuzzy'}"
                    else:
                        who = actor if _same(pv, sv) else "you"
                    c.execute("INSERT INTO item_provenance(item_id, field, parsed_value, saved_value, actor) "
                              "VALUES (?,?,?,?,?)", (iid, f, None if pv is None else str(pv),
                                                     None if sv is None else str(sv), who))
    return rid


def _same(a, b) -> bool:
    if a is None and b is None:
        return True
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return str(a) == str(b)


def delete_receipt(rid: int) -> None:
    run("DELETE FROM receipts WHERE id = ?", (rid,))


def purchases(include_unverified: bool = False) -> pd.DataFrame:
    """One row per purchased line, with product, store and date.

    Receipts whose lines don't match their printed total are left out unless include_unverified=True,
    so no advice, comparison or saving is ever based on them."""
    df = q("""
        SELECT i.id AS item_id, i.receipt_id, r.purchased_on, COALESCE(r.status, 'manual') AS receipt_status, s.id AS store_id, s.name AS store,
               p.id AS product_id, p.name, p.brand, p.kind, p.category, p.size_value, p.size_unit, p.dimension,
               p.base_qty, p.sold_by_weight, p.display_unit, p.shelf_life_days, p.freezable,
               i.raw_text, i.qty, i.qty_unit, i.unit_price, i.line_total, i.discount, i.paid,
               i.base_qty_total, i.unit_cost, i.confidence, i.line_cents, i.discount_cents, i.paid_cents,
               i.match_method, i.match_score, i.confirmed, p.variant
        FROM items i JOIN receipts r ON r.id = i.receipt_id
        JOIN stores s ON s.id = r.store_id JOIN products p ON p.id = i.product_id
        WHERE ? OR COALESCE(r.status, 'manual') != 'unverified'
        ORDER BY r.purchased_on, i.id""", (int(include_unverified),))
    if not df.empty:
        df["purchased_on"] = pd.to_datetime(df["purchased_on"])
        df["month"] = df["purchased_on"].dt.to_period("M").astype(str)
    return df


def receipts_df() -> pd.DataFrame:
    df = q("""SELECT r.*, s.name AS store, COUNT(i.id) AS n_items
              FROM receipts r JOIN stores s ON s.id = r.store_id LEFT JOIN items i ON i.receipt_id = r.id
              GROUP BY r.id ORDER BY r.purchased_on DESC, r.id DESC""")
    if not df.empty:
        df["purchased_on"] = pd.to_datetime(df["purchased_on"])
    return df


def wipe_all() -> None:
    with tx() as c:
        for t in ["savings", "recommendations", "audit_status", "shelf_prices", "item_provenance", "item_changes",
                  "receipt_adjustments", "decisions", "review_sessions", "pocket_imports", "saved_lists",
                  "product_merges", "items", "receipts", "aliases", "nutrition", "products", "stores", "cpi_cache",
                  "imports"]:
            c.execute(f"DELETE FROM {t}")


def is_empty() -> bool:
    row = one("SELECT COUNT(*) AS n FROM receipts")
    return not row or row["n"] == 0


def export_tables() -> dict[str, pd.DataFrame]:
    names = ["stores", "products", "nutrition", "aliases", "receipts", "items", "shelf_prices",
             "recommendations", "savings", "settings"]
    return {n: q(f"SELECT * FROM {n}") for n in names}


def to_json(obj) -> str:
    return json.dumps(obj, default=str)
