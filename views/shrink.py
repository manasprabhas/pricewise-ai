import pandas as pd
import streamlit as st

from pricewise import db, shrink, ui

ui.header("Shrinkflation", "Products you buy that got smaller while the price stayed the same or went up. "
          "Found by comparing package sizes of the same brand and product across your receipts.")
if db.is_empty():
    ui.empty("No purchases yet.")
    st.stop()

events = shrink.detect()
if not events:
    ui.empty("No shrinking packages in what you buy. PriceWise keeps checking every new receipt.")
    st.stop()

tag_slot = st.empty()

yearly = []
for i, e in enumerate(events):
    with st.container(border=True):
        st.subheader(f"{e.brand} {e.name}".strip())
        st.write(f"{e.verdict}. The smaller size first appeared on {ui.d(e.first_seen)}.")
        ratio = max(0.2, 1 + e.size_change)
        box = lambda h, size, price, unit, tone: (
            f'<div style="display:flex;flex-direction:column;align-items:center;gap:6px">'
            f'<div style="width:120px;height:{int(190 * h)}px;background:{tone};border:3px solid var(--ink);'
            f'display:flex;align-items:center;justify-content:center;font-family:var(--display);font-weight:900;'
            f'font-size:1.9rem;color:var(--ink)">{ui.esc(size)}</div>'
            f'<div style="font-family:var(--display);font-weight:800;font-size:1.5rem">${price:.2f}</div>'
            f'<div style="font-size:.9rem">{ui.esc(unit)}</div></div>')
        st.html('<div style="display:flex;gap:42px;align-items:flex-end;margin:.6rem 0 1rem">'
                + box(1.0, e.old_size, e.old_price, e.old_unit, "var(--paper)")
                + '<div style="font-family:var(--display);font-size:3rem;font-weight:900;padding-bottom:70px">→</div>'
                + box(ratio, e.new_size, e.new_price, e.new_unit, "var(--tag)")
                + f'<div style="padding-bottom:60px">{ui.sticker(f"{e.size_change:+.0%} size", "")}<br><br>'
                + f'{ui.sticker(f"{e.unit_change:+.0%} per unit", "")}</div></div>')
        st.dataframe(pd.DataFrame([
            {"size": e.old_size, "typical price": e.old_price, "per unit": e.old_unit, "bought": e.old_count,
             "price based on": f"last {e.old_priced_on} purchases", "first seen": e.old_first, "last seen": e.old_last},
            {"size": e.new_size, "typical price": e.new_price, "per unit": e.new_unit, "bought": e.new_count,
             "price based on": f"first {e.new_priced_on} purchases", "first seen": e.new_first,
             "last seen": e.new_last}]), hide_index=True, width="stretch",
            column_config={"typical price": st.column_config.NumberColumn(format="$%.2f"),
                           "bought": st.column_config.NumberColumn(format="%d×"),
                           "first seen": st.column_config.DateColumn(format="MMM D, YYYY"),
                           "last seen": st.column_config.DateColumn(format="MMM D, YYYY")})
        if e.coexist:
            ui.note(f"<b>Both sizes were on sale at the same time</b> ({e.old_size} last bought {ui.d(e.old_last)}, "
                    f"after the {e.new_size} first appeared). That can be a new size beside the old one rather than "
                    "a replacement; check the shelf before treating it as shrinkflation.")
        if min(e.old_count, e.new_count) < 2:
            st.caption("Limited history: one of the sizes was bought only once, so its typical price is a single "
                       "receipt.")
        st.caption(f"Seen at {e.stores}.")
        packs = st.slider("Packages a year", 1, 104, max(1, round(e.packs_per_year)), key=f"pp{i}",
                          help=f"Your receipts suggest about {e.packs_per_year:.0f} a year. Move it to see the "
                               "effect at a different pace.")
        extra = e.extra_for(packs)
        yearly.append(extra)
        st.write(f"At {packs} packages a year that's about **${extra:,.2f} more a year** for the same amount of food "
                 f"({e.old_unit} → {e.new_unit}).")
        st.page_link("views/swaps.py", label="Find a cheaper swap", icon=":material/swap_horiz:")

with tag_slot.container():
    ui.tags([("Products that shrank", len(events), "in your own purchases", "red"),
             ("Extra you pay a year", float(sum(yearly)), "at the pace set on each product", "red")])
