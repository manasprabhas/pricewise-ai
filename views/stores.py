import numpy as np
import plotly.graph_objects as go
import streamlit as st

from pricewise import analytics, db, ui, units

ui.header("Store showdown", "Which store has been cheapest for the things you actually buy, compared per pound, "
          "gallon or item. Brands can differ between stores; the comparison is for the same kind of food.")
if db.is_empty():
    ui.empty("Add receipts from at least two stores to compare them.")
    st.stop()

days = st.select_slider("Look back", [30, 60, 90, 120, 180, 365], value=120, format_func=lambda d: f"{d} days")
m = analytics.store_matrix(days)
stores = [c for c in m.columns if c != "unit"]
if len(stores) < 2:
    ui.empty("Only one store in this period. Shop somewhere else once and come back.")
    st.stop()
vals = m[stores].astype(float)
best = vals.min(axis=1)
premium = vals.div(best, axis=0) - 1
score = analytics.store_scorecard(days)

items = []
for s, r in score.iterrows():
    tone = "green" if s == score.index[0] else ("red" if s == score.index[-1] else "paper")
    items.append((s, f"+{r['avg_premium']:.0%}" if r["avg_premium"] > 0.004 else "Cheapest",
                  f"cheapest for {int(r['times_cheapest'])} of {int(r['kinds_compared'])} foods", tone))
ui.tags(items)

st.subheader("Price gap by food")
order = premium.max(axis=1).sort_values(ascending=False).index
z = premium.loc[order].values * 100
text = [[("" if np.isnan(vals.loc[k, s]) else f"${vals.loc[k, s]:.2f}/{'ea' if m.loc[k, 'unit'] == 'each' else m.loc[k, 'unit']}")
         for s in stores] for k in order]
fig = go.Figure(go.Heatmap(z=z, x=stores, y=[k.title() for k in order], text=text, texttemplate="%{text}",
                           colorscale=[[0, ui.PAPER], [0.25, ui.TAG], [1, ui.MARKDOWN]], zmin=0, zmax=60,
                           colorbar=dict(title="% above<br>cheapest", ticksuffix="%"), xgap=3, ygap=3,
                           hovertemplate="%{y} at %{x}: %{text} (%{z:.0f}% above cheapest)<extra></extra>"))
fig.update_layout(plot_bgcolor=ui.FREEZER, xaxis=dict(side="top", showgrid=False, linewidth=0),
                  yaxis=dict(autorange="reversed", showgrid=False, linewidth=0))
ui.chart(fig, height=60 + 30 * len(order))

st.subheader("What your shopping would have cost at one store")
pur = db.purchases()
recent = pur[pur["purchased_on"] >= pur["purchased_on"].max() - np.timedelta64(days, "D")]
qty = recent.groupby("kind")["base_qty_total"].sum()
costs = {}
for s in stores:
    unit_costs = (m[s] / m["unit"].map(units.base_factor)).dropna()
    covered = qty.index.intersection(unit_costs.index)
    costs[s] = (float((qty[covered] * unit_costs[covered]).sum()), len(covered))
actual = float(recent["paid"].sum())
fig = go.Figure(go.Bar(x=list(costs), y=[v[0] for v in costs.values()], marker_color=[ui.store_colors(stores)[s] for s in costs],
                       text=[f"${v[0]:,.0f}<br>{v[1]} of {len(qty)} foods" for v in costs.values()], textposition="outside"))
fig.add_hline(y=actual, line_dash="dash", line_width=3, line_color=ui.MARKDOWN,
              annotation_text=f"What you actually paid: ${actual:,.0f}")
fig.update_layout(yaxis_tickprefix="$", showlegend=False)
ui.chart(fig, 360)
st.caption("Uses each store's median price for the foods it sells and the amounts you bought. Foods a store "
           "never sold you are left out of its bar, so compare bars with similar coverage.")
