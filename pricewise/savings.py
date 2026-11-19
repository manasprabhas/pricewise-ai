"""Savings scoreboard and the recommendation learning loop.

Three kinds of savings are never mixed:
- refund      money a store gave back (you confirmed it)
- documented  advice you followed, checked against what you actually paid afterwards
- estimated   advice you have not acted on yet (what it could save)
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from . import db

EVAL_WINDOW_DAYS = 60


def record(kind: str, product_id: int | None, store_id: int | None, message: str, ref_unit_cost: float | None,
           expected_saving: float) -> int:
    existing = db.one("SELECT id FROM recommendations WHERE message = ? AND status = 'open'", (message,))
    if existing:
        return existing["id"]
    return db.run("""INSERT INTO recommendations(created_on, kind, product_id, store_id, message, ref_unit_cost,
                     expected_saving) VALUES (?,?,?,?,?,?,?)""",
                  (date.today().isoformat(), kind, product_id, store_id, message, ref_unit_cost, expected_saving))


def decide(rec_id: int, status: str) -> None:
    db.run("UPDATE recommendations SET status = ?, decided_on = ? WHERE id = ?",
           (status, date.today().isoformat(), rec_id))


def evaluate(pur: pd.DataFrame | None = None) -> pd.DataFrame:
    """Check followed advice against later purchases and write documented savings."""
    pur = db.purchases() if pur is None else pur
    recs = db.q("SELECT * FROM recommendations WHERE status = 'followed' AND product_id IS NOT NULL")
    for _, r in recs.iterrows():
        start = pd.Timestamp(r["decided_on"] or r["created_on"])
        later = pur[(pur["product_id"] == r["product_id"]) & (pur["purchased_on"] >= start)
                    & (pur["purchased_on"] <= start + pd.Timedelta(days=EVAL_WINDOW_DAYS))]
        if later.empty or not r["ref_unit_cost"]:
            continue
        saved = float(((r["ref_unit_cost"] - later["unit_cost"]) * later["base_qty_total"]).sum())
        db.run("UPDATE recommendations SET outcome_saving = ?, evaluated_on = ? WHERE id = ?",
               (round(saved, 2), date.today().isoformat(), int(r["id"])))
        db.run("DELETE FROM savings WHERE (recommendation_id = ? AND kind = 'documented') OR source_key = ?",
               (int(r["id"]), f"rec:{int(r['id'])}"))
        if saved > 0:
            db.run("INSERT INTO savings(logged_on, kind, amount, description, recommendation_id, source_key) "
                   "VALUES (?,?,?,?,?,'rec:' || ?)",
                   (later["purchased_on"].max().date().isoformat(), "documented", round(saved, 2),
                    f"{r['message']} ({len(later)} purchases checked)", int(r["id"]), int(r["id"])))
    return db.q("SELECT * FROM recommendations ORDER BY created_on DESC")


def scoreboard() -> dict:
    s = db.q("SELECT kind, SUM(amount) AS amount, COUNT(*) AS n FROM savings GROUP BY kind")
    totals = dict(zip(s["kind"], s["amount"]))
    recs = db.q("SELECT * FROM recommendations")
    open_est = float(recs.loc[recs["status"] == "open", "expected_saving"].fillna(0).sum()) if not recs.empty else 0.0
    evaluated = recs[recs["outcome_saving"].notna()] if not recs.empty else recs
    hits = int((evaluated["outcome_saving"] > 0).sum()) if not evaluated.empty else 0
    return {
        "refund": float(totals.get("refund", 0.0)),
        "documented": float(totals.get("documented", 0.0)),
        "estimated": float(totals.get("estimated", 0.0)) + open_est,
        "followed": int((recs["status"] == "followed").sum()) if not recs.empty else 0,
        "ignored": int((recs["status"] == "ignored").sum()) if not recs.empty else 0,
        "open": int((recs["status"] == "open").sum()) if not recs.empty else 0,
        "evaluated": len(evaluated), "hits": hits,
    }


def ledger() -> pd.DataFrame:
    return db.q("SELECT logged_on, kind, amount, description FROM savings ORDER BY logged_on DESC, id DESC")
