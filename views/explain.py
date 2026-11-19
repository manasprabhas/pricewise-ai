import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from pricewise import db, pvm, ui, units

ui.header("Why it cost more", "Splits any change in spending into four honest reasons: prices moved, you bought "
          "more or less, you picked pricier or cheaper options, or you bought new things.")
if db.is_empty():
    ui.empty("You need at least two trips to compare.")
    st.stop()

pur = db.purchases()
mode = st.segmented_control("Compare", ["Months", "Trips"], default="Months")
if mode == "Trips":
    recs = db.receipts_df()
    labels = {int(r["id"]): f"{r['store']}, {ui.d(r['purchased_on'])} (${r['total']:.2f})" for _, r in recs.iterrows()}
    ids = list(labels)
    c = st.columns(2)
    a = c[0].selectbox("Earlier trip", ids, index=min(1, len(ids) - 1), format_func=labels.get)
    b = c[1].selectbox("Later trip", ids, index=0, format_func=labels.get)
    A, B = pur[pur["receipt_id"] == a], pur[pur["receipt_id"] == b]
    name_a, name_b = labels[a].split(" (")[0], labels[b].split(" (")[0]
else:
    months = sorted(pur["month"].unique())
    if len(months) < 2:
        ui.empty("You need two months of receipts to compare months.")
        st.stop()
    nice = {m: pd.Period(m).strftime("%B %Y") for m in months}
    full = months[:-1] if len(months) > 2 else months
    c = st.columns(2)
    a = c[0].selectbox("Earlier month", months, index=months.index(full[-2]), format_func=nice.get)
    b = c[1].selectbox("Later month", months, index=months.index(full[-1]), format_func=nice.get)
    A, B = pur[pur["month"] == a], pur[pur["month"] == b]
    name_a, name_b = nice[a], nice[b]
    if b == months[-1] and len(months) > 2:
        st.caption(f"{nice[b]} isn't over yet, so it will look smaller than a full month.")

ex = pvm.explain(A, B)
ui.tags([
    (name_a, ex.spend_a, f"{A['receipt_id'].nunique()} trips", "paper"),
    (name_b, ex.spend_b, f"{B['receipt_id'].nunique()} trips", "paper"),
    ("Change", ex.change, ex.headline().split(". ", 1)[-1], "red" if ex.change > 0 else "green"),
])

order = ["price", "volume", "mix", "new", "dropped"]
fig = go.Figure(go.Waterfall(
    x=[name_a] + [pvm.LABELS[k].split(" (")[0] for k in order] + [name_b],
    measure=["absolute"] + ["relative"] * len(order) + ["total"],
    y=[ex.spend_a] + [ex.effects[k] for k in order] + [0],
    text=[f"${ex.spend_a:,.0f}"] + [units.fmt_money(ex.effects[k], signed=True) for k in order] + [f"${ex.spend_b:,.0f}"],
    textposition="outside", connector=dict(line=dict(color=ui.INK, width=1, dash="dot")),
    increasing=dict(marker=dict(color=ui.MARKDOWN)), decreasing=dict(marker=dict(color=ui.PRODUCE)),
    totals=dict(marker=dict(color=ui.INK)),
    hovertemplate="%{x}: %{text}<extra></extra>"))
fig.update_layout(yaxis_tickprefix="$", showlegend=False)
ui.chart(fig, 420)
st.caption(f"The five pieces add up exactly to the change (rounding difference ${abs(ex.check()):.2f}). "
           "“Pricier choices” includes picking a dearer brand, size or store for the same food.")

st.subheader("Food by food")
if not ex.by_kind.empty:
    top = ex.by_kind.head(10)
    ui.rows([(ui.sticker(units.fmt_money(r["change"], signed=True), "" if r["change"] > 0 else "green"),
              r["kind"].title(), pvm.kind_sentence(r).split(": ", 1)[-1],
              f"${r['spend_b']:,.2f}", "red" if r["change"] > 0 else "green") for _, r in top.iterrows()])
    with st.expander("All foods as a table"):
        st.dataframe(ex.by_kind[["kind", "spend_a", "spend_b", "change", "price", "volume", "mix", "new", "dropped"]]
                     .rename(columns={"spend_a": name_a, "spend_b": name_b}), hide_index=True, width="stretch")
