from datetime import date

import streamlit as st

from pricewise import db, decision as D, pocket, ui

ui.header("Pocket (phone)", "A price checker that lives in one file on your phone and works with no signal: look a "
          "product up, type the shelf price, see it against what you paid. Notes come back here as a small file, "
          "with a preview first.")

tab_make, tab_import, tab_how = st.tabs(["Make Pocket for your phone", "Import from your phone", "How it works"])

with tab_make:
    if db.is_empty():
        ui.empty("Pocket carries your price history, and there isn't any yet. Import a receipt or open the demo.")
    else:
        lists = D.lists()
        opts = {None: "My usual week (worked out from your receipts)"}
        opts.update({int(r["id"]): r["name"] for _, r in lists.iterrows()})
        c = st.columns([3, 1, 2])
        lid = c[0].selectbox("Shopping list to take along", list(opts), format_func=opts.get)
        budget = c[1].number_input("Budget", min_value=0.0, value=35.0, step=5.0, format="%.0f")
        device = c[2].text_input("Phone name", value="my phone", help="Shown on imports so you know where a price came from.")
        b = pocket.bundle(lid, int(round(budget * 100)), device.strip() or "my phone")
        html = pocket.make_html(b)
        n_prices = sum(len(p["stores"]) for p in b["products"].values())
        ui.tags([("Products", len(b["products"]), f"{n_prices} store prices with dates", "paper"),
                 ("On the list", len(b["list"]["items"]), b["list"]["name"], "paper"),
                 ("Prices as of", ui.d(date.fromisoformat(b["prices_as_of"])), f"older than {pocket.STALE_DAYS} days marked stale", "paper"),
                 ("File size", f"{len(html) / 1024:.0f} KB", "fonts included; no internet needed", "paper")])
        st.download_button("Download pocket.html", html, file_name="pricewise-pocket.html", mime="text/html",
                           type="primary")
        st.caption("Send the file to your phone (cable, AirDrop, Nearby Share, or email it to yourself), then open it "
                   "from Files or Downloads. Bookmark it or add it to your home screen. Make a fresh one after "
                   "importing new receipts so its prices stay current.")
        with st.expander("Try it here first"):
            st.components.v1.html(html, height=720, scrolling=True)

with tab_import:
    up = st.file_uploader("Pocket export (.json)", type=["json"], key="pocket_up")
    if up is not None:
        try:
            data = pocket.parse_export(up.getvalue())
        except pocket.PocketError as e:
            st.error(str(e))
            st.stop()
        pv = pocket.preview(data)
        if pv["already"]:
            ui.note(f"<b>Already imported</b> on {ui.esc(pv['already']['imported_on'])}. Importing it again adds "
                    "nothing: every price is checked by its own id.")
        ui.tags([("New prices", pv["new"], f"from {ui.esc(data.get('device') or 'phone')}", "green" if pv["new"] else "paper"),
                 ("Skipped", pv["skipped"], "already here or not usable", "paper"),
                 ("List changes", len(pv["list_changes"]), "checked, removed, swapped, added", "paper"),
                 ("Final list", len(pv["final_list"]), "saved as a list you can plan with", "paper")])
        if not pv["observations"].empty:
            st.dataframe(pv["observations"].drop(columns=["uuid"]), hide_index=True, width="stretch",
                         column_config={"price": st.column_config.NumberColumn(format="$%.2f"),
                                        "seconds": st.column_config.NumberColumn("Time to note", format="%.1f s")})
        if pv["list_changes"]:
            with st.expander("List changes on the phone"):
                st.dataframe(pv["list_changes"], hide_index=True, width="stretch")
        if st.button("Import", type="primary", disabled=bool(pv["already"]) and not pv["new"]):
            r = pocket.apply_import(data)
            st.success(f"Imported {r['added']} prices" + (f", skipped {r['skipped']}" if r["skipped"] else "")
                       + (". The trip's final list is saved under Plan a basket › A saved list." if r["list_id"] else "."))
    imports = db.q("SELECT * FROM pocket_imports ORDER BY imported_on DESC")
    if not imports.empty:
        st.markdown("**Imported so far**")
        st.dataframe(imports.drop(columns=["timings"]), hide_index=True, width="stretch")

with tab_how:
    ui.rows([
        (ui.sticker("1"), "Search and pick the exact package", "Sizes and variants of the same food are one tap apart, "
                          "so the 15.4 oz box never borrows the 18 oz price.", "", ""),
        (ui.sticker("2"), "Type the shelf price on the keypad", "The basis is always shown: one package, “2 for”, or "
                          "per lb, with the unit price worked out.", "", ""),
        (ui.sticker("3"), "Compare with dates", "Last paid and usual here, the best elsewhere, each with its date and a "
                          "stale label past 120 days. A product with no history just records the shelf price.", "", ""),
        (ui.sticker("4"), "Shop", "Tick items into the cart for a running total and the budget left; remove, change the "
                          "amount or swap for a cheaper same-variant product.", "", ""),
        (ui.sticker("5"), "Export, then import here", "A small file with your prices, list changes and timings. The "
                          "preview shows what's new; repeats are skipped.", "", ""),
    ])
    ui.note("<b>Privacy you can check:</b> the file carries a content-security policy that blocks every network "
            "connection, and it contains no web addresses. Turn on airplane mode and it still works.")
    st.caption("Barcode scanning is planned for a later version.")
