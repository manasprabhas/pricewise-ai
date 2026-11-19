"""Nutrition per dollar.

Reference values are approximate per-100 g figures for common generic foods, rounded from USDA
FoodData Central (SR Legacy / Foundation). Products you add can be looked up online with a free
FoodData Central API key, or typed in from the package label.
"""
from __future__ import annotations

import math

import pandas as pd
import requests

from . import db, units

# kind: (protein g, kcal, fiber g) per 100 g, approximate
REFERENCE: dict[str, tuple[float, float, float]] = {
    "whole milk": (3.3, 61, 0.0),
    "eggs": (12.6, 143, 0.0),
    "chicken breast": (22.5, 120, 0.0),
    "chicken thighs": (19.7, 121, 0.0),
    "bananas": (1.1, 89, 2.6),
    "white rice": (7.1, 365, 1.3),
    "greek yogurt": (10.3, 59, 0.0),
    "cereal": (6.0, 390, 4.0),
    "peanut butter": (22.5, 588, 6.0),
    "bread": (12.3, 252, 6.0),
    "ground beef": (17.2, 254, 0.0),
    "apples": (0.3, 52, 2.4),
    "broccoli": (2.8, 34, 2.6),
    "black beans": (21.6, 341, 15.5),
    "cheddar": (24.9, 403, 0.0),
    "oats": (13.2, 379, 10.1),
    "pasta": (13.0, 371, 3.2),
    "canned tuna": (25.5, 116, 0.0),
    "coffee": (0.0, 0, 0.0),
    "orange juice": (0.7, 45, 0.2),
    "potatoes": (2.0, 77, 2.2),
    "tofu": (17.3, 144, 2.3),
    "spinach": (2.9, 23, 2.2),
    "lentils": (24.6, 352, 10.7),
}

METRICS = {
    "protein": ("protein_g", "g protein", "Protein"),
    "kcal": ("kcal", "kcal", "Calories"),
    "fiber": ("fiber_g", "g fiber", "Fiber"),
}


def grams_per_base_unit(p: dict | pd.Series) -> float | None:
    dim = p["dimension"]
    if dim == "weight":
        return 1.0
    if dim == "volume":
        return float(p.get("density_g_ml") or 1.0)
    gpe = p.get("grams_per_each")
    return float(gpe) if gpe and not (isinstance(gpe, float) and math.isnan(gpe)) else None


def table(days: int = 120) -> pd.DataFrame:
    """Per product: recent typical price and how much protein / kcal / fiber one dollar buys."""
    pur = db.purchases()
    prods = db.products()
    if pur.empty or prods.empty:
        return pd.DataFrame()
    cutoff = pur["purchased_on"].max() - pd.Timedelta(days=days)
    recent = pur[pur["purchased_on"] >= cutoff]
    med = recent.groupby("product_id")["unit_cost"].median().rename("unit_cost")
    df = prods.merge(med, left_on="id", right_index=True, how="inner")
    df = df[df["protein_g"].notna()]
    rows = []
    for _, p in df.iterrows():
        g = grams_per_base_unit(p)
        if not g or not p["unit_cost"]:
            continue
        grams_per_dollar = g / p["unit_cost"]
        rows.append({
            "product_id": int(p["id"]), "label": db.product_label(p), "kind": p["kind"], "category": p["category"],
            "unit_price": units.fmt_unit_price(p["unit_cost"], p["display_unit"]),
            "protein_per_dollar": p["protein_g"] / 100 * grams_per_dollar,
            "kcal_per_dollar": p["kcal"] / 100 * grams_per_dollar,
            "fiber_per_dollar": p["fiber_g"] / 100 * grams_per_dollar,
            "cost_per_25g_protein": (25 / (p["protein_g"] / 100 * grams_per_dollar)) if p["protein_g"] else None,
            "protein_g": p["protein_g"], "kcal": p["kcal"], "fiber_g": p["fiber_g"],
            "source": p["nutrition_source"],
        })
    return pd.DataFrame(rows)


def usda_search(query: str, api_key: str, timeout: float = 10) -> list[dict]:
    """Look up generic foods on USDA FoodData Central. Returns per-100 g values."""
    r = requests.get("https://api.nal.usda.gov/fdc/v1/foods/search",
                     params={"api_key": api_key or "DEMO_KEY", "query": query,
                             "dataType": "Foundation,SR Legacy", "pageSize": 6}, timeout=timeout)
    r.raise_for_status()
    out = []
    for f in r.json().get("foods", []):
        vals = {"203": None, "208": None, "291": None}
        for n in f.get("foodNutrients", []):
            num = str(n.get("nutrientNumber"))
            if num in vals and vals[num] is None:
                vals[num] = n.get("value")
        if vals["203"] is None:
            continue
        out.append({"fdc_id": f.get("fdcId"), "description": f.get("description"),
                    "protein_g": vals["203"] or 0.0, "kcal": vals["208"] or 0.0, "fiber_g": vals["291"] or 0.0})
    return out
