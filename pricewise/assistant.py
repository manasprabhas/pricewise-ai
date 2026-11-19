"""Shopping assistant. The AI part only works out WHAT you are asking; Python computes every number.

Offline: keyword rules pick the question type.  With Gemini on: Gemini picks it (handles any wording).
Every answer lists its evidence so you can check it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import pandas as pd
from rapidfuzz import fuzz, process

from . import analytics, audit, db, gemini, money, nutrition, pvm, shrink, stockup, units


@dataclass
class Answer:
    text: str
    table: pd.DataFrame | None = None
    evidence: list[str] = field(default_factory=list)
    tool: str = ""
    routed_by: str = "rules"


TOOLS = {
    "top_spend": "Which products I spend the most on. args: n (int), days (int, optional)",
    "cheapest_store": "Where I have paid least for an item. args: item (str)",
    "product_history": "What I paid for an item over time. args: item (str)",
    "price_increases": "Which products went up in price recently. args: days (int)",
    "spend_change": "Why spending changed between the last two months. args: none",
    "protein_per_dollar": "Foods with the most protein (or calories, fiber) per dollar. args: metric (protein|kcal|fiber)",
    "plan_budget": "Can my usual shopping fit a budget. args: budget (number), weeks (int, default 1)",
    "trip_worth_it": "Whether visiting more than one store is worth it. args: none",
    "overcharges": "Possible overcharges on my receipts. args: none",
    "shrinkflation": "Products that shrank. args: none",
    "stock_up": "What to stock up on or wait for. args: none",
}

SUGGESTIONS = [
    "Why did I spend more last month?",
    "Where have I paid the lowest price for chicken?",
    "Which products went up in price recently?",
    "What gives me the most protein per dollar?",
    "Can I keep a week of groceries under $35?",
    "Is a second store worth the trip?",
    "Did any receipt overcharge me?",
    "What should I stock up on?",
]


# ---------------- helpers ----------------
def _resolve_kind(text: str) -> str | None:
    kinds = db.q("SELECT DISTINCT kind FROM products")["kind"].tolist()
    if not kinds or not text:
        return None
    cleaned = re.sub(r"\b(the|a|an|for|on|of|my|i|price|prices|paid|pay|buy|bought|cheapest|lowest|where|have|"
                     r"how|much|did|do|is|was|what|at|to|in|least|usually|last|month|week|cost|costs)\b", " ",
                     text.lower())
    cleaned = re.sub(r"[^a-z ]", " ", cleaned).strip()
    if not cleaned:
        return None
    best = process.extractOne(cleaned, kinds, scorer=fuzz.WRatio)
    if best and best[1] >= 60:
        return best[0]
    names = db.q("SELECT kind, lower(brand || ' ' || name) AS n FROM products")
    best = process.extractOne(cleaned, names["n"].tolist(), scorer=fuzz.WRatio)
    if best and best[1] >= 70:
        return names.iloc[best[2]]["kind"]
    return None


def _money(x: float) -> str:
    return units.fmt_money(x)


# ---------------- tools (all numbers come from here) ----------------
def top_spend(n: int = 5, days: int | None = 90) -> Answer:
    pur = db.purchases()
    if days:
        pur = pur[pur["purchased_on"] >= pur["purchased_on"].max() - pd.Timedelta(days=days)]
    t = pur.groupby("kind").agg(spent=("paid", "sum"), trips=("receipt_id", "nunique")).sort_values(
        "spent", ascending=False).head(n)
    total = pur["paid"].sum()
    lines = [f"{i}. **{k.title()}**: {_money(r['spent'])} ({r['spent'] / total:.0%} of spending, {int(r['trips'])} trips)"
             for i, (k, r) in enumerate(t.iterrows(), 1)]
    return Answer(f"Your top {len(t)} over the last {days} days:\n\n" + "\n".join(lines),
                  t.reset_index().rename(columns={"kind": "item"}),
                  [f"{len(pur)} purchase lines, total {_money(total)}"])


def cheapest_store(item: str) -> Answer:
    kind = _resolve_kind(item)
    if not kind:
        return Answer(f"I couldn't find anything like “{item}” in your purchases.")
    m = analytics.store_matrix()
    if kind not in m.index:
        return Answer(f"You haven't bought {kind} in the last 120 days.")
    unit = m.loc[kind, "unit"]
    vals = m.loc[kind].drop("unit").dropna().astype(float).sort_values()
    label = "ea" if unit == "each" else unit
    best = vals.index[0]
    others = ", ".join(f"{s} ${v:.2f}/{label}" for s, v in vals.iloc[1:].items())
    text = f"You've paid least for **{kind}** at **{best}**: ${vals.iloc[0]:.2f}/{label}."
    if others:
        gap = vals.iloc[-1] / vals.iloc[0] - 1
        text += f" Elsewhere: {others}. The priciest is {gap:.0%} more."
    return Answer(text, vals.round(2).rename("price per " + label).reset_index().rename(columns={"index": "store"}),
                  ["Median price you paid per store, last 120 days", "Brands can differ between stores"])


def product_history(item: str) -> Answer:
    kind = _resolve_kind(item)
    if not kind:
        return Answer(f"I couldn't find anything like “{item}” in your purchases.")
    pur = db.purchases()
    g = pur[pur["kind"] == kind].sort_values("purchased_on")
    du = g["display_unit"].mode().iloc[0]
    last = g.iloc[-1]
    lo = g.loc[g["unit_cost"].idxmin()]
    text = (f"**{kind.title()}**: last paid {units.fmt_unit_price(last['unit_cost'], du)} at {last['store']} on "
            f"{units.day(last['purchased_on'])}. Your lowest was {units.fmt_unit_price(lo['unit_cost'], du)} at "
            f"{lo['store']} on {units.day(lo['purchased_on'])}; usual {units.fmt_unit_price(g['unit_cost'].median(), du)}.")
    t = g[["purchased_on", "store", "brand", "paid"]].copy()
    t["price"] = [units.fmt_unit_price(c, du) for c in g["unit_cost"]]
    return Answer(text, t.tail(10), [f"{len(g)} purchases since {units.day(g['purchased_on'].min())}"])


def price_increases(days: int = 60) -> Answer:
    pur = db.purchases()
    cutoff = pur["purchased_on"].max() - pd.Timedelta(days=days)
    rows = []
    for pid, g in pur.groupby("product_id"):
        before, after = g[g["purchased_on"] < cutoff], g[g["purchased_on"] >= cutoff]
        if len(before) < 2 or after.empty:
            continue
        b, a = before["unit_cost"].median(), after["unit_cost"].median()
        if a > b * 1.05:
            du = g["display_unit"].iloc[0]
            rows.append({"product": db.product_label(g.iloc[0]), "before": units.fmt_unit_price(b, du),
                         "now": units.fmt_unit_price(a, du), "change": a / b - 1})
    if not rows:
        return Answer(f"Nothing you buy went up more than 5% in the last {days} days.")
    t = pd.DataFrame(rows).sort_values("change", ascending=False)
    lines = [f"- **{r['product']}**: {r['before']} → {r['now']} (+{r['change']:.0%})" for _, r in t.head(6).iterrows()]
    return Answer(f"{len(t)} products cost more in the last {days} days than before:\n\n" + "\n".join(lines),
                  t.assign(change=t["change"].map("{:+.0%}".format)),
                  ["Same product, median price before vs. after the cutoff"])


def spend_change() -> Answer:
    pur = db.purchases()
    months = sorted(pur["month"].unique())
    full = months[:-1] if len(months) > 2 else months
    if len(full) < 2:
        return Answer("I need at least two full months of receipts to compare.")
    a, b = full[-2], full[-1]
    ex = pvm.explain(pur[pur["month"] == a], pur[pur["month"] == b])
    lines = [f"- {pvm.LABELS[k]}: {units.fmt_money(v, signed=True)}" for k, v in
             sorted(ex.effects.items(), key=lambda kv: -abs(kv[1])) if abs(v) >= 0.01]
    top = [pvm.kind_sentence(r) for _, r in ex.by_kind.head(3).iterrows()]
    return Answer(f"{b} vs {a}: {_money(ex.spend_b)} vs {_money(ex.spend_a)}. {ex.headline()}\n\n" + "\n".join(lines)
                  + "\n\nBiggest movers:\n" + "\n".join(f"- {s}" for s in top),
                  evidence=[f"Pieces add up to the change exactly (difference ${abs(ex.check()):.2f})"])


def protein_per_dollar(metric: str = "protein") -> Answer:
    t = nutrition.table()
    if t.empty:
        return Answer("No nutrition data yet.")
    col, unit, name = {"protein": ("protein_per_dollar", "g protein", "protein"),
                       "kcal": ("kcal_per_dollar", "kcal", "calories"),
                       "fiber": ("fiber_per_dollar", "g fiber", "fiber")}.get(metric, ("protein_per_dollar", "g protein", "protein"))
    t = t.sort_values(col, ascending=False)
    lines = [f"{i}. **{r['label']}**: {r[col]:.0f} {unit} per $1" for i, (_, r) in enumerate(t.head(5).iterrows(), 1)]
    return Answer(f"Most {name} per dollar, from what you buy:\n\n" + "\n".join(lines),
                  t[["label", col, "unit_price"]].head(10),
                  ["Your median price over 120 days", "Approximate USDA values per 100 g"])


def _usual_plan(weeks: int, extra_stop_cents: int | None = None):
    """Your usual basket priced with the Decision Lab engine, with every same-kind alternative approved
    (the assistant answers 'can I', so it looks at every option you've bought before)."""
    from datetime import date, timedelta
    from . import decision as D
    cutoff = D.today_cutoff()
    basket = D.infer_basket(cutoff, weeks)
    for b in basket:
        b["approved_substitutes"] = [c["product_id"] for c in D.substitute_candidates(b["product_id"])
                                     if D._variant_ok(D.product_facts(b["product_id"]), c, {})[0]]
    settings = {} if extra_stop_cents is None else {"extra_stop_cents": extra_stop_cents}
    inputs = D.build_inputs(cutoff, basket, settings)
    return basket, inputs, D.compute(inputs)


def plan_budget(budget: float, weeks: int = 1) -> Answer:
    basket, inputs, result = _usual_plan(weeks)
    if not basket:
        return Answer("I need a few weeks of receipts to know your usual basket.")
    if not result["complete"]:
        best = result["partial"][0] if result["partial"] else None
        return Answer("No store combination has recent prices for your whole usual basket"
                      + (f"; the closest is {money.fmt(best['checkout_cents'])} for {best['covered']} of "
                         f"{best['total_items']} items at {best['name']}." if best else "."))
    cheapest_cash = min(result["complete"], key=lambda p: p["cash_cents"])
    budget_c = money.cents(budget)
    text = (f"Your usual {weeks}-week basket ({len(basket)} items) costs about "
            f"**{money.fmt(cheapest_cash['cash_cents'])} at the till** at {cheapest_cash['name']}.")
    if cheapest_cash["cash_cents"] <= budget_c:
        text += f" ✅ That fits under {money.fmt(budget_c)} with {money.fmt(budget_c - cheapest_cash['cash_cents'])} to spare"
        if cheapest_cash["extra_stops"]:
            text += f" (it needs {cheapest_cash['extra_stops']} extra stop(s); that trip cost isn't cash at the till)"
        text += "."
    else:
        over = cheapest_cash["cash_cents"] - budget_c
        lines = sorted(cheapest_cash["lines"], key=lambda l: -l["cost_cents"])
        cut, names = 0, []
        for l in lines:
            if cut >= over:
                break
            cut += l["cost_cents"]
            names.append(f"{l['item']} ({money.fmt(l['cost_cents'])})")
        text += f" It's {money.fmt(over)} over {money.fmt(budget_c)}. Leaving out {', '.join(names)} would fit."
    t = pd.DataFrame([{"item": l["item"], "store": l["store"], "buy": l["label"], "cost": l["cost_cents"] / 100}
                      for l in cheapest_cash["lines"]])
    return Answer(text, t, ["Decision Lab engine, prices seen up to today", "Open Plan a basket to change anything"])


def trip_worth_it() -> Answer:
    from . import decision as D
    basket, inputs, result = _usual_plan(2)
    th = D.stop_cost_threshold(result)
    stop = inputs["settings"]["extra_stop_cents"]
    if not th:
        return Answer("Not enough complete plans to compare one store with two.")
    if th["advantage_cents"] <= 0:
        return Answer(f"Stick to one store: **{th['single']}** is cheapest even before travel.")
    verdict = "Yes" if stop < th["tie_at_cents"] else "No"
    return Answer(f"{verdict}: **{th['multi']}** saves {money.fmt(th['advantage_cents'])} before travel on your usual "
                  f"2-week basket. It's worth it while an extra stop costs you less than "
                  f"**{money.fmt(th['tie_at_cents'])}**; your setting is {money.fmt(stop)}.",
                  evidence=["Your usual 2-week basket", "Plan a basket shows the items behind this"])


def overcharges() -> Answer:
    issues = [i for i in audit.audit() if i.status == "open"]
    if not issues:
        return Answer("No open overcharge flags. 🎉")
    total = sum(i.amount for i in issues if i.kind != "unusual_price")
    lines = [f"- **{i.title}**: {i.label}, {i.store} {units.day(i.purchased_on)} ({_money(i.amount)})" for i in issues[:6]]
    return Answer(f"{len(issues)} open flags, about {_money(total)} you could ask back (not counting price "
                  f"surprises):\n\n" + "\n".join(lines), evidence=["Open Overcharges for the refund note"])


def shrinkflation() -> Answer:
    ev = shrink.detect()
    if not ev:
        return Answer("No shrinking packages found in what you buy.")
    lines = [f"- **{e.brand} {e.name}**: {e.old_size} → {e.new_size}, {e.verdict.lower()}; per-unit price "
             f"{e.unit_change:+.0%} (≈{_money(e.yearly_extra)} a year at your pace)" for e in ev]
    return Answer("Packages that shrank:\n\n" + "\n".join(lines))


def stock_up() -> Answer:
    adv = [a for a in stockup.advise() if a.action != "normal"]
    if not adv:
        return Answer("Nothing is at an unusually good price right now. Normal shopping.")
    lines = [f"- **{a.action.title()}**: {a.label}. {a.message}" for a in adv[:6]]
    return Answer("\n".join(lines))


# ---------------- routing ----------------
def route_rules(q: str) -> tuple[str, dict]:
    s = q.lower()
    num = re.search(r"\$?\s*(\d+(?:\.\d+)?)", s)
    if re.search(r"overcharg|refund|double scan|scanned twice|charged wrong", s):
        return "overcharges", {}
    if re.search(r"shrink|smaller package|getting smaller", s):
        return "shrinkflation", {}
    if re.search(r"stock up|stock-up|good time to buy|bulk|wait for (a )?sale", s):
        return "stock_up", {}
    if re.search(r"worth|second store|another store|two stores|multiple stores|trip", s):
        return "trip_worth_it", {}
    if re.search(r"under \$?\d|budget|keep .* \$\d", s):
        weeks = 2 if re.search(r"two weeks|2 weeks|fortnight", s) else 1
        return "plan_budget", {"budget": float(num.group(1)) if num else 50.0, "weeks": weeks}
    if re.search(r"why .*(spen|cost|more|less)|spending (change|go up|went)|spent more|spend more", s):
        return "spend_change", {}
    if re.search(r"protein|calorie|kcal|fiber|fibre|nutriti|healthy", s):
        metric = "kcal" if re.search(r"calorie|kcal", s) else "fiber" if re.search(r"fib", s) else "protein"
        return "protein_per_dollar", {"metric": metric}
    if re.search(r"increase|went up|go up|gone up|more expensive|price rise|rising", s):
        return "price_increases", {"days": 60}
    if re.search(r"spend the most|top \d|most money|biggest|spend most", s):
        return "top_spend", {"n": int(num.group(1)) if num and float(num.group(1)) < 30 else 5}
    if re.search(r"cheapest|least|lowest|best price|where .*(buy|get)", s):
        return "cheapest_store", {"item": q}
    if re.search(r"how much|history|paid for|price of|last time", s):
        return "product_history", {"item": q}
    if _resolve_kind(q):
        return "product_history", {"item": q}
    return "help", {}


def route_gemini(q: str) -> tuple[str, dict]:
    prompt = ("Pick the one tool that answers the user's grocery question and fill its arguments. "
              "Return JSON {\"tool\": name, \"args\": {...}}. Use \"help\" if none fits.\nTools:\n"
              + "\n".join(f"- {k}: {v}" for k, v in TOOLS.items()) + f"\nQuestion: {q}")
    data = json.loads(gemini.generate([{"text": prompt}], json_mode=True))
    return data.get("tool", "help"), data.get("args", {}) or {}


def ask(q: str) -> Answer:
    routed_by = "rules"
    try:
        if gemini.enabled():
            tool, args = route_gemini(q)
            routed_by = "Gemini"
        else:
            tool, args = route_rules(q)
    except Exception:
        tool, args = route_rules(q)
    fn = globals().get(tool) if tool in TOOLS else None
    if fn is None:
        return Answer("I can answer questions like:\n\n" + "\n".join(f"- {s}" for s in SUGGESTIONS),
                      tool="help", routed_by=routed_by)
    try:
        ans = fn(**args)
    except TypeError:
        ans = fn()
    except Exception as e:  # never invent: say what failed
        ans = Answer(f"I couldn't work that out from your data ({e}).")
    ans.tool, ans.routed_by = tool, routed_by
    return ans
