"""Digital receipts: emailed receipts (.eml), PDFs, web pages (.html), copied text, and order-history CSVs.

No camera and no OCR: the text is already exact, so parsing is far more reliable than a photo.
Drop files into the inbox folder (data/inbox) and import them with one click, or upload/paste them.
"""
from __future__ import annotations

import email
import hashlib
import io
import re
from datetime import datetime
from email import policy
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd

from . import catalog, db, matcher, scanner, units

SUPPORTED = (".eml", ".pdf", ".html", ".htm", ".txt")


def inbox_dir() -> Path:
    d = db.db_path().parent / "inbox"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------- text extraction ----------------
class _HTMLText(HTMLParser):
    BLOCK = {"p", "div", "tr", "br", "li", "h1", "h2", "h3", "h4", "table", "section", "article", "header", "footer"}
    CELL = {"td", "th"}

    def __init__(self):
        super().__init__()
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")
        elif tag in self.CELL:
            self.out.append("   ")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    p = _HTMLText()
    p.feed(html)
    text = "".join(p.out).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]{2,}", "   ", l).strip() for l in text.splitlines()]
    return "\n".join(l for l in lines if l)


def pdf_to_text(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def email_to_text(data: bytes) -> tuple[str, dict]:
    msg = email.message_from_bytes(data, policy=policy.default)
    hints = {"subject": msg.get("subject", ""), "from": msg.get("from", "")}
    try:
        hints["date"] = parsedate_to_datetime(msg["date"]).date().isoformat() if msg["date"] else None
    except (TypeError, ValueError):
        hints["date"] = None
    plain, html_body, pdfs = None, None, []
    for part in msg.walk():
        ctype = part.get_content_type()
        if part.get_content_maintype() == "multipart":
            continue
        if ctype == "application/pdf" or (part.get_filename() or "").lower().endswith(".pdf"):
            pdfs.append(part.get_payload(decode=True))
        elif ctype == "text/plain" and plain is None and not part.get_filename():
            plain = part.get_content()
        elif ctype == "text/html" and html_body is None and not part.get_filename():
            html_body = part.get_content()
    if pdfs:
        text = "\n".join(pdf_to_text(p) for p in pdfs)
    elif html_body:
        text = html_to_text(html_body)
    else:
        text = plain or ""
    return text, hints


def extract(filename: str, data: bytes) -> tuple[str, dict]:
    ext = Path(filename).suffix.lower()
    if ext == ".eml":
        return email_to_text(data)
    if ext == ".pdf":
        return pdf_to_text(data), {}
    if ext in (".html", ".htm"):
        return html_to_text(data.decode("utf-8", errors="replace")), {}
    return data.decode("utf-8", errors="replace"), {}


# ---------------- parsing ----------------
_QTY_INLINE = re.compile(r"^(?P<name>.*?\S)\s+(?:Qty|QTY|Quantity)[:\s]*(?P<q>\d+(?:\.\d+)?)\s+\$?(?P<p>\d+[.,]\d{2})\s*$")
_X_QTY = re.compile(r"^(?P<q>\d{1,2})\s*[x×]\s+(?P<name>.*?\S)\s+\$?(?P<p>\d+[.,]\d{2})\s*$")


def normalize_digital(text: str) -> str:
    """Rewrite common digital-receipt layouts into the line shapes the receipt parser understands."""
    out = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        line = re.sub(r"\$\s+(\d)", r"$\1", line)
        m = _QTY_INLINE.match(line) or _X_QTY.match(line)
        if m:
            q, total = float(m.group("q")), float(m.group("p").replace(",", "."))
            if q and q != 1 and q == int(q):
                out.append(f"{int(q)} @ {total / q:.2f}")
            out.append(f"{m.group('name')} {total:.2f}")
            continue
        out.append(line)
    return "\n".join(out)


def parse(text: str, hints: dict | None = None) -> scanner.ParsedReceipt:
    hints = hints or {}
    rec = scanner.parse_text(normalize_digital(text), {})
    rec.engine = "digital"
    if rec.store is None:
        blob = f"{hints.get('from', '')} {hints.get('subject', '')}".upper()
        known = db.stores()["name"].tolist()
        for name in known + scanner.KNOWN_STORES:
            if name.upper() in blob:
                rec.store = name if name in known or name in ("ALDI", "HEB", "H-E-B") else name.title()
                break
    if rec.purchased_on is None and hints.get("date"):
        rec.purchased_on = hints["date"]
    for it in rec.items:
        it.confidence = max(it.confidence, 0.95) if not it.notes else it.confidence
    return scanner.check(rec)


def parse_with_gemini(text: str) -> scanner.ParsedReceipt:
    from . import gemini
    import json
    raw = gemini.generate([{"text": gemini.RECEIPT_PROMPT + "\n\nReceipt text:\n" + text[:20000]}], json_mode=True)
    rec = scanner.from_gemini(json.loads(raw))
    rec.engine = "digital+gemini"
    return rec


# ---------------- inbox ----------------
def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:20]


def already_imported(h: str) -> bool:
    return db.one("SELECT 1 AS x FROM imports WHERE hash = ?", (h,)) is not None


def mark_imported(h: str, filename: str, receipt_id: int | None) -> None:
    db.run("INSERT OR REPLACE INTO imports(hash, filename, receipt_id, imported_on) VALUES (?,?,?,?)",
           (h, filename, receipt_id, datetime.now().isoformat(timespec="seconds")))


def inbox_files() -> list[Path]:
    files = sorted(p for p in inbox_dir().iterdir() if p.suffix.lower() in SUPPORTED)
    return [p for p in files if not already_imported(file_hash(p.read_bytes()))]


# ---------------- order-history CSV ----------------
def import_csv(df: pd.DataFrame, cols: dict[str, str | None], default_store: str | None = None) -> dict:
    """cols maps: date, store, item, qty, total (line total) or price (each), unit (optional).
    Unknown items become new products (you can rename them later). Returns a summary."""
    rows = df.copy()
    rows["_date"] = pd.to_datetime(rows[cols["date"]], errors="coerce").dt.date
    rows = rows.dropna(subset=["_date"])
    rows["_store"] = rows[cols["store"]].astype(str).str.strip() if cols.get("store") else (default_store or "Unknown store")
    rows["_qty"] = pd.to_numeric(rows[cols["qty"]], errors="coerce").fillna(1.0) if cols.get("qty") else 1.0
    money = lambda s: pd.to_numeric(s.astype(str).str.replace(r"[$,]", "", regex=True), errors="coerce")
    if cols.get("total"):
        rows["_total"] = money(rows[cols["total"]])
    else:
        rows["_total"] = money(rows[cols["price"]]) * rows["_qty"]
    rows = rows.dropna(subset=["_total"])
    created, receipts, lines_n, skipped = 0, 0, 0, 0
    kinds = catalog.kinds()
    for (d, store), g in rows.groupby(["_date", "_store"]):
        sid = db.get_or_create_store(store)
        lines = []
        for _, r in g.iterrows():
            name = str(r[cols["item"]]).strip()
            cands = matcher.match(name, sid)
            if cands and cands[0].score >= matcher.AUTO_ACCEPT:
                pid = cands[0].product_id
            else:
                unit = str(r[cols["unit"]]).lower() if cols.get("unit") else ""
                by_weight = unit in ("lb", "lbs", "kg")
                size = units.parse_size(name)
                from rapidfuzz import fuzz, process
                best = process.extractOne(matcher.normalize(name).lower(), kinds, scorer=fuzz.WRatio)
                kind = best[0] if best and best[1] >= 85 else re.sub(r"\d.*$", "", name.lower()).strip() or name.lower()
                pid = catalog.create_product(name=name.title(), kind=kind, category="Other",
                                             size_value=None if by_weight else (size[0] if size else 1),
                                             size_unit="lb" if by_weight else (size[1] if size else "each"),
                                             sold_by_weight=by_weight)
                created += 1
            matcher.learn(name, sid, pid)
            p = db.product(pid)
            qty = float(r["_qty"])
            lines.append(dict(product_id=pid, qty=qty, qty_unit="lb" if p["sold_by_weight"] else "each",
                              unit_price=round(float(r["_total"]) / qty, 2) if qty else None,
                              line_total=float(r["_total"]), raw_text=name))
        try:
            db.save_receipt(sid, d, lines, source="csv")
        except db.DuplicateReceipt:
            skipped += 1          # this trip is already saved (from this CSV, or from an email, PDF or photo)
            continue
        receipts += 1
        lines_n += len(lines)
    return {"receipts": receipts, "lines": lines_n, "new_products": created, "skipped_duplicates": skipped}
