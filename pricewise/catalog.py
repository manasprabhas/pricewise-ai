"""Small helpers for picking products, kinds and categories in forms."""
from __future__ import annotations

from . import db, nutrition

CATEGORIES = ["Produce", "Meat & fish", "Dairy & eggs", "Bakery", "Pantry", "Frozen", "Drinks", "Snacks",
              "Household", "Personal care", "Other"]

SHELF_LIFE_GUESS = {"Produce": 7, "Meat & fish": 3, "Dairy & eggs": 14, "Bakery": 7, "Pantry": 365, "Frozen": 180,
                    "Drinks": 30, "Snacks": 90, "Household": 3650, "Personal care": 3650, "Other": 30}


def product_options() -> dict[str, int]:
    df = db.products()
    out: dict[str, int] = {}
    for _, p in df.iterrows():
        label = db.product_label(p)
        if label in out:
            label = f"{label} #{int(p['id'])}"
        out[label] = int(p["id"])
    return dict(sorted(out.items(), key=lambda kv: kv[0].lower()))


def kinds() -> list[str]:
    existing = db.q("SELECT DISTINCT kind FROM products")["kind"].tolist()
    return sorted(set(existing) | set(nutrition.REFERENCE))


def create_product(name: str, kind: str, category: str, size_value: float | None, size_unit: str | None,
                   brand: str = "", sold_by_weight: bool = False, shelf_life_days: int | None = None,
                   freezable: bool = False, grams_per_each: float | None = None, variant: str | None = None) -> int:
    pid = db.add_product(name=name, kind=kind, category=category, size_value=size_value, size_unit=size_unit,
                         brand=brand, variant=variant, sold_by_weight=sold_by_weight, grams_per_each=grams_per_each,
                         shelf_life_days=shelf_life_days or SHELF_LIFE_GUESS.get(category, 30),
                         freezable=freezable)
    ref = nutrition.REFERENCE.get(kind.strip().lower())
    if ref:
        db.set_nutrition(pid, *ref, source="Approx. USDA reference values")
    return pid
