from datetime import date, timedelta

import streamlit as st

from pricewise import catalog, db, decision as D, lab_ui, ui

KEY = "lab"
ui.header("Plan a basket", "Price a basket at every combination of your stores, using prices you actually saw. "
          "See what it costs at the till, whether a second store pays off, and exactly which value would change "
          "the plan.")
if db.is_empty():
    ui.empty("Load the demo or import a few receipts first; plans need prices you've seen.")
    st.stop()

cutoff = D.today_cutoff()
st.caption(D.today_label() + ".")

# ---------- where the basket comes from
lists = D.lists()
c = st.columns([2, 3, 1])
source = c[0].segmented_control("Start from", ["My usual week", "A saved list", "Empty"],
                                default=st.session_state.get(f"{KEY}_source", "A saved list" if not lists.empty
                                                             else "My usual week"), key=f"{KEY}_src")
picked_list = None
if source == "A saved list" and not lists.empty:
    picked_list = c[1].selectbox("List", lists["id"].tolist(),
                                 format_func=lambda i: lists.set_index("id").loc[i, "name"], key=f"{KEY}_list")
if c[2].button("Load", width="stretch") or f"{KEY}_basket" not in st.session_state:
    if source == "A saved list" and picked_list:
        st.session_state[f"{KEY}_basket"] = D.load_list(int(picked_list))
    elif source == "My usual week":
        st.session_state[f"{KEY}_basket"] = D.infer_basket(cutoff, 1)
    else:
        st.session_state[f"{KEY}_basket"] = []
    st.session_state[f"{KEY}_ver"] = st.session_state.get(f"{KEY}_ver", 0) + 1
    st.session_state[f"{KEY}_exclude"] = []
    st.session_state[f"{KEY}_source"] = source
    st.rerun()

left, right = st.columns([5, 4], gap="large")
with left:
    basket = lab_ui.basket_editor(KEY, cutoff)
with right:
    settings = lab_ui.settings_panel(KEY, st.session_state.get(f"{KEY}_settings_in"))
lab_ui.substitutes_panel(KEY, basket, cutoff, settings)

c = st.columns([3, 1])
list_name = c[0].text_input("Save this basket as a list", placeholder="Weekly basics", label_visibility="collapsed")
if c[1].button("Save as list", disabled=not basket or not list_name):
    D.save_list(list_name, basket)
    st.toast(f"Saved list “{list_name}”.")

if not basket:
    ui.empty("Add products to the basket, or load your usual week.")
    st.stop()

inputs = D.build_inputs(cutoff, basket, settings)
result = D.compute(inputs)
st.divider()
lab_ui.full_results(KEY, inputs, result)
did = lab_ui.freeze_box(KEY, inputs, result, "current")
if did:
    st.page_link("views/decisions.py", label=f"Open saved decision #{did}", icon=":material/lock:")
