from pathlib import Path

import streamlit as st

from pricewise import gemini, review_ui, scanner, ui

KEY = "scan_review"
SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "sample_receipt.png"

ui.header("Photo of a paper receipt", "The backup way in, for trips with no digital receipt. Take a flat, well-lit "
          "photo, check what was read, fix anything doubtful, then save.")

review_ui.show_saved(KEY)
if KEY in st.session_state:
    review_ui.render(KEY)
    st.stop()

ready_offline = scanner.tesseract_ready()
ready_gemini = gemini.enabled()
c1, c2 = st.columns([3, 2], gap="large")
with c1:
    photo = st.file_uploader("Receipt photo", type=["jpg", "jpeg", "png", "webp"],
                             help="On a phone this opens the camera. Flat, well lit, whole receipt in frame.")
    use_camera = st.toggle("Use this device's camera instead", help="Works on this computer. On a phone over "
                           "Wi-Fi, use the upload button above; it offers the camera too.")
    if use_camera:
        photo = st.camera_input("Point at the receipt") or photo
    sample = st.button("Try the sample receipt")
with c2:
    engines = {"Offline reader": "offline", "Gemini (free tier)": "gemini"}
    choice = st.radio("Reader", list(engines), index=1 if ready_gemini else 0, horizontal=True,
                      help="Offline keeps everything on this computer. Gemini reads messy receipts better.")
    rotate = st.segmented_control("Rotate photo", [0, 90, 180, 270], default=0, format_func=lambda x: f"{x}°")
    if engines[choice] == "offline" and not ready_offline:
        ui.note("<b>Tesseract isn't installed yet.</b> Install it (free) using the setup guide, or switch on "
                "Gemini in Settings. Digital receipts and the in-store check don't need it.")
    if engines[choice] == "gemini" and not ready_gemini:
        ui.note("<b>Gemini is off.</b> Add a free API key and switch it on in Settings.")
    st.page_link("views/import_receipts.py", label="Have a digital receipt? Import it instead",
                 icon=":material/mail:")

image, mime = None, "image/png"
if sample:
    image = SAMPLE.read_bytes()
elif photo is not None:
    image, mime = photo.getvalue(), photo.type or "image/jpeg"
if image:
    st.image(image, width=320)
    if st.button("Read receipt", type="primary") or sample:
        with st.spinner("Reading every line..."):
            try:
                rec = scanner.scan(image, mime, rotate=rotate or 0, engine=engines[choice])
            except Exception as e:
                st.error(f"The scanner stopped: {e}")
                st.stop()
        if not rec.items:
            st.error("No product lines found. Try a sharper, flatter photo, rotate it, or add the purchase by hand.")
            st.stop()
        review_ui.start(rec, KEY, "receipt photo")
        st.rerun()
