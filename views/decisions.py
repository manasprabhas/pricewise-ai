from datetime import date, timedelta

import pandas as pd
import streamlit as st

from pricewise import db, decision as D, lab_ui, money, ui

ui.header("Saved decisions", "Every saved decision keeps its basket, settings, eligible prices and result. "
          "Reopen one to prove it reproduces, replay it against today's data, or rerun it as a new scenario.")
rows = db.q("SELECT id, created_at, name, mode, cutoff, trip_receipt_id, calc_version, result_hash FROM decisions "
            "ORDER BY id DESC")
if rows.empty:
    ui.empty("No saved decisions yet. Plan a basket or replay a trip, then press Save.")
    st.page_link("views/lab.py", label="Plan a basket", icon=":material/shopping_basket:")
    st.stop()

labels = {int(r["id"]): f"#{r['id']} {r['name']} ({r['mode']}, prices before {r['cutoff']})" for _, r in rows.iterrows()}
did = st.selectbox("Decision", list(labels), format_func=labels.get)
d = D.load_decision(did)
res, inp = d["result"], d["inputs"]
w = D.plan_by_name(res, res["winner"]) if res["winner"] else None
ui.tags([("Saved plan", w["name"] if w else "no complete plan",
          f"decision cost {money.fmt(w['decision_cents'])}" if w else "", ""),
         ("Prices used", str(len(inp["observations"])), f"all dated before {inp['cutoff']}", "paper"),
         ("Fingerprint", d["result_hash"][:8], f"{d['calc_version']}, saved {d['created_at']}", "paper")])

st.subheader("Prove it")
c = st.columns(3)
if c[0].button("Reproduce from frozen inputs", width="stretch"):
    st.session_state["dec_check"] = ("frozen", did)
if c[1].button("Replay against today's database", width="stretch"):
    st.session_state["dec_check"] = ("live", did)
if c[2].button("Run it again as a new scenario", width="stretch"):
    st.session_state["dec_check"] = ("new", did)
check = st.session_state.get("dec_check")
if check and check[1] == did:
    if check[0] == "frozen":
        rep = D.reproduce(did)
        (st.success if rep["same"] else st.error)(
            f"Recomputed from the frozen inputs: fingerprint {D.digest(rep['result'])[:8]} "
            f"{'matches' if rep['same'] else 'DOES NOT match'} the saved {d['result_hash'][:8]}.")
    elif check[0] == "live":
        live = D.replay_live(did)
        if live["same_result"]:
            st.success(f"Rebuilt from today's database with the same cutoff ({inp['cutoff']}): identical result. "
                       "Anything imported since then is dated on or after the cutoff, so it stays invisible.")
        else:
            st.warning("Today's database gives a different result for the same cutoff, because history BEFORE the "
                       f"cutoff was changed: {len(live['added'])} price(s) added, {len(live['removed'])} removed, "
                       f"{len(live['changed'])} changed. The saved decision itself is unchanged."
                       + (f" Winner: {live['result']['winner']} (saved: {res['winner']})." if not live["same_winner"]
                          else " The winner is the same."))
            if live["added"] or live["removed"] or live["changed"]:
                st.caption("Added: " + ", ".join(live["added"][:12]) + " | Removed: " + ", ".join(live["removed"][:12])
                           + " | Changed: " + ", ".join(live["changed"][:12]))
    else:
        cutoff = D.today_cutoff()
        new_inp = D.build_inputs(cutoff, inp["basket"], inp["settings"])
        new_res = D.compute(new_inp)
        old_ids = {o["obs_id"] for o in inp["observations"]}
        newer = [o for o in new_inp["observations"] if o["obs_id"] not in old_ids]
        nw = D.plan_by_name(new_res, new_res["winner"]) if new_res["winner"] else None
        (st.info if new_res["winner"] == res["winner"] else st.warning)(
            f"With every price known now ({cutoff}), the plan is **{new_res['winner'] or 'no complete plan'}**"
            + (f" at {money.fmt(nw['decision_cents'])}" if nw else "")
            + f" (saved decision: {res['winner'] or 'no complete plan'}). {len(newer)} newer price(s) became eligible.")
        if newer:
            used = {o for p in new_res["complete"][:1] for l in p["lines"] for o in l["estimate"]["obs_ids"]}
            st.dataframe(pd.DataFrame([{
                "newly eligible": o["obs_id"], "date": o["date"], "store": o["store"],
                "product": new_inp["products"][str(o["product_id"])]["label"],
                "price": money.fmt(o["price_cents"]) + ("/lb" if o["basis"] == "lb" else "/pkg"),
                "source": o["source"], "used by the new plan": "yes" if o["obs_id"] in used else ""}
                for o in sorted(newer, key=lambda o: str(o["date"]), reverse=True)]),
                hide_index=True, width="stretch")
        if newer and nw and w and nw["decision_cents"] == w["decision_cents"] and inp["settings"]["method"] == "median":
            st.caption("The newer price is used, but the recent median absorbs a single new price, so the total "
                       "doesn't move. Open the scenario and switch to “Last eligible price” to see it take effect.")
        if st.button("Open this scenario in Plan a basket"):
            st.session_state["lab_basket"] = inp["basket"]
            st.session_state["lab_settings_in"] = {k: v for k, v in inp["settings"].items() if k != "exclude_obs"}
            st.session_state["lab_ver"] = st.session_state.get("lab_ver", 0) + 1
            st.session_state["lab_exclude"] = []
            for k in ("lab_method", "lab_age", "lab_stop", "lab_budget"):
                st.session_state.pop(k, None)
            st.switch_page("views/lab.py")

st.subheader("The saved result")
st.caption("Read-only: this is exactly what was decided, with the evidence available then.")
lab_ui.full_results(f"dec{did}", inp, res)

outcome = None
if d["trip_receipt_id"] and db.one("SELECT id FROM receipts WHERE id = ?", (d["trip_receipt_id"],)):
    outcome = D.compare_with_receipt(inp, res, d["trip_receipt_id"])
    st.subheader("Outcome")
    st.write(f"Trip at {outcome['store']}: {outcome['n_compared']} items compared, absolute error "
             f"{money.fmt(outcome['abs_error_cents'])}, net {money.fmt(outcome['net_error_cents'], signed=True)}, "
             f"unexplained {money.fmt(outcome['unreconciled_cents'])}.")
st.subheader("Share it")
lab_ui.export_buttons("dec", did, outcome)
if st.button("Delete this decision"):
    db.run("DELETE FROM decisions WHERE id = ?", (did,))
    st.rerun()
