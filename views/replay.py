import pandas as pd
import streamlit as st

from pricewise import db, decision as D, lab_ui, money, observations, ui

KEY = "replay"
STAGES = [("Before shopping", "what PriceWise knew"), ("Decision", "the plan it would make"),
          ("Actual receipt", "what really happened")]

ui.header("Replay a trip", "Go back to the morning of a past trip. PriceWise hides everything it learned after "
          "that day, makes its plan, then reveals what you actually bought and where the estimate held up.")
if db.is_empty():
    ui.empty("Replays need a few past trips. Load the demo or import receipts.")
    st.stop()

recs = db.receipts_df()
recs = recs[recs["purchased_on"] > recs["purchased_on"].min() + pd.Timedelta(days=28)]   # needs some history first
if recs.empty:
    ui.empty("Replays need at least four weeks of history before a trip.")
    st.stop()
labels = {int(r["id"]): f"{r['store']}, {ui.d(r['purchased_on'])} {r['purchased_on'].year} (${r['total']:.2f}, "
                        f"{r['n_items']} items)" for _, r in recs.iterrows()}
trip_id = st.selectbox("Trip to replay", list(labels), format_func=labels.get, key=f"{KEY}_trip")
trip = recs[recs["id"] == trip_id].iloc[0]
cutoff = trip["purchased_on"].date().isoformat()
if st.session_state.get(f"{KEY}_for") != trip_id:          # a new trip starts a new replay
    for k in [k for k in st.session_state if k.startswith(f"{KEY}_") and k not in (f"{KEY}_trip",)]:
        del st.session_state[k]
    st.session_state[f"{KEY}_for"] = trip_id
    st.session_state[f"{KEY}_stage"] = 0

stage = st.session_state.get(f"{KEY}_stage", 0)
ui.timeline(stage, [(t, d) for t, d in STAGES])
nav = st.columns(3)
for i, (t, _) in enumerate(STAGES):
    locked = i == 2 and not st.session_state.get(f"{KEY}_saved_id")
    if nav[i].button(t, key=f"{KEY}_go{i}", width="stretch", disabled=locked,
                     type="primary" if i == stage else "secondary"):
        st.session_state[f"{KEY}_stage"] = i
        st.rerun()

# ---------- basket origin (kept across stages)
origins = {"inferred": "My usual basket from trips before this day",
           "list": "A saved list",
           "actual": "The items I actually bought (reconstructed after the trip)"}
if stage < 2:
    oc = st.columns([3, 2])
    origin = oc[0].radio("Basket", list(origins), format_func=origins.get, horizontal=False, key=f"{KEY}_origin",
                         disabled=bool(st.session_state.get(f"{KEY}_saved_id")))
    lists = D.lists()
    list_id = (oc[1].selectbox("List", lists["id"].tolist(), format_func=lambda i: lists.set_index("id").loc[i, "name"],
                               key=f"{KEY}_listpick") if origin == "list" and not lists.empty else None)
    if origin == "actual":
        oc[1].warning("This basket uses knowledge of the trip itself (what you bought). Prices stay time-correct, "
                      "but the item list is hindsight. It's labelled that way everywhere.")
    sig = (origin, list_id)
    if st.session_state.get(f"{KEY}_basket_sig") != sig:
        st.session_state[f"{KEY}_basket"] = (D.infer_basket(cutoff, 1) if origin == "inferred"
                                             else D.load_list(int(list_id)) if origin == "list" and list_id
                                             else D.basket_from_receipt(trip_id) if origin == "actual" else [])
        st.session_state[f"{KEY}_basket_sig"] = sig
        st.session_state[f"{KEY}_ver"] = st.session_state.get(f"{KEY}_ver", 0) + 1

# ---------- 1. before shopping
if stage == 0:
    known = observations.load(cutoff)
    hidden = observations.hidden_after(cutoff)
    ui.tags([("Prices PriceWise knew", str(len(known)), f"everything dated before {cutoff}", ""),
             ("Hidden from the decision", str(hidden), "this trip and everything after it", "paper"),
             ("Newest known price", str(known["date"].max()) if not known.empty else "—",
              "the decision can't see past this day", "paper")])
    lab_ui.knowledge_chart(cutoff)
    basket = st.session_state.get(f"{KEY}_basket", [])
    st.subheader(f"The basket ({len(basket)} items)")
    st.write(", ".join(D.product_facts(b["product_id"])["label"] + (f" × {b['qty']:g}" if b["qty"] != 1 else "")
                       for b in basket) or "empty")
    if st.button("Next: make the decision", type="primary"):
        st.session_state[f"{KEY}_stage"] = 1
        st.rerun()

# ---------- 2. decision
elif stage == 1:
    frozen = st.session_state.get(f"{KEY}_saved_id")
    if frozen:
        d = D.load_decision(frozen)
        ui.note(f"<b>Frozen as decision #{frozen}.</b> The reveal compares the receipt with exactly this plan. "
                "To try other settings, pick the trip again to start a new replay.")
        lab_ui.full_results(f"{KEY}_frozen", d["inputs"], d["result"])
    else:
        left, right = st.columns([5, 4], gap="large")
        with left:
            basket = lab_ui.basket_editor(KEY, cutoff)
        with right:
            settings = lab_ui.settings_panel(KEY)
        lab_ui.substitutes_panel(KEY, basket, cutoff, settings)
        if basket:
            inputs = D.build_inputs(cutoff, basket, settings)
            result = D.compute(inputs)
            st.divider()
            lab_ui.full_results(KEY, inputs, result)
            with st.container(border=True):
                st.markdown("**Freeze this plan, then reveal the receipt.** Once frozen, nothing you learn from the "
                            "receipt can change it.")
                name = st.text_input("Name", value=f"Replay: {labels[trip_id]}", key=f"{KEY}_dname")
                if st.button("Freeze and reveal the receipt", type="primary"):
                    did = D.save(inputs, result, name + (" (basket reconstructed after the trip)"
                                                         if st.session_state.get(f"{KEY}_origin") == "actual" else ""),
                                 "replay", trip_id)
                    st.session_state[f"{KEY}_saved_id"] = did
                    st.session_state[f"{KEY}_stage"] = 2
                    st.rerun()

# ---------- 3. the reveal
else:
    did = st.session_state[f"{KEY}_saved_id"]
    d = D.load_decision(did)
    out = D.compare_with_receipt(d["inputs"], d["result"], trip_id)
    winner = D.plan_by_name(d["result"], d["result"]["winner"]) if d["result"]["winner"] else None
    obs_total = sum(o["saving_cents"] for o in out["observed"])
    ui.tags([
        ("The plan said", winner["name"] if winner else "no complete plan",
         f"decision cost {money.fmt(winner['decision_cents'])}" if winner else "", ""),
        ("You shopped at", out["store"],
         {"followed": "exactly the planned store",
          "partly": f"1 of the plan's {len(out['plan_stores'])} stores; the rest are estimates",
          "other": "not a planned store; the plan is an estimate"}[out["plan_relation"]],
         "green" if out["plan_relation"] == "followed" else "paper"),
        ("Prediction error", out["abs_error_cents"] / 100,
         f"{out['n_compared']} items compared; net {money.fmt(out['net_error_cents'], signed=True)}", "paper"),
        ("Observed differences", obs_total / 100,
         (f"{len(out['observed'])} items seen elsewhere that week; "
          + ("your choice saved this" if obs_total > 0 else "your choice cost this" if obs_total < 0 else "no difference"))
         if out["observed"] else "nothing comparable seen that week",
         "green" if obs_total > 0 else "red" if obs_total < 0 else "paper"),
    ])
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.subheader("Prediction vs. receipt")
        tone = {"compared": "", "substituted": "yellow", "missing": "ink", "extra": "ink"}
        rows = []
        for r in out["rows"]:
            pred = money.fmt(r["predicted_cents"]) if r["predicted_cents"] is not None else "—"
            act = money.fmt(r["actual_cents"]) if r["actual_cents"] is not None else "—"
            err = money.fmt(r["error_cents"], signed=True) if r["error_cents"] is not None else ""
            title = r["item"] or r["bought"]
            if r["type"] == "substituted":
                title = f"{r['item']} → bought {r['bought']}"
            detail = (f"{r['qty']:g} {r['unit']}: predicted {pred}, paid {act}. " if r["type"] != "missing"
                      else "") + r["note"]
            rows.append((ui.sticker(r["type"], tone[r["type"]]), title, detail, err,
                         "red" if (r["error_cents"] or 0) > 0 else "green" if (r["error_cents"] or 0) < 0 else ""))
        ui.rows(rows)
        st.caption("Error = paid − predicted for the store you actually visited. Positive means the receipt cost more "
                   "than predicted. Substituted, missing and extra items are never counted as errors.")
        adj = sum(a["cents"] for a in out["adjustments"])
        ui.note(f"<b>Every cent of the receipt:</b> lines {money.fmt(sum(r['actual_cents'] or 0 for r in out['rows']))}"
                + (f" + adjustments {money.fmt(adj)}" if adj else "") + f" + tax {money.fmt(out['tax_cents'])} = "
                f"{money.fmt(out['accounted_cents'])}; receipt total {money.fmt(out['total_cents'])}; "
                f"<b>unexplained {money.fmt(out['unreconciled_cents'])}</b>.")
        st.subheader("Stores you didn't visit")
        if out["observed"]:
            ui.rows([(ui.sticker("seen", "green"), o["item"],
                      f"{o['other_store']} had it at {money.fmt(o['other_price_cents'])} on {o['other_seen']}; you paid "
                      f"{money.fmt(o['paid_unit_cents'])}", money.fmt(o["saving_cents"], signed=True),
                      "green" if o["saving_cents"] > 0 else "red") for o in out["observed"]])
            st.caption("Only counted where the same product's price at another store was actually seen in the week "
                       "before the trip. A positive number is money the choice saved; negative is money it cost.")
        else:
            ui.empty("No other store's price for these products was seen in the week before. Any comparison with "
                     "other stores is an estimate, not a saving.")
        refunds = db.q("SELECT amount, description FROM savings WHERE kind = 'refund' AND receipt_id = ?", (trip_id,))
        st.subheader("Refunds for this receipt (separate ledger)")
        if refunds.empty:
            st.write("None recorded.")
        else:
            for _, r in refunds.iterrows():
                st.write(f"{money.fmt(money.cents(r['amount']))}: {r['description']}")
    with right:
        pur = db.purchases(include_unverified=True)
        lines = pur[pur["receipt_id"] == trip_id]
        by_item = {r["item_id"]: r for r in out["rows"] if r["item_id"]}
        strip = []
        for _, l in lines.iterrows():
            r = by_item.get(int(l["item_id"]))
            note = ""
            if r and r["type"] == "compared" and r["error_cents"] is not None:
                note = ui.sticker(f"{money.fmt(r['error_cents'], signed=True)} vs plan",
                                  "" if r["error_cents"] > 0 else "green")
            elif r:
                note = ui.sticker(r["type"], "yellow" if r["type"] == "substituted" else "ink")
            strip.append({"name": l["raw_text"] or db.product_label(l), "price": f"{l['paid']:.2f}", "note_html": note,
                          "flag": bool(r and (r["error_cents"] or 0) > 0)})
        ui.receipt(out["store"], f"{trip['purchased_on']:%A} {ui.d(trip['purchased_on'])}, revealed", strip,
                   out["total_cents"] / 100, footer=f"Decision #{did} frozen before this receipt was used",
                   animate=True)
        st.subheader("Share it")
        lab_ui.export_buttons(KEY, did, out)
