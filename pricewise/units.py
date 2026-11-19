"""Unit handling: parse package sizes, convert to base units, compute unit prices.

Base units: grams (weight), millilitres (volume), each (count).
Every comparison happens in base units; display units are only for people.
"""
from __future__ import annotations

import re

UNITS: dict[str, tuple[str, float]] = {
    "g": ("weight", 1.0),
    "kg": ("weight", 1000.0),
    "oz": ("weight", 28.349523125),
    "lb": ("weight", 453.59237),
    "ml": ("volume", 1.0),
    "l": ("volume", 1000.0),
    "fl oz": ("volume", 29.5735295625),
    "pt": ("volume", 473.176473),
    "qt": ("volume", 946.352946),
    "gal": ("volume", 3785.411784),
    "each": ("count", 1.0),
    "dozen": ("count", 12.0),
}

ALIASES = {
    "gram": "g", "grams": "g", "gr": "g",
    "kilogram": "kg", "kgs": "kg",
    "ounce": "oz", "ounces": "oz",
    "pound": "lb", "pounds": "lb", "lbs": "lb", "#": "lb",
    "millilitre": "ml", "milliliter": "ml", "mls": "ml",
    "litre": "l", "liter": "l", "liters": "l", "litres": "l", "ltr": "l",
    "floz": "fl oz", "fl.oz": "fl oz", "fl. oz": "fl oz", "fl oz.": "fl oz", "fluid ounce": "fl oz",
    "fluid ounces": "fl oz",
    "pint": "pt", "pints": "pt", "quart": "qt", "quarts": "qt",
    "gallon": "gal", "gallons": "gal", "ga": "gal",
    "ct": "each", "count": "each", "ea": "each", "pk": "each", "pack": "each", "pc": "each",
    "pcs": "each", "unit": "each", "units": "each",
    "doz": "dozen", "dz": "dozen",
}

DIMENSION_UNITS = {
    "weight": ["oz", "lb", "g", "kg"],
    "volume": ["fl oz", "gal", "qt", "l", "ml"],
    "count": ["each", "dozen"],
}


def normalize_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    u = unit.strip().lower().rstrip(".")
    u = re.sub(r"\s+", " ", u)
    if u in UNITS:
        return u
    return ALIASES.get(u)


def dimension_of(unit: str) -> str:
    key = normalize_unit(unit)
    if key is None:
        raise ValueError(f"Unknown unit: {unit!r}")
    return UNITS[key][0]


def to_base(value: float, unit: str) -> tuple[str, float]:
    """Return (dimension, quantity in base units)."""
    key = normalize_unit(unit)
    if key is None:
        raise ValueError(f"Unknown unit: {unit!r}")
    dim, factor = UNITS[key]
    return dim, float(value) * factor


def base_factor(unit: str) -> float:
    key = normalize_unit(unit)
    if key is None:
        raise ValueError(f"Unknown unit: {unit!r}")
    return UNITS[key][1]


_SIZE_RE = re.compile(
    r"(?:(?P<count>\d+)\s*[x×]\s*)?(?P<num>\d+(?:\.\d+)?|\.\d+)\s*(?P<unit>fl\.?\s?oz|[a-zA-Z#]+)",
    re.IGNORECASE,
)


def parse_size(text: str) -> tuple[float, str] | None:
    """Find the first package size in a string. '6 x 12 fl oz' -> (72.0, 'fl oz')."""
    if not text:
        return None
    for m in _SIZE_RE.finditer(text):
        unit = normalize_unit(m.group("unit"))
        if unit is None:
            continue
        value = float(m.group("num"))
        if m.group("count"):
            value *= int(m.group("count"))
        return value, unit
    return None


def default_display_unit(dimension: str, base_qty: float | None, sold_by_weight: bool = False) -> str:
    if dimension == "weight":
        if sold_by_weight or (base_qty or 0) >= UNITS["lb"][1] * 0.99:
            return "lb"
        return "oz"
    if dimension == "volume":
        return "gal" if (base_qty or 0) >= UNITS["fl oz"][1] * 64 else "fl oz"
    return "each"


def unit_price(price: float, base_qty: float, display_unit: str) -> float:
    """Price per display unit, given a price for base_qty base units."""
    if not base_qty:
        return float("nan")
    return price / base_qty * base_factor(display_unit)


def per_unit(unit_cost_base: float, display_unit: str) -> float:
    """Convert a cost-per-base-unit into cost per display unit."""
    return unit_cost_base * base_factor(display_unit)


def fmt_money(x: float | None, signed: bool = False) -> str:
    if x is None or x != x:  # NaN check
        return "—"
    s = f"${abs(x):,.2f}"
    if signed:
        return ("+" if x > 0.004 else "−" if x < -0.004 else "") + s
    return ("−" if x < -0.004 else "") + s


def fmt_unit_price(unit_cost_base: float | None, display_unit: str) -> str:
    if unit_cost_base is None or unit_cost_base != unit_cost_base:
        return "—"
    v = per_unit(unit_cost_base, display_unit)
    label = "ea" if display_unit == "each" else display_unit
    if v < 0.10:
        return f"{v * 100:.1f}¢/{label}"
    return f"${v:,.2f}/{label}"


def fmt_size(value: float | None, unit: str | None) -> str:
    if value is None or unit is None:
        return ""
    v = f"{value:g}"
    return f"{v} {'ct' if unit == 'each' else unit}"


def day(x) -> str:
    """'Nov 7' (no leading zero, works on Windows)."""
    return f"{x:%b} {x.day}"
