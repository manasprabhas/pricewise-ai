"""Evidence: measuring PriceWise honestly, including the ways measuring can fool itself.

1. Receipt reading, field by field, with denominators. A labelled line is matched to a predicted line only when the
   amount agrees to the cent, the names are similar, AND no attribute or size disagrees (so two same-price
   variants can't stand in for each other). Then each labelled field (amount, quantity, item discount, size,
   variant) is scored separately: "qty 41/43" means 43 labelled lines carried a quantity and 41 were read right.
2. A blind-spot suite: small cases built to fool a naive evaluator, run live, showing which check catches each.
3. OCR comparison: plain Tesseract against the OpenCV pipeline, on the same images.
4. A decision benchmark: for every past receipt line, predict its price from observations dated strictly
   before that trip, with several rules; report price error and coverage (how often a rule had an answer).
5. Effort, from timed review sessions, and a reproducibility manifest (hashes of inputs, code and results).
"""
from __future__ import annotations

import hashlib
import json
import platform
import statistics
import time
from dataclasses import dataclass
from datetime import date
from importlib import metadata
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz

from . import db, digital, matcher, money, observations, reconcile, scanner, units

SCORER_VERSION = "fields-v2"
PHOTO = {".png", ".jpg", ".jpeg", ".webp"}
DOC = {".pdf", ".eml", ".html", ".htm", ".txt"}
FIELDS = ["line_total", "qty", "discount", "size", "variant"]
FIELD_NAMES = {"line_total": "Amount", "qty": "Quantity / weight", "discount": "Item discount",
               "size": "Package size", "variant": "Variant", "text": "Line found"}


# ------------------------------------------------------------------ scoring ----------------------------------------
def _size(text: str):
    s = units.parse_size(text or "")
    if not s:
        return None
    try:
        return units.to_base(*s)
    except ValueError:
        return None


def _compatible(pred_text: str, want: dict) -> bool:
    """Same-price lines must not differ in an attribute or a printed size."""
    a, b = matcher.normalize(pred_text), matcher.normalize(want["text"])
    if matcher.attribute_conflict(a, b):
        return False
    if want.get("variant") and want["variant"].upper() not in f" {pred_text.upper()} " and \
            matcher.normalize(want["variant"]) not in a:
        return False
    sp, sw = _size(pred_text), _size(want["text"])
    if sp and sw and (sp[0] != sw[0] or abs(sp[1] - sw[1]) > 0.02 * sw[1]):
        return False
    return True


def match_lines(rec: scanner.ParsedReceipt, label: dict, strict: bool = True) -> list[tuple[int, int | None]]:
    """(label index, predicted index or None). Each predicted line is used at most once."""
    pred = list(rec.items)
    used, out = set(), []
    for li, want in enumerate(label["lines"]):
        best, best_score = None, 0
        for i, p in enumerate(pred):
            if i in used or abs(p.line_total - float(want["line_total"])) > 0.005:
                continue
            if strict and not _compatible(p.raw_text, want):
                continue
            s = fuzz.token_set_ratio(matcher.normalize(p.raw_text), matcher.normalize(want["text"]))
            if s > best_score:
                best, best_score = i, s
        if best is not None and best_score >= 70:
            used.add(best)
            out.append((li, best))
        else:
            out.append((li, None))
    return out


def score_naive(rec: scanner.ParsedReceipt, label: dict) -> dict:
    """The first evaluator: amount + similar name only. Kept to show what it misses."""
    m = match_lines(rec, label, strict=False)
    found = sum(1 for _, p in m if p is not None)
    n = len(label["lines"])
    return {"recall": found / n if n else 1.0, "precision": found / len(rec.items) if rec.items else 0.0,
            "lines_correct": found}


def score(rec: scanner.ParsedReceipt, label: dict) -> dict:
    """Per-field counts with denominators, plus receipt-level checks and examples of what went wrong."""
    m = match_lines(rec, label)
    fields = {f: [0, 0] for f in FIELDS}
    fields["text"] = [0, len(label["lines"])]
    missing, wrong = [], []
    for li, pi in m:
        want = label["lines"][li]
        if pi is None:
            missing.append(want["text"])
            for f in FIELDS:
                if f in want or f == "line_total":
                    fields[f][1] += 1
            continue
        fields["text"][0] += 1
        p = rec.items[pi]
        got = {"line_total": round(p.line_total, 2), "qty": round(float(p.qty or 1), 3),
               "discount": round(float(p.discount or 0), 2)}
        size = _size(p.raw_text)
        for f in FIELDS:
            if f not in want and f != "line_total":
                continue
            fields[f][1] += 1
            if f == "size":
                ws = _size(str(want["size"]))
                ok = bool(size and ws and size[0] == ws[0] and abs(size[1] - ws[1]) <= 0.02 * ws[1])
                shown = units.fmt_size(*units.parse_size(p.raw_text)) if size else "none"
            elif f == "variant":
                ok = True       # a match already requires the variant
                shown = want["variant"]
            else:
                ok = abs(float(got[f]) - float(want[f])) < (0.0005 if f == "qty" else 0.005)
                shown = got[f]
            if ok:
                fields[f][0] += 1
            else:
                wrong.append({"line": want["text"], "field": FIELD_NAMES[f], "label": want[f], "read": shown,
                              "confidence": round(float(p.confidence), 2)})
    used = {p for _, p in m if p is not None}
    extra = [rec.items[i].raw_text for i in range(len(rec.items)) if i not in used]
    target = label.get("subtotal")
    return {
        "lines_labelled": len(label["lines"]), "lines_read": len(rec.items), "lines_correct": fields["text"][0],
        "fields": fields, "missing": missing, "extra": extra, "wrong": wrong,
        "store_ok": (rec.store or "").lower() == (label.get("store") or "").lower(),
        "date_ok": rec.purchased_on == label.get("date"),
        "total_ok": rec.total is not None and abs(rec.total - float(label.get("total", -1))) < 0.005,
        "reconciles": target is not None and abs(rec.items_sum - float(target)) <= 0.02,
        "recall": fields["text"][0] / len(label["lines"]) if label["lines"] else 1.0,
        "precision": fields["text"][0] / len(rec.items) if rec.items else 0.0,
    }


# ------------------------------------------------------------- reading a folder ------------------------------------
def read_file(path: Path, engine: str = "offline", pipeline: str = "opencv") -> scanner.ParsedReceipt:
    data = path.read_bytes()
    if path.suffix.lower() in PHOTO:
        if engine == "gemini":
            mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
            return scanner.scan(data, mime, engine="gemini")
        return read_image(data, pipeline)
    text, hints = digital.extract(path.name, data)
    return digital.parse(text, hints)


def read_image(data: bytes, pipeline: str = "opencv") -> scanner.ParsedReceipt:
    """pipeline: 'opencv' (resize, denoise, adaptive threshold, deskew) or 'plain' (greyscale only)."""
    if pipeline == "plain":
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise ValueError("Not an image")
    else:
        img = scanner.preprocess(data)
    text, confs = scanner.ocr(img)
    return scanner.parse_text(text, confs)


def labelled_files(folder: Path) -> list[tuple[Path, dict]]:
    out = []
    if not folder.exists():
        return out
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() in PHOTO | DOC and path.with_suffix(".json").exists():
            out.append((path, json.loads(path.with_suffix(".json").read_text())))
    return out


def evaluate_folder(folder: Path, engine: str = "offline", pipeline: str = "opencv") -> dict:
    rows, wrong, missing, extra = [], [], [], []
    for path, label in labelled_files(folder):
        t0 = time.perf_counter()
        try:
            rec = read_file(path, engine, pipeline)
            r = score(rec, label)
            naive = score_naive(rec, label)
        except Exception as e:                     # a crash is a total failure, not a skip
            r = {"lines_labelled": len(label["lines"]), "lines_read": 0, "lines_correct": 0, "recall": 0.0,
                 "precision": 0.0, "store_ok": False, "date_ok": False, "total_ok": False, "reconciles": False,
                 "fields": {f: [0, len(label["lines"]) if f in ("text", "line_total") else 0] for f in
                            FIELDS + ["text"]}, "missing": [l["text"] for l in label["lines"]], "extra": [],
                 "wrong": [], "error": str(e)}
            naive = {"recall": 0.0, "precision": 0.0, "lines_correct": 0}
        kind = "photo" if path.suffix.lower() in PHOTO else "digital"
        rows.append({"file": path.name, "kind": kind, **{k: v for k, v in r.items()
                                                          if k not in ("fields", "missing", "extra", "wrong")},
                     "naive_recall": naive["recall"], "seconds": round(time.perf_counter() - t0, 2),
                     **{f"{f}_ok": r["fields"][f][0] for f in r["fields"]},
                     **{f"{f}_n": r["fields"][f][1] for f in r["fields"]}})
        wrong += [{"file": path.name, **w} for w in r["wrong"]]
        missing += [{"file": path.name, "line": t} for t in r["missing"]]
        extra += [{"file": path.name, "line": t} for t in r["extra"]]
    df = pd.DataFrame(rows)
    fields = []
    if not df.empty:
        for f in ["text"] + FIELDS:
            ok, n = int(df.get(f"{f}_ok", pd.Series([0])).sum()), int(df.get(f"{f}_n", pd.Series([0])).sum())
            fields.append({"field": FIELD_NAMES[f], "correct": ok, "labelled": n,
                           "accuracy": ok / n if n else None})
        for col, name in (("store_ok", "Store"), ("date_ok", "Date"), ("total_ok", "Printed total"),
                          ("reconciles", "Reconciles to subtotal")):
            fields.append({"field": name, "correct": int(df[col].sum()), "labelled": len(df),
                           "accuracy": float(df[col].mean())})
    return {"receipts": df, "fields": pd.DataFrame(fields), "wrong": pd.DataFrame(wrong),
            "missing": pd.DataFrame(missing), "extra": pd.DataFrame(extra),
            "manifest": manifest(folder, engine, pipeline, df)}


def manifest(folder: Path, engine: str, pipeline: str, df: pd.DataFrame) -> dict:
    files = {}
    for path, _ in labelled_files(folder):
        files[path.name] = hashlib.sha256(path.read_bytes() + path.with_suffix(".json").read_bytes()).hexdigest()[:16]
    versions = {}
    for pkg in ("opencv-python-headless", "opencv-python", "pytesseract", "rapidfuzz", "pandas", "pypdf"):
        try:
            versions[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            pass
    try:
        tess = str(scanner.find_tesseract() and __import__("pytesseract").get_tesseract_version())
    except Exception:
        tess = "not installed"
    cols = [c for c in df.columns if c != "seconds"] if not df.empty else []
    results = hashlib.sha256(df[cols].to_csv(index=False).encode()).hexdigest()[:16] if cols else ""
    return {"scorer": SCORER_VERSION, "engine": engine, "pipeline": pipeline, "files": files,
            "dataset": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:16],
            "python": platform.python_version(), "tesseract": tess, "packages": versions, "results": results}


# ------------------------------------------------------------------ blind spots ------------------------------------
@dataclass
class BlindSpot:
    name: str
    what: str
    label: dict
    rec: scanner.ParsedReceipt
    product: dict | None = None      # for the identity check: the product the line was saved as


def _r(items, sub=None, total=None):
    r = scanner.ParsedReceipt(store="Walmart", purchased_on="2026-11-07", subtotal=sub, total=total)
    r.items = [scanner.ParsedItem(raw_text=t, line_total=p, qty=q, discount=d) for t, p, q, d in items]
    return r


def blind_spot_cases() -> list[BlindSpot]:
    base = [{"text": "GV WHL MLK GAL", "line_total": 3.48, "qty": 1, "discount": 0.5},
            {"text": "GV LRG EGGS 12CT", "line_total": 7.16, "qty": 4, "discount": 0.0}]
    lab = {"store": "Walmart", "date": "2026-11-07", "subtotal": 10.14, "total": 10.14, "lines": base}
    variants = {"store": "Walmart", "date": "2026-11-07", "subtotal": 8.58, "total": 8.58,
                "lines": [{"text": "FAIRLIFE 2% MILK 52OZ", "line_total": 4.29},
                          {"text": "FAIRLIFE LACTOSE FREE WHOLE 52OZ", "line_total": 4.29, "variant": "lactose free"}]}
    cereal = {"store": "Walmart", "date": "2026-11-07", "subtotal": 4.48, "total": 4.48,
              "lines": [{"text": "SUNNY CRNCH HNY OAT 18OZ", "line_total": 4.48, "size": "18 oz"}]}
    return [
        BlindSpot("Read perfectly", "The control: everything right.", lab,
                  _r([("GV WHL MLK GAL", 3.48, 1, 0.5), ("GV LRG EGGS 12CT", 7.16, 4, 0)], 10.14, 10.14)),
        BlindSpot("Same-price variants", "Both milks cost $4.29; the reader takes the lactose-free line for "
                                         "another 2%.", variants,
                  _r([("FAIRLIFE 2% MILK 52OZ", 4.29, 1, 0), ("FAIRLIFE 2% MILK 52OZ", 4.29, 1, 0)], 8.58, 8.58)),
        BlindSpot("Missing quantity", "“4 @ 1.79” is lost: the line total is right, the count isn't.", lab,
                  _r([("GV WHL MLK GAL", 3.48, 1, 0.5), ("GV LRG EGGS 12CT", 7.16, 1, 0)], 10.14, 10.14)),
        BlindSpot("Extra line", "A “TOTAL SAVINGS 0.50” footer is read as a product.", lab,
                  _r([("GV WHL MLK GAL", 3.48, 1, 0.5), ("GV LRG EGGS 12CT", 7.16, 4, 0),
                      ("TOTAL SAVINGS", 0.50, 1, 0)], 10.14, 10.14)),
        BlindSpot("Coupon on the wrong product", "The 50¢ coupon is attached to the eggs, not the milk. The total "
                                                 "is unchanged.", lab,
                  _r([("GV WHL MLK GAL", 3.48, 1, 0), ("GV LRG EGGS 12CT", 7.16, 4, 0.5)], 10.14, 10.14)),
        BlindSpot("One digit misread", "3.48 read as 3.43.", lab,
                  _r([("GV WHL MLK GAL", 3.43, 1, 0.5), ("GV LRG EGGS 12CT", 7.16, 4, 0)], 10.14, 10.14)),
        BlindSpot("Right text, wrong product size", "The line is read perfectly but saved as the 15.4 oz box.",
                  cereal, _r([("SUNNY CRNCH HNY OAT 18OZ", 4.48, 1, 0)], 4.48, 4.48),
                  product={"brand": "Sunny Crunch", "name": "Honey Oat Cereal", "size_value": 15.4, "size_unit": "oz",
                           "base_qty": 15.4 * 28.349523125, "dimension": "weight", "sold_by_weight": 0,
                           "variant": None}),
    ]


def run_blind_spots() -> pd.DataFrame:
    from . import identity
    rows = []
    for c in blind_spot_cases():
        naive = score_naive(c.rec, c.label)
        full = score(c.rec, c.label)
        perfect_full = (all(ok == n for ok, n in full["fields"].values()) and not full["extra"])
        lines = [(money.cents(i.line_total), money.cents(i.discount)) for i in c.rec.items]
        rec = reconcile.run(lines, [], money.cents(c.label["subtotal"]), 0, money.cents(c.label["total"]))
        flags = identity.line_flags(c.rec.items[0].raw_text, c.product) if c.product else []
        rows.append({
            "case": c.name, "what happens": c.what,
            "naive evaluator": "caught" if (naive["recall"] < 1 or naive["precision"] < 1) else "missed",
            "field-level evaluator": "caught" if not perfect_full else "missed",
            "reconciliation": "caught" if rec.status != "reconciled" else "missed",
            "identity check": "caught" if flags else ("missed" if c.product else "n/a"),
            "detail": "; ".join([f"{w['field']}: label {w['label']}, read {w['read']}" for w in full["wrong"]]
                                + [f"extra line “{e}”" for e in full["extra"]]
                                + [f"not found “{m}”" for m in full["missing"]] + flags),
        })
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ OCR comparison ---------------------------------
def degraded_images(data: bytes) -> list[tuple[str, bytes]]:
    """Synthetic stress versions of one photo, clearly labelled as synthetic."""
    import cv2
    import numpy as np
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    out = [("original", img)]
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), 3.5, 1.0)
    out.append(("tilted 3.5°", cv2.warpAffine(img, m, (w, h), borderValue=(255, 255, 255))))
    out.append(("blurred", cv2.GaussianBlur(img, (5, 5), 1.6)))
    faded = cv2.addWeighted(img, 0.45, np.full_like(img, 235), 0.55, 0)
    out.append(("faded thermal", faded))
    rng = np.random.default_rng(7)
    noisy = np.clip(img.astype(np.int16) + rng.normal(0, 28, img.shape), 0, 255).astype(np.uint8)
    out.append(("grainy", noisy))
    small = cv2.resize(img, None, fx=0.55, fy=0.55, interpolation=cv2.INTER_AREA)
    out.append(("low resolution", small))
    shadow = img.copy().astype(np.float32)
    shadow *= np.linspace(0.45, 1.0, w)[None, :, None]
    out.append(("shadow across", shadow.astype(np.uint8)))
    return [(n, cv2.imencode(".png", im)[1].tobytes()) for n, im in out]


def ocr_comparison(photo: Path, label: dict) -> pd.DataFrame:
    rows = []
    for name, data in degraded_images(photo.read_bytes()):
        for pipe in ("plain", "opencv"):
            t0 = time.perf_counter()
            try:
                r = score(read_image(data, pipe), label)
                ok = sum(v[0] for k, v in r["fields"].items() if k != "text")
                n = sum(v[1] for k, v in r["fields"].items() if k != "text")
                rows.append({"image": name, "pipeline": "Plain Tesseract" if pipe == "plain" else "OpenCV pipeline",
                             "lines found": f"{r['lines_correct']}/{r['lines_labelled']}",
                             "recall": r["recall"], "fields right": f"{ok}/{n}", "extra lines": len(r["extra"]),
                             "reconciles": r["reconciles"], "seconds": round(time.perf_counter() - t0, 2)})
            except Exception as e:
                rows.append({"image": name, "pipeline": pipe, "lines found": "error", "recall": 0.0,
                             "fields right": str(e)[:60], "extra lines": 0, "reconciles": False, "seconds": 0})
    return pd.DataFrame(rows)


# --------------------------------------------------------------- decision benchmark --------------------------------
RULES = [
    ("Last paid, any age", {"method": "last", "max_age_days": 100000}),
    ("Last paid, ≤120 days", {"method": "last", "max_age_days": 120}),
    ("Recent median, ≤60 days", {"method": "median", "max_age_days": 60}),
    ("Recent median, ≤120 days", {"method": "median", "max_age_days": 120}),
]


def benchmark(warmup_days: int = 28) -> dict:
    """Replay every past receipt line in date order. For each, predict its price from observations dated strictly
    before that trip (same product, same store), with each rule. Nothing from the trip itself, or later, is seen."""
    from . import decision as D
    allobs = observations.load()
    if allobs.empty:
        return {"rules": pd.DataFrame(), "lines": pd.DataFrame(), "n": 0}
    allobs = allobs.copy()
    allobs["date"] = allobs["date"].astype(str).str[:10]
    targets = allobs[allobs["source"] == "receipt"]
    start = (date.fromisoformat(targets["date"].min()) + pd.Timedelta(days=warmup_days)).isoformat()
    targets = targets[targets["date"] >= start]
    chosen = {"method": D.DEFAULT_SETTINGS["method"], "max_age_days": D.DEFAULT_SETTINGS["max_age_days"]}
    rows = []
    grouped = {k: g.sort_values("date").to_dict("records") for k, g in allobs.groupby(["product_id", "store"])}
    for t in targets.itertuples():
        hist = [o for o in grouped.get((t.product_id, t.store), []) if o["date"] < t.date]
        inputs_base = {"cutoff": t.date, "observations": hist}
        row = {"date": t.date, "store": t.store, "product_id": int(t.product_id), "actual": int(t.price_cents)}
        for name, s in RULES:
            e = D.estimate({**inputs_base, "settings": s}, int(t.product_id), t.store)
            row[name] = e["price_cents"] if e["ok"] else None
        rows.append(row)
    lines = pd.DataFrame(rows)
    out = []
    for name, s in RULES:
        have = lines[lines[name].notna()]
        err = (have[name] - have["actual"]).astype(float)
        pct = (err.abs() / have["actual"]).astype(float)
        out.append({"rule": name, "chosen": s == chosen, "coverage": len(have) / len(lines) if len(lines) else 0,
                    "lines predicted": f"{len(have)}/{len(lines)}",
                    "mean abs error": err.abs().mean() / 100 if len(have) else None,
                    "median abs % error": pct.median() if len(have) else None,
                    "within 5%": (pct <= 0.05).mean() if len(have) else None,
                    "bias": err.mean() / 100 if len(have) else None})
    return {"rules": pd.DataFrame(out), "lines": lines, "n": len(lines), "start": start,
            "chosen": next(n for n, s in RULES if s == chosen)}


# ---------------------------------------------------------------------- effort -------------------------------------
def effort() -> dict:
    df = db.q("SELECT * FROM review_sessions WHERE seconds IS NOT NULL")
    pocket = db.q("SELECT timings FROM pocket_imports WHERE timings IS NOT NULL")
    taps = []
    for t in pocket["timings"]:
        try:
            taps += [float(x) for x in json.loads(t) if x is not None]
        except (ValueError, TypeError):
            pass
    out = {"sessions": df, "n": len(df), "pocket_n": len(taps)}
    if len(df):
        out.update(median_seconds=float(df["seconds"].median()),
                   seconds_per_row=float((df["seconds"] / df["rows"].clip(lower=1)).median()),
                   corrected_per_receipt=float(df["fields_corrected"].mean()),
                   flagged_share=float(df["rows_flagged"].sum() / max(df["rows"].sum(), 1)))
    if taps:
        out.update(pocket_median=statistics.median(taps), pocket_under_10=sum(t < 10 for t in taps) / len(taps))
    return out
