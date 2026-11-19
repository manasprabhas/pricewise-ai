import plotly.graph_objects as go
import streamlit as st

from pricewise import catalog, credentials, db, nutrition, ui

ui.header("Nutrition per dollar", "How much protein, energy or fiber each dollar buys, using the prices you "
          "actually paid. Nutrition values are approximate USDA reference values per 100 g.")
if db.is_empty():
    ui.empty("No purchases yet.")
    st.stop()

t = nutrition.table()
if t.empty:
    ui.empty("None of your products have nutrition values yet. Add them below.")
else:
    metric = st.segmented_control("Rank by", ["Protein", "Calories", "Fiber"], default="Protein")
    col, unit = {"Protein": ("protein_per_dollar", "g protein"), "Calories": ("kcal_per_dollar", "kcal"),
                 "Fiber": ("fiber_per_dollar", "g fiber")}[metric]
    cats = st.pills("Categories", sorted(t["category"].unique()), selection_mode="multi")
    v = t[t["category"].isin(cats)] if cats else t
    v = v[v[col] > 0].sort_values(col, ascending=False).head(15)
    if not v.empty:
        best = v.iloc[0]
        ui.tags([(f"Most {metric.lower()} per $1", f"{best[col]:,.0f} {unit.split()[0]}", best["label"], ""),
                 ("Cheapest 25 g of protein", float(t["cost_per_25g_protein"].min()),
                  t.loc[t["cost_per_25g_protein"].idxmin(), "label"], "green")])
        fig = go.Figure(go.Bar(y=v["label"], x=v[col], orientation="h",
                               marker=dict(color=ui.TAG, line=dict(color=ui.INK, width=2)),
                               text=[f"{x:,.0f} {unit}" for x in v[col]], textposition="outside",
                               customdata=v["unit_price"],
                               hovertemplate="%{y}<br>%{x:.0f} " + unit + " per $1 at %{customdata}<extra></extra>"))
        fig.update_layout(yaxis=dict(autorange="reversed"), xaxis_title=f"{unit} per $1",
                          xaxis_range=[0, float(v[col].max()) * 1.22])
        ui.chart(fig, 90 + 34 * len(v))
    with st.expander("Cost of 25 g protein, every product"):
        tt = t[t["cost_per_25g_protein"].notna() & (t["protein_g"] >= 1)].sort_values("cost_per_25g_protein")
        st.dataframe(tt[["label", "cost_per_25g_protein", "unit_price", "protein_g", "kcal", "fiber_g", "source"]],
                     hide_index=True, width="stretch",
                     column_config={"label": "Product", "cost_per_25g_protein": st.column_config.NumberColumn(
                         "25 g protein costs", format="$%.2f"), "unit_price": "Your price",
                         "protein_g": "Protein /100 g", "kcal": "kcal /100 g", "fiber_g": "Fiber /100 g"})

st.subheader("Fill in nutrition for a product")
prods = db.products()
missing = prods[prods["protein_g"].isna()]
opts = catalog.product_options()
id_to_label = {v: k for k, v in opts.items()}
choices = [id_to_label[int(i)] for i in (missing["id"] if not missing.empty else prods["id"])]
pick = st.selectbox("Product", choices, help="Products without nutrition values are listed first.")
pid = opts[pick]
tab_label, tab_usda = st.tabs(["From the label", "Search USDA"])
with tab_label:
    with st.form("nut"):
        c = st.columns(3)
        pr = c[0].number_input("Protein g per 100 g", min_value=0.0)
        kc = c[1].number_input("kcal per 100 g", min_value=0.0)
        fb = c[2].number_input("Fiber g per 100 g", min_value=0.0)
        if st.form_submit_button("Save"):
            db.set_nutrition(pid, pr, kc, fb, "Package label")
            st.success("Saved.")
with tab_usda:
    st.caption("Free. Works without a key for a few searches; add your free api.data.gov key in Settings for more.")
    query = st.text_input("Search for", value=db.product(pid)["kind"])
    if st.button("Search USDA FoodData Central"):
        try:
            st.session_state["usda"] = nutrition.usda_search(query, credentials.get("usda_api_key"))
        except Exception as e:
            st.error(f"Couldn't reach USDA: {e}")
    for n, r in enumerate(st.session_state.get("usda", [])):
        c = st.columns([5, 2])
        c[0].write(f"{r['description']}: {r['protein_g']} g protein, {r['kcal']} kcal, {r['fiber_g']} g fiber per 100 g")
        if c[1].button("Use this", key=f"usda{n}"):
            db.set_nutrition(pid, r["protein_g"], r["kcal"], r["fiber_g"], f"USDA FDC {r['fdc_id']}")
            st.success("Saved.")
