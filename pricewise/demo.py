"""Fictional demo history: 6 months of weekly trips at Walmart, Aldi and Kroger.

Prices are invented for demonstration. Built-in stories the features can find:
- Eggs jump ~20% in September; beef and coffee creep up.         -> price alerts, trip explainer
- Spinach, tofu and lentils first appear in October.               -> "new items" in the explainer
- Sunny Crunch cereal shrinks from 18 oz to 15.4 oz at the same price. -> shrinkflation
- Kroger puts milk and chicken on sale every 4 weeks.               -> stock-up cycles
- The latest Kroger receipt has a double scan, a weight-maths error
  and a missed advertised sale (spaghetti 2 for $2).                                     -> overcharge finder
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from . import db, matcher, nutrition

STORES = {"Kroger": 4.5, "Walmart": 3.2, "Aldi": 2.1}

# kind: settings + one product per store: (brand, name, size, unit, base price, receipt text)
# For sold-by-weight kinds the price is per lb.
CATALOG = {
    "whole milk": dict(cat="Dairy & eggs", every=1, shelf=10, products={
        "Walmart": ("Great Value", "Whole Milk", 1, "gal", 3.48, "GV WHL MLK GAL"),
        "Aldi": ("Friendly Farms", "Whole Milk", 1, "gal", 3.19, "FF WHOLE MILK GAL"),
        "Kroger": ("Kroger", "Whole Milk", 1, "gal", 3.69, "KRO WHL MILK GAL")}),
    "eggs": dict(cat="Dairy & eggs", every=2, shelf=35, gpe=50, products={
        "Walmart": ("Great Value", "Large Eggs", 12, "each", 2.98, "GV LG EGGS 12CT"),
        "Aldi": ("Goldhen", "Large Eggs", 12, "each", 2.65, "GOLDHEN LRG EGGS"),
        "Kroger": ("Kroger", "Large Eggs", 12, "each", 3.29, "KRO LG EGG 12")}),
    "chicken breast": dict(cat="Meat & fish", every=1, shelf=3, freeze=True, weight=(1.4, 3.1), products={
        "Walmart": ("", "Boneless Chicken Breast", None, "lb", 3.97, "CHKN BRST BNLS"),
        "Aldi": ("Kirkwood", "Boneless Chicken Breast", None, "lb", 3.69, "KW BNLS CHKN BRST"),
        "Kroger": ("", "Boneless Chicken Breast", None, "lb", 4.49, "BNLS SKNLS CHK BRST")}),
    "bananas": dict(cat="Produce", every=1, shelf=6, weight=(1.8, 3.4), products={
        "Walmart": ("", "Bananas", None, "lb", 0.58, "BANANAS"),
        "Aldi": ("", "Bananas", None, "lb", 0.55, "BANANAS LB"),
        "Kroger": ("", "Bananas", None, "lb", 0.65, "BANANA YELLOW")}),
    "white rice": dict(cat="Pantry", every=6, shelf=365, products={
        "Walmart": ("Great Value", "Long Grain White Rice", 5, "lb", 4.24, "GV LG WHT RICE 5LB"),
        "Aldi": ("Earthly Grains", "Long Grain White Rice", 5, "lb", 3.89, "EG WHITE RICE 5#"),
        "Kroger": ("Kroger", "Long Grain White Rice", 5, "lb", 4.79, "KRO WHT RCE 5LB")}),
    "greek yogurt": dict(cat="Dairy & eggs", every=2, shelf=14, products={
        "Walmart": ("Bluehill", "Plain Greek Yogurt", 32, "oz", 5.98, "BLUEHILL GRK YGT 32"),
        "Aldi": ("Friendly Farms", "Plain Greek Yogurt", 32, "oz", 4.29, "FF GREEK YOG PLN"),
        "Kroger": ("Bluehill", "Plain Greek Yogurt", 32, "oz", 6.29, "BLUEHILL GRK YOGURT")}),
    "cereal": dict(cat="Pantry", every=3, shelf=180, products={
        "Walmart": ("Sunny Crunch", "Honey Oat Cereal", 18, "oz", 4.49, "SUNNY CRNCH HNY OAT 18OZ"),
        "Aldi": ("Millville", "Honey Oat Clusters", 14, "oz", 2.89, "MV HNY OAT CLSTR"),
        "Kroger": ("Sunny Crunch", "Honey Oat Cereal", 18, "oz", 4.79, "SUNNY CRUNCH CRL 18OZ")}),
    "peanut butter": dict(cat="Pantry", every=5, shelf=180, products={
        "Walmart": ("Peak", "Creamy Peanut Butter", 16, "oz", 2.98, "PEAK CRMY PB 16OZ"),
        "Aldi": ("Peanut Delight", "Creamy Peanut Butter", 16, "oz", 1.95, "PD CREAMY PB"),
        "Kroger": ("Peak", "Creamy Peanut Butter", 16, "oz", 3.29, "PEAK PNT BTR CRMY")}),
    "bread": dict(cat="Bakery", every=1, shelf=7, freeze=True, products={
        "Walmart": ("Great Value", "Whole Wheat Bread", 20, "oz", 1.88, "GV WW BREAD 20OZ"),
        "Aldi": ("L'oven Fresh", "Whole Wheat Bread", 20, "oz", 1.45, "LF WHOLE WHEAT BRD"),
        "Kroger": ("Kroger", "Whole Wheat Bread", 20, "oz", 2.29, "KRO 100% WW BREAD")}),
    "ground beef": dict(cat="Meat & fish", every=2, shelf=3, freeze=True, products={
        "Walmart": ("", "Ground Beef 80/20", 1, "lb", 4.97, "GRD BF 80/20 1LB"),
        "Aldi": ("", "Ground Beef 80/20", 1, "lb", 4.59, "80/20 GRND BEEF"),
        "Kroger": ("", "Ground Beef 80/20", 1, "lb", 5.49, "GRND BEEF 80/20")}),
    "apples": dict(cat="Produce", every=2, shelf=30, products={
        "Walmart": ("", "Gala Apples", 3, "lb", 4.47, "GALA APPLES 3LB"),
        "Aldi": ("", "Gala Apples", 3, "lb", 3.99, "APPLES GALA 3#"),
        "Kroger": ("", "Gala Apples", 3, "lb", 4.99, "GALA APPL 3LB BAG")}),
    "broccoli": dict(cat="Produce", every=1, shelf=6, weight=(0.7, 1.6), products={
        "Walmart": ("", "Broccoli Crowns", None, "lb", 1.68, "BROCCOLI CROWNS"),
        "Aldi": ("", "Broccoli Crowns", None, "lb", 1.49, "BROC CROWNS"),
        "Kroger": ("", "Broccoli Crowns", None, "lb", 1.99, "BROCCOLI CRWN")}),
    "black beans": dict(cat="Pantry", every=6, shelf=365, products={
        "Walmart": ("Great Value", "Dry Black Beans", 1, "lb", 1.24, "GV BLK BNS DRY"),
        "Aldi": ("Dakota's Pride", "Dry Black Beans", 1, "lb", 0.99, "DP BLACK BEANS"),
        "Kroger": ("Kroger", "Dry Black Beans", 1, "lb", 1.49, "KRO BLK BEANS 1LB")}),
    "cheddar": dict(cat="Dairy & eggs", every=3, shelf=30, products={
        "Walmart": ("Great Value", "Sharp Cheddar Block", 8, "oz", 2.47, "GV SHRP CHDR 8OZ"),
        "Aldi": ("Happy Farms", "Sharp Cheddar Block", 8, "oz", 2.29, "HF SHARP CHED"),
        "Kroger": ("Kroger", "Sharp Cheddar Block", 8, "oz", 2.99, "KRO CHDR SHRP 8")}),
    "oats": dict(cat="Pantry", every=6, shelf=365, products={
        "Walmart": ("Great Value", "Old Fashioned Oats", 42, "oz", 4.98, "GV OLD FSHN OATS"),
        "Aldi": ("Millville", "Old Fashioned Oats", 42, "oz", 3.79, "MV OATS OLD FASH"),
        "Kroger": ("Kroger", "Old Fashioned Oats", 42, "oz", 5.29, "KRO OATS 42OZ")}),
    "pasta": dict(cat="Pantry", every=3, shelf=365, qty=(1, 2), products={
        "Walmart": ("Great Value", "Spaghetti", 16, "oz", 1.12, "GV SPAG 16OZ"),
        "Aldi": ("Reggano", "Spaghetti", 16, "oz", 0.95, "REGGANO SPAGHETTI"),
        "Kroger": ("Kroger", "Spaghetti", 16, "oz", 1.39, "KRO SPAGHETTI")}),
    "canned tuna": dict(cat="Meat & fish", every=4, shelf=730, qty=(2, 4), products={
        "Walmart": ("Great Value", "Chunk Light Tuna", 5, "oz", 0.98, "GV CHNK LT TUNA"),
        "Aldi": ("Northern Catch", "Chunk Light Tuna", 5, "oz", 0.85, "NC CHUNK TUNA"),
        "Kroger": ("Kroger", "Chunk Light Tuna", 5, "oz", 1.19, "KRO TNA CHNK LT")}),
    "coffee": dict(cat="Drinks", every=4, shelf=120, products={
        "Walmart": ("Great Value", "Classic Roast Ground Coffee", 30.5, "oz", 8.98, "GV CLSC RST CFE"),
        "Aldi": ("Barissimo", "Medium Roast Ground Coffee", 30.5, "oz", 7.49, "BARISSIMO MED RST"),
        "Kroger": ("Kroger", "Classic Roast Ground Coffee", 30.5, "oz", 10.49, "KRO GRND COFFEE")}),
    "orange juice": dict(cat="Drinks", every=2, shelf=10, products={
        "Walmart": ("Great Value", "Orange Juice", 52, "fl oz", 3.28, "GV OJ 52OZ"),
        "Aldi": ("Nature's Nectar", "Orange Juice", 52, "fl oz", 2.99, "NN ORANGE JCE"),
        "Kroger": ("Kroger", "Orange Juice", 52, "fl oz", 3.99, "KRO ORNG JUICE 52")}),
    "potatoes": dict(cat="Produce", every=4, shelf=30, products={
        "Walmart": ("", "Russet Potatoes", 5, "lb", 3.47, "RUSSET POT 5LB"),
        "Aldi": ("", "Russet Potatoes", 5, "lb", 2.99, "RST POTATOES 5#"),
        "Kroger": ("", "Russet Potatoes", 5, "lb", 3.99, "POTATOES RUSSET 5")}),
    "chicken thighs": dict(cat="Meat & fish", every=3, shelf=3, freeze=True, weight=(1.3, 2.8), products={
        "Walmart": ("", "Boneless Chicken Thighs", None, "lb", 2.47, "CHKN THGHS BNLS"),
        "Aldi": ("Kirkwood", "Boneless Chicken Thighs", None, "lb", 2.29, "KW CHKN THIGH"),
        "Kroger": ("", "Boneless Chicken Thighs", None, "lb", 2.99, "BNLS CHK THGH")}),
    "spinach": dict(cat="Produce", every=2, shelf=6, start=date(2026, 10, 1), products={
        "Walmart": ("Marketside", "Baby Spinach", 10, "oz", 2.18, "MKTSD BBY SPNCH"),
        "Aldi": ("Little Salad Bar", "Baby Spinach", 10, "oz", 1.99, "LSB SPINACH 10OZ"),
        "Kroger": ("Kroger", "Baby Spinach", 10, "oz", 2.79, "KRO BABY SPIN")}),
    "tofu": dict(cat="Meat & fish", every=2, shelf=14, start=date(2026, 10, 1), products={
        "Walmart": ("Great Value", "Extra Firm Tofu", 14, "oz", 1.98, "GV XFRM TOFU"),
        "Aldi": ("Simply Nature", "Extra Firm Tofu", 14, "oz", 1.79, "SN TOFU X FIRM"),
        "Kroger": ("Kroger", "Extra Firm Tofu", 14, "oz", 2.49, "KRO TOFU XFRM")}),
    "lentils": dict(cat="Pantry", every=4, shelf=365, start=date(2026, 10, 1), products={
        "Walmart": ("Great Value", "Dry Green Lentils", 1, "lb", 1.38, "GV LNTL 1LB"),
        "Aldi": ("Dakota's Pride", "Dry Green Lentils", 1, "lb", 1.19, "DP LENTILS"),
        "Kroger": ("Kroger", "Dry Green Lentils", 1, "lb", 1.79, "KRO LENTILS 1LB")}),
}

START = date(2026, 5, 2)
END = date(2026, 11, 7)
SHRINK_DATE = date(2026, 8, 15)


def _inflation(kind: str, d: date) -> float:
    f = 1.0
    if d >= date(2026, 7, 1):
        f *= 1.02
    if d >= date(2026, 9, 15):
        f *= 1.02
    if kind == "eggs" and d >= date(2026, 9, 1):
        f *= 1.20
    if kind == "ground beef" and d >= date(2026, 8, 1):
        f *= 1.06
    if kind == "coffee" and d >= date(2026, 10, 1):
        f *= 1.08
    return f


def _price(store: str, kind: str, base: float, d: date, week: int) -> tuple[float, bool]:
    price = base * _inflation(kind, d)
    promo = False
    if store == "Kroger" and kind in ("whole milk", "chicken breast") and week % 4 == 1:
        price = 2.99
        promo = True
    if store == "Walmart" and kind == "coffee" and week % 5 == 2:
        price -= 1.50
        promo = True
    return round(price, 2), promo


def load_demo(seed: int = 7) -> dict:
    """Wipe the database and load the demo history. Returns a short summary."""
    rng = random.Random(seed)
    db.init_db()
    db.wipe_all()

    store_ids = {name: db.get_or_create_store(name, dist) for name, dist in STORES.items()}
    db.run("UPDATE stores SET kroger_location_id = ? WHERE name = 'Kroger'", ("demo-location",))

    # products
    pid: dict[tuple[str, str], int] = {}
    shrunk_pid = None
    for kind, spec in CATALOG.items():
        for store, (brand, name, size, unit, _, raw) in spec["products"].items():
            key = (brand, name, size)
            existing = next((v for (k, s), v in pid.items() if k == kind and
                             CATALOG[kind]["products"][s][:3] == (brand, name, size)), None)
            if existing:
                pid[(kind, store)] = existing
                continue
            new_id = db.add_product(
                name=name, brand=brand, kind=kind, category=spec["cat"], size_value=size, size_unit=unit,
                sold_by_weight=size is None, grams_per_each=spec.get("gpe"),
                density_g_ml=1.03 if kind in ("whole milk", "orange juice") else 1.0,
                shelf_life_days=spec["shelf"], freezable=spec.get("freeze", False))
            pid[(kind, store)] = new_id
            ref = nutrition.REFERENCE.get(kind)
            if ref:
                db.set_nutrition(new_id, *ref, source="Approx. USDA reference values")
        if kind == "cereal":
            shrunk_pid = db.add_product(name="Honey Oat Cereal", brand="Sunny Crunch", kind="cereal",
                                        category=spec["cat"], size_value=15.4, size_unit="oz", shelf_life_days=180)
            db.set_nutrition(shrunk_pid, *nutrition.REFERENCE["cereal"], source="Approx. USDA reference values")

    # weekly trips
    last_bought: dict[str, int] = {}
    n_receipts = 0
    weeks = (END - START).days // 7 + 1
    rotation = ["Kroger", "Walmart", "Aldi"]
    for week in range(weeks):
        d = START + timedelta(days=7 * week)
        store = rotation[week % 3]
        lines = []
        for kind, spec in CATALOG.items():
            if spec.get("start") and d < spec["start"]:
                continue
            gap = week - last_bought.get(kind, -99)
            due = spec["every"] + rng.choice([0, 0, 0, 1, -1 if spec["every"] > 2 else 0])
            if gap < due:
                continue
            brand, name, size, unit, base, raw = spec["products"][store]
            product_id = pid[(kind, store)]
            if kind == "cereal" and brand == "Sunny Crunch" and d >= SHRINK_DATE:
                product_id = shrunk_pid
                raw = raw.replace("18OZ", "15.4OZ")
            price, _ = _price(store, kind, base, d, week)
            if size is None:  # sold by weight
                lo, hi = spec["weight"]
                if kind == "chicken breast" and d >= date(2026, 10, 1):
                    lo, hi = lo + 0.8, hi + 1.0     # October: cooking for guests
                w = round(rng.uniform(lo, hi), 2)
                line_total = round(w * price, 2)
                lines.append(dict(product_id=product_id, qty=w, qty_unit="lb", unit_price=price,
                                  line_total=line_total, raw_text=f"{raw} {w:.2f} LB @ {price:.2f}/LB"))
            else:
                qlo, qhi = spec.get("qty", (1, 1))
                qty = rng.randint(qlo, qhi)
                lines.append(dict(product_id=product_id, qty=qty, qty_unit="each", unit_price=price,
                                  line_total=round(qty * price, 2), raw_text=raw if qty == 1 else f"{qty} @ {raw}"))
            last_bought[kind] = week

        if d == END:  # latest Kroger receipt: three problems for the overcharge finder
            for l in lines:
                p = db.product(l["product_id"])
                if p["kind"] == "bananas":
                    l["line_total"] = round(l["line_total"] + 0.30, 2)   # weight maths does not add up
            yog = next((l for l in lines if db.product(l["product_id"])["kind"] == "greek yogurt"), None)
            if yog is None:
                yog = dict(product_id=pid[("greek yogurt", "Kroger")], qty=1, qty_unit="each", unit_price=6.29,
                           line_total=6.29, raw_text="BLUEHILL GRK YOGURT")
                lines.append(yog)
            lines.append(dict(yog))  # scanned twice

        if not lines:
            continue
        rid = db.save_receipt(store_ids[store], d, lines, tax=0.0, source="demo")
        n_receipts += 1
        for l in lines:
            matcher.learn(l["raw_text"], store_ids[store], l["product_id"])

    # The advertised sale the latest Kroger receipt missed
    chicken_k = pid[("chicken breast", "Kroger")]
    db.run("INSERT INTO shelf_prices(product_id, store_id, observed_on, price, per, kind, note, terms, source) "
           "VALUES (?,?,?,?,?,?,?,?,?)",
           (pid[("pasta", "Kroger")], store_ids["Kroger"], END.isoformat(), 1.00, "package", "ad",
            "Weekly ad: Kroger spaghetti 2 for $2",
            "Buy 2, $1.00 each; Kroger card required (used: yes); limit 4; valid Nov 4-10", "you, from the paper ad"))

    # Weekly ads you noted this week (for the stock-up advisor)
    for kind, store, price, per, note in (("coffee", "Walmart", 7.48, "package", "Walmart rollback: GV coffee $7.48"),
                                          ("chicken breast", "Aldi", 2.99, "lb", "Aldi ad: Kirkwood chicken breast $2.99/lb")):
        db.run("INSERT INTO shelf_prices(product_id, store_id, observed_on, price, per, kind, note) VALUES (?,?,?,?,?,?,?)",
               (pid[(kind, store)], store_ids[store], (END - timedelta(days=1)).isoformat(), price, per, "ad", note))

    # Kroger daily price checks (what the optional Kroger connection would collect)
    milk_k = pid[("whole milk", "Kroger")]
    for i in range(70):
        d = END - timedelta(days=69 - i)
        week = (d - START).days // 7
        for prod, kind, base in ((milk_k, "whole milk", 3.69), (chicken_k, "chicken breast", 4.49)):
            price, promo = _price("Kroger", kind, base, d, week)
            db.run("INSERT INTO shelf_prices(product_id, store_id, observed_on, price, per, kind, note) "
                   "VALUES (?,?,?,?,?,?,?)",
                   (prod, store_ids["Kroger"], d.isoformat(), price, "lb" if kind == "chicken breast" else "package",
                    "kroger", "Kroger API check (demo)" + (" · promo" if promo else "")))

    # A past refund and recommendations for the scoreboard and learning loop
    old = db.one("SELECT id FROM receipts WHERE purchased_on = '2026-09-12'")
    db.run("INSERT INTO savings(logged_on, kind, amount, description, receipt_id) VALUES (?,?,?,?,?)",
           ("2026-09-14", "refund", 2.98, "Eggs scanned twice at Walmart, refunded at customer service",
            old["id"] if old else None))
    milk_a = pid[("whole milk", "Aldi")]
    eggs_a = pid[("eggs", "Aldi")]
    coffee_a = pid[("coffee", "Aldi")]
    yog_a = pid[("greek yogurt", "Aldi")]
    recs = [
        ("2026-08-20", "switch_store", milk_a, store_ids["Aldi"], "Buy milk at Aldi instead of Kroger",
         3.69 / 3785.411784, 0.50, "followed", "2026-08-21"),
        ("2026-09-05", "switch_store", eggs_a, store_ids["Aldi"], "Buy eggs at Aldi instead of Kroger",
         3.29 / 12, 0.64, "followed", "2026-09-06"),
        ("2026-09-26", "swap", coffee_a, store_ids["Aldi"], "Try Barissimo coffee instead of Kroger brand",
         10.49 / (30.5 * 28.349523125), 3.00, "followed", "2026-09-27"),
        ("2026-10-10", "swap", yog_a, store_ids["Aldi"], "Try Friendly Farms Greek yogurt instead of Bluehill",
         6.29 / (32 * 28.349523125), 2.00, "ignored", "2026-10-11"),
        ("2026-11-07", "switch_store", pid[("chicken breast", "Aldi")], store_ids["Aldi"],
         "Buy chicken breast at Aldi instead of Kroger", None, 6.50, "open", None),
    ]
    for r in recs:
        db.run("""INSERT INTO recommendations(created_on, kind, product_id, store_id, message, ref_unit_cost,
                  expected_saving, status, decided_on) VALUES (?,?,?,?,?,?,?,?,?)""", r)

    # The brief's live test case: with exact products only, a second store (Aldi) saves exactly $0.64 before travel
    from . import decision
    test = [("Gala Apples", None, 1.0), ("Broccoli Crowns", None, 0.9), ("Honey Oat Cereal", 15.4, 1.0),
            ("Boneless Chicken Thighs", None, 0.8), ("Baby Spinach", None, 1.0)]
    basket = []
    for name, size, qty in test:
        row = db.one("SELECT id FROM products WHERE name = ? AND brand IN ('', 'Sunny Crunch', 'Marketside') "
                     "AND (? IS NULL OR size_value = ?) ORDER BY id LIMIT 1", (name, size, size))
        basket.append({"product_id": row["id"], "qty": qty, "approved_substitutes": [], "requirements": {}})
    decision.save_list("Test basket: is Aldi worth a second stop?", basket)
    db.set_setting("demo_loaded", "1")
    db.set_setting("demo_clock", (END + timedelta(days=1)).isoformat())
    return {"receipts": n_receipts, "products": len(db.products()), "stores": len(store_ids)}


def open_demo(reset: bool = False) -> None:
    """Switch to the separate demo database (data/demo.db), loading it on first use. Your data is untouched."""
    db.set_profile("demo")
    db.init_db()
    if reset or db.is_empty():
        load_demo()


def leave_demo() -> None:
    db.set_profile("mine")
    db.init_db()
