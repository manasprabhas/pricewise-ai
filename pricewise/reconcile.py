"""Reconciling a receipt: every line and adjustment must add up to the printed subtotal and total.

Algorithm (all in integer cents):
1. Every line is typed: item, item discount, deposit, fee, store-wide coupon, return, subtotal, tax, total.
2. Item discounts belong to an item (by the product they name, else the item above).
   Adjustments (deposits, fees, store-wide coupons, returns) stay separate, before or after the subtotal line.
3. Check A (when a subtotal is printed):
       items - item discounts + adjustments printed before the subtotal = subtotal
4. Check B (when a total is printed):
       subtotal (printed, else computed) + tax + adjustments printed after the subtotal = total
5. A receipt is RECONCILED only when every printed figure checks out exactly to the cent; UNVERIFIED when any
   check fails; NO PRINTED TOTAL when there is nothing to check against.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import money

ADJ_SIGN = {"deposit": +1, "fee": +1, "store_coupon": -1, "return": -1}
ADJ_LABEL = {"deposit": "Deposits", "fee": "Fees", "store_coupon": "Store-wide coupons", "return": "Returns"}


@dataclass
class Adjustment:
    kind: str                 # deposit | fee | store_coupon | return
    amount_cents: int         # always positive; the sign comes from the kind
    description: str = ""
    after_subtotal: bool = False

    @property
    def signed(self) -> int:
        return ADJ_SIGN[self.kind] * abs(self.amount_cents)


@dataclass
class Check:
    name: str
    expected_cents: int | None
    actual_cents: int | None
    ok: bool | None           # None = not checkable (figure not printed)
    explain: str


@dataclass
class Reconciliation:
    status: str               # reconciled | unverified | no_total
    checks: list[Check] = field(default_factory=list)
    items_cents: int = 0
    item_discounts_cents: int = 0
    adjustments_before_cents: int = 0
    adjustments_after_cents: int = 0
    computed_subtotal_cents: int = 0

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.ok is False]

    def to_dict(self) -> dict:
        return asdict(self)


def run(lines: list[tuple[int, int]], adjustments: list[Adjustment], subtotal_cents: int | None,
        tax_cents: int | None, total_cents: int | None) -> Reconciliation:
    """lines: (line_cents, item_discount_cents) per item."""
    items = sum(l for l, _ in lines)
    disc = sum(d for _, d in lines)
    before = sum(a.signed for a in adjustments if not a.after_subtotal)
    after = sum(a.signed for a in adjustments if a.after_subtotal)
    computed_sub = items - disc + before
    r = Reconciliation("no_total", [], items, disc, before, after, computed_sub)
    tax = tax_cents or 0

    if subtotal_cents is not None:
        ok = computed_sub == subtotal_cents
        r.checks.append(Check(
            "Subtotal", subtotal_cents, computed_sub, ok,
            f"Items {money.fmt(items)} − item discounts {money.fmt(disc)}"
            + (f" {'+' if before >= 0 else '−'} adjustments {money.fmt(abs(before))}" if before else "")
            + f" = {money.fmt(computed_sub)}; printed subtotal {money.fmt(subtotal_cents)}"
            + ("" if ok else f" (off by {money.fmt(computed_sub - subtotal_cents, signed=True)})")))
    if total_cents is not None:
        base = subtotal_cents if subtotal_cents is not None else computed_sub
        expected = base + tax + after
        ok = expected == total_cents
        r.checks.append(Check(
            "Total", total_cents, expected, ok,
            f"{'Subtotal' if subtotal_cents is not None else 'Lines'} {money.fmt(base)} + tax {money.fmt(tax)}"
            + (f" {'+' if after >= 0 else '−'} after-subtotal items {money.fmt(abs(after))}" if after else "")
            + f" = {money.fmt(expected)}; printed total {money.fmt(total_cents)}"
            + ("" if ok else f" (off by {money.fmt(expected - total_cents, signed=True)})")))
    if r.checks:
        r.status = "reconciled" if all(c.ok for c in r.checks) else "unverified"
    return r


def likely_misreads(lines: list[tuple[int, int]], diff_cents: int) -> list[int]:
    """Lines where changing ONE printed digit would remove the mismatch exactly (a typical OCR slip:
    1.64 read for 1.34). diff_cents = computed - printed. Returns line indexes, best guesses first."""
    out = []
    for i, (l, _) in enumerate(lines):
        target = l - diff_cents
        if target < 0:
            continue
        a, b = str(l), str(target)
        if len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1:
            out.append(i)
    return out
