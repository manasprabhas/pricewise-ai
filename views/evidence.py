import json
import re
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from pricewise import db, evidence as E, ui

ROOT = Path(__file__).resolve().parent.parent
SETS = {"Fictional samples (ship with PriceWise)": ROOT / "eval" / "sample",
        "Your pilot receipts (eval/real)": ROOT / "eval" / "real"}

ui.header("Evidence", "How well PriceWise actually works, measured so that it can't flatter itself: every number "
          "comes with its denominator, the evaluator is tested against cases built to fool it, and every run "
          "carries a fingerprint so it can be reproduced.")

tab_read, tab_blind, tab_ocr, tab_rules, tab_effort, tab_pilot = st.tabs(
    ["Reading accuracy", "Blind spots", "Plain vs OpenCV", "Price rules", "Effort", "Pilot"])


def pct(x):
    return "—" if x is None or x != x else f"{x:.0%}"


# ---------------------------------------------------------------- reading accuracy --------------------------------
with tab_read:
    c = st.columns([3, 2, 2, 1])
    which = c[0].selectbox("Receipts", list(SETS), key="ev_set")
    folder = SETS[which]
    files = E.labelled_files(folder)
    engine = c[1].segmented_control("Photos read by", ["offline", "gemini"], default="offline", key="ev_engine",
                                    format_func={"offline": "Tesseract", "gemini": "Gemini"}.get)
    pipe = c[2].segmented_control("Photo clean-up", ["opencv", "plain"], default="opencv", key="ev_pipe",
                                  format_func={"opencv": "OpenCV", "plain": "None"}.get)
    run = c[3].button("Run", type="primary", disabled=not files, key="ev_run")
    if not files:
        ui.empty("No labelled receipts here yet. The Pilot tab walks you through labelling your own.")
    if "fiction" in which.lower():
        st.caption("These three are fictional and score 100%, which only shows the tool works. Real accuracy comes "
                   "from your own receipts in the Pilot tab.")
    if run:
        with st.spinner("Reading every receipt and scoring each field…"):
            prev = st.session_state.get("ev_result", {}).get("manifest")
            st.session_state["ev_result"] = E.evaluate_folder(folder, engine or "offline", pipe or "opencv")
            st.session_state["ev_prev"] = prev
    res = st.session_state.get("ev_result")
    if res and not res["receipts"].empty:
        f = res["fields"]
        lines = f[f["field"] == "Line found"].iloc[0]
        ui.tags([
            ("Lines found", f"{lines['correct']}/{lines['labelled']}", pct(lines["accuracy"]), "paper"),
            ("Extra lines", len(res["extra"]), "read but not on the receipt", "red" if len(res["extra"]) else "green"),
            ("Wrong fields", len(res["wrong"]), "on lines that were found", "red" if len(res["wrong"]) else "green"),
            ("Receipts", len(res["receipts"]), f"{int(res['receipts']['reconciles'].sum())} reconcile", "paper"),
        ])
        st.markdown("**Field by field**")
        st.dataframe(f, hide_index=True, width="stretch",
                     column_config={"field": "Field", "correct": "Right", "labelled": "Labelled (denominator)",
                                    "accuracy": st.column_config.ProgressColumn("Accuracy", min_value=0,
                                                                                max_value=1, format="percent")})
        st.caption("A field with 0 labelled has no evidence either way. Label it in your pilot receipts to measure it.")
        cc = st.columns(3)
        for col, (name, df, empty) in zip(cc, [("Confidently wrong", res["wrong"], "No wrong fields."),
                                               ("Missing lines", res["missing"], "No lines missed."),
                                               ("Extra lines", res["extra"], "No extra lines.")]):
            col.markdown(f"**{name}**")
            if df.empty:
                col.caption(empty)
            else:
                if name == "Confidently wrong":
                    df = df.sort_values("confidence", ascending=False)
                col.dataframe(df, hide_index=True, width="stretch", height=200)
        with st.expander("Per receipt"):
            st.dataframe(res["receipts"][["file", "kind", "lines_labelled", "lines_read", "lines_correct", "recall",
                                          "naive_recall", "store_ok", "date_ok", "total_ok", "reconciles", "seconds"]],
                         hide_index=True, width="stretch",
                         column_config={"recall": st.column_config.NumberColumn("Recall", format="percent"),
                                        "naive_recall": st.column_config.NumberColumn(
                                            "Recall (naive scorer)", format="percent",
                                            help="The old amount-plus-name scorer. Higher than Recall means it "
                                                 "counted something wrong as right.")})
        m = res["manifest"]
        prev = st.session_state.get("ev_prev")
        same = prev and prev["dataset"] == m["dataset"] and prev["results"] == m["results"]
        ui.evidence([f"Dataset {m['dataset']} ({len(m['files'])} files) · scorer {m['scorer']} · "
                     f"engine {m['engine']} · clean-up {m['pipeline']}",
                     f"Python {m['python']} · Tesseract {m['tesseract']} · "
                     + ", ".join(f"{k} {v}" for k, v in m["packages"].items()),
                     f"Results fingerprint {m['results']}"
                     + (" · identical to the previous run ✓" if same else
                        " · run again to check it reproduces" if not prev else
                        " · differs from the previous run (different data or settings)")])
        st.download_button("Download manifest", json.dumps(m, indent=1), "manifest.json")

# ------------------------------------------------------------------ blind spots -----------------------------------
with tab_blind:
    st.caption("Small cases built to fool an evaluator, run live every time this page opens. A naive scorer that "
               "only checks amount and name would report most of them as perfect reads.")
    bs = E.run_blind_spots()
    checks = ["naive evaluator", "field-level evaluator", "reconciliation", "identity check"]
    head = "".join(f"<th>{c}</th>" for c in ["Case"] + checks)
    body = []
    for _, r in bs.iterrows():
        control = r["case"] == "Read perfectly"
        cells = []
        for c in checks:
            v = r[c]
            cls, txt = (("na", "n/a") if v == "n/a" else ("ok", "no false alarm") if control else
                        ("ok", "caught") if v == "caught" else ("miss", "missed"))
            cells.append(f'<td class="{cls}">{txt}</td>')
        body.append(f'<tr><td><b>{ui.esc(r["case"])}</b><br><small>{ui.esc(r["what happens"])}</small>'
                    f'{"<br><small class=d>" + ui.esc(r["detail"]) + "</small>" if r["detail"] else ""}</td>'
                    + "".join(cells) + "</tr>")
    st.html(f'<table class="pw-matrix"><tr>{head}</tr>{"".join(body)}</table>')
    st.caption("No single check catches everything. The field-level evaluator is what measures the reader; "
               "reconciliation protects saved data; the identity check protects which product a line belongs to; "
               "your tick in review covers the rest.")

# ---------------------------------------------------------------- OCR comparison ----------------------------------
with tab_ocr:
    sample = ROOT / "eval" / "sample" / "sample_receipt.png"
    real_photos = [(p, l) for p, l in E.labelled_files(SETS["Your pilot receipts (eval/real)"])
                   if p.suffix.lower() in E.PHOTO]
    st.caption("The same image read twice: by Tesseract alone, and after the OpenCV clean-up (resize, denoise, "
               "adaptive threshold, deskew). The stress versions are synthetic: generated from the sample photo by "
               "tilting, blurring, fading, adding grain, shrinking and shading.")
    src = st.radio("Images", ["Sample photo + synthetic stress versions"]
                   + ([f"Your {len(real_photos)} pilot photos"] if real_photos else []), horizontal=True)
    if st.button("Run the comparison", type="primary", key="ocr_run"):
        with st.spinner("Reading each image twice…"):
            if src.startswith("Sample"):
                st.session_state["ocr"] = E.ocr_comparison(sample, json.loads(sample.with_suffix(".json").read_text()))
            else:
                rows = []
                for p, lab in real_photos:
                    for pipe in ("plain", "opencv"):
                        r = E.score(E.read_image(p.read_bytes(), pipe), lab)
                        rows.append({"image": p.name, "pipeline": "Plain Tesseract" if pipe == "plain"
                                     else "OpenCV pipeline", "recall": r["recall"],
                                     "lines found": f"{r['lines_correct']}/{r['lines_labelled']}",
                                     "extra lines": len(r["extra"]), "reconciles": r["reconciles"]})
                st.session_state["ocr"] = pd.DataFrame(rows)
    df = st.session_state.get("ocr")
    if df is not None and not df.empty:
        fig = go.Figure()
        for pipe, color in (("Plain Tesseract", "#9AA6B8"), ("OpenCV pipeline", "#1C2B4B")):
            g = df[df["pipeline"] == pipe]
            fig.add_bar(x=g["image"], y=g["recall"], name=pipe, marker_color=color,
                        text=g["lines found"], textposition="outside")
        fig.update_layout(barmode="group", yaxis=dict(tickformat=".0%", range=[0, 1.15], title="Lines read right"),
                          title="Lines read right, by image")
        ui.chart(fig, 360)
        wins = (df.pivot(index="image", columns="pipeline", values="recall").diff(axis=1).iloc[:, -1])
        st.caption(f"OpenCV better on {int((wins > 0).sum())}, equal on {int((wins == 0).sum())}, worse on "
                   f"{int((wins < 0).sum())} of {len(wins)} images. Clean-up costs about "
                   f"{df[df['pipeline'] == 'OpenCV pipeline']['seconds'].median() - df[df['pipeline'] == 'Plain Tesseract']['seconds'].median():.1f}s "
                   "more per photo." if "seconds" in df else "")
        st.dataframe(df, hide_index=True, width="stretch",
                     column_config={"recall": st.column_config.NumberColumn(format="percent")})

# ---------------------------------------------------------------- price rules -------------------------------------
with tab_rules:
    if db.is_empty():
        ui.empty("No receipts yet. Open the demo to see the benchmark on fictional data.")
    else:
        b = E.benchmark()
        if not b["n"]:
            ui.empty("Not enough history yet: the benchmark needs a month of receipts.")
        else:
            st.caption(f"Every receipt line from {b['start']} on ({b['n']} lines) is predicted from observations "
                       "dated strictly before that trip, at the same store. Coverage is how often a rule had an "
                       "answer at all; a rule that answers less often can look more accurate.")
            r = b["rules"].copy()
            best = r.loc[r["mean abs error"].idxmin(), "rule"]
            ch = r[r["chosen"]].iloc[0]
            ui.tags([
                ("PriceWise uses", b["chosen"].split(",")[0], b["chosen"].split(", ")[-1], ""),
                ("Its typical error", float(ch["mean abs error"]), f"mean, per line; bias {ch['bias']:+.2f}", "paper"),
                ("Coverage", pct(ch["coverage"]), ch["lines predicted"], "paper"),
                ("Lowest error", best.split(",")[0], best.split(", ")[-1] + " · on this data",
                 "green" if best == b["chosen"] else "paper"),
            ])
            if best != b["chosen"]:
                gap = ch["mean abs error"] - r["mean abs error"].min()
                ui.note(f"<b>Honest result:</b> on this data, “{ui.esc(best)}” predicts slightly better than the "
                        f"rule PriceWise uses (by ${gap:.3f} a line). The median is kept as the default because a "
                        "single sale or misread price moves it less; switch to “Last eligible price” in Plan a basket "
                        "if your stores rarely run promotions.")
            order = r.sort_values("mean abs error", ascending=False)
            fig = go.Figure(go.Bar(
                y=order["rule"] + order["chosen"].map({True: "  ★ default", False: ""}), x=order["mean abs error"],
                orientation="h", marker=dict(color=["#FFD43B" if c else "#1C2B4B" for c in order["chosen"]],
                                             line=dict(color="#1C2B4B", width=2)),
                text=[f"${e:.3f} · answers {c:.0%} of lines" for e, c in zip(order["mean abs error"],
                                                                             order["coverage"])],
                textposition="outside", cliponaxis=False,
                hovertemplate="%{y}<br>mean abs error $%{x:.3f}<extra></extra>"))
            fig.update_layout(xaxis=dict(title="Mean absolute error per line (lower is better)", tickprefix="$",
                                         range=[0, r["mean abs error"].max() * 1.6]),
                              title="Price error by rule", margin=dict(l=10, r=40, t=56, b=10))
            ui.chart(fig, 300)
            r["bias"] = r["bias"].map(lambda b: f"{'−' if b < 0 else '+'}${abs(b):.3f}")
            st.dataframe(r, hide_index=True, width="stretch",
                         column_config={"chosen": st.column_config.CheckboxColumn("PriceWise default"),
                                        "coverage": st.column_config.ProgressColumn(format="percent", min_value=0,
                                                                                    max_value=1),
                                        "mean abs error": st.column_config.NumberColumn(format="$%.3f"),
                                        "median abs % error": st.column_config.NumberColumn(format="percent"),
                                        "within 5%": st.column_config.NumberColumn(format="percent"),
                                        "bias": st.column_config.TextColumn(help="Average signed error. Negative: predicts too low.")})

# ---------------------------------------------------------------------- effort ------------------------------------
with tab_effort:
    ef = E.effort()
    if not ef["n"] and not ef["pocket_n"]:
        ui.empty("Nothing timed yet. Every receipt you review is timed from opening to saving, and Pocket times "
                 "each shelf price you note. Import a receipt to start.")
    else:
        tags = []
        if ef["n"]:
            tags += [("Receipts reviewed", ef["n"], "timed from open to save", "paper"),
                     ("Typical review", f"{ef['median_seconds']:.0f}s", f"{ef['seconds_per_row']:.1f}s per line",
                      "paper"),
                     ("Fixes per receipt", f"{ef['corrected_per_receipt']:.1f}",
                      f"{ef['flagged_share']:.0%} of lines flagged", "paper")]
        if ef["pocket_n"]:
            tags.append(("Pocket price notes", f"{ef['pocket_median']:.1f}s",
                         f"median; {ef['pocket_under_10']:.0%} under 10 s (n={ef['pocket_n']})",
                         "green" if ef["pocket_under_10"] >= 0.9 else "paper"))
        ui.tags(tags)
        if ef["n"]:
            st.dataframe(ef["sessions"], hide_index=True, width="stretch")

# ------------------------------------------------------------------------ pilot -----------------------------------
with tab_pilot:
    real = SETS["Your pilot receipts (eval/real)"]
    real.mkdir(parents=True, exist_ok=True)
    have = E.labelled_files(real)
    kinds = pd.Series(["photo" if p.suffix.lower() in E.PHOTO else "digital" for p, _ in have])
    stores = {l.get("store", "?") for _, l in have}
    n_lines = sum(len(l["lines"]) for _, l in have)
    goals = [("Receipts labelled", len(have), 30), ("Stores", len(stores), 3),
             ("Photos", int((kinds == "photo").sum()) if len(kinds) else 0, 10),
             ("Digital", int((kinds == "digital").sum()) if len(kinds) else 0, 10)]
    ui.tags([(g, f"{n}/{t}", "done" if n >= t else f"{t - n} to go", "green" if n >= t else "paper")
             for g, n, t in goals])
    st.caption(f"{n_lines} labelled lines so far. Include faded, long, discounted and weighed-item receipts: the "
               "easy ones flatter the numbers. Files stay in eval/real on this computer and are never committed.")
    st.markdown("#### Label a receipt")
    ui.note("<b>Type the label from the paper or the email itself.</b> Don't copy PriceWise's reading: a label "
            "copied from the reader can only ever agree with it. Remove card numbers, names and loyalty numbers "
            "from the file first.")
    up = st.file_uploader("Receipt file", type=sorted(x.strip(".") for x in E.PHOTO | E.DOC), key="pilot_file")
    c = st.columns(5)
    store = c[0].text_input("Store", key="pl_store")
    when = c[1].date_input("Date", value=date.today(), format="MM/DD/YYYY", key="pl_date")
    sub = c[2].number_input("Subtotal", min_value=0.0, step=0.01, format="%.2f", value=None, key="pl_sub")
    tax = c[3].number_input("Tax", min_value=0.0, step=0.01, format="%.2f", value=0.0, key="pl_tax")
    tot = c[4].number_input("Total", min_value=0.0, step=0.01, format="%.2f", value=None, key="pl_tot")
    blank = pd.DataFrame([{"text": "", "line_total": None, "qty": None, "discount": None, "size": "",
                           "variant": ""} for _ in range(6)])
    lines = st.data_editor(blank, num_rows="dynamic", hide_index=True, width="stretch", key="pl_lines",
                           column_config={"text": st.column_config.TextColumn("Line as printed", width="large"),
                                          "line_total": st.column_config.NumberColumn("Line total", format="$%.2f"),
                                          "qty": st.column_config.NumberColumn("Qty / weight", format="%.3f"),
                                          "discount": st.column_config.NumberColumn("Item discount", format="$%.2f"),
                                          "size": st.column_config.TextColumn("Size", help="Only if printed."),
                                          "variant": st.column_config.TextColumn("Variant",
                                                                                 help="e.g. lactose free")})
    rows = []
    for _, r in lines.iterrows():
        if str(r["text"]).strip() and pd.notna(r["line_total"]):
            row = {"text": str(r["text"]).strip(), "line_total": round(float(r["line_total"]), 2)}
            for k in ("qty", "discount"):
                if pd.notna(r[k]):
                    row[k] = float(r[k])
            for k in ("size", "variant"):
                if str(r[k] or "").strip():
                    row[k] = str(r[k]).strip()
            rows.append(row)
    check = round(sum(r["line_total"] - r.get("discount", 0) for r in rows), 2)
    if rows and sub is not None:
        st.caption(f"Your lines add up to ${check:.2f}; the subtotal you typed is ${sub:.2f}"
                   + (" ✓" if abs(check - sub) < 0.005 else " (they differ: check deposits, coupons or a typo)."))
    ready = up is not None and store.strip() and rows and tot is not None
    if st.button("Save label", type="primary", disabled=not ready, key="pl_save"):
        stem = re.sub(r"[^a-z0-9]+", "-", f"{store}-{when.isoformat()}".lower()).strip("-")
        n = 1
        while (real / f"{stem}-{n}{Path(up.name).suffix.lower()}").exists():
            n += 1
        path = real / f"{stem}-{n}{Path(up.name).suffix.lower()}"
        path.write_bytes(up.getvalue())
        path.with_suffix(".json").write_text(json.dumps(
            {"store": store.strip(), "date": when.isoformat(), "subtotal": sub, "tax": tax, "total": tot,
             "lines": rows, "labelled_by": "a person, from the receipt", "labelled_on": date.today().isoformat()},
            indent=1))
        st.success(f"Saved {path.name} and its label. Run it in Reading accuracy.")
    if have:
        with st.expander(f"Labelled so far ({len(have)})"):
            st.dataframe(pd.DataFrame([{"file": p.name, "store": l.get("store"), "date": l.get("date"),
                                        "lines": len(l["lines"]), "total": l.get("total")} for p, l in have]),
                         hide_index=True, width="stretch")
