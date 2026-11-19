"""Product recognition: turn receipt text like 'GV WHL MLK GAL' into a known product.

Order of checks:
1. A correction you saved before for this store (exact alias)  -> certain
2. A correction you saved at any store                          -> near certain
3. Fuzzy match on expanded words, with a size-conflict penalty   -> suggestion
Anything below the auto-accept score is shown to you to confirm. Learned aliases make the next receipt faster.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz, process

from . import db, units

ABBREVIATIONS = {
    "GV": "GREAT VALUE", "GRT": "GREAT", "VAL": "VALUE", "KRO": "KROGER", "KRGR": "KROGER", "FF": "FRIENDLY FARMS",
    "WHL": "WHOLE", "WHLE": "WHOLE", "MLK": "MILK", "MIL": "MILK", "CHKN": "CHICKEN", "CHK": "CHICKEN",
    "CKN": "CHICKEN", "BRST": "BREAST", "BRS": "BREAST", "BNLS": "BONELESS", "BNL": "BONELESS", "SKNLS": "SKINLESS",
    "THGH": "THIGH", "THGHS": "THIGHS", "GRD": "GROUND", "GRND": "GROUND", "BF": "BEEF", "BNNA": "BANANA",
    "BAN": "BANANA", "BANANAS": "BANANA", "YGT": "YOGURT", "YOG": "YOGURT", "YGRT": "YOGURT", "GRK": "GREEK",
    "PB": "PEANUT BUTTER", "PNT": "PEANUT", "BTR": "BUTTER", "CRL": "CEREAL", "CER": "CEREAL", "BRD": "BREAD",
    "WW": "WHOLE WHEAT", "WHT": "WHEAT", "LG": "LARGE", "LRG": "LARGE", "EGG": "EGGS", "ORG": "ORGANIC",
    "CHDR": "CHEDDAR", "CHED": "CHEDDAR", "SHRD": "SHREDDED", "CHS": "CHEESE", "OJ": "ORANGE JUICE",
    "ORNG": "ORANGE", "JCE": "JUICE", "POT": "POTATOES", "POTS": "POTATOES", "RST": "RUSSET", "BRCLI": "BROCCOLI",
    "BROC": "BROCCOLI", "SPNCH": "SPINACH", "SPIN": "SPINACH", "LNTL": "LENTILS", "LENT": "LENTILS",
    "BLK": "BLACK", "BNS": "BEANS", "TNA": "TUNA", "CHNK": "CHUNK", "LT": "LIGHT", "CFE": "COFFEE",
    "COF": "COFFEE", "GRND": "GROUND", "OATS": "OATS", "OLD": "OLD", "FSHN": "FASHIONED", "PAST": "PASTA",
    "SPAG": "SPAGHETTI", "RCE": "RICE", "WHT RCE": "WHITE RICE", "APPL": "APPLES", "APL": "APPLES",
    "GALA": "GALA", "TOFU": "TOFU", "FRM": "FIRM", "XFRM": "EXTRA FIRM", "HNY": "HONEY", "OAT": "OAT",
    "CRNCH": "CRUNCH", "NF": "NONFAT", "PLN": "PLAIN", "LF": "LACTOSE FREE", "LCTSFR": "LACTOSE FREE",
    "KW": "KIRKWOOD", "HG": "HALF GALLON", "DCF": "DECAF", "UNSWT": "UNSWEETENED", "GF": "GLUTEN FREE",
    "SF": "SUGAR FREE", "RF": "REDUCED FAT", "LOWFAT": "LOW FAT",
}

# Words that make two otherwise identical products different. If one side has one and the other doesn't,
# the match can never be automatic.
ATTRIBUTES = ["LACTOSE FREE", "ORGANIC", "NONFAT", "SKIM", "LOW FAT", "REDUCED FAT", "2%", "1%", "DECAF",
              "UNSWEETENED", "SUGAR FREE", "GLUTEN FREE", "DIET", "ZERO", "CHOCOLATE", "VANILLA", "STRAWBERRY",
              "CRUNCHY", "CREAMY", "BONE IN", "BONELESS", "SKINLESS", "UNSALTED", "LOW SODIUM", "PLANT", "ALMOND",
              "OAT MILK", "SOY", "SPICY", "FAMILY SIZE", "HALF GALLON"]

NOISE = {"EA", "LB", "LBS", "OZ", "CT", "GAL", "@", "N", "F", "T", "X", "TAX", "FS"}

AUTO_ACCEPT = 88.0


@dataclass
class Candidate:
    product_id: int
    label: str
    score: float
    reason: str


def normalize(raw: str) -> str:
    """Uppercase, drop prices, codes and punctuation, expand abbreviations."""
    s = raw.upper()
    s = re.sub(r"\$?\d+\.\d{2}\b", " ", s)          # prices
    s = re.sub(r"\b\d{6,}\b", " ", s)              # UPC / item codes
    s = re.sub(r"\b\d+(\.\d+)?\s*(OZ|FL ?OZ|CT|LB|LBS|GAL|ML|L|KG|G|PK|#)\b", " ", s)  # sizes
    s = re.sub(r"[^A-Z0-9 .]", " ", s)
    tokens = []
    for t in s.split():
        t = t.strip(".")
        if not t or t in NOISE or re.fullmatch(r"\d+(\.\d+)?", t):
            continue
        tokens.append(ABBREVIATIONS.get(t, t))
    return re.sub(r"\s+", " ", " ".join(tokens)).strip()


_SIZE_TOKEN = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(FL\.?\s?OZ|OZ|CT|LBS|LB|GAL|ML|KG|PK|G|L|#)(?![A-Z])")


def alias_key(raw: str) -> str:
    """Stable key for remembering a receipt line.

    Drops what changes from trip to trip (prices, the weight on by-the-pound lines, a "2 @" count prefix) but
    KEEPS the package size, so "HNY OAT 18OZ" and "HNY OAT 15.4OZ" can never share a learned alias.
    """
    s = re.sub(r"(\d)0Z\b", r"\1OZ", raw.upper())                       # OCR slip: 160Z -> 16OZ
    s = re.sub(r"^\s*\d{1,2}\s*@\s*(?=[A-Z])", " ", s)                  # "2 @ ITEM" quantity prefix
    s = re.sub(r"\d+(\.\d+)?\s*(LB|LBS|KG|OZ)\s*@.*$", " ", s)          # "2.31 LB @ 0.58/LB" (weighed items)
    sizes = []

    def keep(m: re.Match) -> str:
        unit = re.sub(r"[.\s]", "", m.group(2)).replace("LBS", "LB").replace("#", "LB")
        sizes.append(f"{float(m.group(1)):g}{unit}")
        return " "
    s = _SIZE_TOKEN.sub(keep, s)
    s = re.sub(r"\$?\d+\.\d{2}\b", " ", s)                              # prices
    s = re.sub(r"[^A-Z ]", " ", s)
    words = re.sub(r"\s+", " ", s).strip()
    return (words + " " + " ".join(f"[{x}]" for x in sizes)).strip()


def _product_strings() -> list[tuple[int, str, dict]]:
    df = db.products()
    out = []
    for _, p in df.iterrows():
        text = f"{p['brand']} {p['name']} {p['kind']}"
        out.append((int(p["id"]), normalize(text), p.to_dict()))
    return out


def _size_conflict(raw: str, prod: dict) -> bool:
    found = units.parse_size(raw)
    if not found or prod.get("sold_by_weight") or not prod.get("base_qty"):
        return False
    value, unit = found
    if unit in ("lb",) and "@" in raw:   # a weight line, not a package size
        return False
    try:
        dim, base = units.to_base(value, unit)
    except ValueError:
        return False
    if dim != prod["dimension"]:
        return False
    return abs(base - prod["base_qty"]) / prod["base_qty"] > 0.05


def match(raw: str, store_id: int | None = None, limit: int = 3) -> list[Candidate]:
    key = alias_key(raw)
    if store_id is not None:
        row = db.one("SELECT product_id FROM aliases WHERE raw_norm = ? AND store_id = ?", (key, store_id))
        if row:
            p = db.product(row["product_id"])
            return [Candidate(p["id"], db.product_label(p), 100.0, "You confirmed this at this store before")]
    row = db.one("SELECT product_id FROM aliases WHERE raw_norm = ? ORDER BY times_seen DESC LIMIT 1", (key,))
    if row:
        p = db.product(row["product_id"])
        return [Candidate(p["id"], db.product_label(p), 95.0, "You confirmed this text at another store")]

    choices = _product_strings()
    if not choices:
        return []
    query = normalize(raw)
    if not query:
        return []
    scored = process.extract(query, {pid: s for pid, s, _ in choices}, scorer=fuzz.token_set_ratio,
                             limit=max(limit * 3, 6))
    lookup = {pid: prod for pid, _, prod in choices}
    out: list[Candidate] = []
    for _, score, pid in scored:
        prod = lookup[pid]
        target = normalize(f"{prod['brand']} {prod['name']}")
        partial = fuzz.partial_ratio(query, target)
        sort = fuzz.token_sort_ratio(query, target)
        final = 0.45 * score + 0.35 * sort + 0.2 * partial
        reason = "Similar words"
        if _size_conflict(raw, prod):
            final -= 25
            reason = "Similar words, but the package size differs"
        conflict = attribute_conflict(query, normalize(f"{prod['brand']} {prod['name']}"))
        if conflict:
            final = min(final - 30, AUTO_ACCEPT - 10)
            reason = f"Similar words, but only one says “{conflict.lower()}”"
        out.append(Candidate(pid, db.product_label(prod), round(final, 1), reason))
    out.sort(key=lambda c: c.score, reverse=True)
    return out[:limit]


def attribute_conflict(a: str, b: str) -> str | None:
    """Return an attribute word present in one description but not the other."""
    pa, pb = f" {a} ", f" {b} "
    for attr in ATTRIBUTES:
        if (f" {attr} " in pa) != (f" {attr} " in pb):
            if attr == "HALF GALLON":
                continue          # a size word: the size check handles it
            return attr
    return None


def learn(raw: str, store_id: int | None, product_id: int) -> None:
    key = alias_key(raw)
    if not key:
        return
    db.run("""INSERT INTO aliases(raw_norm, store_id, product_id) VALUES (?,?,?)
              ON CONFLICT(raw_norm, store_id) DO UPDATE SET product_id = excluded.product_id,
              times_seen = times_seen + 1""", (key, store_id, product_id))
