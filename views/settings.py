import io
import zipfile
from datetime import date

import streamlit as st

from pricewise import access, backup, catalog, credentials, db, demo, gemini, kroger, scanner, ui

ui.header("Settings and data", "Everything is stored in one file on this computer. Nothing is sent anywhere "
          "unless you switch on Gemini, Kroger or the USDA and BLS lookups below.")



def save_secret(name: str, value: str) -> None:
    try:
        credentials.set(name, value)
    except credentials.CredentialStoreUnavailable as e:
        st.error(str(e))
        st.stop()


# the laptop itself only: other devices can't see or change keys, the PIN or your data
if not access.is_local(access.client_ip()):
    ui.empty("Settings can only be changed on the laptop itself.")
    st.stop()

tab_ai, tab_alerts, tab_phone, tab_kroger, tab_data = st.tabs(
    ["Receipt reading and AI", "Alerts", "Phone access", "Kroger prices", "Your data"])

with tab_phone:
    st.write("Other devices on your Wi-Fi (such as your phone with run-for-phone.bat) must enter this PIN. "
             "The laptop itself never needs it. Without a PIN, other devices are refused.")
    st.write("Status: **" + ("PIN set" if access.pin_set() else "no PIN, phone access is off") + "**")
    with st.form("set_pin", clear_on_submit=True):
        new_pin = st.text_input("New PIN (6 to 12 digits)", type="password", max_chars=12)
        if st.form_submit_button("Save PIN", type="primary"):
            try:
                access.set_pin(new_pin)
                st.success("PIN saved.")
            except ValueError as e:
                st.error(str(e))
    if access.pin_set() and st.button("Turn off phone access (remove PIN)"):
        access.clear_pin()
        st.rerun()
    st.caption("Over plain Wi-Fi the connection isn't encrypted, so use this only on your own home network. "
               "Optional: turn on HTTPS with a free local certificate (see the setup guide).")
    if not credentials.available():
        st.warning("No secure credential store was found on this computer. API keys can only be given as "
                   "environment variables (PRICEWISE_GEMINI_API_KEY and so on).")

with tab_ai:
    st.subheader("Offline reader (Tesseract)")
    path = scanner.find_tesseract()
    if path:
        st.success(f"Found Tesseract at {path}")
    else:
        st.warning("Tesseract not found. Install it from the setup guide, or type its location:")
    tp = st.text_input("Tesseract location", value=db.get_setting("tesseract_cmd") or "",
                       placeholder=r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if st.button("Save location"):
        db.set_setting("tesseract_cmd", tp.strip())
        st.rerun()

    st.subheader("Gemini (optional, free tier)")
    st.write("Reads crumpled or faded receipts better and understands questions in any wording. Off by default. "
             "On Google's free tier, what you send may be used to improve Google's products, so only the receipt "
             "photo or question is sent, never your history.")
    on = st.toggle("Use Gemini", value=db.get_setting("gemini_enabled") == "1")
    key = st.text_input("Gemini API key", value=credentials.get("gemini_api_key"), type="password",
                        help="Free from Google AI Studio (aistudio.google.com). Stored in your computer's credential "
                             "store (Windows Credential Manager), not in PriceWise's database.")
    model = st.text_input("Model", value=gemini.model(), help="Leave as is unless Google renames its models.")
    c = st.columns([1, 1, 4])
    if c[0].button("Save Gemini settings", type="primary"):
        db.set_setting("gemini_enabled", "1" if on else "0")
        save_secret("gemini_api_key", key)
        db.set_setting("gemini_model", model.strip() or "gemini-flash-latest")
        st.success("Saved.")
    if c[1].button("Test connection"):
        try:
            st.success("Gemini says: " + gemini.generate([{"text": "Reply with the single word: ready"}]).strip())
        except Exception as e:
            st.error(str(e))

    st.subheader("USDA nutrition lookup (optional)")
    usda = st.text_input("api.data.gov key", value=credentials.get("usda_api_key"), type="password",
                         help="Free at api.data.gov/signup. Without it, a few searches a day still work.")
    if st.button("Save USDA key"):
        save_secret("usda_api_key", usda)
        st.success("Saved.")

with tab_alerts:
    th = st.slider("Flag a price when it's this far from your usual", 5, 50, int(db.get_float("alert_threshold_pct", 15)),
                   format="%d%%")
    mh = st.slider("Only after this many earlier purchases", 1, 10, int(db.get_float("min_history", 3)))
    hh = st.number_input("People in your household", 1, 12, int(db.get_float("household_size", 2)))
    if st.button("Save alert settings", type="primary"):
        db.set_setting("alert_threshold_pct", th)
        db.set_setting("min_history", mh)
        db.set_setting("household_size", hh)
        st.success("Saved.")

with tab_kroger:
    st.write("For Kroger-family stores (Kroger, Ralphs, Fred Meyer, King Soopers, Fry's, Smith's, QFC and more). "
             "Create a free app at developer.kroger.com to get a client ID and secret. PriceWise then records daily "
             "prices for products you link, so the stock-up radar learns sale rhythms faster.")
    kon = st.toggle("Use Kroger price checks", value=db.get_setting("kroger_enabled") == "1")
    cid = st.text_input("Client ID", value=credentials.get("kroger_client_id"))
    sec = st.text_input("Client secret", value=credentials.get("kroger_client_secret"), type="password")
    if st.button("Save Kroger settings", type="primary"):
        db.set_setting("kroger_enabled", "1" if kon else "0")
        save_secret("kroger_client_id", cid)
        save_secret("kroger_client_secret", sec)
        st.success("Saved.")
    if kroger.enabled():
        c = st.columns([2, 3])
        zip_code = c[0].text_input("Your ZIP code")
        if c[0].button("Find stores") and zip_code:
            try:
                st.session_state["kroger_locs"] = kroger.find_locations(zip_code)
            except Exception as e:
                st.error(str(e))
        for loc in st.session_state.get("kroger_locs", []):
            cc = st.columns([4, 2])
            cc[0].write(f"{loc['name']}, {loc['address']}")
            if cc[1].button("Use this store", key=loc["id"]):
                sid = db.get_or_create_store(loc["name"] if "kroger" not in loc["name"].lower() else "Kroger")
                db.run("UPDATE stores SET kroger_location_id = ? WHERE id = ?", (loc["id"], sid))
                st.success("Saved.")
        st.subheader("Link a product")
        opts = catalog.product_options()
        lp = st.selectbox("Your product", list(opts))
        term = st.text_input("Search Kroger for", value=db.product(opts[lp])["kind"])
        store = db.one("SELECT kroger_location_id FROM stores WHERE kroger_location_id NOT IN ('', 'demo-location') "
                       "AND kroger_location_id IS NOT NULL")
        if st.button("Search") and store:
            try:
                st.session_state["kroger_hits"] = kroger.search(term, store["kroger_location_id"])
            except Exception as e:
                st.error(str(e))
        for h in st.session_state.get("kroger_hits", []):
            cc = st.columns([4, 2])
            cc[0].write(f"{h['brand'] or ''} {h['description']} ({h['size']}): ${h['regular']}"
                        + (f", promo ${h['promo']}" if h["promo"] else ""))
            if cc[1].button("Link", key=h["product_id"]):
                db.run("UPDATE products SET kroger_product_id = ? WHERE id = ?", (h["product_id"], opts[lp]))
                st.success("Linked.")
        st.caption("To check every day automatically, schedule `python -m pricewise.kroger` with Windows Task "
                   "Scheduler (see the setup guide).")

with tab_data:
    tables = db.export_tables()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, df in tables.items():
            if name == "settings":
                df = df[~df["key"].str.contains("key|secret|client_id|pin", regex=True)]
            z.writestr(f"{name}.csv", df.to_csv(index=False))
    c = st.columns(3)
    c[0].download_button("Export everything (CSV)", buf.getvalue(), file_name=f"pricewise-export-{date.today()}.zip",
                         help="API keys are left out of the export.")
    c[1].download_button("Back up the database", backup.make_backup() if db.db_path().exists() else b"",
                         file_name=f"pricewise-backup-{date.today()}.db",
                         help="A consistent copy of everything, even while the app runs.")
    st.subheader("Restore a backup")
    up = st.file_uploader("PriceWise backup (.db)", type=["db"], key="restore_up")
    if up is not None:
        try:
            found = backup.check_file(up.getvalue())
            st.write("Backup contains: " + ", ".join(f"{v} {k}" for k, v in found.items()))
            st.write("Currently open: " + ", ".join(f"{v} {k}" for k, v in backup.counts().items()))
            if st.button("Restore it (your current data is kept as a copy first)", type="primary"):
                res = backup.restore(up.getvalue())
                st.success("Restored: " + ", ".join(f"{v} {k}" for k, v in res["restored"].items())
                           + (f". Previous data saved as {res['previous_copy']}." if res["previous_copy"] else "."))
        except backup.RestoreError as e:
            st.error(str(e))
    st.divider()
    st.subheader("Demo and your data")
    if db.profile() == "demo":
        st.write("You're looking at the **demo** (data/demo.db). Your own data (data/pricewise.db) is untouched.")
        c = st.columns(3)
        if c[0].button("Back to my data", type="primary"):
            demo.leave_demo()
            st.rerun()
        if c[1].button("Reset the demo"):
            demo.open_demo(reset=True)
            st.rerun()
    else:
        st.write("You're looking at **your data** (data/pricewise.db). The demo lives in a separate file.")
        if st.button("Open the demo"):
            demo.open_demo()
            st.rerun()
        if st.button("Delete all my data"):
            st.session_state["confirm"] = "wipe"
        if st.session_state.get("confirm") == "wipe":
            st.warning("This deletes every receipt, product, decision and saving in your data. Settings stay. "
                       "Back up first if unsure.")
            cc = st.columns([1, 1, 4])
            if cc[0].button("Yes, delete it", type="primary"):
                db.wipe_all()
                st.session_state.pop("confirm")
                st.rerun()
            if cc[1].button("Cancel"):
                st.session_state.pop("confirm")
                st.rerun()

st.divider()
st.subheader("System check")
from pricewise import doctor  # noqa: E402

checks = doctor.run()
st.caption("The same check run.bat does before starting. Required items must pass; optional ones only matter for "
           "the feature named.")
st.dataframe([{"": "✅" if c.ok else ("❌" if c.required else "➖"), "item": c.name,
               "needed": "required" if c.required else "optional", "detail": c.detail} for c in checks],
             hide_index=True, width="stretch")
