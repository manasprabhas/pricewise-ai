"""The review-and-save screen shared by every way of bringing a receipt in (digital, photo)."""
from __future__ import annotations

import time
from datetime import date, datetime

import pandas as pd
import streamlit as st

from . import analytics, audit, catalog, db, identity, matcher, money, reconcile, scanner, ui, units

NEW = "➕ New product…"
SKIP = "— Skip this line —"
ADJ_NAMES = {"deposit": "Deposit", "fee": "Fee", "store_coupon": "Store-wide coupon", "return": "Return"}
ADJ_KINDS = {v: k for k, v in ADJ_NAMES.items()}


def source_view(raw_text: str, rows: pd.DataFrame, adjustments: pd.DataFrame) -> None:
    """The receipt exactly as it arrived, every line marked with what the reader made of it."""
    if not raw_text:
        return
    item_texts = [str(t) for t in rows["receipt_text"].tolist()]
    adj_texts = [str(t) for t in adjustments["description"].tolist()] if not adjustments.empty else []
    used = set()
    out = []
    for n, line in enumerate(raw_text.splitlines(), 1):
        if not line.strip():
            continue
        up = line.upper()
        mark, cls = "·", "ign"
        for i, t in enumerate(item_texts):
            if i not in used and t and t.upper() in up:
                used.add(i)
                mark, cls = f"{i + 1}", "item"
                break
        else:
            if any(a and a.upper() in up for a in adj_texts):
                mark, cls = "±", "adj"
            elif any(w in up for w in ("SUBTOTAL", "SUB TOTAL", "TAX", "TOTAL", "BALANCE")):
                mark, cls = "Σ", "sum"
            elif any(w in up for w in ("SAVINGS", "COUPON", "DISCOUNT", "PRICE CUT", "MEMBER")):
                mark, cls = "−", "disc"
        out.append(f'<div class="ln {cls}"><span class="no">{n}</span><span class="mk">{ui.esc(mark)}</span>'
                   f'<span class="tx">{ui.esc(line.rstrip())}</span></div>')
    st.html('<div class="pw-src"><div class="cap">Source, exactly as received</div>' + "".join(out) +
            '<div class="key"><b>1–9</b> becomes that row · <b>−</b> item discount · <b>±</b> adjustment · '
            '<b>Σ</b> printed figure · <b>·</b> not used</div></div>')


def _reasons(raw: str, read_conf: float, top, second: float, notes: list[str], prod: dict | None) -> list[str]:
    out = []
    if read_conf < 0.7:
        out.append(f"Text read at {read_conf:.0%} confidence")
    if top is None or top.score < 60:
        out.append("No product matched")
    elif top.score < matcher.AUTO_ACCEPT:
        out.append(f"Best match only {top.score:.0f}%")
    elif top.score - second < 4:
        out.append(f"Two products match almost equally ({top.score:.0f}% vs {second:.0f}%)")
    if prod:
        out += identity.line_flags(raw, prod)
    out += notes
    return out


def start(rec: scanner.ParsedReceipt, key: str, source_name: str = "", import_hash: str | None = None) -> None:
    """Match every line to a product and put the receipt up for review."""
    store_id = None
    if rec.store:
        store_id = next((int(r["id"]) for _, r in db.stores().iterrows() if r["name"].lower() == rec.store.lower()), None)
    opts = catalog.product_options()
    id_to_label = {v: k for k, v in opts.items()}
    rows = []
    for it in rec.items:
        cands = matcher.match(it.raw_text, store_id)
        top = cands[0] if cands else None
        second = cands[1].score if len(cands) > 1 else 0
        pid = top.product_id if top and top.score >= 60 else None
        reasons = _reasons(it.raw_text, float(it.confidence), top, second, list(it.notes),
                           db.product(pid) if pid else None)
        rows.append({
            "use": True, "receipt_text": it.raw_text,
            "product": id_to_label.get(pid) if pid else NEW,
            "confirmed": False,
            "qty": float(it.qty or 1),
            "unit": it.qty_unit if it.qty_unit in ("each", "lb", "oz", "kg") else "each",
            "unit_price": it.unit_price, "line_total": float(it.line_total), "discount": float(it.discount),
            "read_confidence": round(float(it.confidence), 2),
            "match": f"{top.score:.0f}% {top.reason.lower()}" if top else "No match",
            "match_score": float(top.score) if top else None,
            "check": "✓" if not reasons else "Check",
            "why": "; ".join(reasons) if reasons else "Read clearly and matched with confidence",
        })
    df = pd.DataFrame(rows)
    adj = pd.DataFrame([{"kind": ADJ_NAMES[a.kind], "description": a.description,
                         "amount": a.amount_cents / 100, "after_subtotal": a.after_subtotal}
                        for a in rec.adjustments], columns=["kind", "description", "amount", "after_subtotal"])
    st.session_state[key] = {"rec": rec, "rows": df, "original": df.copy(), "adjustments": adj,
                             "source": source_name, "hash": import_hash, "started": time.time(),
                             "store_id": store_id}


def show_saved(key: str) -> None:
    flag = f"{key}_saved"
    if flag not in st.session_state or key in st.session_state:
        return
    rid = st.session_state.pop(flag)
    pur = db.purchases(include_unverified=True)
    status = db.one("SELECT status FROM receipts WHERE id = ?", (rid,))["status"]
    if status == "unverified":
        st.warning("Saved as unverified: it's kept for your records but left out of comparisons, alerts and advice.")
    alerts = analytics.alerts_for(pur, [rid])
    issues = [i for i in audit.audit([rid], pur) if i.status == "open"]
    st.success(f"Saved {len(pur[pur['receipt_id'] == rid])} items. Here's what PriceWise noticed:")
    found = [(ui.sticker(audit.SHORT[i.kind]), f"{i.title}: {i.label}", " ".join(i.evidence[:2]),
              f"${i.amount:.2f}", "red") for i in issues]
    found += [(ui.sticker(f"{a.pct:+.0%}", "yellow" if a.direction == "high" else "green"), a.label,
               f"Paid {a.paid_unit}, usual {a.usual_unit}. {a.reason[:1].upper() + a.reason[1:]}.", a.paid_unit,
               "red" if a.direction == "high" else "green") for a in alerts]
    if found:
        ui.rows(found)
    else:
        ui.empty("Nothing unusual on this receipt. Prices were in your normal range.")
    st.divider()


def _row_flags(edited: pd.DataFrame, opts: dict) -> dict[int, list[str]]:
    """Reasons recomputed for the product each row is CURRENTLY assigned to (so a fix clears its flag)."""
    out = {}
    for idx, r in edited.iterrows():
        if not r.get("use") or r.get("product") in (SKIP, None):
            continue
        reasons = []
        if r["product"] == NEW:
            reasons.append("New product: describe it below")
        else:
            prod = db.product(opts[r["product"]])
            reasons += identity.line_flags(r["receipt_text"], prod)
            if prod["sold_by_weight"] and (r["unit"] == "each" or not r["qty"]):
                reasons.append("Sold by weight but no weight entered")
        if (r.get("read_confidence") or 1) < 0.7:
            reasons.append(f"Text read at {r['read_confidence']:.0%} confidence")
        if r["line_total"] is None or r["line_total"] != r["line_total"]:
            reasons.append("No price")
        if reasons:
            out[idx] = reasons
    return out


def _checks(kept: pd.DataFrame, adj: pd.DataFrame, rec, tax: float, subtotal, total) -> reconcile.Reconciliation:
    lines = [(money.cents(r["line_total"] or 0), money.cents(r["discount"] or 0)) for _, r in kept.iterrows()]
    adjustments = [reconcile.Adjustment(ADJ_KINDS.get(a["kind"], "fee"), abs(money.cents(a["amount"] or 0)),
                                        a["description"] or "", bool(a["after_subtotal"]))
                   for _, a in adj.iterrows() if a.get("kind") in ADJ_KINDS and (a.get("amount") or 0)]
    r = reconcile.run(lines, adjustments,
                      money.cents(subtotal) if subtotal is not None else None, money.cents(tax or 0),
                      money.cents(total) if total is not None else None)
    r.adjustment_list = adjustments
    return r


def _check_tags(r: reconcile.Reconciliation, tax_c: int, n_kept: int, n_confirmed: int, n_flagged: int) -> None:
    by = {c.name: c for c in r.checks}
    sub, tot = by.get("Subtotal"), by.get("Total")

    def tone(c):
        return "paper" if c is None else "green" if c.ok else "red"

    def foot(c, what):
        if c is None:
            return f"no {what} printed: can't be checked"
        return "matches to the cent" if c.ok else f"off by {money.fmt(c.actual_cents - c.expected_cents, signed=True)}"
    before = r.adjustments_before_cents
    after = r.adjustments_after_cents
    ui.tags([
        ("Items − item discounts", (r.items_cents - r.item_discounts_cents) / 100,
         f"{n_kept} lines; discounts {money.fmt(r.item_discounts_cents)}", "paper"),
        ("Adjustments", (before + after) / 100,
         f"{money.fmt(before, signed=True)} before subtotal, {money.fmt(after, signed=True)} after", "paper"),
        ("Subtotal check", (sub.expected_cents / 100) if sub else "—", foot(sub, "subtotal"), tone(sub)),
        ("Total check", (tot.expected_cents / 100) if tot else "—",
         f"incl. tax {money.fmt(tax_c)}; " + foot(tot, "total"), tone(tot)),
    ])
    st.html(f'''<div class="pw-two-truths">
      <div class="{"ok" if r.status == "reconciled" else "bad"}"><b>Arithmetic</b>
        {"Every printed figure checks out." if r.status == "reconciled" else
         "Nothing printed to check against." if r.status == "no_total" else "A printed figure doesn't match."}
        <span>Proves the <i>amounts</i> were read right. Says nothing about <i>which product</i> each line is.</span></div>
      <div class="{"ok" if n_confirmed == n_kept and n_kept else "mid"}"><b>Products</b>
        You confirmed {n_confirmed} of {n_kept}; {n_flagged} still flagged.
        <span>Only your tick proves a line is the right product. A wrong product can still add up perfectly.</span></div>
    </div>''')
    for c in r.checks:
        if not c.ok:
            ui.note(f"<b>{ui.esc(c.name)}:</b> {ui.esc(c.explain)}")


def render(key: str) -> None:
    state = st.session_state[key]
    rec: scanner.ParsedReceipt = state["rec"]
    opts = catalog.product_options()
    if state.get("source"):
        st.caption(f"Reviewing: {state['source']}")

    top = st.columns([2, 2, 1, 1, 1])
    stores = db.stores()
    store_names = stores["name"].tolist()
    idx = next((i for i, n in enumerate(store_names) if rec.store and n.lower() == rec.store.lower()), len(store_names))
    store_choice = top[0].selectbox("Store", store_names + ["➕ New store…"], index=idx, key=f"{key}_store")
    new_store = (top[0].text_input("New store name", value=(rec.store or "").title(), key=f"{key}_newstore")
                 if store_choice == "➕ New store…" else None)
    when = top[1].date_input("Date", value=date.fromisoformat(rec.purchased_on) if rec.purchased_on else date.today(),
                             format="MM/DD/YYYY", key=f"{key}_date")
    subtotal = top[2].number_input("Printed subtotal", value=float(rec.subtotal) if rec.subtotal is not None else None,
                                   min_value=0.0, step=0.01, format="%.2f", key=f"{key}_sub",
                                   help="Leave empty if the receipt doesn't print one.")
    tax = top[3].number_input("Tax", value=float(rec.tax or 0), min_value=0.0, step=0.01, format="%.2f",
                              key=f"{key}_tax")
    total = top[4].number_input("Printed total", value=float(rec.total) if rec.total is not None else None,
                                min_value=0.0, step=0.01, format="%.2f", key=f"{key}_tot")
    for w in rec.warnings:
        ui.note(f"<b>Reader note:</b> {ui.esc(w)}")

    reader = {"gemini": "Gemini", "offline": "offline photo", "digital": "digital receipt",
              "digital+gemini": "digital receipt with Gemini"}.get(rec.engine, rec.engine)
    left, right = st.columns([1, 2.6], gap="medium")
    with right:
        st.caption(f"Read from the {reader}. Tick “Right product” once you've checked a line; rows marked "
                   "Check say why in the last column.")
        edited = st.data_editor(
            state["rows"], hide_index=True, width="stretch", num_rows="dynamic", key=f"{key}_editor",
            column_order=["use", "check", "receipt_text", "product", "confirmed", "line_total", "why", "qty", "unit",
                          "discount", "read_confidence"],
            column_config={
                "use": st.column_config.CheckboxColumn("Keep", width="small"),
                "check": st.column_config.TextColumn("", disabled=True, width="small"),
                "receipt_text": st.column_config.TextColumn("Receipt text", width="medium"),
                "product": st.column_config.SelectboxColumn("Product", options=[NEW, SKIP] + list(opts),
                                                            width="medium", required=True),
                "confirmed": st.column_config.CheckboxColumn("Right product", width="small",
                                                             help="Tick once you've checked this is the product "
                                                                  "you bought (brand, variant and size)."),
                "qty": st.column_config.NumberColumn("Qty / weight", min_value=0.0, step=0.01, format="%.2f"),
                "unit": st.column_config.SelectboxColumn("Unit", options=["each", "lb", "oz", "kg"], width="small"),
                "line_total": st.column_config.NumberColumn("Line total", format="$%.2f", required=True),
                "discount": st.column_config.NumberColumn("Item discount", format="$%.2f"),
                "read_confidence": st.column_config.ProgressColumn("Read", min_value=0.0, max_value=1.0,
                                                                   format="percent", width="small"),
                "why": st.column_config.TextColumn("Why it's flagged", disabled=True, width="medium"),
            })
        with st.expander(f"Adjustments kept apart from items ({len(state['adjustments'])})",
                         expanded=not state["adjustments"].empty):
            st.caption("Deposits, fees, store-wide coupons and returns. They count toward the totals but are never "
                       "spread onto an item's price. A coupon that names no product belongs here.")
            adj = st.data_editor(
                state["adjustments"], hide_index=True, width="stretch", num_rows="dynamic", key=f"{key}_adj",
                column_config={
                    "kind": st.column_config.SelectboxColumn("Kind", options=list(ADJ_KINDS), required=True),
                    "description": st.column_config.TextColumn("As printed", width="medium"),
                    "amount": st.column_config.NumberColumn("Amount", format="$%.2f", min_value=0.0),
                    "after_subtotal": st.column_config.CheckboxColumn("After subtotal",
                                                                      help="Printed below the subtotal line."),
                })
    with left:
        source_view(rec.raw_text, edited, adj)

    kept = edited[(edited["use"] == True) & (edited["product"] != SKIP)].copy()  # noqa: E712
    flags = _row_flags(edited, opts)
    r = _checks(kept, adj, rec, tax, subtotal, total)
    n_conf = int(kept["confirmed"].fillna(False).astype(bool).sum()) if not kept.empty else 0
    _check_tags(r, money.cents(tax or 0), len(kept), n_conf, len([i for i in flags if i in kept.index]))
    ok = r.status == "reconciled"
    if r.failed and not kept.empty:
        lines = [(money.cents(x["line_total"] or 0), money.cents(x["discount"] or 0)) for _, x in kept.iterrows()]
        c = r.failed[0]
        for i in reconcile.likely_misreads(lines, c.actual_cents - c.expected_cents)[:2]:
            row = kept.iloc[i]
            fixed = (lines[i][0] - (c.actual_cents - c.expected_cents)) / 100
            ui.note(f"<b>Likely misread:</b> “{ui.esc(row['receipt_text'])}” reads ${row['line_total']:.2f}; "
                    f"${fixed:.2f} differs by one digit and would make every check pass.")
    if flags:
        ui.rows([(ui.sticker("Check", "yellow"), str(edited.loc[i, "receipt_text"]), "; ".join(v),
                  str(edited.loc[i, "product"])[:40], "") for i, v in flags.items()])

    new_rows = kept[kept["product"] == NEW]
    new_defs = {}
    if not new_rows.empty:
        st.subheader("Describe the new products")
        st.caption("Brand, variant and size are separate, so two sizes or a lactose-free version never share a price "
                   "history.")
        kinds = catalog.kinds()
        unit_list = ["oz", "lb", "fl oz", "gal", "each", "g", "kg", "ml", "l", "qt", "dozen"]
        for idx, row in new_rows.iterrows():
            guess = units.parse_size(row["receipt_text"])
            weighted = row["unit"] in ("lb", "kg", "oz") and (row["qty"] or 1) != 1
            with st.container(border=True):
                st.markdown(f"**{row['receipt_text']}**")
                c = st.columns([3, 2, 2, 2, 2])
                name = c[0].text_input("Product name", value=matcher.normalize(row["receipt_text"]).title(),
                                       key=f"{key}n{idx}")
                brand = c[1].text_input("Brand", key=f"{key}b{idx}")
                variant = c[2].text_input("Variant", key=f"{key}v{idx}", placeholder="e.g. lactose free",
                                          help="What makes it a different product from the regular one.")
                kind = c[3].selectbox("Kind of food", kinds + ["other"], key=f"{key}k{idx}",
                                      index=next((i for i, k in enumerate(kinds) if k in name.lower()), len(kinds)),
                                      help="Groups the same food across brands and stores, e.g. 'eggs'.")
                category = c[4].selectbox("Category", catalog.CATEGORIES, key=f"{key}c{idx}")
                c2 = st.columns([1, 1, 1, 2])
                by_weight = c2[0].checkbox("Sold by weight", value=weighted, key=f"{key}w{idx}")
                size = c2[1].number_input("Package size", value=float(guess[0]) if guess else 1.0, min_value=0.0,
                                          key=f"{key}s{idx}", disabled=by_weight)
                unit = c2[2].selectbox("Size unit", unit_list, key=f"{key}u{idx}", disabled=by_weight,
                                       index=unit_list.index(guess[1]) if guess and guess[1] in unit_list else 0)
                freezable = c2[3].checkbox("Can be frozen", key=f"{key}f{idx}")
                new_defs[idx] = dict(name=name, brand=brand, kind=kind if kind != "other" else name.lower(),
                                     category=category, size_value=None if by_weight else size,
                                     size_unit="lb" if by_weight else unit, sold_by_weight=by_weight,
                                     freezable=freezable, variant=variant.strip() or None)

    store_id_guess = (int(stores.loc[stores["name"] == store_choice, "id"].iloc[0])
                      if store_choice in store_names else None)
    same_day = db.q("SELECT id FROM receipts WHERE store_id = ? AND purchased_on = ? AND source = 'in-store'",
                    (store_id_guess, when.isoformat())) if store_id_guess else pd.DataFrame()
    replace = False
    if not same_day.empty:
        replace = st.checkbox("Replace my in-store list from this day with this receipt (recommended; the shelf "
                              "prices you noted are kept and checked against it)", value=True, key=f"{key}_replace")

    acknowledged = True
    if not ok:
        why = ("no printed subtotal or total to check against" if r.status == "no_total"
               else "; ".join(f"{c.name.lower()} off by {money.fmt(c.actual_cents - c.expected_cents, signed=True)}"
                              for c in r.failed))
        ui.note(f"<b>This receipt doesn't reconcile:</b> {ui.esc(why)}. Fix the lines above, or save it as "
                "<b>unverified</b>. Unverified receipts are listed in Price memory but never used for comparisons, "
                "alerts, advice or savings.")
        acknowledged = st.checkbox("Save it as unverified", key=f"{key}_ack")

    if store_id_guess:
        total_guess = total if total is not None else round((r.computed_subtotal_cents + money.cents(tax or 0)) / 100, 2)
        similar = db.similar_receipts(store_id_guess, when.isoformat(), total_guess)
        similar = similar[similar["source"] != "in-store"] if not similar.empty else similar
        if not similar.empty:
            ui.note(f"<b>Possible duplicate:</b> a {ui.esc(similar.iloc[0]['source'])} receipt from this store and "
                    f"day with the same total (${similar.iloc[0]['total']:.2f}) is already saved.")
    allow_dup = st.session_state.get(f"{key}_allow_dup", False)

    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("Save receipt", type="primary", disabled=kept.empty or not acknowledged, key=f"{key}_save"):
        if store_choice == "➕ New store…":
            if not (new_store or "").strip():
                st.error("Type the store name first.")
                st.stop()
            store_id = db.get_or_create_store(new_store)
        else:
            store_id = store_id_guess
        original = state["original"]
        actor = {"offline": "parser:offline", "gemini": "gemini"}.get(rec.engine,
                                                                       "parser:digital" if rec.engine.startswith(
                                                                           "digital") else f"parser:{rec.engine}")
        lines, corrected = [], 0
        for idx, row in kept.iterrows():
            pid = catalog.create_product(**new_defs[idx]) if row["product"] == NEW else opts[row["product"]]
            p = db.product(pid)
            orig = original.loc[idx] if idx in original.index else None
            parsed = None
            if orig is not None:
                parsed = {"qty": float(orig["qty"] or 1), "line_total": float(orig["line_total"]),
                          "discount": float(orig["discount"] or 0),
                          "product_id": opts.get(orig["product"]) if orig["product"] in opts else None}
                corrected += sum(1 for f in ("qty", "line_total", "discount", "product", "unit")
                                 if str(orig[f]) != str(row[f]))
            changed_product = orig is None or orig["product"] != row["product"]
            method = ("you" if changed_product or row["product"] == NEW else
                      "alias" if "learned" in str(row.get("match", "")).lower() else "fuzzy")
            lines.append(dict(product_id=pid, qty=float(row["qty"] or 1),
                              qty_unit=row["unit"] if p["sold_by_weight"] else "each", unit_price=row["unit_price"],
                              line_total=float(row["line_total"]), discount=float(row["discount"] or 0),
                              raw_text=row["receipt_text"], confidence=float(row["read_confidence"] or 1),
                              match_method=method, match_score=row.get("match_score"),
                              confirmed=bool(row["confirmed"]) or method == "you", parsed=parsed, actor=actor))
        try:
            rid = db.save_receipt(store_id, when, lines, subtotal=subtotal, tax=tax, total=total,
                                  source=rec.engine if rec.engine.startswith("digital") else f"scan-{rec.engine}",
                                  allow_duplicate=allow_dup, status=None if ok else "unverified",
                                  adjustments=r.adjustment_list, source_ref=state.get("source") or None,
                                  source_text=rec.raw_text or None)
        except db.DuplicateReceipt as e:
            st.session_state[f"{key}_dup"] = e.existing_id
            st.rerun()
        for l in lines:
            matcher.learn(l["raw_text"], store_id, l["product_id"])
        db.run("INSERT INTO review_sessions(receipt_id, source, started_at, saved_at, seconds, rows, rows_flagged, "
               "fields_corrected) VALUES (?,?,?,?,?,?,?,?)",
               (rid, rec.engine, datetime.fromtimestamp(state["started"]).isoformat(timespec="seconds"),
                datetime.now().isoformat(timespec="seconds"), round(time.time() - state["started"], 1), len(lines),
                int((state["original"]["check"] != "✓").sum()), corrected))
        if replace:
            for rid_old in same_day["id"]:
                db.delete_receipt(int(rid_old))
        if state.get("hash"):
            from . import digital
            digital.mark_imported(state["hash"], state.get("source") or "", rid)
        for k in (key, f"{key}_dup", f"{key}_allow_dup"):
            st.session_state.pop(k, None)
        st.session_state[f"{key}_saved"] = rid
        st.rerun()
    if st.session_state.get(f"{key}_dup"):
        st.error(f"Not saved: this exact purchase is already saved as receipt #{st.session_state[f'{key}_dup']}. "
                 "Saving it again would count every item twice.")
        if st.checkbox("I really bought this twice: save it again", key=f"{key}_allow_dup"):
            st.caption("Press Save receipt again.")
    if b2.button("Cancel", key=f"{key}_cancel"):
        for k in (key, f"{key}_dup", f"{key}_allow_dup"):
            st.session_state.pop(k, None)
        st.rerun()
