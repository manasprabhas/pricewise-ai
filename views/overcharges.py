import streamlit as st

from pricewise import audit, db, ui

ui.header("Checkout problems", "Every receipt is checked for three different kinds of problem, because each is "
          "proved differently. Review a flag before it can go in a refund note.")
if db.is_empty():
    ui.empty("No receipts yet.")
    st.stop()

issues = audit.audit()
open_ = [i for i in issues if i.status == "open"]
reviewed = [i for i in issues if i.status == "reviewed"]
refunded = [i for i in issues if i.status == "refunded"]
ui.tags([
    ("Flags to review", len([i for i in open_ if i.kind != "unusual_price"]),
     f"${sum(i.amount for i in open_ if i.kind != 'unusual_price'):.2f} in question", "paper"),
    ("Reviewed, ready to claim", float(sum(i.amount for i in reviewed if i.claimable)),
     f"{len([i for i in reviewed if i.claimable])} flags go in the note", "red" if reviewed else "paper"),
    ("Refunds you got", float(sum(i.amount for i in refunded)), f"{len(refunded)} claims", "green" if refunded else "paper"),
    ("Receipts checked", int(db.receipts_df().shape[0]), "every line, every time", "paper"),
])

cats = list(audit.CATEGORY_HELP)
counts = {c: len([i for i in issues if i.category == c and i.status in ("open", "reviewed")]) for c in cats}
pick = st.segmented_control("Kind", cats + ["Closed"], default=cats[0], key="audit_cat",
                            format_func=lambda c: f"{c} ({counts[c]})" if c in counts else c)
if pick == "Closed":
    view = [i for i in issues if i.status not in ("open", "reviewed")]
else:
    st.caption(audit.CATEGORY_HELP[pick])
    view = [i for i in issues if i.category == pick and i.status in ("open", "reviewed")]

if not view:
    ui.empty({"Calculation mismatch": "Every receipt's own numbers add up.",
              "Possible double scan": "No item rang up twice.",
              "Price-tag or promotion mismatch": "You paid what every tag and offer you recorded said. Note shelf "
                                                 "tags and ads in Add a purchase, or on your phone with Pocket.",
              "Price surprise": "No unusual prices.", "Closed": "Nothing closed yet."}.get(pick, "Nothing here."))
for i in view:
    with st.container(border=True):
        c = st.columns([1.3, 6, 2])
        c[0].html(ui.sticker(audit.SHORT[i.kind], "yellow" if i.kind == "unusual_price" else ""))
        c[1].markdown(f"**{i.title}: {i.label}**  \n{i.store}, {ui.d(i.purchased_on)} {i.purchased_on.year} · "
                      f"{audit.STATUS[i.status]}")
        c[2].html(f'<div class="pw-big" style="text-align:right;color:var(--markdown)">${i.amount:.2f}</div>')
        ui.evidence(i.evidence)
        if i.needs_terms and i.status == "open":
            ui.note("<b>Terms needed.</b> A missed offer is only claimable with what the offer required: minimum "
                    "quantity, store card, limits and dates. Add them from the tag or ad you saw.")
            t = st.columns([5, 1])
            terms = t[0].text_input("Offer terms", key=f"t{i.key}", label_visibility="collapsed",
                                    placeholder="buy 2; store card required; limit 4; valid Nov 4-10")
            if t[1].button("Save terms", key=f"st{i.key}", disabled=not terms.strip()):
                audit.set_terms(i.shelf_id, terms)
                st.rerun()
        if i.status == "open":
            b = st.columns([2, 2, 4])
            if i.kind != "unusual_price" and b[0].button("I checked: it's real", key=f"rv{i.key}", type="primary",
                                                          disabled=i.needs_terms):
                audit.set_status(i, "reviewed")
                st.toast("Reviewed. It's now in the refund note.")
                st.rerun()
            if b[1].button("Not a problem", key=f"x{i.key}"):
                audit.set_status(i, "dismissed")
                st.rerun()
        elif i.status == "reviewed":
            b = st.columns([2, 2, 2, 2])
            if b[0].button("I got a refund", key=f"r{i.key}", type="primary"):
                audit.set_status(i, "refunded")
                st.toast(f"Logged ${i.amount:.2f} in the refunds ledger.")
                st.rerun()
            if b[1].button("Asked, no refund", key=f"d{i.key}"):
                audit.set_status(i, "denied")
                st.rerun()
            if b[2].button("Back to not reviewed", key=f"o{i.key}"):
                audit.set_status(i, "open")
                st.rerun()
        else:
            if st.button("Reopen", key=f"ro{i.key}"):
                audit.set_status(i, "open")
                st.rerun()

st.subheader("Refund note")
st.caption("Built only from flags you marked reviewed. Unreviewed flags and price surprises never go in.")
by_receipt = {}
for i in reviewed:
    if i.claimable:
        by_receipt.setdefault(i.receipt_id, []).append(i)
if not by_receipt:
    n_open = len([i for i in open_ if i.claimable])
    ui.empty("Nothing reviewed yet." + (f" {n_open} flags are waiting: press “I checked: it's real” on the ones you "
                                        "confirmed at home." if n_open else ""))
else:
    labels = {rid: f"{v[0].store}, {ui.d(v[0].purchased_on)} ({len(v)} problems, ${sum(x.amount for x in v):.2f})"
              for rid, v in by_receipt.items()}
    rid = st.selectbox("Receipt", list(labels), format_func=labels.get)
    note = audit.refund_note(by_receipt[rid])
    st.code(note, language=None, wrap_lines=True)
    st.download_button("Download note", note, file_name=f"refund-note-{rid}.txt")
