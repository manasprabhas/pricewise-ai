import streamlit as st

from pricewise import db, savings, swaps, ui

ui.header("Cheaper swaps", "Cheaper versions of foods you buy, picked only from products you've already bought, "
          "so you know you like them. Savings use the amounts you actually buy.")
if db.is_empty():
    ui.empty("No purchases yet.")
    st.stop()

min_pct = st.slider("Only show swaps that save at least", 5, 40, 10, format="%d%%") / 100
found = swaps.find(min_saving=min_pct)
if not found:
    ui.empty("No cheaper versions among the products you've bought. Try a lower threshold.")
    st.stop()

ui.tags([("If you made every swap", float(sum(s.yearly_saving for s in found)), "a year, at your buying pace", "green"),
         ("Swaps found", str(len(found)), "one per kind of food", "paper")])

for s in found:
    with st.container(border=True):
        c = st.columns([5, 2])
        c[0].markdown(f"**{s.from_label}** at {s.from_unit}  \n→ **{s.to_label}** at {s.to_unit} (at {s.where})")
        if s.protein_note:
            c[0].caption(s.protein_note)
        c[1].html(f'<div style="text-align:right">{ui.sticker(f"-{s.saving_pct:.0%}", "green")}'
                  f'<div class="pw-big" style="color:var(--produce)">${s.yearly_saving:,.0f}</div>'
                  f'<div style="font-size:.85rem">a year</div></div>')
        if c[1].button("I'll try it", key=f"sw{s.from_id}-{s.to_id}"):
            pur = db.purchases()
            ref_cost = float(pur[pur["product_id"] == s.from_id]["unit_cost"].median())
            store = db.one("SELECT id FROM stores WHERE name = ?", (s.where,))
            rid = savings.record("swap", s.to_id, store["id"] if store else None,
                                 f"Try {s.to_label} instead of {s.from_label}", ref_cost, s.yearly_saving / 12)
            savings.decide(rid, "followed")
            st.toast("Noted. Your next purchases will show whether it really saved money.")
