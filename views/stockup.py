import plotly.graph_objects as go
import streamlit as st

from pricewise import analytics, catalog, db, kroger, savings, stockup, ui, units

ui.header("Stock-up radar", "Learns what a good price is for you, how fast you use each food, and whether it "
          "goes on sale in a rhythm. Then: stock up, wait, or buy as normal, without buying more than you'll use.")
if db.is_empty():
    ui.empty("No purchases yet.")
    st.stop()

advice = stockup.advise()
up = [a for a in advice if a.action == "stock up"]
wait = [a for a in advice if a.action == "wait"]
ui.tags([("Stock up now", str(len(up)), "good prices you've seen this week", "green" if up else "paper"),
         ("Wait for a sale", str(len(wait)), "a dip is due within two weeks", "paper"),
         ("Foods tracked", str(len(advice)), "bought 3+ times", "paper")])

if kroger.enabled():
    if st.button("Check Kroger prices now"):
        try:
            st.write(kroger.check_prices())
        except Exception as e:
            st.error(str(e))

show = st.segmented_control("Show", ["Stock up and wait", "Everything"], default="Stock up and wait")
view = [a for a in advice if a.action != "normal"] if show == "Stock up and wait" else advice
for a in view:
    with st.container(border=True):
        c = st.columns([1, 6, 2])
        tone = {"stock up": "green", "wait": "ink", "normal": "yellow"}[a.action]
        c[0].html(ui.sticker(a.action.title(), tone))
        c[1].markdown(f"**{a.label}**  \n{a.message}")
        c[1].caption(f"You use about {a.weekly_use:.2g} {'items' if a.display_unit == 'each' else a.display_unit} a "
                     f"week. Good price: {a.good_price}. Usual: {a.usual}. Based on {a.observed} prices.")
        c[2].html(f'<div class="pw-big" style="text-align:right">{a.current}</div>')
        if a.action == "stock up":
            b = st.columns([2, 2, 5])
            if b[0].button("I'll stock up", key=f"su{a.product_id}", type="primary"):
                usual_cost = float(db.purchases().query("product_id == @a.product_id")["unit_cost"].median())
                rid = savings.record("stock_up", a.product_id, None, f"Stock up on {a.label} at {a.current}",
                                     usual_cost, a.expected_saving)
                savings.decide(rid, "followed")
                st.toast(f"Logged. Estimated saving ${a.expected_saving:.2f}; checked against your next receipts.")
            b[1].caption(f"Suggested: {a.suggest_qty:g} {a.qty_unit}, saves about ${a.expected_saving:.2f}")

st.subheader("Look at one food")
opts = catalog.product_options()
labels = {a.label: a for a in advice}
pick = st.selectbox("Product", list(labels))
a = labels[pick]
h = analytics.product_history(a.product_id)
du = a.display_unit
lbl = "ea" if du == "each" else du
fig = go.Figure()
good = h["unit_cost"].quantile(0.2)
fig.add_hrect(y0=0, y1=units.per_unit(good, du), fillcolor=ui.PRODUCE, opacity=.12, line_width=0,
              annotation_text="good price for you", annotation_position="bottom left")
for src, g in h.groupby("source"):
    fig.add_scatter(x=g["purchased_on"], y=g["unit_cost"].map(lambda c: units.per_unit(c, du)), name=src,
                    mode="markers" if src != "Kroger price check" else "lines",
                    line=dict(color=ui.INK_SOFT, width=1.5, shape="hv"),
                    marker=dict(size=10 if src == "Receipt" else 9, color=ui.INK if src == "Receipt" else ui.TAG,
                                line=dict(color=ui.INK, width=1.5), symbol="circle" if src == "Receipt" else "diamond"))
if a.next_sale is not None:
    fig.add_vline(x=a.next_sale, line_dash="dash", line_color=ui.MARKDOWN, line_width=2)
    fig.add_annotation(x=a.next_sale, y=1, yref="paper", text=f"next dip ~{ui.d(a.next_sale)}", showarrow=False,
                       font=dict(color=ui.MARKDOWN), xanchor="left")
fig.update_layout(yaxis_tickprefix="$", yaxis_title=f"$ per {lbl}")
ui.chart(fig, 340)

st.subheader("Will I use it before it spoils?")
c = st.columns([4, 2])
wp = c[0].selectbox("Food", list(opts), key="waste_pick")
qty = c[1].number_input("How many (or lb)", min_value=0.5, value=3.0, step=0.5)
w = stockup.waste_check(opts[wp], qty)
border = "var(--markdown)" if w["ok"] is False else "var(--produce)" if w["ok"] else "var(--tag)"
st.html(f'<div class="pw-note" style="border-color:{border}"><b>{"Fine" if w["ok"] else "Waste risk" if w["ok"] is False else "Unknown"}:</b> '
        f'{ui.esc(w["message"])}</div>')
