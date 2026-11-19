import pandas as pd
import streamlit as st

from pricewise import analytics, audit, db, demo, savings, shrink, stockup, swaps, ui, units

ACTIONS = [
    ("views/import_receipts.py", "Import a receipt", "mail",
     "Drop in an emailed receipt, PDF or order history. Every line is checked against the printed total."),
    ("views/lab.py", "Plan a basket", "shopping_basket",
     "Price a basket at every combination of your stores, and see the exact value that would change the plan."),
    ("views/replay.py", "Replay a trip", "history_toggle_off",
     "Go back to before a past trip, let PriceWise decide with what it knew then, then reveal the receipt."),
]


def three_actions() -> None:
    cols = st.columns(3)
    for col, (page, title, icon, text) in zip(cols, ACTIONS):
        with col, st.container(border=True):
            st.html(f'<div class="pw-action"><div class="t">{ui.esc(title)}</div><div class="d">{ui.esc(text)}</div></div>')
            st.page_link(page, label=title, icon=f":material/{icon}:")


if db.is_empty():
    ui.header("Your grocery price memory", "PriceWise remembers every price you pay, checks every receipt, and "
              "plans where to buy, using only prices you actually saw. Everything stays on this computer.")
    if db.profile() == "demo":
        demo.open_demo()
        st.rerun()
    c1, c2 = st.columns(2)
    with c1, st.container(border=True):
        st.subheader("Open the demo")
        st.write("Six months of made-up trips to Walmart, Aldi and Kroger, kept in a separate demo file. Your own "
                 "data is never mixed with it or changed by it.")
        if st.button("Open the demo", type="primary"):
            with st.spinner("Printing six months of receipts..."):
                demo.open_demo()
            st.rerun()
    with c2, st.container(border=True):
        st.subheader("Start with your own receipts")
        st.write("Import a digital receipt, photograph a paper one, or type a purchase. Plans get useful after "
                 "three or four trips.")
        st.page_link("views/import_receipts.py", label="Import a receipt", icon=":material/mail:")
        st.page_link("views/scan.py", label="Photo of a paper receipt", icon=":material/receipt_long:")
        st.page_link("views/add.py", label="Add a purchase", icon=":material/edit_note:")
    st.stop()

pur = db.purchases()
recs = db.receipts_df()
issues = audit.audit()
open_money = [i for i in issues if i.status in ("open", "reviewed") and i.claimable]
board = savings.scoreboard()

ui.header("Today", "Three things to do, then your latest trip, checked line by line.")
three_actions()

left, right = st.columns([5, 7], gap="large")
with left:
    options = recs.head(8)
    labels = {int(r["id"]): f"{r['store']}, {ui.d(r['purchased_on'])}" for _, r in options.iterrows()}
    rid = st.selectbox("Receipt", list(labels), format_func=labels.get, label_visibility="collapsed")
    r = recs[recs["id"] == rid].iloc[0]
    pur_all = db.purchases(include_unverified=True)
    lines = pur_all[pur_all["receipt_id"] == rid]
    alerts = {a.item_id: a for a in analytics.alerts_for(pur, [rid])}
    by_item: dict[int, list] = {}
    for i in issues:
        if i.receipt_id == rid:
            for it in i.item_ids:
                by_item.setdefault(it, []).append(i)
    rows = []
    for _, l in lines.iterrows():
        notes, flag = [], False
        for i in by_item.get(int(l["item_id"]), []):
            if i.kind == "unusual_price":
                continue
            notes.append(ui.sticker(f"{audit.SHORT[i.kind]} ${i.amount:.2f}"))
            flag = True
        a = alerts.get(int(l["item_id"]))
        if a:
            notes.append(ui.sticker(f"{a.pct:+.0%} vs usual", "yellow" if a.direction == "high" else "green"))
        rows.append({"name": l["raw_text"] or db.product_label(l), "price": f"{l['paid']:.2f}", "flag": flag,
                     "note_html": " ".join(notes)})
    ui.receipt(r["store"], f"{r['purchased_on']:%A} {ui.d(r['purchased_on'])}, {r['purchased_on'].year}", rows, float(r["total"]),
               footer=f"{len(lines)} items, checked by PriceWise" + (
                   "; UNVERIFIED, kept out of advice" if r.get("status") == "unverified" else ""), animate=True)

with right:
    month = pur["month"].max()
    months = sorted(pur["month"].unique())
    prev = months[-2] if len(months) > 1 else None
    spent = pur.loc[pur["month"] == month, "paid"].sum()
    foot = ""
    if prev:
        prev_spent = pur.loc[pur["month"] == prev, "paid"].sum()
        foot = f"{pd.Period(prev).strftime('%B')}: ${prev_spent:,.2f}"
    ui.tags([
        (f"Spent in {pd.Period(month).strftime('%B')}", float(spent), foot, ""),
        ("Money you can ask back", float(sum(i.amount for i in open_money)),
         f"{len(open_money)} problems on your receipts", "red" if open_money else "paper"),
        ("Saved so far", board["refund"] + board["documented"],
         f"${board['refund']:.2f} refunds, ${board['documented']:.2f} from advice", "green"),
    ])

    st.subheader("Do next")
    todo = []
    for i in open_money[:3]:
        todo.append((ui.sticker("Claim"), f"{i.title}: {i.label}", f"{i.store}, {ui.d(i.purchased_on)}",
                     f"${i.amount:.2f}", "red"))
    for a in [x for x in stockup.advise(pur=pur) if x.action != "normal"][:3]:
        todo.append((ui.sticker("Stock up" if a.action == "stock up" else "Wait",
                                "green" if a.action == "stock up" else "ink"), a.label, a.message,
                     a.current, "green" if a.action == "stock up" else ""))
    for e in shrink.detect(pur)[:1]:
        todo.append((ui.sticker("Shrunk", "yellow"), f"{e.brand} {e.name}: {e.old_size} → {e.new_size}",
                     e.verdict, f"{e.unit_change:+.0%}", "red"))
    sw = swaps.find(pur=pur)[:1]
    for s in sw:
        todo.append((ui.sticker("Swap", "yellow"), f"{s.from_label} → {s.to_label}",
                     f"{s.saving_pct:.0%} cheaper per unit at {s.where}", f"${s.yearly_saving:,.0f}/yr", "green"))
    if todo:
        ui.rows(todo)
    else:
        ui.empty("Nothing needs your attention. Scan your next receipt after shopping.")
    c1, c2, c3 = st.columns(3)
    c1.page_link("views/overcharges.py", label="Overcharges", icon=":material/gpp_maybe:")
    c2.page_link("views/stockup.py", label="Stock-up radar", icon=":material/radar:")
    c3.page_link("views/import_receipts.py", label="Import next receipt", icon=":material/mail:")
