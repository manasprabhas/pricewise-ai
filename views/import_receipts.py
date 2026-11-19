from pathlib import Path

import pandas as pd
import streamlit as st

from pricewise import db, digital, gemini, review_ui, ui

KEY = "import_review"
SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "sample_email_receipt.eml"

ui.header("Import digital receipts", "Emailed receipts, PDFs from store apps and online order history. The text is "
          "already exact, so there's no photo to take and almost nothing to fix.")

review_ui.show_saved(KEY)
if KEY in st.session_state:
    review_ui.render(KEY)
    st.stop()


def begin(filename: str, data: bytes, use_gemini: bool) -> None:
    try:
        text, hints = digital.extract(filename, data)
        rec = digital.parse_with_gemini(text) if use_gemini else digital.parse(text, hints)
    except Exception as e:
        st.error(f"Couldn't read {filename}: {e}")
        return
    if not rec.items:
        st.error(f"No product lines found in {filename}. If it's an unusual layout, switch on “Read with Gemini”, "
                 "or paste the item lines as text.")
        return
    review_ui.start(rec, KEY, filename, digital.file_hash(data))
    st.rerun()


use_gemini = st.toggle("Read with Gemini", value=False, disabled=not gemini.enabled(),
                       help="Optional. Handles unusual receipt layouts. Sends only the receipt text. Switch Gemini "
                            "on in Settings first.")
tab_file, tab_inbox, tab_paste, tab_csv = st.tabs(["Receipt file", "Inbox folder", "Paste text", "Order history (CSV)"])

with tab_file:
    c1, c2 = st.columns([3, 2], gap="large")
    with c1:
        up = st.file_uploader("Emailed receipt (.eml), PDF, web page (.html) or text file",
                              type=["eml", "pdf", "html", "htm", "txt"])
        if up is not None and st.button("Read this receipt", type="primary"):
            begin(up.name, up.getvalue(), use_gemini)
        if st.button("Try the sample emailed receipt"):
            begin(SAMPLE.name, SAMPLE.read_bytes(), use_gemini)
    with c2:
        ui.note("<b>Getting the files.</b> Most email apps can save a message as a file (.eml) or print it to PDF. "
                "Store apps usually let you view or share a receipt as a PDF. Many stores will email receipts if "
                "you ask at checkout or turn it on in their app.")

with tab_inbox:
    folder = digital.inbox_dir()
    st.write("Save receipt files into this folder whenever you get them. PriceWise lists the ones it hasn't "
             "imported yet:")
    st.code(str(folder), language=None)
    files = digital.inbox_files()
    if not files:
        ui.empty("No new files in the inbox.")
    else:
        st.write(f"**{len(files)} new receipt{'s' if len(files) > 1 else ''}**")
        for f in files[:20]:
            c = st.columns([5, 2])
            c[0].write(f.name)
            if c[1].button("Import", key=f"inbox_{f.name}"):
                begin(f.name, f.read_bytes(), use_gemini)

with tab_paste:
    text = st.text_area("Paste the receipt text", height=220,
                        placeholder="Copy the items from the email or order page, one per line, e.g.\n"
                                    "Whole Milk 1 gal   $3.48\nBananas 2.31 lb @ $0.58/lb   $1.34\nSubtotal $4.82")
    if st.button("Read pasted text", type="primary", disabled=not text.strip()):
        begin("pasted text", text.encode(), use_gemini)

with tab_csv:
    st.write("For order history you've downloaded, or a spreadsheet you kept. One row per item.")
    up = st.file_uploader("CSV file", type=["csv"], key="csv_up")
    if up is not None:
        df = pd.read_csv(up)
        st.dataframe(df.head(8), hide_index=True, width="stretch")
        cols = ["(none)"] + df.columns.tolist()
        guess = lambda *names: next((i for i, c in enumerate(cols) if any(n in c.lower() for n in names)), 0)
        c = st.columns(3)
        m = {"date": c[0].selectbox("Date column", cols, index=guess("date")),
             "store": c[1].selectbox("Store column", cols, index=guess("store", "retailer", "merchant")),
             "item": c[2].selectbox("Item column", cols, index=guess("item", "product", "description", "name"))}
        c = st.columns(3)
        m["qty"] = c[0].selectbox("Quantity column", cols, index=guess("qty", "quantity"))
        m["total"] = c[1].selectbox("Line total column", cols, index=guess("total", "amount", "paid"))
        m["price"] = c[2].selectbox("…or price each column", cols, index=guess("price", "unit"))
        default_store = st.text_input("Store name (if there's no store column)") if m["store"] == "(none)" else None
        m = {k: (None if v == "(none)" else v) for k, v in m.items()}
        ready = m["date"] and m["item"] and (m["total"] or m["price"]) and (m["store"] or default_store)
        if st.button("Import order history", type="primary", disabled=not ready):
            with st.spinner("Matching items to your products..."):
                res = digital.import_csv(df, m, default_store)
            st.success(f"Imported {res['lines']} items on {res['receipts']} trips. {res['new_products']} new products "
                       "were created; rename or group them in Add a purchase if needed.")
            if res["skipped_duplicates"]:
                st.info(f"Skipped {res['skipped_duplicates']} trips that were already saved.")
