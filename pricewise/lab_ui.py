"""Decision Lab interface pieces, shared by 'Plan a basket', 'Replay a trip' and 'Saved decisions'."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from . import catalog, db, decision as D, money, observations, summary, ui, units

METHODS = {"median": "Recent median", "last": "Last eligible price"}


# ---------------------------------------------------------------- basket
def basket_editor(key: str, cutoff) -> list[dict]:
    """Editable basket. Exact products first; substitutes are approved per item, one by one."""
    basket: list[dict] = st.session_state.get(f"{key}_basket", [])
    opts = catalog.product_options()
    id_to_label = {v: k for k, v in opts.items()}
    df = pd.DataFrame([{"product": id_to_label.get(b["product_id"]), "qty": b["qty"],
                        "swaps": (f"{len(b['approved_substitutes'])} approved" if b["approved_substitutes"]
                                  else "exact only")} for b in basket] or [{"product": None, "qty": 1.0, "swaps": ""}])
    edited = st.data_editor(
        df, num_rows="dynamic", hide_index=True, width="stretch", key=f"{key}_editor_{len(basket)}"
        f"_{st.session_state.get(f'{key}_ver', 0)}",
        column_config={
            "product": st.column_config.SelectboxColumn("Product (exact)", options=list(opts), width="medium"),
            "qty": st.column_config.NumberColumn("Qty (pkg or lb)", min_value=0.1, step=0.1, format="%.2f",
                                                 width="small"),
            "swaps": st.column_config.TextColumn("Swaps", disabled=True, width="small")})
    by_pid = {b["product_id"]: b for b in basket}
    new = []
    for _, r in edited.dropna(subset=["product"]).iterrows():
        pid = opts.get(r["product"])
        if pid is None:
            continue
        p = db.product(pid)
        qty = float(r["qty"] or 1)
        if not p["sold_by_weight"]:
            qty = float(max(1, round(qty)))            # packaged goods: whole packages only
        old = by_pid.get(pid, {})
        new.append({"product_id": pid, "qty": qty, "approved_substitutes": old.get("approved_substitutes", []),
                    "requirements": old.get("requirements", {})})
    st.session_state[f"{key}_basket"] = new
    return new


def substitutes_panel(key: str, basket: list[dict], cutoff, settings: dict) -> None:
    """Opt-in substitutes per item: brand, size, variant, unit price and what it does to the basket."""
    if not basket:
        return
    with st.expander(f"Substitutes and requirements ({sum(len(b['approved_substitutes']) for b in basket)} "
                     "approved)", expanded=False):
        st.caption("Plans use the exact product unless you approve an alternative here. Requirements such as "
                   "lactose-free or organic are always respected; an alternative that breaks one is never used.")
        labels = {b["product_id"]: D.product_facts(b["product_id"])["label"] for b in basket}
        pick = st.selectbox("Item", [b["product_id"] for b in basket], format_func=labels.get, key=f"{key}_subitem")
        item = next(b for b in basket if b["product_id"] == pick)
        req = D.product_facts(pick)
        cands = D.substitute_candidates(pick)
        attrs = sorted({a.strip() for a in (req["variant"] or "").split(",") if a.strip()}
                       | {"lactose free", "organic", "gluten free", "decaf", "unsweetened", "low fat"})
        c1, c2 = st.columns(2)
        must = c1.multiselect("Must be", attrs, default=[a.strip() for a in (item["requirements"].get("variant")
                                                                             or req["variant"]).split(",") if a.strip()],
                              key=f"{key}_must_{pick}")
        minpack = None
        if not req["sold_by_weight"] and req["base_qty"]:
            du = req["display_unit"]
            mp = c2.number_input(f"Minimum package size ({'ct' if du == 'each' else du})", min_value=0.0,
                                 value=float(item["requirements"].get("min_pack_base", 0) or 0)
                                 / units.base_factor(du), key=f"{key}_minpack_{pick}")
            minpack = mp * units.base_factor(du) if mp else None
        item["requirements"] = {"variant": ", ".join(must), **({"min_pack_base": minpack} if minpack else {})}
        if not cands:
            ui.empty("No other products of this kind in your history yet.")
            return
        probe_basket = [{"product_id": pick, "qty": item["qty"], "approved_substitutes": [c["product_id"] for c in cands],
                         "requirements": item["requirements"]}]
        probe = D.build_inputs(cutoff, probe_basket, settings)
        cache: dict = {}
        stores = sorted(probe["stores"])
        exact_best = min((o for o in (D.option(probe, probe_basket[0], pick, s, cache) for s in stores) if o["ok"]),
                         key=lambda o: o["cost_cents"], default=None)
        rows = []
        for c in cands:
            ok, why = D._variant_ok(req, c, item["requirements"])
            best = min((o for o in (D.option(probe, probe_basket[0], c["product_id"], s, cache) for s in stores)
                        if o["ok"]), key=lambda o: o["cost_cents"], default=None)
            rows.append({"approve": c["product_id"] in item["approved_substitutes"] and ok,
                         "product": c["label"], "variant": c["variant"] or "regular", "size": c["size"],
                         "best price": (f"{money.fmt(best['price_cents'])}/{'lb' if best['basis'] == 'lb' else 'pkg'} "
                                        f"at {best['store']}") if best else "no recent price",
                         "you'd buy": f"{best['qty']:g} {best['unit']}" if best else "",
                         "basket difference": (money.fmt(best["cost_cents"] - exact_best["cost_cents"], signed=True)
                                               if best and exact_best else "—"),
                         "allowed": "yes" if ok else f"no: {why}", "_id": c["product_id"]})
        t = pd.DataFrame(rows)
        ed = st.data_editor(t.drop(columns=["_id"]), hide_index=True, width="stretch",
                            key=f"{key}_subs_{pick}", disabled=[c for c in t.columns if c not in ("approve",)],
                            column_config={"approve": st.column_config.CheckboxColumn("Approve", width="small")})
        approved = [int(t.iloc[i]["_id"]) for i in range(len(t)) if ed.iloc[i]["approve"] and t.iloc[i]["allowed"] == "yes"]
        if any(ed.iloc[i]["approve"] and t.iloc[i]["allowed"] != "yes" for i in range(len(t))):
            st.caption("Alternatives that break a requirement can't be approved.")
        item["approved_substitutes"] = approved
        st.caption(f"Compared with the exact product at its cheapest store"
                   + (f" ({money.fmt(exact_best['cost_cents'])} at {exact_best['store']})." if exact_best else
                      " (no recent price for the exact product)."))


# ---------------------------------------------------------------- settings
def settings_panel(key: str, defaults: dict | None = None) -> dict:
    d = {**D.DEFAULT_SETTINGS, **(defaults or {})}
    c = st.columns(2) + st.columns(2)
    method = c[0].segmented_control("Price estimate", list(METHODS), default=d["method"], format_func=METHODS.get,
                                    key=f"{key}_method") or d["method"]
    max_age = c[1].select_slider("Oldest price allowed", [7, 14, 21, 30, 45, 60, 90, 120, 180, 365],
                                 value=d["max_age_days"], format_func=lambda v: f"{v} days", key=f"{key}_age")
    stop = c[2].number_input("Cost of each extra store ($)", min_value=0.0, max_value=50.0, step=0.05,
                             value=d["extra_stop_cents"] / 100, format="%.2f", key=f"{key}_stop",
                             help="Driving plus, if you want, your time. One store is the trip you'd make anyway.")
    budget = c[3].number_input("Cash budget ($)", min_value=0.0, step=5.0, value=(d["budget_cents"] or 0) / 100,
                               format="%.2f", key=f"{key}_budget")
    with st.popover("Work out the extra-store cost"):
        miles = st.number_input("Extra miles for a second store", 0.0, 50.0, 3.0, 0.5, key=f"{key}_miles")
        cpm = st.number_input("Driving cost per mile ($)", 0.0, 2.0, db.get_float("cost_per_mile", 0.25), 0.05,
                              key=f"{key}_cpm")
        mins = st.number_input("Extra minutes", 0.0, 120.0, db.get_float("minutes_per_stop", 15), 5.0, key=f"{key}_mins")
        vot = st.number_input("Your time ($ per hour, 0 = free)", 0.0, 200.0,
                              db.get_float("value_of_time_per_hour", 0), 5.0, key=f"{key}_vot")
        calc = miles * cpm + mins / 60 * vot
        st.write(f"= **{money.fmt(money.cents(calc))}** (driving {money.fmt(money.cents(miles * cpm))}, time "
                 f"{money.fmt(money.cents(mins / 60 * vot))})")
        if st.button("Use this value", key=f"{key}_usecalc"):
            st.session_state[f"{key}_stop"] = round(calc, 2)
            st.rerun()
    return {**d, "method": method, "max_age_days": int(max_age), "extra_stop_cents": money.cents(stop),
            "budget_cents": money.cents(budget) if budget else None,
            "exclude_obs": st.session_state.get(f"{key}_exclude", [])}


# ---------------------------------------------------------------- results
def _age(cutoff: str, d: str | None) -> str:
    if not d:
        return "—"
    days = (date.fromisoformat(cutoff) - date.fromisoformat(d)).days
    return f"{days} d"


def verdict(inputs: dict, result: dict) -> None:
    w = D.plan_by_name(result, result["winner"]) if result["winner"] else None
    s = inputs["settings"]
    if not w:
        best = result["partial"][0] if result["partial"] else None
        ui.tags([("No complete plan", "None", "approve substitutes or allow older prices", "red")]
                + ([("Best partial plan", best["checkout_cents"] / 100,
                     f"{best['name']}: {best['covered']} of {best['total_items']} items", "paper")] if best else []))
        return
    budget = s.get("budget_cents")
    cash_foot = (f"checkout {money.fmt(w['checkout_cents'])}"
                 + (f" + tax {money.fmt(w['tax_cents'])}" if w["tax_known"] else ", tax unknown"))
    tags = [(f"Plan: {w['name']}", w["decision_cents"] / 100,
             f"includes {money.fmt(w['trip_cents'])} for {w['extra_stops']} extra stop(s)", ""),
            ("Cash at the till", w["cash_cents"] / 100, cash_foot, "paper")]
    if budget:
        fits = w["fits_budget"]
        tags.append((f"{money.fmt(budget)} budget", "Fits" if fits else "Over",
                     (f"{money.fmt(budget - w['cash_cents'])} left at the till" if fits
                      else f"{money.fmt(w['cash_cents'] - budget)} over")
                     + ("; the trip cost isn't cash at the till" if fits and w["decision_cents"] > budget else ""),
                     "green" if fits else "red"))
    tags.append(("Evidence", f"{w['covered']}/{w['total_items']}",
                 f"newest {w['evidence_newest']}, oldest {w['evidence_oldest']}"
                 + (f"; {w['limited_lines']} on limited history" if w["limited_lines"] else ""), "paper"))
    ui.tags(tags)


def plans_table(key: str, inputs: dict, result: dict) -> None:
    rows = []
    for p in result["complete"]:
        rows.append({"rank": len(rows) + 1, "plan": p["name"], "checkout": p["checkout_cents"] / 100,
                     "tax": p["tax_cents"] / 100 if p["tax_known"] else None, "trip": p["trip_cents"] / 100,
                     "decision cost": p["decision_cents"] / 100, "coverage": f"{p['covered']}/{p['total_items']}",
                     "oldest price": _age(inputs["cutoff"], p["evidence_oldest"]),
                     "swaps": p["substitutions"], "limited": p["limited_lines"]})
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                     column_config={c: st.column_config.NumberColumn(c, format="$%.2f")
                                    for c in ("checkout", "tax", "trip", "decision cost")})
    else:
        ui.empty("No store combination prices every item. See the partial plans below.")
    if result["partial"]:
        st.markdown("**Partial plans, not ranked** (each is missing items):")
        ui.rows([(ui.sticker(f"{p['covered']}/{p['total_items']}", "yellow"),
                  f"{money.fmt(p['checkout_cents'])} for {p['covered']} of {p['total_items']} items at {p['name']}",
                  " · ".join(f"{m['item']}: {m['reasons'][0] if len(m['reasons']) == 1 else '; '.join(m['reasons'])}"
                             for m in p["missing"][:3]) + (" …" if len(p["missing"]) > 3 else ""),
                  "", "") for p in result["partial"][:4]])


def cost_chart(result: dict) -> None:
    plans = result["complete"][:6]
    if not plans:
        return
    names = [p["name"] for p in plans]
    fig = go.Figure()
    fig.add_bar(y=names, x=[p["checkout_cents"] / 100 for p in plans], orientation="h", name="Checkout",
                marker_color=ui.INK, hovertemplate="%{y}: checkout $%{x:.2f}<extra></extra>")
    fig.add_bar(y=names, x=[p["tax_cents"] / 100 for p in plans], orientation="h", name="Tax (known)",
                marker_color=ui.INK_SOFT, hovertemplate="%{y}: tax $%{x:.2f}<extra></extra>")
    fig.add_bar(y=names, x=[p["trip_cents"] / 100 for p in plans], orientation="h", name="Extra stops",
                marker_color=ui.TAG, marker_line=dict(color=ui.INK, width=1),
                hovertemplate="%{y}: extra stops $%{x:.2f}<extra></extra>")
    lo = min(p["checkout_cents"] for p in plans) / 100
    fig.update_layout(barmode="stack", yaxis=dict(autorange="reversed"), xaxis=dict(tickprefix="$",
                      range=[lo * 0.9, max(p["decision_cents"] for p in plans) / 100 * 1.02]))
    ui.chart(fig, height=90 + 44 * len(plans))
    st.caption("The axis starts near the cheapest checkout so small differences are visible.")


def what_changes(key: str, inputs: dict, result: dict) -> None:
    t1, t2, t3 = st.tabs(["Extra-store cost", "Price age", "Estimate method"])
    with t1:
        th = D.stop_cost_threshold(result)
        if not th:
            ui.empty("Needs at least one complete one-store plan and one complete multi-store plan to compare.")
        elif th["advantage_cents"] <= 0:
            ui.note(f"<b>{ui.esc(th['single'])}</b> is cheapest even before travel; a second store never pays off "
                    f"for this basket.")
        else:
            cur = inputs["settings"]["extra_stop_cents"]
            tie = th["tie_at_cents"]
            single_wins = cur >= tie
            ui.note(f"<b>{ui.esc(th['multi'])}</b> saves <b>{money.fmt(th['advantage_cents'])}</b> before travel. "
                    f"The plans tie at <b>{money.fmt(tie)}</b> per extra store"
                    f"{' (a tie goes to fewer stops)' if th['extra_stops'] == 1 else ''}: below it the extra stop "
                    f"pays off, from {money.fmt(tie)} up <b>{ui.esc(th['single'])}</b> alone wins. You set "
                    f"{money.fmt(cur)}, so <b>{ui.esc(th['single'] if single_wins else th['multi'])}</b> wins.")
            hi = max(tie * 2, cur * 1.3, 200)
            xs = [0, hi]
            single = next(p for p in result["complete"] if p["name"] == th["single"])
            multi = next(p for p in result["complete"] if p["name"] == th["multi"])
            fig = go.Figure()
            fig.add_scatter(x=[x / 100 for x in xs], y=[single["cash_cents"] / 100] * 2, name=th["single"],
                            mode="lines", line=dict(color=ui.INK, width=3))
            fig.add_scatter(x=[x / 100 for x in xs],
                            y=[(multi["cash_cents"] + x * th["extra_stops"]) / 100 for x in xs], name=th["multi"],
                            mode="lines", line=dict(color=ui.PRODUCE, width=3))
            fig.add_vline(x=tie / 100, line_dash="dash", line_color=ui.MARKDOWN,
                          annotation_text=f"tie {money.fmt(tie)}", annotation_position="top")
            fig.add_scatter(x=[cur / 100], y=[(single["cash_cents"] if single_wins else
                                               multi["cash_cents"] + cur * th["extra_stops"]) / 100],
                            mode="markers", marker=dict(size=14, color=ui.TAG, line=dict(color=ui.INK, width=2)),
                            name="your setting")
            fig.update_layout(xaxis=dict(title="Cost of each extra store", tickprefix="$"),
                              yaxis=dict(title="Decision cost", tickprefix="$"))
            ui.chart(fig, 300)
            carriers = th["carriers"]
            st.markdown(f"**The {len(carriers)} item{'s' if len(carriers) != 1 else ''} carrying the advantage:**")
            ui.rows([(ui.sticker(money.fmt(c["diff_cents"]), "green"), c["item"],
                      f"{c['single_store']} {money.fmt(c['single_cents'])} vs {c['multi_store']} "
                      f"{money.fmt(c['multi_cents'])}", "", "") for c in carriers])
            rest = [r for r in th["items"] if r not in carriers and r["diff_cents"] != 0]
            if rest:
                st.caption("Other items that differ: " + ", ".join(
                    f"{r['item']} ({money.fmt(r['diff_cents'], signed=True)})" for r in rest))
    with t2:
        ages = [7, 14, 21, 30, 45, 60, 90, 120, 180, 365]
        rows = D.sweep(inputs, "max_age_days", ages)
        winners = sorted({r["winner"] or "no complete plan" for r in rows})
        palette = {w: c for w, c in zip(winners, [ui.INK, ui.PRODUCE, "#D9A400", "#7F93B0", ui.MARKDOWN, "#8E5B9E"])}
        cells = "".join(
            f'<div class="pw-age" style="background:{palette[r["winner"] or "no complete plan"]}">'
            f'<b>{r["value"]} d</b><span>{ui.esc(r["winner"] or (r["best_partial"] or "no plan"))}</span>'
            f'<span>{money.fmt(r["decision_cents"]) if r["decision_cents"] else ""}</span></div>' for r in rows)
        st.html(f'<div class="pw-ages">{cells}</div>')
        pts = D.change_points(rows)
        if pts:
            st.write("The plan changes " + "; ".join(
                f"between **{a} and {b} days** ({wa or 'no complete plan'} → {wb or 'no complete plan'})"
                for a, b, wa, wb in pts) + ".")
        else:
            st.write("The winner is the same for every age limit from 7 to 365 days.")
    with t3:
        out = []
        for m in METHODS:
            alt = json.loads(D.canonical(inputs))
            alt["settings"]["method"] = m
            r = D.compute(alt)
            w = r["complete"][0] if r["complete"] else None
            out.append((METHODS[m], r["winner"] or "no complete plan", w["decision_cents"] if w else None))
        ui.rows([(ui.sticker(name.split()[0], "ink"), winner, "", money.fmt(c) if c else "", "")
                 for name, winner, c in out])
        st.caption("Last eligible price follows the newest price you saw; the recent median is steadier but slower "
                   "to notice a change.")


def plan_detail(key: str, inputs: dict, result: dict) -> None:
    names = [p["name"] for p in result["complete"]] + [f"{p['name']} (partial)" for p in result["partial"][:3]]
    if not names:
        return
    c1, c2 = st.columns([3, 2])
    pick = c1.selectbox("Plan", names, key=f"{key}_detail_pick")
    plan = D.plan_by_name(result, pick.replace(" (partial)", ""))
    pins = st.session_state.setdefault(f"{key}_pins", [])
    if c2.button("Pin this plan for comparison", key=f"{key}_pin", disabled=len(pins) >= 2 and pick not in
                 [p["label"] for p in pins]):
        pins.append({"label": f"{pick} ({METHODS[inputs['settings']['method']].lower()}, "
                              f"{inputs['settings']['max_age_days']} d, stop {money.fmt(inputs['settings']['extra_stop_cents'])})",
                     "plan": plan, "cutoff": inputs["cutoff"]})
        st.session_state[f"{key}_pins"] = pins[-2:]
    lines = pd.DataFrame([{
        "item": l["item"], "store": l["store"], "bought as": "same product" if l["exact"] else l["label"],
        "qty": f"{l['qty']:g} {l['unit']}", "price": f"{money.fmt(l['price_cents'])}/{'lb' if l['basis'] == 'lb' else 'pkg'}",
        "cost": l["cost_cents"] / 100, "evidence": f"{l['estimate']['method']} of {l['estimate']['n']}",
        "newest": l["estimate"]["newest"], "history": "LIMITED: " + l["estimate"]["limited_why"]
        if l["estimate"]["limited"] else "ok"} for l in plan["lines"]])
    st.caption("Click a line to see exactly where its price came from.")
    ev = st.dataframe(lines, hide_index=True, width="stretch", on_select="rerun",
                      selection_mode="single-row", key=f"{key}_lines_{pick}",
                      column_config={"cost": st.column_config.NumberColumn("cost", format="$%.2f")})
    if plan["missing"]:
        ui.note("<b>Not priced:</b> " + "; ".join(f"{ui.esc(m['item'])} ({ui.esc('; '.join(m['reasons']))})"
                                                   for m in plan["missing"]))
    if ev.selection.rows:
        evidence_inspector(key, inputs, plan["lines"][ev.selection.rows[0]])


def evidence_inspector(key: str, inputs: dict, line: dict) -> None:
    est = line["estimate"]
    obs = [o for o in inputs["observations"] if o["obs_id"] in est["obs_ids"]]
    with st.container(border=True):
        st.markdown(f"#### Evidence: {line['item']} at {line['store']}")
        if not line["exact"]:
            st.write(f"Substitute: **{line['label']}**, {line['qty']:g} {line['unit']} to cover the requested amount.")
        steps = []
        for o in sorted(obs, key=lambda o: str(o["date"])):
            if o["source"] == "receipt":
                net = o["line_cents"] - (o["discount_cents"] or 0)
                steps.append(f"{o['date']}  receipt line “{o['raw_text']}”: line {money.fmt(o['line_cents'])}"
                             + (f" − discount {money.fmt(o['discount_cents'])}" if o["discount_cents"] else "")
                             + f" = net {money.fmt(net)}; ÷ {o['qty']:g} {'lb' if o['basis'] == 'lb' else 'pkg'}"
                             f" = {money.fmt(o['price_cents'])}/{'lb' if o['basis'] == 'lb' else 'pkg'}")
                sc = db.q("SELECT cents, description FROM receipt_adjustments WHERE receipt_id = ? AND "
                          "kind = 'store_coupon'", (o["receipt_id"],))
                if not sc.empty:
                    steps.append(f"    that receipt also had a store-wide coupon ({money.fmt(int(sc['cents'].sum()))}); "
                                 "unit prices are BEFORE store-wide coupons, which are kept separately")
            else:
                steps.append(f"{o['date']}  {o['source']} price {money.fmt(o['price_cents'])}/"
                             f"{'lb' if o['basis'] == 'lb' else 'pkg'}" + (f" ({o['note']})" if o.get("note") else "")
                             + (f"; terms: {o['terms']}" if o.get("terms") else ""))
        method = ("median of these" if est["method"] == "median" else "the newest of these")
        steps.append(f"Estimate = {method} = {money.fmt(est['price_cents'])}; × {line['qty']:g} {line['unit']} "
                     f"= {money.fmt(line['cost_cents'])}")
        if est["limited"]:
            steps.append(f"Limited history: {est['limited_why']}")
        ui.evidence(steps)
        ex = st.session_state.setdefault(f"{key}_exclude", [])
        c = st.columns(len(obs) if obs else 1)
        for i, o in enumerate(obs[:4]):
            if c[i].button(f"Exclude {o['date']} {o['source']}", key=f"{key}_ex_{o['obs_id']}",
                           help="Recompute as if this price had never been seen."):
                ex.append(o["obs_id"])
                st.session_state[f"{key}_exclude"] = ex
                st.rerun()


def pinned(key: str) -> None:
    pins = st.session_state.get(f"{key}_pins", [])
    if not pins:
        return
    st.subheader("Pinned plans")
    cols = st.columns(len(pins))
    for col, pin in zip(cols, pins):
        p = pin["plan"]
        with col, st.container(border=True):
            st.markdown(f"**{pin['label']}**")
            st.write(f"Items {money.fmt(p['checkout_cents'])} · trip {money.fmt(p['trip_cents'])} · "
                     f"decision {money.fmt(p['decision_cents'])}")
            st.write(f"Coverage {p['covered']}/{p['total_items']} · oldest price {_age(pin['cutoff'], p['evidence_oldest'])}")
            st.dataframe(pd.DataFrame([{"item": l["item"], "store": l["store"], "cost": l["cost_cents"] / 100}
                                       for l in p["lines"]]), hide_index=True, width="stretch",
                         column_config={"cost": st.column_config.NumberColumn(format="$%.2f")})
    if st.button("Clear pinned plans", key=f"{key}_clearpins"):
        st.session_state[f"{key}_pins"] = []
        st.rerun()
    ex = st.session_state.get(f"{key}_exclude", [])
    if ex:
        st.caption(f"{len(ex)} observation(s) excluded from this scenario.")


def full_results(key: str, inputs: dict, result: dict) -> None:
    verdict(inputs, result)
    ex = st.session_state.get(f"{key}_exclude", [])
    if ex:
        c1, c2 = st.columns([4, 1])
        c1.caption(f"Excluding {len(ex)} observation(s): {', '.join(ex)}")
        if c2.button("Restore them", key=f"{key}_restore"):
            st.session_state[f"{key}_exclude"] = []
            st.rerun()
    cost_chart(result)
    plans_table(key, inputs, result)
    st.subheader("What changes the decision?")
    what_changes(key, inputs, result)
    st.subheader("Where every dollar comes from")
    plan_detail(key, inputs, result)
    pinned(key)


def freeze_box(key: str, inputs: dict, result: dict, mode: str, trip_receipt_id: int | None = None) -> int | None:
    with st.container(border=True):
        st.markdown("**Save this decision** — its basket, settings, eligible prices and result are frozen. "
                    "Later imports and product edits can't change it.")
        c1, c2 = st.columns([3, 1])
        name = c1.text_input("Name", value=st.session_state.get(f"{key}_name", ""), key=f"{key}_name_in",
                             placeholder="Weekly shop, Aldi worth it?", label_visibility="collapsed")
        if c2.button("Save decision", type="primary", key=f"{key}_freeze", disabled=not result["complete"]
                     and not result["partial"]):
            did = D.save(inputs, result, name or f"Decision {date.today().isoformat()}", mode, trip_receipt_id)
            st.session_state[f"{key}_saved_id"] = did
            st.toast(f"Saved decision #{did}.")
            return did
    return st.session_state.get(f"{key}_saved_id")


def knowledge_chart(cutoff: str, product_ids: set[int] | None = None) -> None:
    """Every observation over time, with what the decision could and couldn't see."""
    allobs = observations.load(None, product_ids=product_ids)
    cut = pd.Timestamp(cutoff)
    if allobs.empty:
        return
    allobs["d"] = pd.to_datetime(allobs["date"])
    colors = ui.store_colors(sorted(allobs["store"].unique()))
    fig = go.Figure()
    for store, g in allobs.groupby("store"):
        seen = g[g["d"] < cut]
        hid = g[g["d"] >= cut]
        fig.add_scatter(x=seen["d"], y=[store] * len(seen), mode="markers", name=f"{store} (known)",
                        marker=dict(size=7, color=colors[store]), showlegend=False,
                        hovertemplate=f"{store}<br>%{{x|%b %d}}<extra>known</extra>")
        fig.add_scatter(x=hid["d"], y=[store] * len(hid), mode="markers", name=f"{store} (hidden)",
                        marker=dict(size=7, color="rgba(0,0,0,0)", line=dict(color=ui.INK_SOFT, width=1)),
                        showlegend=False, hovertemplate=f"{store}<br>%{{x|%b %d}}<extra>hidden</extra>")
    fig.add_vrect(x0=cut, x1=allobs["d"].max() + pd.Timedelta(days=2), fillcolor=ui.INK, opacity=0.08, line_width=0,
                  annotation_text="hidden from the decision", annotation_position="top left")
    fig.add_vline(x=cut, line_color=ui.MARKDOWN, line_width=3)
    fig.update_layout(yaxis=dict(title=""), xaxis=dict(title="Price observations over time"))
    ui.chart(fig, 220)


def export_buttons(key: str, did: int, outcome: dict | None = None) -> None:
    d = D.load_decision(did)
    redact = st.toggle("Redact (leave out receipt numbers, evidence ids, locations and personal data)", value=True,
                       key=f"{key}_redact_{did}")
    html_doc = summary.render(d, outcome, redact=redact)
    st.download_button("Download one-page summary", html_doc, file_name=f"pricewise-decision-{did}"
                       f"{'-redacted' if redact else ''}.html", mime="text/html", key=f"{key}_dl_{did}")
    st.caption("Open it in a browser and print to PDF to share as a PDF.")
