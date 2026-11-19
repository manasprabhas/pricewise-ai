import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from pricewise import analytics, db, ui

ui.header("Spending and inflation", "Where your grocery money goes each month, and how fast your own prices are "
          "rising compared with official US food inflation.")
if db.is_empty():
    ui.empty("No purchases yet.")
    st.stop()

pur = db.purchases()
by = st.segmented_control("Split spending by", ["Category", "Store"], default="Category")
col = "category" if by == "Category" else "store"
data = analytics.spend_by(pur, col)
fig = go.Figure()
groups = data.groupby(col)["paid"].sum().sort_values(ascending=False).index
palette = ui.store_colors(list(groups)) if col == "store" else {g: c for g, c in zip(groups, ui.STORE_COLORS * 3)}
for g in groups:
    d = data[data[col] == g]
    fig.add_bar(x=[pd.Period(m).strftime("%b") for m in d["month"]], y=d["paid"], name=g, marker_color=palette[g],
                marker_line=dict(color=ui.PAPER, width=1), hovertemplate=f"{g}: $%{{y:.2f}}<extra></extra>")
fig.update_layout(barmode="stack", yaxis_tickprefix="$")
ui.chart(fig, 380)

st.subheader("Your inflation vs. the official number")
pi = analytics.personal_inflation(pur)
c = st.columns([3, 2])
with c[1]:
    st.write("Your index compares the same product at the same store month to month, weighted by how much you "
             "spend on it. Switching stores doesn't count as inflation.")
    fetch = st.button("Get official US numbers", help="Downloads the food-at-home price index from the US Bureau "
                      "of Labor Statistics (free, no account). Needs internet once; then it's saved.")
cpi = pd.DataFrame()
try:
    years = sorted({int(m[:4]) for m in pi["month"]})
    cpi = analytics.official_cpi(years[0], years[-1], refresh=fetch)
except Exception as e:
    if fetch:
        c[1].error(f"Couldn't reach the Bureau of Labor Statistics: {e}")
with c[0]:
    fig = go.Figure()
    chg = pi.dropna(subset=["change"])
    fig.add_bar(x=[pd.Period(m).strftime("%b") for m in chg["month"]], y=chg["change"] * 100, name="You",
                marker_color=[ui.MARKDOWN if v > 0 else ui.PRODUCE for v in chg["change"]],
                text=[f"{v:+.1%}" for v in chg["change"]], textposition="outside",
                hovertemplate="You: %{y:.1f}%<extra></extra>")
    if not cpi.empty:
        cp = cpi[cpi["month"].isin(chg["month"])]
        fig.add_scatter(x=[pd.Period(m).strftime("%b") for m in cp["month"]], y=cp["change"] * 100,
                        name="Official food at home", mode="lines+markers", line=dict(color=ui.INK, width=3),
                        hovertemplate="Official: %{y:.1f}%<extra></extra>")
    fig.update_layout(yaxis_ticksuffix="%", yaxis_title="Price change from the month before")
    ui.chart(fig, 360)
total = pi["index"].iloc[-1] / 100 - 1
ui.tags([("Your prices since " + pd.Period(pi["month"].iloc[0]).strftime("%B"), f"{total:+.1%}",
          f"{len(pi) - 1} month-to-month comparisons", "red" if total > 0 else "green")]
        + ([("Official, same period", f"{cpi['value'].iloc[-1] / cpi[cpi['month'] == pi['month'].iloc[0]]['value'].iloc[0] - 1:+.1%}",
             "US food at home (BLS CPI)", "paper")]
           if not cpi.empty and (cpi["month"] == pi["month"].iloc[0]).any() else []))
