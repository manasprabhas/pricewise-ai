import pandas as pd
import streamlit as st

from pricewise import catalog, db, failure_demo, identity, ui, units

ui.header("Check matches", "Every receipt line is tied to one exact product: brand, variant and size. This is "
          "where you catch the lines that might be tied to the wrong one, fix them, merge duplicates, and undo any "
          "of it.")
if db.is_empty():
    ui.empty("No receipts yet. Import a receipt, or open the demo from the sidebar.")
    st.stop()

queue = identity.review_queue()
ch = identity.changes(200)
merges = db.q("SELECT * FROM product_merges ORDER BY id DESC")
n_size = int(queue["reason"].str.contains("Receipt says").sum()) if not queue.empty else 0
ui.tags([
    ("Lines to look at", len(queue), f"{n_size} disagree with their product", "red" if n_size else "paper"),
    ("Confirmed by you", int(db.one("SELECT COUNT(*) n FROM items WHERE confirmed = 1")["n"]),
     f"of {int(db.one('SELECT COUNT(*) n FROM items')['n'])} saved lines", "paper"),
    ("Fixes made", int((ch["undone"] == 0).sum()) if not ch.empty else 0, "every one can be undone", "paper"),
    ("Merges", int(merges["undone_on"].isna().sum()) if not merges.empty else 0, "reversible", "paper"),
])

opts = catalog.product_options()
labels = list(opts)

tab_q, tab_fail, tab_merge, tab_prov, tab_log = st.tabs(
    ["Review queue", "Deliberate failure", "Merge duplicates", "Where a line came from", "Change log"])


def reassign_box(item: dict, key: str) -> None:
    """Confirm the current product, or move the line to another one and see the unit price change first."""
    cur = db.product(int(item["product_id"]))
    c = st.columns([3, 1, 1])
    current_label = next((l for l, p in opts.items() if p == cur["id"]), labels[0])
    pick = c[0].selectbox("Product", labels, index=labels.index(current_label), key=f"{key}_pick",
                          label_visibility="collapsed")
    new_pid = opts[pick]
    if new_pid == cur["id"]:
        if c[1].button("It's right", key=f"{key}_ok", type="primary"):
            identity.confirm_item(int(item["item_id"]))
            st.toast("Confirmed. It leaves the queue; the change log can undo it.")
            st.rerun()
    else:
        new = db.product(new_pid)
        paid = (item["paid_cents"] or 0) / 100
        calc = db.compute_item(new, float(item["qty"] or 1), item["qty_unit"] or "each", paid, 0)
        before = units.fmt_unit_price(item["unit_cost"], cur["display_unit"])
        after = units.fmt_unit_price(calc["unit_cost"], cur["display_unit"] if cur["dimension"] == new["dimension"]
                                     else new["display_unit"])
        warn = identity.line_flags(item["raw_text"], new)
        c[0].caption(f"Unit price {before} → **{after}**" + (f" · still flagged: {warn[0]}" if warn else ""))
        if c[1].button("Move it", key=f"{key}_mv", type="primary"):
            identity.reassign_item(int(item["item_id"]), new_pid, reason=item.get("reason") or "fixed in review")
            st.toast(f"Moved. Unit price is now {after}. PriceWise will match this text to it at this store.")
            st.rerun()


with tab_q:
    if queue.empty:
        ui.empty("Nothing to check. Every line agrees with its product, and every automatic match has been checked.")
    else:
        kinds = st.segmented_control("Show", ["Disagrees with its product", "Automatic, unchecked", "All"],
                                     default="All", key="q_filter")
        view = queue
        if kinds == "Disagrees with its product":
            view = queue[queue["severity"] == 0]
        elif kinds == "Automatic, unchecked":
            view = queue[queue["severity"] == 1]
        st.caption(f"{len(view)} lines. Most serious first: a receipt that names a different size or variant from "
                   "its product mixes two price histories.")
        for _, r in view.head(25).iterrows():
            with st.container(border=True):
                a, b = st.columns([2, 3])
                a.markdown(f"<span class='pw-mono'>{ui.esc(r['raw_text'])}</span>", unsafe_allow_html=True)
                a.caption(f"{r['store']} · {r['purchased_on']} · paid ${(r['paid_cents'] or 0) / 100:.2f} · "
                          f"saved as {r['label']} at {r['unit_price']}")
                b.markdown(("🔴 " if r["severity"] == 0 else "🟡 ") + ui.esc(r["reason"]))
                with b:
                    reassign_box(r.to_dict(), f"q{int(r['item_id'])}")
        if len(view) > 25:
            st.caption(f"Showing 25 of {len(view)}.")

with tab_fail:
    s = failure_demo.state()
    st.markdown("#### An 18 oz cereal saved as the 15.4 oz one")
    st.caption("A store's CSV export maps its product code to the wrong size. The receipt adds up perfectly, so "
               "arithmetic alone can't catch it. Walk through where it surfaces and what fixing it changes.")
    ui.timeline(s["step"], [("Plant", "import the bad line"), ("Spot", "see where it surfaces"),
                            ("Fix", "correct it, see the price change")])
    if s["small"] is None or s["big"] is None:
        ui.empty("This walkthrough uses the demo's two Sunny Crunch cereal sizes. Open the demo from the sidebar.")
    elif s["step"] == 0:
        ui.rows([(ui.sticker("CSV"), failure_demo.RAW, "walmart-export.csv, row 14 · mapped to product code "
                                                       "078742-15 (the 15.4 oz box)", f"${failure_demo.PRICE:.2f}", "")])
        st.caption(f"Dated {failure_demo.DATE}, older than the planner's 120-day window, so it can't change "
                   "today's plans while you experiment.")
        if st.button("Import the bad line", type="primary"):
            failure_demo.plant()
            st.rerun()
    else:
        it = s["item"]
        ui.tags([
            ("Arithmetic", "Reconciles", "subtotal and total match to the cent", "green"),
            ("Product", db.product_label(db.product(int(it["product_id"]))).replace("Sunny Crunch ", ""),
             "matched by the import, not by you" if s["step"] == 1 else "corrected by you", "red" if s["step"] == 1
             else "green"),
            ("Unit price", s["unit_now"], f"as 15.4 oz {s['as_small']} · as 18 oz {s['as_big']}",
             "red" if s["step"] == 1 else "green"),
        ])
        if s["step"] == 1:
            st.markdown("**Where it surfaces**")
            ui.rows([
                (ui.sticker("1", "yellow"), "Review queue (first tab)", s["flag"] or "", "flagged", "red"),
                (ui.sticker("2", "yellow"), "Import review",
                 "The same check runs on every row while you review an import, before anything is saved.",
                 "row reason", ""),
                (ui.sticker("3", "yellow"), "Price memory",
                 f"The 15.4 oz history gains a {s['unit_now']} point that is really an 18 oz box.", "skews", ""),
            ])
            st.markdown("**Fix it**")
            reassign_box({**it, "item_id": it["id"], "reason": s["flag"]}, "fail")
        else:
            ui.note(f"<b>Fixed.</b> The line now belongs to the 18 oz box. Its unit price went from "
                    f"{s['as_small']} to <b>{s['as_big']}</b>, and the 15.4 oz price history no longer contains it. "
                    "The fix is in the change log, where it can be undone.")
        if st.button("Reset the walkthrough"):
            failure_demo.remove()
            st.rerun()

with tab_merge:
    st.caption("When the same product was saved twice (say, once from a photo and once typed in), merge them so "
               "their price history is one. Every moved line, alias and shelf price is recorded, so a merge can be "
               "undone exactly.")
    c = st.columns(2)
    a = c[0].selectbox("Merge this product…", labels, index=None, placeholder="Duplicate to fold in", key="m_from")
    b = c[1].selectbox("…into this one", labels, index=None, placeholder="Product to keep", key="m_into")
    if a and b:
        if opts[a] == opts[b]:
            st.caption("Pick two different products.")
        else:
            n = db.one("SELECT COUNT(*) n FROM items WHERE product_id = ?", (opts[a],))["n"]
            warns = identity.merge_warnings(opts[a], opts[b])
            for w in warns:
                ui.note(f"<b>Careful:</b> {ui.esc(w)}")
            sure = st.checkbox("Merge anyway", key="m_sure") if warns else True
            if st.button(f"Merge ({n} receipt lines move)", type="primary", disabled=not sure):
                identity.merge_products(opts[a], opts[b])
                st.toast("Merged. Undo it below any time.")
                st.rerun()
    if not merges.empty:
        st.markdown("**Merges**")
        names = {int(r["id"]): db.product_label(r) for _, r in db.products(include_merged=True).iterrows()}
        for _, m in merges.iterrows():
            cc = st.columns([5, 1])
            done = pd.notna(m["undone_on"])
            moved = __import__("json").loads(m["moved"])
            cc[0].markdown(f"{'~~' if done else ''}{names.get(int(m['from_product_id']), '?')} → "
                           f"{names.get(int(m['into_product_id']), '?')}{'~~' if done else ''}  \n"
                           f"<small>{len(moved['items'])} lines, {len(moved['aliases'])} aliases, "
                           f"{len(moved['shelf'])} shelf prices · {m['merged_on']}"
                           f"{' · undone ' + str(m['undone_on']) if done else ''}</small>", unsafe_allow_html=True)
            if not done and cc[1].button("Undo", key=f"um{int(m['id'])}"):
                identity.undo_merge(int(m["id"]))
                st.rerun()
    st.markdown("**What PriceWise has learned**")
    st.caption("Receipt wording is learned per store: “CRL” at Kroger and “CRL” at Walmart can mean different "
               "products.")
    al = db.q("""SELECT a.id, a.raw_norm, COALESCE(s.name, 'any store') AS store, a.product_id, a.times_seen
                 FROM aliases a LEFT JOIN stores s ON s.id = a.store_id ORDER BY a.times_seen DESC""")
    if not al.empty:
        names = {int(r["id"]): db.product_label(r) for _, r in db.products(include_merged=True).iterrows()}
        al["product"] = al["product_id"].map(lambda x: names.get(int(x), "?"))
        st.dataframe(al[["raw_norm", "store", "product", "times_seen"]], hide_index=True, width="stretch", height=240,
                     column_config={"raw_norm": "Receipt wording", "store": "At", "product": "Means",
                                    "times_seen": st.column_config.NumberColumn("Seen", format="%d×")})

with tab_prov:
    st.caption("For any saved line: the source it came from, what the reader produced, what was corrected and by "
               "whom, and what was saved.")
    recs = db.q("""SELECT r.id, r.purchased_on, s.name AS store, r.source, r.total_cents FROM receipts r
                   JOIN stores s ON s.id = r.store_id ORDER BY r.purchased_on DESC, r.id DESC""")
    pick = st.selectbox("Receipt", recs.to_dict("records"),
                        format_func=lambda r: f"{r['purchased_on']} · {r['store']} · "
                                              f"${(r['total_cents'] or 0) / 100:.2f} · {r['source']}")
    if pick:
        rid = int(pick["id"])
        r = db.one("SELECT * FROM receipts WHERE id = ?", (rid,))
        items = db.q("""SELECT i.id, i.raw_text, i.product_id, i.qty, i.qty_unit, i.line_cents, i.discount_cents,
                               i.match_method, i.match_score, i.confirmed FROM items i WHERE i.receipt_id = ?
                        ORDER BY i.id""", (rid,))
        prov = db.q("SELECT * FROM item_provenance WHERE item_id IN (SELECT id FROM items WHERE receipt_id = ?)",
                    (rid,))
        changes = db.q("SELECT * FROM item_changes WHERE item_id IN (SELECT id FROM items WHERE receipt_id = ?)",
                       (rid,))
        ui.evidence([f"Source: {r['source']}" + (f" · {r['source_ref']}" if r.get("source_ref") else ""),
                     f"Reconciliation: {r['status']}", f"{len(items)} lines, {len(prov)} provenance records, "
                                                       f"{len(changes)} later changes"])
        rows = []
        for _, it in items.iterrows():
            p = db.product(int(it["product_id"]))
            pv = prov[prov["item_id"] == it["id"]]
            fixes = [f"{x['field']}: {x['parsed_value']} → {x['saved_value']} ({x['actor']})"
                     for _, x in pv.iterrows() if str(x["parsed_value"]) != str(x["saved_value"])]
            later = [f"{x['field']} {x['old_value']} → {x['new_value']} by {x['actor']}"
                     + (" (undone)" if x["undone"] else "") for _, x in changes[changes["item_id"] == it["id"]].iterrows()]
            who = ("you" if it["confirmed"] and it["match_method"] in ("you", "manual") else
                   f"you confirmed a {it['match_method']} match" if it["confirmed"] else
                   f"{it['match_method']} match, unchecked")
            rows.append({"line": it["raw_text"], "product": db.product_label(p), "product chosen by": who,
                         "paid": (it["line_cents"] - (it["discount_cents"] or 0)) / 100,
                         "corrected at review": "; ".join(fixes) or ("—" if len(pv) else "no reader record"),
                         "changed later": "; ".join(later) or "—"})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                     column_config={"paid": st.column_config.NumberColumn(format="$%.2f"),
                                    "line": st.column_config.TextColumn(width="medium"),
                                    "product": st.column_config.TextColumn(width="medium")})
        if r.get("source_text"):
            with st.expander("Source text, exactly as received"):
                st.code(r["source_text"], language=None)

with tab_log:
    if ch.empty:
        ui.empty("No fixes yet. Anything you confirm, move or merge shows up here with an Undo.")
    else:
        names = {int(r["id"]): db.product_label(r) for _, r in db.products(include_merged=True).iterrows()}
        for _, c in ch.head(40).iterrows():
            cc = st.columns([5, 1])
            if c["field"] == "product_id":
                what = (f"Moved “{c['raw_text']}” from {names.get(int(c['old_value']), '?')} to "
                        f"{names.get(int(c['new_value']), '?')}")
            else:
                what = f"Confirmed “{c['raw_text']}”"
            cc[0].markdown(f"{'~~' if c['undone'] else ''}{ui.esc(what)}{'~~' if c['undone'] else ''}  \n"
                           f"<small>{c['at']} · by {c['actor']}{' · ' + ui.esc(c['reason']) if c['reason'] else ''}"
                           f"{' · undone' if c['undone'] else ''}</small>", unsafe_allow_html=True)
            if not c["undone"] and cc[1].button("Undo", key=f"uc{int(c['id'])}"):
                identity.undo_change(int(c["id"]))
                st.rerun()
