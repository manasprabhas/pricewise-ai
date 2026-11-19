from datetime import date

import pandas as pd
import streamlit as st

from pricewise import catalog, db, nutrition, ui

ui.header("Add a purchase", "No receipt? Type it in. You can also add new products and note prices you see on "
          "shelf tags or in weekly ads, which helps catch missed sales.")

tab_buy, tab_product, tab_price = st.tabs(["Purchases", "New product", "Shelf or ad price"])

with tab_buy:
    stores = db.stores()
    opts = catalog.product_options()
    if not opts:
        ui.empty("Add a product first in the New product tab.")
    else:
        c = st.columns([2, 1, 1])
        store_name = c[0].selectbox("Store", stores["name"].tolist() + ["➕ New store…"])
        new_store = c[0].text_input("Store name") if store_name == "➕ New store…" else None
        when = c[1].date_input("Date", value=date.today(), format="MM/DD/YYYY")
        tax = c[2].number_input("Tax", min_value=0.0, step=0.01, format="%.2f")
        if "manual_rows" not in st.session_state:
            st.session_state["manual_rows"] = pd.DataFrame(
                [{"product": None, "qty": 1.0, "line_total": None, "discount": 0.0}])
        st.caption("For items sold by weight, put the weight in pounds as the quantity.")
        rows = st.data_editor(
            st.session_state["manual_rows"], num_rows="dynamic", hide_index=True, width="stretch",
            key="manual_editor",
            column_config={
                "product": st.column_config.SelectboxColumn("Product", options=list(opts), width="large"),
                "qty": st.column_config.NumberColumn("Qty or lb", min_value=0.0, step=0.01, format="%.2f"),
                "line_total": st.column_config.NumberColumn("Paid for the line", format="$%.2f", min_value=0.0),
                "discount": st.column_config.NumberColumn("Discount", format="$%.2f", min_value=0.0),
            })
        valid = rows.dropna(subset=["product", "line_total"])
        st.write(f"**{len(valid)} lines, ${float((valid['line_total'] - valid['discount'].fillna(0)).sum()):,.2f}**")
        if st.button("Save purchase", type="primary", disabled=valid.empty):
            sid = db.get_or_create_store(new_store) if new_store else int(
                stores.loc[stores["name"] == store_name, "id"].iloc[0])
            lines = []
            for _, r in valid.iterrows():
                pid = opts[r["product"]]
                p = db.product(pid)
                qty = float(r["qty"] or 1)
                lines.append(dict(product_id=pid, qty=qty, qty_unit="lb" if p["sold_by_weight"] else "each",
                                  unit_price=round(float(r["line_total"]) / qty, 2) if qty else None,
                                  line_total=float(r["line_total"]), discount=float(r["discount"] or 0),
                                  raw_text=db.product_label(p)))
            try:
                db.save_receipt(sid, when, lines, tax=tax, source="manual")
            except db.DuplicateReceipt as e:
                st.error(f"Not saved: this exact purchase is already saved as receipt #{e.existing_id}.")
                st.stop()
            st.session_state.pop("manual_rows", None)
            st.success(f"Saved {len(lines)} lines.")
            st.page_link("views/memory.py", label="See price memory", icon=":material/history:")

with tab_product:
    with st.form("new_product", clear_on_submit=True):
        c = st.columns([3, 2])
        name = c[0].text_input("Product name", placeholder="Whole Milk")
        brand = c[1].text_input("Brand", placeholder="Great Value (leave empty for loose produce)")
        c = st.columns(3)
        kinds = catalog.kinds()
        kind = c[0].selectbox("Kind of food", kinds + ["other (use the name)"],
                              help="The same food across brands and stores, e.g. 'eggs'. Used for comparisons.")
        category = c[1].selectbox("Category", catalog.CATEGORIES)
        by_weight = c[2].checkbox("Sold by weight (price per lb)")
        c = st.columns(4)
        size = c[0].number_input("Package size", min_value=0.0, value=1.0)
        unit = c[1].selectbox("Unit", ["oz", "lb", "fl oz", "gal", "each", "dozen", "g", "kg", "ml", "l", "qt"])
        shelf = c[2].number_input("Keeps for (days)", min_value=1, value=catalog.SHELF_LIFE_GUESS["Other"])
        freezable = c[3].checkbox("Can be frozen")
        gpe = st.number_input("Grams per item (only for items counted, like eggs: 50)", min_value=0.0, value=0.0)
        if st.form_submit_button("Add product", type="primary"):
            if not name.strip():
                st.error("Give the product a name.")
            else:
                k = name.lower() if kind.startswith("other") else kind
                pid = catalog.create_product(name, k, category, None if by_weight else size, "lb" if by_weight else unit,
                                             brand, by_weight, int(shelf), freezable, gpe or None)
                note = " Nutrition filled in from reference values." if k in nutrition.REFERENCE else ""
                st.success(f"Added {db.product_label(db.product(pid))}.{note}")

with tab_price:
    st.write("Saw a price on a shelf tag or in a weekly ad? Note it. PriceWise checks your next receipt against it "
             "and uses it to learn when things go on sale.")
    opts = catalog.product_options()
    stores = db.stores()
    if opts and not stores.empty:
        with st.form("shelf_price", clear_on_submit=True):
            c = st.columns([3, 2])
            prod = c[0].selectbox("Product", list(opts))
            store = c[1].selectbox("Store", stores["name"].tolist())
            c = st.columns(4)
            price = c[0].number_input("Price", min_value=0.0, step=0.01, format="%.2f")
            per = c[1].selectbox("Per", ["package", "lb"])
            kind = c[2].selectbox("Seen on", ["shelf", "ad"], format_func={"shelf": "Shelf tag", "ad": "Weekly ad"}.get)
            when = c[3].date_input("Date", value=date.today(), format="MM/DD/YYYY")
            note = st.text_input("What the tag or ad says", placeholder="Spaghetti 2 for $5")
            terms = st.text_input("Offer terms", placeholder="buy 2; store card required; limit 4; valid through Sat",
                                  help="Needed before a missed offer can go in a refund note: minimum quantity, card "
                                       "or coupon, limits and dates. Leave empty for a plain shelf price.")
            if st.form_submit_button("Save price", type="primary") and price > 0:
                import uuid
                db.run("INSERT INTO shelf_prices(product_id, store_id, observed_on, price, per, kind, note, terms, "
                       "source, uuid) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (opts[prod], int(stores.loc[stores["name"] == store, "id"].iloc[0]), when.isoformat(), price,
                        per, kind, note or None, terms.strip() or None, "you, on the laptop", str(uuid.uuid4())))
                st.success("Saved." + ("" if terms.strip() or kind == "shelf" else
                                       " Add the offer's terms later if you want to claim a missed offer."))
