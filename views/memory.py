import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from pricewise import analytics, db, ui, units

ui.header("Price memory", "Every price you've paid, per product. Pick one to see its history, your best price "
          "and where you got it.")
if db.is_empty():
    ui.empty("No purchases yet. Scan a receipt or add a purchase.")
    st.stop()

pur = db.purchases()
tab_products, tab_receipts = st.tabs(["Products", "Receipts"])

with tab_products:
    stats = analytics.product_stats(pur)
    c = st.columns([3, 4])
    search = c[0].text_input("Search", placeholder="milk, chicken, Aldi...", label_visibility="collapsed")
    cats = sorted(stats["category"].unique())
    pick_cats = c[1].pills("Category", cats, selection_mode="multi", label_visibility="collapsed")
    view = stats.copy()
    if search:
        s = search.lower()
        view = view[view["label"].str.lower().str.contains(s) | view["kind"].str.contains(s) |
                    view["last_store"].str.lower().str.contains(s)]
    if pick_cats:
        view = view[view["category"].isin(pick_cats)]
    view = view.sort_values("last_date", ascending=False)
    view["last_paid"] = [units.fmt_unit_price(r["last_unit_cost"], r["display_unit"]) for _, r in view.iterrows()]
    view["usual"] = [units.fmt_unit_price(r["median_unit_cost"], r["display_unit"]) for _, r in view.iterrows()]
    view["lowest"] = [units.fmt_unit_price(r["min_unit_cost"], r["display_unit"]) for _, r in view.iterrows()]
    event = st.dataframe(
        view[["label", "times_bought", "last_paid", "usual", "lowest", "change_vs_median", "last_store", "last_date"]],
        hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", height=330,
        column_config={"label": st.column_config.TextColumn("Product", width="large"),
                       "times_bought": st.column_config.NumberColumn("Bought", format="%d×"),
                       "last_paid": "Last paid", "usual": "Usual", "lowest": "Lowest",
                       "change_vs_median": st.column_config.NumberColumn("Last vs usual", format="percent"),
                       "last_store": "Last store",
                       "last_date": st.column_config.DateColumn("Last bought", format="MMM D")})
    sel = event.selection.rows
    if view.empty:
        ui.empty("No products match.")
        st.stop()
    if sel:
        pid = int(view.iloc[sel[0]]["product_id"])
    else:
        pid = int(view.loc[view["times_bought"].idxmax(), "product_id"])
        st.caption("Showing the product you buy most. Click a row to pick another.")

    p = db.product(pid)
    h = analytics.product_history(pid)
    du = p["display_unit"]
    rec = h[h["source"] == "Receipt"]
    st.subheader(db.product_label(p))
    last = rec.iloc[-1]
    low = rec.loc[rec["unit_cost"].idxmin()]
    good = h["unit_cost"].quantile(0.2)
    lbl = "ea" if du == "each" else du
    ui.tags([
        ("Last paid", float(units.per_unit(last["unit_cost"], du)), f"per {lbl} at {last['store']}, {ui.d(last['purchased_on'])}", ""),
        ("Your lowest", float(units.per_unit(low["unit_cost"], du)), f"per {lbl} at {low['store']}, {ui.d(low['purchased_on'])}", "green"),
        ("Usual", float(units.per_unit(rec["unit_cost"].median(), du)), f"median of {len(rec)} purchases", "paper"),
    ])
    fig = go.Figure()
    fig.add_hrect(y0=0, y1=units.per_unit(good, du), fillcolor=ui.PRODUCE, opacity=.10, line_width=0,
                  annotation_text="your best 20% of prices", annotation_position="bottom left")
    colors = ui.store_colors(h["store"].unique().tolist())
    for store, g in rec.groupby("store"):
        fig.add_scatter(x=g["purchased_on"], y=g["unit_cost"].map(lambda c: units.per_unit(c, du)), name=store,
                        mode="lines+markers", line=dict(color=colors[store], width=2.5, shape="hv"),
                        marker=dict(size=10, line=dict(color=ui.PAPER, width=2)),
                        hovertemplate=f"{store}<br>%{{x|%b %d}}: $%{{y:.2f}}/{lbl}<extra></extra>")
    other = h[h["source"] != "Receipt"]
    for src, g in other.groupby("source"):
        fig.add_scatter(x=g["purchased_on"], y=g["unit_cost"].map(lambda c: units.per_unit(c, du)), name=src,
                        mode="markers", marker=dict(size=7, symbol="diamond-open", color=ui.INK_SOFT),
                        hovertemplate=f"{src}<br>%{{x|%b %d}}: $%{{y:.2f}}/{lbl}<extra></extra>")
    fig.update_layout(yaxis_title=f"$ per {lbl}", yaxis_tickprefix="$")
    ui.chart(fig, 360)
    t = rec[["purchased_on", "store", "raw_text", "qty", "paid"]].sort_values("purchased_on", ascending=False).copy()
    t["unit price"] = [units.fmt_unit_price(c, du) for c in rec.sort_values("purchased_on", ascending=False)["unit_cost"]]
    st.dataframe(t, hide_index=True, width="stretch",
                 column_config={"purchased_on": st.column_config.DateColumn("Date", format="MMM D, YYYY"),
                                "store": "Store", "raw_text": "Receipt text", "qty": "Qty",
                                "paid": st.column_config.NumberColumn("Paid", format="$%.2f")})

with tab_receipts:
    recs = db.receipts_df()
    labels = {int(r["id"]): f"{ui.d(r['purchased_on'])} {r['purchased_on'].year}, {r['store']}, ${r['total']:.2f} "
                            f"({r['n_items']} items)" + (" UNVERIFIED" if r.get("status") == "unverified" else "")
              for _, r in recs.iterrows()}
    rid = st.selectbox("Receipt", list(labels), format_func=labels.get)
    r = recs[recs["id"] == rid].iloc[0]
    pur_all = db.purchases(include_unverified=True)
    lines = pur_all[pur_all["receipt_id"] == rid]
    c1, c2 = st.columns([1, 1], gap="large")
    with c1:
        ui.receipt(r["store"], f"{r['purchased_on']:%A} {ui.d(r['purchased_on'])}, {r['purchased_on'].year}",
                   [{"name": l["raw_text"] or db.product_label(l), "price": f"{l['paid']:.2f}"} for _, l in lines.iterrows()],
                   float(r["total"]), footer=f"Saved from: {r['source']}")
    with c2:
        status = r.get("status") or "manual"
        st.html(ui.sticker({"reconciled": "Reconciled", "manual": "Typed in", "unverified": "Unverified"}[status],
                           {"reconciled": "green", "manual": "ink", "unverified": ""}[status]))
        st.write(db.RECEIPT_STATUSES[status] + ".")
        st.write(f"Tax ${r['tax'] or 0:.2f}. Subtotal ${r['subtotal'] or 0:.2f}.")
        if st.button("Delete this receipt", help="Removes it and its prices from your memory."):
            st.session_state["confirm_delete"] = rid
        if st.session_state.get("confirm_delete") == rid:
            st.warning("Delete this receipt for good?")
            if st.button("Yes, delete it", type="primary"):
                db.delete_receipt(rid)
                st.session_state.pop("confirm_delete")
                st.rerun()
