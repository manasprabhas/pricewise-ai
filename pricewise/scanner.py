"""Receipt scanner: image -> clean structured lines you review before saving.

Offline path: OpenCV cleans the photo, Tesseract reads the text, rules turn lines into items.
Optional path: Gemini reads the photo directly (better on crumpled or faded receipts).
Both paths end in the same checks: totals, weight maths and confidence flags.
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np

from . import money, reconcile

from . import db

KNOWN_STORES = ["WALMART", "ALDI", "KROGER", "TARGET", "COSTCO", "PUBLIX", "SAFEWAY", "WHOLE FOODS", "TRADER JOE",
                "MEIJER", "H-E-B", "HEB", "LIDL", "SPROUTS", "FOOD LION", "GIANT", "WEGMANS", "RALPHS", "FRED MEYER",
                "KING SOOPERS", "HARRIS TEETER", "SHOPRITE", "STOP & SHOP", "WINCO", "HY-VEE", "SAM'S CLUB"]

SKIP_WORDS = ["CHANGE", "CASH", "VISA", "DEBIT", "CREDIT", "MASTERCARD", "AMEX", "TEND", "APPROV", "ACCOUNT",
              "AUTH", "REF #", "ITEMS SOLD", "THANK", "TOTAL SAVINGS", "YOU SAVED", "BALANCE", "TERMINAL",
              "CARD", "EBT", "CASHIER", "STORE #", "ST#", "TC#", "TR#", "PAYMENT", "SIGNATURE", "REWARDS",
              "POINTS", "FUEL", "SURVEY", "RECEIPT", "TEL", "PHONE", "WWW", ".COM", "ORDER #", "ORDER NUMBER",
              "DRIVER", "UNSUBSCRIBE", "PRIVACY", "TRACK"]
DEPOSIT_WORDS = ["BOTTLE DEP", "BTL DEP", "CAN DEP", "DEPOSIT", "CRV"]
FEE_WORDS = ["BAG FEE", "BAG CHARGE", "DELIVERY FEE", "SERVICE FEE", "FEE", "TIP"]
STORE_COUPON_WORDS = ["STORE COUPON", "ORDER DISCOUNT", "% OFF ORDER", "OFF YOUR ORDER", "ORDER SAVINGS",
                      "BASKET DISCOUNT", "STOREWIDE", "STORE-WIDE"]
RETURN_WORDS = ["RETURN", "REFUND"]
DISCOUNT_WORDS = ["SAVINGS", "COUPON", "DISCOUNT", "PROMO", "MARKDOWN", "SC ", "KROGER SAVINGS", "INSTANT"]

PRICE_AT_END = re.compile(r"^(?P<name>.*?\S)\s+\$?(?P<neg>-)?\$?(?P<price>\d{1,4}[.,]\d{2})(?P<neg2>-)?\s*[A-Z*]{0,3}\s*$")
WEIGHT = re.compile(r"(?P<w>\d+(?:\.\d+)?)\s*(?P<u>LB|LBS|KG|OZ)\s*@\s*\$?(?P<p>\d+(?:\.\d+)?)\s*/?\s*(?:LB|KG|OZ)?",
                    re.IGNORECASE)
MULTI = re.compile(r"^(?P<q>\d{1,2})\s*@\s*\$?(?P<p>\d+\.\d{2})", re.IGNORECASE)
DATE_PATTERNS = [
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), "mdY"),
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "Ymd"),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2})\b"), "mdy"),
    (re.compile(r"\b(\d{1,2})-(\d{1,2})-(\d{2,4})\b"), "mdy-"),
]


@dataclass
class ParsedItem:
    raw_text: str
    line_total: float
    qty: float = 1.0
    qty_unit: str = "each"
    unit_price: float | None = None
    discount: float = 0.0
    confidence: float = 0.9
    notes: list[str] = field(default_factory=list)


@dataclass
class ParsedReceipt:
    store: str | None = None
    purchased_on: str | None = None
    items: list[ParsedItem] = field(default_factory=list)
    subtotal: float | None = None
    tax: float | None = None
    total: float | None = None
    raw_text: str = ""
    engine: str = "offline"
    warnings: list[str] = field(default_factory=list)
    adjustments: list = field(default_factory=list)     # reconcile.Adjustment
    reconciliation: object = None                       # reconcile.Reconciliation

    @property
    def items_sum(self) -> float:
        """Items minus item discounts plus adjustments printed before the subtotal (what the subtotal should be)."""
        c = sum(money.cents(i.line_total) - money.cents(i.discount) for i in self.items)
        c += sum(a.signed for a in self.adjustments if not a.after_subtotal)
        return c / 100


# ---------------- image + OCR ----------------
def find_tesseract() -> str | None:
    saved = db.get_setting("tesseract_cmd")
    candidates = [saved, shutil.which("tesseract"), r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                  r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                  os.path.expanduser(r"~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe")]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def tesseract_ready() -> bool:
    return find_tesseract() is not None


def preprocess(image_bytes: bytes, rotate: int = 0) -> np.ndarray:
    import cv2
    arr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("That file is not an image the scanner can open. Use a JPG or PNG photo.")
    if rotate:
        code = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}[rotate % 360]
        img = cv2.rotate(img, code)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    if w < 1400:
        scale = 1400 / w
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.fastNlMeansDenoising(gray, h=12)
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15)
    # straighten small tilts
    coords = np.column_stack(np.where(bw < 128))
    if len(coords) > 500:
        angle = cv2.minAreaRect(coords.astype(np.float32))[-1]
        angle = angle - 90 if angle > 45 else angle
        if 0.5 < abs(angle) < 15:
            (hh, ww) = bw.shape
            m = cv2.getRotationMatrix2D((ww // 2, hh // 2), angle, 1.0)
            bw = cv2.warpAffine(bw, m, (ww, hh), flags=cv2.INTER_CUBIC, borderValue=255)
    return bw


def ocr(image: np.ndarray) -> tuple[str, dict[int, float]]:
    """Return text and a confidence (0-1) per output line number."""
    import pytesseract
    cmd = find_tesseract()
    if not cmd:
        raise RuntimeError("Tesseract is not installed. See the setup guide, or switch on Gemini in Settings.")
    pytesseract.pytesseract.tesseract_cmd = cmd
    cfg = "--oem 1 --psm 6 -c preserve_interword_spaces=1"
    data = pytesseract.image_to_data(image, config=cfg, output_type=pytesseract.Output.DICT)
    lines: dict[tuple, list] = {}
    for i, word in enumerate(data["text"]):
        if not word.strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append((data["left"][i], word, float(data["conf"][i])))
    text_lines, confs = [], {}
    for n, key in enumerate(sorted(lines)):
        words = sorted(lines[key])
        text_lines.append(" ".join(w for _, w, _ in words))
        cs = [c for _, _, c in words if c >= 0]
        confs[n] = (sum(cs) / len(cs) / 100) if cs else 0.5
    return "\n".join(text_lines), confs


# ---------------- text -> receipt ----------------
def _money(s: str) -> float:
    return float(s.replace(",", "."))


MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"], 1)}
MONTH_DATE = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})\b",
                        re.IGNORECASE)


def _find_date(text: str) -> str | None:
    m = MONTH_DATE.search(text)
    if m:
        try:
            return date(int(m.group(3)), MONTHS[m.group(1).lower()[:3]], int(m.group(2))).isoformat()
        except ValueError:
            pass
    for rx, fmt in DATE_PATTERNS:
        m = rx.search(text)
        if not m:
            continue
        a, b, c = m.groups()
        try:
            if fmt == "Ymd":
                d = date(int(a), int(b), int(c))
            else:
                year = int(c) if len(c) == 4 else 2000 + int(c)
                d = date(year, int(a), int(b))
        except ValueError:
            continue
        if date(2015, 1, 1) <= d <= date.today().replace(year=date.today().year + 1):
            return d.isoformat()
    return None


def _find_store(lines: list[str]) -> str | None:
    known = [s.upper() for s in db.stores()["name"].tolist()] if not db.stores().empty else []
    head = " ".join(lines[:8]).upper()
    for name in known + KNOWN_STORES:
        if name and name in head:
            return name.title() if name not in ("ALDI", "HEB", "H-E-B") else name
    return None


def adjustment_kind(up: str, negative: bool) -> str | None:
    """Deposits, fees, store-wide coupons and returns are kept apart from item prices."""
    if any(w in up for w in STORE_COUPON_WORDS):
        return "store_coupon"
    if any(w in up for w in RETURN_WORDS) and negative:
        return "return"
    if any(w in up for w in DEPOSIT_WORDS) and not negative:
        return "deposit"
    if any(re.search(rf"\b{re.escape(w)}\b", up) for w in FEE_WORDS) and not negative:
        return "fee"
    return None


_DISCOUNT_NOISE = re.compile(r"\b(KROGER|STORE|MFR|MANUFACTURER|DIGITAL|SAVINGS|SAVING|COUPON|CPN|DISCOUNT|PROMO|"
                             r"MARKDOWN|INSTANT|SC|OFF|YOU|SAVED|LOYALTY|CARD|PRICE|SALE)\b")


def attach_discount(rec: ParsedReceipt, name: str, price: float, line: str) -> None:
    """Attach an item-level discount. If the discount names a product, it goes to THAT product, even when another
    line sits in between; otherwise to the item directly above. Every choice is written into the item's notes."""
    from rapidfuzz import fuzz
    if not rec.items:
        rec.adjustments.append(reconcile.Adjustment("store_coupon", money.cents(price), name))
        return
    words = re.sub(r"[^A-Z ]", " ", _DISCOUNT_NOISE.sub(" ", name.upper()))
    words = re.sub(r"\s+", " ", words).strip()
    target, how = rec.items[-1], "the line above (the discount names no product)"
    if len(words) >= 3:
        from .matcher import normalize
        q = normalize(words)
        scored = [(fuzz.partial_ratio(q, normalize(it.raw_text)), i) for i, it in enumerate(rec.items)]
        best, idx = max(scored)
        if best >= 80:
            target = rec.items[idx]
            how = (f"“{target.raw_text}”, which it names" if target is not rec.items[-1]
                   else "the line above, which it names")
        else:
            how = f"the line above; it names “{words}”, which matches no line well, so check it"
            target.confidence = min(target.confidence, 0.6)
    target.discount = round(target.discount + price, 2)
    target.notes.append(f"Discount {price:.2f} ({line.strip()}) attached to {how}")


def parse_text(text: str, line_conf: dict[int, float] | None = None) -> ParsedReceipt:
    line_conf = line_conf or {}
    lines = [l.strip() for l in text.splitlines()]
    rec = ParsedReceipt(raw_text=text)
    rec.store = _find_store([l for l in lines if l])
    rec.purchased_on = _find_date(text)
    pending_weight: tuple[float, str, float] | None = None
    pending_multi: tuple[int, float] | None = None
    pending_name: str | None = None
    seen_subtotal = False

    for n, line in enumerate(lines):
        if not line:
            continue
        line = re.sub(r"(\d)0Z\b", r"\1OZ", line)      # common OCR slip: 16OZ read as 160Z
        up = line.upper()
        conf = line_conf.get(n, 0.9)

        if re.search(r"\bSUB\s*-?\s*TOTAL\b", up):
            m = PRICE_AT_END.match(line)
            if m:
                rec.subtotal = _money(m.group("price"))
                seen_subtotal = True
            continue
        if re.search(r"\bTAX\b", up) and "TOTAL" not in up:
            m = PRICE_AT_END.match(line)
            if m:
                rec.tax = round((rec.tax or 0) + _money(m.group("price")), 2)
            continue
        if re.search(r"\b(TOTAL|AMOUNT DUE|BALANCE DUE)\b", up) and "SAVING" not in up:
            m = PRICE_AT_END.match(line)
            if m and rec.total is None:
                rec.total = _money(m.group("price"))
            continue
        if any(w in up for w in SKIP_WORDS):
            continue

        wm = WEIGHT.search(line)
        pm = PRICE_AT_END.match(line)
        mm = MULTI.match(line)

        if wm and not pm:          # weight on its own line, applies to the next priced line
            pending_weight = (float(wm.group("w")), wm.group("u").lower().rstrip("s"), float(wm.group("p")))
            continue
        if mm and (not pm or not re.sub(MULTI, "", line).strip()):   # "2 @ 1.45" on its own line
            pending_multi = (int(mm.group("q")), float(mm.group("p")))
            continue
        if not pm and pending_name and re.fullmatch(r"\$?\d{1,4}[.,]\d{2}", line.strip()):
            line = f"{pending_name} {line.strip()}"     # digital receipts often put the price on its own line
            up = line.upper()
            pm = PRICE_AT_END.match(line)
        if not pm:
            if re.search(r"[A-Za-z]{3,}", line):   # a product name printed above its weight/price line
                pending_name = line
            continue

        price = _money(pm.group("price"))
        negative = bool(pm.group("neg") or pm.group("neg2"))
        name = pm.group("name").strip()
        if wm and pending_name and not re.sub(WEIGHT, "", name).strip(" @/$"):
            name = f"{pending_name} {name}"
        kind = adjustment_kind(up, negative)
        if kind:
            rec.adjustments.append(reconcile.Adjustment(kind, money.cents(price), name, after_subtotal=seen_subtotal))
            pending_name = None
            continue
        if negative or any(w in up for w in DISCOUNT_WORDS):
            attach_discount(rec, name, price, line)
            pending_name = None
            continue

        item = ParsedItem(raw_text=name, line_total=price, confidence=conf)
        if wm:
            item.qty, item.qty_unit, item.unit_price = float(wm.group("w")), wm.group("u").lower().rstrip("s"), float(wm.group("p"))
        elif pending_weight and abs(pending_weight[0] * pending_weight[2] - price) < 0.6:
            item.qty, item.qty_unit, item.unit_price = pending_weight
            item.raw_text = f"{name} {pending_weight[0]:.2f} LB @ {pending_weight[2]:.2f}/LB"
        elif mm:
            item.qty, item.unit_price = int(mm.group("q")), float(mm.group("p"))
            item.raw_text = re.sub(MULTI, "", name).strip() or name
        elif pending_multi:
            item.qty, item.unit_price = pending_multi
        else:
            item.unit_price = price
        pending_weight, pending_multi, pending_name = None, None, None

        if price > 150:
            item.confidence = min(item.confidence, 0.4)
            item.notes.append("Unusually large price, check it")
        if len(re.sub(r"[^A-Za-z]", "", name)) < 3:
            item.confidence = min(item.confidence, 0.5)
            item.notes.append("Name is hard to read")
        rec.items.append(item)

    check(rec)
    return rec


def check(rec: ParsedReceipt) -> ParsedReceipt:
    """Cross-checks every reading: weight and quantity maths per line, then the receipt reconciliation."""
    rec.warnings = []
    for it in rec.items:
        it.notes = [n for n in it.notes if " = " not in n or "but the line says" not in n]
        if it.unit_price and it.qty and it.qty != 1:
            expected = money.line_cents(it.qty, it.unit_price)
            if expected != money.cents(it.line_total):
                it.confidence = min(it.confidence, 0.55)
                it.notes.append(f"{it.qty:g} × {it.unit_price:.2f} = {expected / 100:.2f}, but the line says "
                                f"{it.line_total:.2f}")
        if it.qty_unit in ("lb", "kg", "oz") and (not it.qty or it.qty == 1) and it.unit_price in (None, it.line_total):
            if re.search(r"\b(LB|KG|PER LB|/LB)\b", it.raw_text.upper()):
                it.notes.append("Weighed item without a readable weight; enter the weight")
                it.confidence = min(it.confidence, 0.6)
    lines = [(money.cents(i.line_total), money.cents(i.discount)) for i in rec.items]
    r = reconcile.run(lines, rec.adjustments,
                      money.cents(rec.subtotal) if rec.subtotal is not None else None,
                      money.cents(rec.tax) if rec.tax is not None else None,
                      money.cents(rec.total) if rec.total is not None else None)
    rec.reconciliation = r
    for c in r.failed:
        rec.warnings.append(f"{c.name} check failed: {c.explain}. A line may be missing or misread.")
        if c.name == "Subtotal":
            for i in reconcile.likely_misreads(lines, c.actual_cents - c.expected_cents):
                rec.items[i].notes.append("One misread digit here would explain the subtotal difference")
    if r.status == "no_total":
        rec.warnings.append("No subtotal or total found, so the items could not be cross-checked.")
    if rec.purchased_on is None:
        rec.warnings.append("No date found. Pick the date below.")
    if rec.store is None:
        rec.warnings.append("Store name not found. Pick the store below.")
    return rec


def from_gemini(data: dict) -> ParsedReceipt:
    rec = ParsedReceipt(engine="gemini", store=data.get("store"), subtotal=data.get("subtotal"),
                        tax=data.get("tax"), total=data.get("total"))
    d = data.get("date")
    if d:
        try:
            rec.purchased_on = datetime.fromisoformat(d).date().isoformat()
        except ValueError:
            rec.purchased_on = _find_date(d)
    for it in data.get("items", []):
        try:
            rec.items.append(ParsedItem(
                raw_text=str(it.get("raw_text") or "?"), line_total=float(it.get("line_total") or 0),
                qty=float(it.get("qty") or 1), qty_unit=(it.get("qty_unit") or "each").lower(),
                unit_price=it.get("unit_price"), discount=float(it.get("discount") or 0),
                confidence=float(it.get("confidence") if it.get("confidence") is not None else 0.85)))
        except (TypeError, ValueError):
            rec.warnings.append(f"Skipped an unreadable line: {it}")
    return check(rec)


def scan(image_bytes: bytes, mime_type: str = "image/jpeg", rotate: int = 0, engine: str = "auto") -> ParsedReceipt:
    from . import gemini
    use_gemini = engine == "gemini" or (engine == "auto" and gemini.enabled())
    if use_gemini:
        return from_gemini(gemini.read_receipt(image_bytes, mime_type))
    img = preprocess(image_bytes, rotate)
    text, confs = ocr(img)
    return parse_text(text, confs)
