"""PriceWise: AI Shopping Price Memory. Start with:  streamlit run app.py"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from pricewise import audit, db, savings, ui

st.set_page_config(page_title="PriceWise", page_icon="🏷️", layout="wide", initial_sidebar_state="auto")
db.init_db()
ui.inject()

if "secrets_checked" not in st.session_state:
    from pricewise import credentials
    stuck = credentials.migrate_from_database()
    st.session_state["secrets_checked"] = True
    if stuck:
        st.warning("Some API keys are still in the database because no secure credential store was found. "
                   "Move them to environment variables (see Settings).")

# other devices on the Wi-Fi must enter the phone PIN; the laptop itself doesn't
from pricewise import access  # noqa: E402

client_ip = access.client_ip()
if not access.is_local(client_ip) and not st.session_state.get("pin_ok"):
    ui.header("PriceWise is locked", "This device is connecting over the network.")
    if not access.pin_set():
        st.error("Phone access is off. On the laptop, open Settings and data → Phone access and set a PIN.")
        st.stop()
    wait = access.locked_out(client_ip)
    if wait:
        st.error(f"Too many wrong PINs. Try again in {int(wait // 60) + 1} minutes.")
        st.stop()
    with st.form("pin"):
        pin = st.text_input("PIN", type="password", max_chars=12)
        if st.form_submit_button("Unlock", type="primary"):
            if access.check_pin(pin, client_ip):
                st.session_state["pin_ok"] = True
                st.rerun()
            st.error("Wrong PIN.")
    st.stop()

P = lambda path, title, icon, **kw: st.Page(f"views/{path}.py", title=title, icon=f":material/{icon}:", **kw)
pages = {
    "Start here": [
        P("home", "Today", "storefront", default=True),
        P("import_receipts", "Import a receipt", "mail"),
        P("lab", "Plan a basket", "shopping_basket"),
        P("replay", "Replay a trip", "history_toggle_off"),
    ],
    "Receipts and products": [
        P("scan", "Photo of a paper receipt", "receipt_long"),
        P("add", "Add a purchase", "edit_note"),
        P("memory", "Price memory", "history"),
        P("checks", "Check matches", "fact_check"),
        P("pocket", "Pocket (phone)", "smartphone"),
    ],
    "Analysis": [
        P("stores", "Store showdown", "compare_arrows"),
        P("explain", "Why it cost more", "waterfall_chart"),
        P("trends", "Spending and inflation", "monitoring"),
        P("overcharges", "Checkout problems", "gpp_maybe"),
        P("shrink", "Shrinkflation", "compress"),
        P("nutrition", "Nutrition per dollar", "nutrition"),
        P("swaps", "Cheaper swaps", "swap_horiz"),
        P("stockup", "Stock-up radar", "radar"),
        P("ask", "Ask PriceWise", "chat"),
    ],
    "Track": [
        P("decisions", "Saved decisions", "lock"),
        P("savings", "Savings scoreboard", "savings"),
        P("evidence", "Evidence", "science"),
        P("settings", "Settings and data", "tune"),
    ],
}
nav = st.navigation(pages, expanded=True)
st.logo("static/logo.png", size="large")

with st.sidebar:
    if not db.is_empty():
        pur = db.purchases()
        month = pur["month"].max()
        spent = pur.loc[pur["month"] == month, "paid"].sum()
        open_issues = [i for i in audit.audit() if i.status in ("open", "reviewed") and i.claimable]
        board = savings.scoreboard()
        st.html(f"""<div class="pw-side-receipt">
<div class="row"><span>{pd.Period(month).strftime('%b %Y').upper()}</span><span>${spent:,.2f}</span></div>
<div class="row"><span>MONEY TO CLAIM</span><span>${sum(i.amount for i in open_issues):,.2f}</span></div>
<div class="row"><span>SAVED SO FAR</span><span>${board['refund'] + board['documented']:,.2f}</span></div>
<div class="row"><span>RECEIPTS</span><span>{pur['receipt_id'].nunique()}</span></div></div>""")
    if db.profile() == "demo":
        st.html('<div class="pw-demo-flag">DEMO DATA<br><span>fictional; your data is untouched</span></div>')
        if st.button("Back to my data", width="stretch"):
            from pricewise import demo as _demo
            _demo.leave_demo()
            st.rerun()
    elif access.is_local(client_ip):
        if st.button("Open the demo", width="stretch", help="A separate demo file; your data stays as is."):
            from pricewise import demo as _demo
            _demo.open_demo()
            st.rerun()

nav.run()
