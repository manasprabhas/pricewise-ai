from datetime import date

import plotly.graph_objects as go
import streamlit as st

from pricewise import db, savings, ui

ui.header("Savings scoreboard", "Real money only. Refunds and advice you followed are counted separately from "
          "estimates, and followed advice is checked against what you actually paid afterwards.")

savings.evaluate()
b = savings.scoreboard()
ui.tags([("Refunds you got", b["refund"], "confirmed at the store", "green"),
         ("Saved by following advice", b["documented"], "checked against your later receipts", "green"),
         ("Could still save", b["estimated"], f"{b['open']} suggestions not acted on yet", "paper")])

c1, c2 = st.columns([3, 2], gap="large")
with c1:
    st.subheader("Did the advice work?")
    recs = db.q("SELECT id, created_on, message, status, expected_saving, outcome_saving FROM recommendations "
                "ORDER BY created_on DESC")
    if recs.empty:
        ui.empty("No advice recorded yet. Use “I'll try it” on Cheaper swaps or “I'll stock up” on the radar.")
    else:
        edited = st.data_editor(
            recs, hide_index=True, width="stretch", disabled=["id", "created_on", "message", "expected_saving",
                                                                        "outcome_saving"],
            column_config={"id": None, "created_on": st.column_config.DateColumn("Date", format="MMM D"),
                           "message": st.column_config.TextColumn("Advice", width="large"),
                           "status": st.column_config.SelectboxColumn("You", options=["open", "followed", "ignored"]),
                           "expected_saving": st.column_config.NumberColumn("Expected", format="$%.2f"),
                           "outcome_saving": st.column_config.NumberColumn("Actual", format="$%.2f",
                                                                           help="Checked against purchases in the "
                                                                                "60 days after you followed it")})
        changed = edited[edited["status"] != recs["status"]]
        for _, r in changed.iterrows():
            savings.decide(int(r["id"]), r["status"])
        if not changed.empty:
            st.rerun()
        if b["evaluated"]:
            st.write(f"**{b['hits']} of {b['evaluated']}** checked pieces of advice actually saved money.")
with c2:
    st.subheader("Ledger")
    led = savings.ledger()
    if led.empty:
        ui.empty("Nothing yet.")
    else:
        sums = led.groupby("kind")["amount"].sum()
        names = {"refund": "Refunds", "documented": "From advice", "estimated": "Estimated"}
        fig = go.Figure(go.Bar(x=[names[k] for k in sums.index], y=sums.values,
                               marker_color=[{"refund": ui.PRODUCE, "documented": ui.TAG, "estimated": ui.INK}[k]
                                             for k in sums.index],
                               marker_line=dict(color=ui.INK, width=2), text=[f"${v:.2f}" for v in sums.values],
                               textposition="outside"))
        fig.update_layout(yaxis_tickprefix="$", showlegend=False)
        ui.chart(fig, 220)
        st.dataframe(led, hide_index=True, width="stretch",
                     column_config={"logged_on": "Date", "kind": "Type",
                                    "amount": st.column_config.NumberColumn("Amount", format="$%.2f"),
                                    "description": "What"})
    with st.expander("Log a refund or saving by hand"):
        with st.form("manual_saving", clear_on_submit=True):
            kind = st.selectbox("Type", ["refund", "documented"],
                                format_func={"refund": "Refund", "documented": "Saving I can show"}.get)
            amt = st.number_input("Amount", min_value=0.0, step=0.01, format="%.2f")
            what = st.text_input("What for")
            if st.form_submit_button("Add") and amt > 0:
                db.run("INSERT INTO savings(logged_on, kind, amount, description) VALUES (?,?,?,?)",
                       (date.today().isoformat(), kind, amt, what))
                st.rerun()
