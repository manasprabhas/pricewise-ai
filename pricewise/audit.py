"""Checkout problems: checks each receipt for money you may be able to get back.

Three kinds of discrepancy, kept apart because they are proved differently:
- calculation mismatch   the receipt's own numbers disagree (weight x price, count x price, lines vs total).
                          Provable from the receipt alone.
- possible double scan   the same packaged item twice. Only you know whether you took home two.
- price-tag or promotion mismatch
                          you paid more than a shelf tag or offer you RECORDED for that store and day. Needs the
                          recorded observation and its terms (limit, card, minimum quantity), or it isn't claimable.
Separately, "price surprises" (much higher than usual) are not discrepancies: prices do rise.

Every flag starts "open". A refund note only ever includes flags you marked "reviewed".

Checks:
- weight maths      weight x price per lb does not equal the line total
- quantity maths    count x price each does not equal the line total
- double scan       the same packaged item appears twice on one receipt
- missed sale       an ad or shelf price you recorded for that store and day was lower than you paid
- total mismatch    the lines plus tax do not add up to the printed total
- unusual price     much higher than you normally pay for it at that store (ask, it may be a real rise)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from . import analytics, db, money, reconcile, units

CERTAINTY = {"weight_math": "Certain", "qty_math": "Certain", "total_mismatch": "Certain",
             "double_scan": "Likely", "missed_sale": "Likely", "unusual_price": "Worth asking"}
SHORT = {"weight_math": "Weight error", "qty_math": "Quantity error", "double_scan": "Double scan",
         "missed_sale": "Missed sale", "total_mismatch": "Total off", "unusual_price": "High price"}
CATEGORY = {"weight_math": "Calculation mismatch", "qty_math": "Calculation mismatch",
            "total_mismatch": "Calculation mismatch", "double_scan": "Possible double scan",
            "missed_sale": "Price-tag or promotion mismatch", "unusual_price": "Price surprise"}
CATEGORY_HELP = {
    "Calculation mismatch": "The receipt's own numbers disagree. Provable from the receipt alone.",
    "Possible double scan": "The same item rang up twice. Only you know whether you took home two.",
    "Price-tag or promotion mismatch": "You paid more than a shelf tag or offer you recorded that day, with its terms.",
    "Price surprise": "Much higher than you usually pay there. Not an error by itself: prices rise.",
}
STATUS = {"open": "Not reviewed", "reviewed": "Reviewed: real", "refunded": "Refunded", "denied": "Asked, refused",
          "dismissed": "Not a problem"}
TITLES = {"weight_math": "Weight maths doesn't add up", "qty_math": "Quantity maths doesn't add up",
          "double_scan": "Possibly scanned twice", "missed_sale": "Advertised price not applied",
          "total_mismatch": "Receipt total doesn't add up", "unusual_price": "Much higher than usual"}


@dataclass
class Issue:
    key: str
    kind: str
    receipt_id: int
    store: str
    purchased_on: pd.Timestamp
    label: str
    amount: float
    evidence: list[str] = field(default_factory=list)
    item_ids: list[int] = field(default_factory=list)
    status: str = "open"
    shelf_id: int | None = None
    needs_terms: bool = False

    @property
    def category(self) -> str:
        return CATEGORY[self.kind]

    @property
    def claimable(self) -> bool:
        return self.kind != "unusual_price" and not self.needs_terms

    @property
    def title(self) -> str:
        return TITLES[self.kind]

    @property
    def certainty(self) -> str:
        return CERTAINTY[self.kind]


def _statuses() -> dict[str, str]:
    df = db.q("SELECT issue_key, status FROM audit_status")
    return dict(zip(df["issue_key"], df["status"]))


def audit(receipt_ids: list[int] | None = None, pur: pd.DataFrame | None = None) -> list[Issue]:
    pur = db.purchases(include_unverified=True) if pur is None else pur   # check every receipt, trusted or not
    if pur.empty:
        return []
    recs = db.receipts_df().set_index("id")
    target_ids = receipt_ids if receipt_ids is not None else list(recs.index)
    shelf = db.q("SELECT sp.*, p.base_qty, p.sold_by_weight FROM shelf_prices sp JOIN products p ON p.id = sp.product_id "
                 "WHERE sp.kind IN ('ad','shelf','kroger')")
    if not shelf.empty:
        shelf["observed_on"] = pd.to_datetime(shelf["observed_on"])
    statuses = _statuses()
    issues: list[Issue] = []
    adjustments: dict[int, list] = {}
    for _, a in db.q("SELECT * FROM receipt_adjustments").iterrows():
        adjustments.setdefault(int(a["receipt_id"]), []).append(
            reconcile.Adjustment(a["kind"], abs(int(a["cents"])), a["description"] or "", bool(a["after_subtotal"])))

    for rid in target_ids:
        lines = pur[pur["receipt_id"] == rid]
        if lines.empty or rid not in recs.index:
            continue
        r = recs.loc[rid]
        store, when = r["store"], r["purchased_on"]

        def add(kind, label, amount, evidence, item_ids, shelf_id=None, needs_terms=False):
            key = f"{kind}:{rid}:{'-'.join(map(str, item_ids))}"
            issues.append(Issue(key, kind, int(rid), store, when, label, round(amount, 2), evidence, item_ids,
                                statuses.get(key, "open"), shelf_id, needs_terms))

        # weight and quantity maths
        for _, l in lines.iterrows():
            if l["unit_price"] and l["qty"] and l["qty"] != 1:
                expected = round(l["qty"] * l["unit_price"], 2)
                diff = round(l["line_total"] - expected, 2)
                if diff > 0.02:
                    kind = "weight_math" if l["qty_unit"] != "each" else "qty_math"
                    unit = l["qty_unit"] if l["qty_unit"] != "each" else ""
                    add(kind, db.product_label(l), diff,
                        [f"Printed: {l['qty']:g} {unit} @ ${l['unit_price']:.2f}".replace("  ", " "),
                         f"{l['qty']:g} × ${l['unit_price']:.2f} = ${expected:.2f}",
                         f"Charged ${l['line_total']:.2f}, which is ${diff:.2f} too much"], [int(l["item_id"])])

        # double scans of packaged items
        packaged = lines[(lines["sold_by_weight"] == 0) & (lines["qty"] == 1)]
        for pid, grp in packaged.groupby("product_id"):
            if len(grp) >= 2:
                add("double_scan", db.product_label(grp.iloc[0]), float(grp["paid"].iloc[1:].sum()),
                    [f"{len(grp)} identical lines at ${grp['paid'].iloc[0]:.2f} each",
                     "If you only took home one, the extra line is a double scan"],
                    [int(x) for x in grp["item_id"]])

        # missed advertised or shelf prices
        if not shelf.empty:
            day = shelf[(shelf["store_id"] == r["store_id"]) & (shelf["observed_on"] == when)
                        & (shelf["kind"].isin(["ad", "shelf"]))]
            for _, s in day.iterrows():
                for _, l in lines[lines["product_id"] == s["product_id"]].iterrows():
                    advertised = analytics.shelf_unit_cost(s)
                    if advertised is None or not l["unit_cost"]:
                        continue
                    over = (l["unit_cost"] - advertised) * l["base_qty_total"]
                    if over > 0.05:
                        du = l["display_unit"]
                        if l["sold_by_weight"]:
                            ad_txt = units.fmt_unit_price(advertised, du)
                            paid_txt = f"Charged {units.fmt_unit_price(l['unit_cost'], du)} for {l['qty']:g} {l['qty_unit']}"
                        else:
                            ad_txt = f"${advertised * l['base_qty']:.2f} each"
                            paid_txt = f"Charged ${l['paid'] / l['qty']:.2f} each for {l['qty']:g}"
                        terms = (s.get("terms") or "").strip()
                        add("missed_sale", db.product_label(l), over,
                            [f"{s['note'] or 'Recorded price'} ({ad_txt}), recorded {s['observed_on']:%b %d}"
                             + (f" by {s['source']}" if s.get("source") else ""),
                             f"Offer terms: {terms}" if terms else "Offer terms: not recorded",
                             paid_txt, f"Difference: ${over:.2f}"], [int(l["item_id"])],
                            shelf_id=int(s["id"]), needs_terms=not terms)

        # receipt total, with deposits, fees, store-wide coupons and returns counted (integer cents)
        if pd.notna(r["total"]):
            adj = adjustments.get(int(rid), [])
            rec = reconcile.run([(int(x), int(y or 0)) for x, y in zip(lines["line_cents"], lines["discount_cents"])],
                                adj, None, money.cents(r["tax"] if pd.notna(r["tax"]) else 0), money.cents(r["total"]))
            tot = next((c for c in rec.checks if c.name == "Total"), None)
            if tot is not None and tot.expected_cents - tot.actual_cents > 2:
                diff = (tot.expected_cents - tot.actual_cents) / 100
                add("total_mismatch", "Whole receipt", diff, [tot.explain], [])

        # unusual price for this store (skip lines already explained by a maths error)
        flagged = {i for iss in issues if iss.receipt_id == rid for i in iss.item_ids}
        for _, l in lines.iterrows():
            if int(l["item_id"]) in flagged:
                continue
            prior = pur[(pur["product_id"] == l["product_id"]) & (pur["store"] == store)
                        & (pur["purchased_on"] < when)
                        & (pur["purchased_on"] >= when - pd.Timedelta(days=120))]
            if len(prior) >= 3 and l["unit_cost"]:
                usual = prior["unit_cost"].median()
                if l["unit_cost"] > prior["unit_cost"].quantile(0.75) * 1.2:
                    du = l["display_unit"]
                    add("unusual_price", db.product_label(l), (l["unit_cost"] - usual) * l["base_qty_total"],
                        [f"Paid {units.fmt_unit_price(l['unit_cost'], du)}",
                         f"Your usual at {store}: {units.fmt_unit_price(usual, du)} ({len(prior)} purchases)",
                         "Could be a real price rise. Check the shelf tag before asking."], [int(l["item_id"])])

    order = list(CERTAINTY)
    issues.sort(key=lambda i: (i.status != "open", -i.purchased_on.value, order.index(i.kind)))
    return issues


def set_status(issue: Issue, status: str) -> None:
    """Change an issue's status. A refund is recorded exactly once per issue (the savings row is keyed by the
    issue), however many times this is called; moving away from 'refunded' removes it again."""
    key = f"audit:{issue.key}"
    with db.tx() as c:
        c.execute("""INSERT INTO audit_status(issue_key, status, updated_on) VALUES (?,?,?)
                     ON CONFLICT(issue_key) DO UPDATE SET status = excluded.status, updated_on = excluded.updated_on""",
                  (issue.key, status, date.today().isoformat()))
        if status == "refunded":
            c.execute("""INSERT OR IGNORE INTO savings(logged_on, kind, amount, description, receipt_id, source_key)
                         VALUES (?,?,?,?,?,?)""",
                      (date.today().isoformat(), "refund", issue.amount,
                       f"{issue.title}: {issue.label} at {issue.store}", issue.receipt_id, key))
        else:
            c.execute("DELETE FROM savings WHERE source_key = ?", (key,))


def set_terms(shelf_id: int, terms: str) -> None:
    db.run("UPDATE shelf_prices SET terms = ? WHERE id = ?", (terms.strip() or None, shelf_id))


def refund_note(issues: list[Issue]) -> str:
    """Only flags you reviewed and that are claimable. Anything else is left out, whatever is passed in."""
    issues = [i for i in issues if i.status == "reviewed" and i.claimable]
    if not issues:
        return ""
    i0 = issues[0]
    total = sum(i.amount for i in issues)
    lines = [f"Hi, I'd like to ask about my receipt from {i0.store} on {i0.purchased_on:%B %d, %Y}.", ""]
    for n, i in enumerate(issues, 1):
        lines.append(f"{n}. {i.label}: {i.title.lower()} ({i.category.lower()}).")
        lines += [f"   {e}" for e in i.evidence]
    lines += ["", f"Total in question: ${total:.2f}. Could you check these and refund any overcharge? Thank you."]
    return "\n".join(lines)
