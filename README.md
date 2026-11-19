# PriceWise: AI Shopping Price Memory

A private grocery price memory that runs on your own laptop. It reads your receipts, remembers every price you
pay, plans where to buy a basket, and then **replays the trip** to show whether the plan was right, using only
what was known before you shopped.
**$0 to run. No account. Your data stays in one file on your computer. A one-file phone companion works in airplane mode.**

## Status: what's built and what isn't

A **personal-use prototype**, tested on fictional data. Accuracy on real receipts is measured with the tools in
*Evidence*, but only you can run that pilot on your own receipts.

| Built and tested | Not built yet / needs you |
|---|---|
| Decision Replay: frozen decisions, replay without look-ahead, prediction vs. receipt to the cent | Real-receipt accuracy numbers (the pilot tooling is ready; the receipts are yours) |
| Basket simulator: coverage, opt-in substitutes, whole packages, estimate method, thresholds | Timing Pocket on a physical phone (tested on an emulated iPhone in airplane mode) |
| Receipt review beside the source, separate subtotal/total checks, deposits, coupons, returns in cents | Barcode scanning in Pocket |
| Product identity: brand, variant, size; size-mismatch check; reversible fixes and merges; provenance | Automatic email collection (Gmail/Outlook API) |
| Checkout problems in three kinds, reviewed before a refund note | Live Walmart and Aldi prices (no free source) |
| Evidence: per-field accuracy with denominators, blind-spot suite, plain vs OpenCV OCR, price-rule benchmark | |
| Pocket: offline one-file phone tool, shopping mode, export and import with preview | |
| Isolated demo, backup and restore, upgrades from older databases, no-network test, launcher checks | |

---

## Quick start (Windows)

1. **Install Python 3.12** from [python.org](https://www.python.org/downloads/). On the first installer screen,
   tick **"Add python.exe to PATH"**.
2. **Unzip** this folder somewhere easy, e.g. `Documents\pricewise-ai`.
3. Double-click **`setup.bat`** (one time, about 3 minutes). It ends with a system check.
4. Double-click **`run.bat`**. It checks everything is installed, then opens PriceWise in your browser.
   If something is missing it says exactly what, instead of crashing.

## The three-minute walkthrough

Click **Open the demo** on *Today*. The demo is a separate file with six months of fictional receipts; your own
data is never touched, and *Back to my data* in the sidebar returns you.

**Minute 1: Plan a basket.** Open *Plan a basket*, start from the saved list *“Test basket: is Aldi worth a
second stop?”*. The verdict shows the plan, the cash at the till against your budget, and how many items each
plan covers. Open **What changes the decision?**: at a second-stop cost of **$0.64** the two plans tie; above it,
one store wins. The two items carrying the difference are named. Click any price to see the receipt line and the
calculation behind it. In **Save this decision**, press **Save decision**.

**Minute 2: Replay a trip.** Open *Replay a trip* and pick the Nov 7 Kroger trip. Step 1 shows only what was
known before that day. Step 2 makes the decision. **Freeze and reveal the receipt**: every cent of the
difference between plan and receipt is accounted for (a planted weighing error shows as +$0.32). Then open
*Saved decisions* and press **Reproduce**, **Replay against today's database** and **Run it again as a new
scenario**: the saved decision never changes, even after newer, cheaper receipts arrive.

**Minute 3: Trust.** Open *Check matches → Deliberate failure*. Import an 18 oz cereal saved as the 15.4 oz box:
the receipt still reconciles perfectly, but the size check flags it. Move it to the right product and watch the
unit price change from $0.29/oz to $0.25/oz; undo it from the change log. Then open *Evidence → Blind spots* to
see which check catches which kind of mistake, and *Pocket (phone) → Try it here first* for the phone tool.

---

## What's inside

**Start here**
| Page | What it does |
|---|---|
| Today | Three actions (import a receipt, plan a basket, replay a trip), then your latest receipt with every problem marked. |
| Import a receipt | Saved emails, PDFs, web pages, pasted text or order-history CSVs. Review beside the source: every line marked with what it became, each printed figure checked separately, deposits, fees, store coupons and returns kept apart from items. |
| Plan a basket | The basket simulator. Cash budget kept separate from the trip trade-off; coverage like “$26 for 7 of 10 items” with the reasons for gaps; opt-in substitutes respecting lactose-free, organic and minimum pack size; whole packages; last-price or median estimate with dates and counts; pin two plans side by side; evidence for every number. |
| Replay a trip | Before shopping → decision → actual receipt. Only observations dated before the trip are visible. Prediction vs receipt with signed error; missing, substituted and extra items; unvisited stores shown as estimates; refunds in their own ledger; a redacted one-page summary. |

**Receipts and products**: photo of a paper receipt, add a purchase (with offer terms for shelf tags and ads),
price memory, **Check matches** (review queue, deliberate-failure walkthrough, reversible merges, retailer-scoped
aliases, where each line came from, change log with undo), and **Pocket**.

**Analysis**: store showdown, why it cost more (price/volume/mix that adds up exactly), spending and personal
inflation, **checkout problems** (calculation mismatch, possible double scan, price-tag or promotion mismatch;
only reviewed flags go in a refund note; a missed offer needs the recorded tag and its terms), **shrinkflation**
(old and new prices, counts, first and last seen, coexisting sizes, a packages-per-year slider), nutrition per
dollar, cheaper swaps, stock-up radar, and a plain-English assistant whose numbers all come from the database.

**Track**: saved decisions, savings scoreboard, **Evidence**, settings and data (backup, restore with a preview,
demo controls, system check).

## Pocket: the offline phone tool
*Pocket (phone)* downloads one HTML file (about 0.5 MB, fonts included) holding your price book. Open it on the
phone from Files or Downloads; it works in airplane mode. Search, pick the exact package, type the shelf price on
the keypad (one package, “2 for”, or per lb, always shown), and see it against what you last paid and usually pay
here and elsewhere, each with a date and a stale label past 120 days. A product with no history just records the
shelf price. Shopping mode ticks items into the cart with a running total and budget left; remove, change amounts
or swap. **Sync** exports a small file; *Import from your phone* previews it and skips anything already imported.
The file carries a content-security policy that blocks every network connection, and contains no web addresses.

## How we know it works
Run `run-tests.bat` (**121 tests**). Among them:
- money in integer cents with HALF_UP rounding: weighed items, multipacks, deposits, returns, store-wide coupons,
  item offers, and a 25-line receipt where a one-digit misread is pinpointed
- the three duplicate cases: the same file twice, the same purchase from another source, a real second purchase
- the brief's acceptance test: save a decision, import a cheaper receipt dated after the cutoff, show the saved
  decision and its replay are unchanged, then open a new scenario where the newer price is eligible
- the $0.64 tie, the coverage change when an observation is removed, whole packages and requirements
- the cereal size mismatch, its fix and its undo; merges undone exactly; aliases scoped per store
- refund notes built only from reviewed flags; offers without terms not claimable
- the blind-spot matrix; a benchmark that never sees a trip or anything after it
- Pocket: self-contained, no web addresses, imports exactly once, multi-buy maths
- upgrading a database from an earlier version, backup and restore, the launcher check, and the core flows
  running with every network connection refused
- a missing or unreadable client address is never treated as the laptop; receipt weights in oz, kg or g are
  converted to pounds in replays; unverified receipts never move a store's tax estimate

Measure accuracy on your own receipts: label them in *Evidence → Pilot* (or by hand, see `eval/README.md`), then
*Evidence → Reading accuracy*, or `python evaluate.py eval/real`. Every run prints a manifest with hashes of the
data, the code versions and the results, so it can be reproduced.

| Target | Status |
|---|---|
| Lines read correctly on real receipts | Measured per field, with denominators, once you label 20–30 receipts |
| Exact money maths | Integer cents everywhere; covered by tests |
| No silent wrong product | Size and variant checks, review queue, confirmed-by-you kept apart from “it reconciles” |
| Decisions can't see the future | Strict cutoff; frozen inputs with SHA-256 fingerprints; tested |
| Note a shelf price in under 10 s | 0.4–0.6 s scripted on an emulated phone; time it yourself with the median shown in Pocket › Sync |

## Your data and privacy
- Your data lives in `data\pricewise.db`; the demo in `data\demo.db`. Back up and restore from *Settings*.
- API keys (all optional) go to Windows Credential Manager or macOS Keychain, never the database or exports.
- Nothing leaves your computer unless you switch on an optional extra (Gemini, Kroger prices, USDA lookup,
  official inflation). A test runs the core features with networking disabled.
- `data\` and `eval\real\` are excluded from Git.

## Optional extras (all off by default, all free)
| Extra | What it adds |
|---|---|
| Tesseract OCR | Reading paper-receipt photos offline. Installer: <https://github.com/UB-Mannheim/tesseract/wiki>. Digital receipts don't need it. |
| Gemini free tier | Better reading of crumpled receipts; questions in any wording. On Google's free tier, what you send may be used to improve Google's products. |
| Kroger price checks | Daily shelf prices for linked products at Kroger-family stores (free developer key). |
| USDA nutrition lookup | Nutrition for products not in the built-in list. |
| Official inflation | US food-at-home CPI to compare with your own. |

`run-for-phone.bat` also opens the laptop app over **home** Wi-Fi with a PIN; Pocket is the better choice in a store.

## Honest limits
- Prices are what **you** paid or recorded, not today's shelf prices (except linked Kroger products).
- On the demo data the price-rule benchmark finds “last paid” very slightly more accurate than the median
  PriceWise uses by default; the Evidence page says so and lets you switch.
- Offline OCR struggles with tilted, faded or crumpled receipts; you always review before saving.
- Pocket has been tested on an emulated phone, not yet timed by a real shopper on a real phone.

## Project layout
```
app.py                  navigation
views/                  one file per page
pricewise/
  money.py              integer cents, HALF_UP, whole packages
  reconcile.py          subtotal and total checks, adjustments, likely misreads
  observations.py       every price observation, with a strict date cutoff
  decision.py           basket simulator, frozen decisions, replay, thresholds
  lab_ui.py             the basket simulator's interface pieces
  summary.py            redacted one-page decision summary
  identity.py           size/variant checks, review queue, reversible fixes and merges
  failure_demo.py       the 18 oz / 15.4 oz walkthrough
  evidence.py           field-level scoring, blind spots, OCR comparison, benchmark, effort
  pocket.py             the phone file and its import (template: pocket_template.html)
  backup.py, doctor.py  backup/restore; installation check
  db.py, units.py, matcher.py, scanner.py, digital.py, review_ui.py, audit.py, shrink.py, analytics.py, pvm.py,
  nutrition.py, swaps.py, stockup.py, assistant.py, savings.py, kroger.py, gemini.py, credentials.py, access.py,
  catalog.py, demo.py, ui.py
evaluate.py             command-line evaluation (also --blind-spots, --benchmark)
eval/                   fictional sample labels; your pilot receipts go in eval/real (private)
tools/pocket_e2e.py     Pocket on an emulated phone in airplane mode (needs Playwright; optional)
static/                 bundled fonts (SIL Open Font License) and logo
samples/                sample receipts
tests/                  115 automated tests
```

Demo data is fictional: store names are real, but every product price and event in it is invented.
