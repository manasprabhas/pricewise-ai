"""A one-page, self-contained decision summary to share (HTML; print it to PDF from any browser).

Redacted by default: no receipt numbers, observation ids, store distances or location ids, receipt text, notes,
or anything about you. Store chain names, dates and prices stay, because they ARE the evidence.
"""
from __future__ import annotations

import html
from datetime import date

from . import money


def _e(x) -> str:
    return html.escape(str(x))


def render(decision: dict, outcome: dict | None = None, redact: bool = True) -> str:
    inp, res = decision["inputs"], decision["result"]
    s = inp["settings"]
    winner = next((p for p in res["complete"] if p["name"] == res["winner"]), None)
    runner = next((p for p in res["complete"] if p["name"] != res["winner"]), None)
    title = decision.get("name") or "Grocery decision"
    mode = ("Replay of a past trip, using only prices known before " + inp["cutoff"]
            if decision.get("mode") == "replay" else "Plan made with prices known before " + inp["cutoff"])

    def plan_table(p: dict) -> str:
        rows = "".join(
            f"<tr><td>{_e(l['item'])}</td><td>{_e(l['store'])}</td>"
            f"<td>{_e(l['label']) if not l['exact'] else 'same product'}</td>"
            f"<td class=n>{l['qty']:g} {l['unit']}</td><td class=n>{money.fmt(l['cost_cents'])}</td>"
            f"<td>{_e(l['estimate']['method'])} of {l['estimate']['n']} price(s), newest {_e(l['estimate']['newest'])}"
            f"{' (limited history)' if l['estimate']['limited'] else ''}</td></tr>" for l in p["lines"])
        miss = "".join(f"<tr class=miss><td>{_e(m['item'])}</td><td colspan=5>not priced: {_e('; '.join(m['reasons']))}"
                       f"</td></tr>" for m in p.get("missing", []))
        return (f"<table><tr><th>Item</th><th>Store</th><th>Bought as</th><th>Qty</th><th>Cost</th>"
                f"<th>Evidence</th></tr>{rows}{miss}</table>")

    parts = [f"<h1>{_e(title)}</h1><p class=sub>{_e(mode)}. Calculation {_e(inp['calc_version'])}, "
             f"fingerprint {_e(decision.get('result_hash', ''))}.</p>"]
    parts.append("<h2>The question</h2><p>Where should this basket be bought, and is a second store worth it? "
                 f"{len(inp['basket'])} items.</p>")
    if winner:
        cash_line = (f"Checkout estimate {money.fmt(winner['checkout_cents'])}"
                     + (f" + tax {money.fmt(winner['tax_cents'])}" if winner["tax_known"] else " (tax unknown)")
                     + (f"; budget {money.fmt(s.get('budget_cents'))}: "
                        f"{'fits' if winner['fits_budget'] else 'over'}" if s.get("budget_cents") else ""))
        parts.append(f"<h2>The plan: {_e(winner['name'])}</h2><p>{cash_line}. Trip cost "
                     f"{money.fmt(winner['trip_cents'])} ({winner['extra_stops']} extra stop(s)). Decision cost "
                     f"<b>{money.fmt(winner['decision_cents'])}</b>.</p>" + plan_table(winner))
        if runner:
            parts.append(f"<p>Runner-up: {_e(runner['name'])} at {money.fmt(runner['decision_cents'])} "
                         f"({money.fmt(runner['decision_cents'] - winner['decision_cents'], signed=True)}).</p>")
    else:
        parts.append("<h2>No complete plan</h2><p>No store combination had prices for every item.</p>")
    partial = res.get("partial", [])[:3]
    if partial:
        parts.append("<p>Partial plans (not ranked): " + "; ".join(
            f"{_e(p['name'])} {money.fmt(p['checkout_cents'])} for {p['covered']} of {p['total_items']} items"
            for p in partial) + ".</p>")
    parts.append("<h2>Assumptions</h2><ul>"
                 f"<li>Price estimate: {_e('last eligible price' if s['method'] == 'last' else 'median of eligible prices')}"
                 f", prices up to {s['max_age_days']} days old.</li>"
                 f"<li>Each extra store costs {money.fmt(int(s['extra_stop_cents']))} (driving and time).</li>"
                 "<li>Only prices observed before the decision date were used.</li>"
                 "<li>Prices are what was paid or seen earlier, not live shelf prices.</li></ul>")
    if outcome:
        rows = "".join(
            f"<tr class={r['type']}><td>{_e(r['type'])}</td><td>{_e(r['item'] or r['bought'])}</td>"
            f"<td class=n>{money.fmt(r['predicted_cents'])}</td><td class=n>{money.fmt(r['actual_cents'])}</td>"
            f"<td class=n>{money.fmt(r['error_cents'], signed=True) if r['error_cents'] is not None else '—'}</td>"
            f"<td>{_e(r['note'])}</td></tr>" for r in outcome["rows"])
        obs = "".join(f"<li>{_e(o['item'])}: {_e(o['other_store'])} had it at {money.fmt(o['other_price_cents'])} "
                      f"on {_e(o['other_seen'])}; observed difference {money.fmt(o['saving_cents'], signed=True)}</li>"
                      for o in outcome["observed"]) or "<li>No comparable alternative was observed in the week before."
        parts.append(f"<h2>What happened at {_e(outcome['store'])}</h2>"
                     f"<p>{ {'followed': 'The trip went to the planned store.', 'partly': 'The trip went to one of the planned stores; the others are estimates only.', 'other': 'The trip did not go to a planned store; the plan is an estimate only.'}[outcome.get('plan_relation', 'other')]} "
                     f"{outcome['n_compared']} items compared; total absolute error {money.fmt(outcome['abs_error_cents'])}, "
                     f"net {money.fmt(outcome['net_error_cents'], signed=True)}. Receipt total "
                     f"{money.fmt(outcome['total_cents'])}; unexplained {money.fmt(outcome['unreconciled_cents'])}.</p>"
                     "<table><tr><th>Row</th><th>Item</th><th>Predicted</th><th>Actual</th><th>Error</th><th>Note</th>"
                     f"</tr>{rows}</table><h3>Observed differences (only where another store's price was seen)</h3>"
                     f"<ul>{obs}</ul>")
    parts.append("<h2>Limitations</h2><ul><li>Estimates rely on the shopper's own past receipts and recorded "
                 "prices; stores not visited are estimates, not savings.</li><li>Brands differ between stores unless "
                 "the same product is named.</li><li>Fictional demo data unless stated otherwise.</li></ul>")
    if not redact:
        ids = sorted({o for p in res["complete"][:1] for l in p["lines"] for o in l["estimate"]["obs_ids"]})
        parts.append(f"<h2>Evidence ids (not redacted)</h2><p class=sub>{_e(', '.join(ids))}</p>"
                     + (f"<p class=sub>Receipt #{decision.get('trip_receipt_id')}</p>"
                        if decision.get("trip_receipt_id") else ""))
    css = ("body{font:15px/1.45 'Segoe UI',system-ui,sans-serif;color:#1C2B4B;max-width:860px;margin:32px auto;"
           "padding:0 20px}h1{font-size:30px;margin:0}h2{font-size:19px;margin:22px 0 6px;border-bottom:2px solid #1C2B4B}"
           ".sub{color:#4A5876;font-size:13px}table{border-collapse:collapse;width:100%;font-size:13px}"
           "td,th{border-bottom:1px solid #ccd;padding:4px 6px;text-align:left;vertical-align:top}.n{text-align:right;"
           "white-space:nowrap}tr.miss td,tr.missing td{color:#B0282A}tr.extra td{color:#4A5876}"
           "@media print{body{margin:0}}")
    foot = f"<p class=sub>Made with PriceWise on {date.today().isoformat()}. {'Redacted copy.' if redact else ''}</p>"
    return (f"<!DOCTYPE html><html lang=en><head><meta charset=utf-8><title>{_e(title)}</title>"
            f"<style>{css}</style></head><body>{''.join(parts)}{foot}</body></html>")
