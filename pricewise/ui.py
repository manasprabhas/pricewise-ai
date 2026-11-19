"""Design system: supermarket shelf tags, markdown stickers and thermal receipt strips.

Palette (freezer aisle under fluorescent light):
  freezer #DDE7E4  page        ink #1C2B4B  text        tag #FFD43B  key numbers
  markdown #E0382F problems    produce #2D7D52 savings  paper #FCFCF8 receipts
"""
from __future__ import annotations

import html
from functools import lru_cache

import plotly.graph_objects as go
import plotly.io as pio
import numpy as np
import streamlit as st

INK = "#1C2B4B"
INK_SOFT = "#4A5876"
FREEZER = "#DDE7E4"
FREEZER_DEEP = "#C3D3CE"
TAG = "#FFD43B"
MARKDOWN = "#E0382F"
PRODUCE = "#2D7D52"
PAPER = "#FCFCF8"
STORE_COLORS = [INK, "#D9A400", PRODUCE, "#7F93B0", "#2B8A99", "#8E5B9E", "#B86F2D"]

CSS = """
<style>
@font-face { font-family: 'Big Shoulders Display'; src: url('/app/static/fonts/BigShouldersDisplay.ttf') format('truetype');
  font-weight: 100 900; font-display: swap; }
@font-face { font-family: 'Atkinson Hyperlegible'; src: url('/app/static/fonts/AtkinsonHyperlegible-Regular.ttf') format('truetype');
  font-weight: 400; font-display: swap; }
@font-face { font-family: 'Atkinson Hyperlegible'; src: url('/app/static/fonts/AtkinsonHyperlegible-Bold.ttf') format('truetype');
  font-weight: 700; font-display: swap; }
@font-face { font-family: 'Courier Prime'; src: url('/app/static/fonts/CourierPrime-Regular.ttf') format('truetype');
  font-weight: 400; font-display: swap; }
@font-face { font-family: 'Courier Prime'; src: url('/app/static/fonts/CourierPrime-Bold.ttf') format('truetype');
  font-weight: 700; font-display: swap; }

:root {
  --ink: #1C2B4B; --ink-soft: #4A5876; --freezer: #DDE7E4; --freezer-deep: #C3D3CE;
  --tag: #FFD43B; --markdown: #E0382F; --produce: #2D7D52; --paper: #FCFCF8;
  --display: 'Big Shoulders Display', 'Arial Narrow', Impact, sans-serif;
  --body: 'Atkinson Hyperlegible', 'Segoe UI', system-ui, sans-serif;
  --thermal: 'Courier Prime', 'Courier New', monospace;
}

html, body, .stApp, [data-testid="stAppViewContainer"] { background: var(--freezer) !important; color: var(--ink); }
.stApp { font-family: var(--body); }
/* fluorescent tube glow along the top of the aisle */
[data-testid="stAppViewContainer"]::before {
  content: ""; position: fixed; inset: 0 0 auto 0; height: 220px; pointer-events: none; z-index: 0;
  background: radial-gradient(120% 100% at 50% -40%, rgba(255,255,255,.75), rgba(255,255,255,0) 70%);
}
[data-testid="stHeader"] { background: transparent; }
[data-testid="stMainBlockContainer"] { padding-top: 2.2rem; max-width: 1180px; }
p, li, label, .stMarkdown { font-family: var(--body); color: var(--ink); }
h1, h2, h3, h4 { font-family: var(--display) !important; color: var(--ink) !important; letter-spacing: .01em; }
h1 { font-weight: 900 !important; font-size: 4.2rem !important; line-height: .95 !important; }
h2 { font-weight: 800 !important; font-size: 2.3rem !important; line-height: 1 !important; margin-top: 1.4rem !important; }
h3 { font-weight: 800 !important; font-size: 1.6rem !important; }
a { color: var(--ink); text-decoration-color: var(--tag); text-decoration-thickness: 3px; }
code { color: var(--ink); background: rgba(28,43,75,.07); }

/* sidebar: the aisle-end sign */
[data-testid="stSidebar"] { background: var(--ink); border-right: 0; }
[data-testid="stSidebar"] * { color: #E9EEF7; }
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p { color: #B9C6DD !important; }
[data-testid="stSidebar"] .stButton > button, [data-testid="stSidebar"] .stButton > button * { color: var(--ink) !important; }
[data-testid="stSidebarHeader"] img { height: 2.6rem; max-width: 100%; }
[data-testid="stSidebarNav"] { padding-top: .25rem; }
[data-testid="stSidebarNavSeparator"] { border-color: rgba(255,255,255,.15); }
[data-testid="stSidebarNav"] header, [data-testid="stNavSectionHeader"] {
  font-family: var(--display) !important; font-weight: 800; font-size: 1.05rem !important; color: var(--tag) !important;
  letter-spacing: .04em; margin-top: .6rem; }
[data-testid="stSidebarNavLink"] { border-radius: 2px; }
[data-testid="stSidebarNavLink"]:hover { background: rgba(255,212,59,.12); }
[data-testid="stSidebarNavLink"][aria-current="page"] { background: var(--tag); }
[data-testid="stSidebarNavLink"][aria-current="page"] * { color: var(--ink) !important; font-weight: 700; }

/* buttons: price-gun labels */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button, [data-testid="stPageLink"] a {
  font-family: var(--body); font-weight: 700; border-radius: 3px; border: 2px solid var(--ink);
  background: var(--paper); color: var(--ink); box-shadow: 3px 3px 0 var(--ink); transition: transform .08s, box-shadow .08s; }
.stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover {
  border-color: var(--ink); color: var(--ink); background: #fff; transform: translate(-1px,-1px); box-shadow: 4px 4px 0 var(--ink); }
.stButton > button:active { transform: translate(2px,2px); box-shadow: 1px 1px 0 var(--ink); }
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] { background: var(--tag); }
.stButton > button:focus-visible, a:focus-visible, input:focus-visible { outline: 3px solid var(--markdown) !important; outline-offset: 2px; }
[data-testid="stPageLink"] a { display: inline-flex; padding: .35rem .8rem; text-decoration: none; }
[data-testid="stPageLink"] a:hover { background: var(--tag); }

/* inputs */
[data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="textarea"], .stNumberInput input, .stDateInput input {
  background: var(--paper) !important; border-radius: 3px !important; }
[data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="textarea"] { border: 2px solid var(--ink) !important; }
[data-testid="stFileUploaderDropzone"] { background: var(--paper); border: 2px dashed var(--ink); border-radius: 3px; }
[data-baseweb="tag"] { background: var(--tag) !important; }
[data-baseweb="tag"] span { color: var(--ink) !important; }
.stSlider [role="slider"] { background: var(--tag); border: 2px solid var(--ink); box-shadow: none; }

/* tabs */
.stTabs [data-baseweb="tab-list"] { gap: .25rem; border-bottom: 2px solid var(--ink); }
.stTabs [data-baseweb="tab"] { padding: .35rem 1rem !important; background: transparent; border-radius: 3px 3px 0 0;
  height: auto !important; }
.stTabs [data-baseweb="tab"] p { font-family: var(--display) !important; font-weight: 800; font-size: 1.2rem; }
.stTabs [aria-selected="true"] { background: var(--ink); }
.stTabs [aria-selected="true"] p { color: var(--tag) !important; }
.stTabs [data-baseweb="tab-highlight"] { display: none; }

/* containers, tables, expanders */
[data-testid="stVerticalBlockBorderWrapper"]:has(> div > [data-testid="stVerticalBlock"]) { border-radius: 3px; }
div[data-testid="stExpander"] details { background: var(--paper); border: 2px solid var(--ink); border-radius: 3px; }
[data-testid="stDataFrame"], [data-testid="stDataEditor"] { border: 2px solid var(--ink); border-radius: 3px; }
[data-testid="stMetric"] { background: var(--paper); border: 2px solid var(--ink); padding: .6rem .8rem; border-radius: 3px; }
[data-testid="stMetricValue"] { font-family: var(--display); font-weight: 800; }
[data-testid="stAlert"] { border-radius: 3px; border: 2px solid var(--ink); }
[data-testid="stChatMessage"] { background: var(--paper); border: 2px solid var(--ink); border-radius: 3px; }
[data-testid="stCaptionContainer"] p { color: var(--ink-soft); }
hr { border-color: rgba(28,43,75,.25); }

/* ---------- shelf tag ---------- */
.pw-tags { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 14px; margin: .2rem 0 1rem; }
.pw-tag { position: relative; background: var(--tag); border: 2px solid var(--ink); padding: 12px 14px 0 34px; color: var(--ink);
  min-height: 132px; display: flex; flex-direction: column; box-shadow: 0 2px 0 rgba(28,43,75,.25); }
.pw-tag::before { content: ""; position: absolute; left: 11px; top: 14px; width: 12px; height: 12px; border-radius: 50%;
  background: var(--freezer); border: 2px solid var(--ink); }
.pw-tag.red { background: var(--markdown); color: #fff; } .pw-tag.red .pw-tag-foot { color: var(--ink); }
.pw-tag.green { background: var(--produce); color: #fff; } .pw-tag.green .pw-tag-foot { color: var(--ink); }
.pw-tag.paper { background: var(--paper); }
.pw-tag-label { font-family: var(--body); font-weight: 700; font-size: .92rem; line-height: 1.15; }
.pw-tag-price { font-family: var(--display); font-weight: 900; font-size: 4.1rem; line-height: .9; margin: 6px 0 8px;
  letter-spacing: -.01em; display: flex; align-items: flex-start; }
.pw-tag-price .cur { font-size: 1.6rem; margin: 6px 2px 0 0; font-weight: 800; }
.pw-tag-price .cents { font-size: 1.9rem; margin: 5px 0 0 2px; font-weight: 800; text-decoration: underline; text-underline-offset: 4px; }
.pw-tag-price .word { font-size: 3.2rem; }
.pw-tag-price .word.long { font-size: 1.9rem; line-height: 1.05; }
.pw-tag-foot { margin: auto -14px 0 -34px; background: var(--paper); border-top: 2px solid var(--ink); padding: 5px 12px;
  font-family: var(--body); font-size: .82rem; color: var(--ink); }

/* ---------- markdown sticker ---------- */
.pw-sticker { display: inline-block; background: var(--markdown); color: #fff; font-family: var(--display); font-weight: 800;
  font-size: 1rem; line-height: 1; padding: 5px 9px 4px; border-radius: 999px; transform: rotate(-4deg);
  border: 2px solid #fff; box-shadow: 0 0 0 2px var(--markdown); white-space: nowrap; }
.pw-sticker.green { background: var(--produce); box-shadow: 0 0 0 2px var(--produce); }
.pw-sticker.yellow { background: var(--tag); color: var(--ink); box-shadow: 0 0 0 2px var(--ink); border-color: var(--tag); }
.pw-sticker.ink { background: var(--ink); box-shadow: 0 0 0 2px var(--ink); }

/* ---------- thermal receipt ---------- */
.pw-receipt-wrap { position: relative; padding-bottom: 12px; filter: drop-shadow(0 3px 0 rgba(28,43,75,.22)); }
.pw-receipt { position: relative; background: var(--paper); font-family: var(--thermal); color: #222; padding: 18px 18px 10px;
  font-size: .95rem; line-height: 1.5; }
.pw-receipt::after { content: ""; position: absolute; left: 0; right: 0; bottom: -10px; height: 10px;
  background: linear-gradient(135deg, var(--paper) 50%, transparent 50%) 0 0 / 14px 10px repeat-x,
              linear-gradient(-135deg, var(--paper) 50%, transparent 50%) 7px 0 / 14px 10px repeat-x; }
.pw-receipt .head { text-align: center; font-weight: 700; letter-spacing: .12em; }
.pw-receipt .sub { text-align: center; color: #666; font-size: .85rem; }
.pw-receipt .rule { border-top: 2px dashed #9aa; margin: 8px 0; }
.pw-receipt .row { display: grid; grid-template-columns: 1fr auto; gap: 8px; align-items: baseline; position: relative; }
.pw-receipt .row .n { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pw-receipt .row .note { grid-column: 1 / -1; margin: 1px 0 6px; }
.pw-receipt .row.flag .n, .pw-receipt .row.flag .p { color: var(--markdown); font-weight: 700; }
.pw-receipt .total { font-weight: 700; font-size: 1.05rem; }
.pw-receipt .barcode { height: 34px; margin: 10px auto 2px; width: 70%;
  background: repeating-linear-gradient(90deg, #222 0 2px, transparent 2px 4px, #222 4px 5px, transparent 5px 8px, #222 8px 11px, transparent 11px 12px); }
@media (prefers-reduced-motion: no-preference) {
  .pw-print .pw-receipt { animation: pw-print 1.1s steps(18, end) both; }
  @keyframes pw-print { from { clip-path: inset(0 0 100% 0); } to { clip-path: inset(0 0 0 0); } }
}

/* ---------- page header ---------- */
.pw-head { margin: 0 0 1.2rem; border-bottom: 3px solid var(--ink); padding-bottom: .6rem; }
.pw-head h1 { margin: 0 !important; padding: 0 !important; }
.pw-head p { font-size: 1.12rem; max-width: 68ch; margin: .45rem 0 0; color: var(--ink-soft); }

/* ---------- action rows ---------- */
.pw-list { border-top: 2px solid var(--ink); }
.pw-item { display: grid; grid-template-columns: auto 1fr auto; gap: 14px; align-items: center; padding: 11px 2px;
  border-bottom: 1px solid rgba(28,43,75,.25); }
.pw-item .t { font-weight: 700; } .pw-item .d { color: var(--ink-soft); font-size: .95rem; }
.pw-item .v { font-family: var(--display); font-weight: 800; font-size: 1.5rem; white-space: nowrap; }
.pw-item .v.red { color: var(--markdown); } .pw-item .v.green { color: var(--produce); }
.pw-big { font-family: var(--display); font-weight: 900; font-size: 2.6rem; line-height: 1; }
.pw-note { background: var(--paper); border-left: 6px solid var(--tag); padding: .7rem .9rem; margin: .4rem 0 1rem; }
.pw-evidence { font-family: var(--thermal); font-size: .9rem; background: var(--paper); padding: .6rem .8rem; border: 1px dashed var(--ink-soft); }
.pw-brand { display: flex; align-items: center; gap: 10px; margin: .2rem 0 1rem; }
.pw-brand .mark { background: var(--tag); color: var(--ink) !important; font-family: var(--display); font-weight: 900;
  font-size: 1.7rem; padding: 2px 10px 0; border: 2px solid var(--tag); line-height: 1.1; }
.pw-brand .mark span { color: var(--ink) !important; }
.pw-brand .sub { font-size: .8rem; line-height: 1.2; opacity: .85; }
.pw-side-receipt { font-family: var(--thermal); background: var(--paper); padding: 10px 12px; margin-top: 1rem; font-size: .85rem; }
.pw-side-receipt, .pw-side-receipt * { color: #222 !important; }
.pw-side-receipt .row { display: flex; justify-content: space-between; }

.pw-demo-flag { background: var(--tag); color: var(--ink) !important; font-family: var(--display); font-weight: 900;
  font-size: 1.3rem; padding: 6px 10px; margin: .8rem 0 .4rem; line-height: 1.1; }
.pw-demo-flag span { font-family: var(--body); font-weight: 400; font-size: .78rem; color: var(--ink) !important; }

/* ---------- home actions ---------- */
.pw-action .t { font-family: var(--display); font-weight: 900; font-size: 2.1rem; line-height: 1; margin-bottom: .35rem; }
.pw-action .d { color: var(--ink-soft); min-height: 4.2em; }

/* ---------- price-age strip ---------- */
.pw-mono { font-family: var(--thermal); font-weight: 700; font-size: 1.02rem; }
.pw-matrix { width: 100%; border-collapse: collapse; border: 2px solid var(--ink); background: #fff; box-shadow: 4px 4px 0 var(--ink); }
.pw-matrix th { background: var(--ink); color: #fff; text-align: left; padding: 8px 10px; font-family: var(--display);
  font-size: 1.05rem; letter-spacing: .02em; }
.pw-matrix td { border-top: 1px solid rgba(28,43,75,.15); padding: 8px 10px; vertical-align: top; font-size: .92rem; }
.pw-matrix td small { color: #556; } .pw-matrix td small.d { color: var(--markdown); font-family: var(--thermal); }
.pw-matrix td.ok, .pw-matrix td.miss, .pw-matrix td.na { text-align: center; font-weight: 700; white-space: nowrap; width: 13%; }
.pw-matrix td.ok { background: rgba(45,125,82,.14); color: var(--produce); }
.pw-matrix td.miss { background: rgba(224,56,47,.12); color: var(--markdown); }
.pw-matrix td.na { color: #99a; font-weight: 400; }
.pw-src { background: var(--paper); border: 2px solid var(--ink); box-shadow: 4px 4px 0 var(--ink); padding: 10px 8px;
  font-family: var(--thermal); font-size: .78rem; color: #222; max-height: 560px; overflow-y: auto; }
.pw-src .cap { font-family: inherit; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; font-size: .7rem;
  color: #556; margin: 0 0 6px 4px; }
.pw-src .ln { display: grid; grid-template-columns: 26px 18px 1fr; gap: 4px; padding: 1px 4px; border-radius: 3px; }
.pw-src .ln .no { color: #99a; text-align: right; }
.pw-src .ln .mk { font-weight: 700; text-align: center; }
.pw-src .ln .tx { white-space: pre; overflow: hidden; text-overflow: ellipsis; }
.pw-src .ln.item { background: rgba(255,210,63,.28); }
.pw-src .ln.item .mk { color: var(--ink); }
.pw-src .ln.disc .mk, .pw-src .ln.disc .tx { color: #2d7d4f; }
.pw-src .ln.adj { background: rgba(79,129,189,.16); }
.pw-src .ln.sum .tx { font-weight: 700; }
.pw-src .ln.ign .tx { color: #888; }
.pw-src .key { margin-top: 8px; color: #667; font-size: .7rem; font-family: var(--body, sans-serif); }
.pw-two-truths { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 6px 0 12px; }
.pw-two-truths > div { border: 2px solid var(--ink); padding: 10px 14px; background: #fff; box-shadow: 3px 3px 0 var(--ink); }
.pw-two-truths > div b { display: block; font-family: var(--display); font-size: 1.25rem; letter-spacing: .02em; }
.pw-two-truths > div span { display: block; color: #556; font-size: .82rem; margin-top: 4px; }
.pw-two-truths .ok { border-left: 10px solid #2d7d4f; }
.pw-two-truths .bad { border-left: 10px solid var(--markdown); }
.pw-two-truths .mid { border-left: 10px solid var(--tag); }
.pw-ages { display: grid; grid-template-columns: repeat(auto-fit, minmax(84px, 1fr)); gap: 4px; margin: .4rem 0 .8rem; }
.pw-age { color: #fff; padding: 6px 8px; min-height: 64px; display: flex; flex-direction: column; gap: 2px;
  border: 2px solid var(--ink); font-size: .78rem; line-height: 1.15; }
.pw-age b { font-family: var(--display); font-size: 1.15rem; }

/* ---------- replay timeline ---------- */
.pw-timeline { display: grid; grid-template-columns: 1fr 1fr 1fr; margin: .2rem 0 1.2rem; position: relative; }
.pw-timeline::before { content: ""; position: absolute; left: 8%; right: 8%; top: 22px; border-top: 4px dotted var(--ink); }
.pw-step { position: relative; text-align: center; padding: 0 6px; }
.pw-step .dot { width: 44px; height: 44px; margin: 0 auto 6px; border-radius: 50%; background: var(--freezer);
  border: 3px solid var(--ink); display: flex; align-items: center; justify-content: center;
  font-family: var(--display); font-weight: 900; font-size: 1.4rem; position: relative; z-index: 1; }
.pw-step.done .dot { background: var(--ink); color: var(--tag); }
.pw-step.now .dot { background: var(--tag); }
.pw-step .t { font-family: var(--display); font-weight: 800; font-size: 1.25rem; line-height: 1; }
.pw-step .d { font-size: .85rem; color: var(--ink-soft); }
.pw-step.locked { opacity: .45; }

@media (max-width: 640px) {
  h1 { font-size: 3rem !important; }
  .pw-tag-price { font-size: 3.3rem; }
  [data-testid="stMainBlockContainer"] { padding-left: 1rem; padding-right: 1rem; padding-top: 4.2rem; }
}
</style>
"""


def inject() -> None:
    st.html(CSS)
    _plotly_template()


def d(x) -> str:
    """'Nov 7' without a leading zero (works on Windows too)."""
    return f"{x:%b} {x.day}"


def esc(x) -> str:
    return html.escape(str(x))


def header(title: str, description: str = "") -> None:
    st.html(f'<div class="pw-head"><h1>{esc(title)}</h1>{f"<p>{esc(description)}</p>" if description else ""}</div>')


def _price_parts(value: float) -> str:
    neg = value < -0.004
    v = abs(value)
    dollars, cents = f"{v:,.2f}".split(".")
    return (f'<span class="cur">{"−" if neg else ""}$</span>{dollars}<span class="cents">{cents}</span>')


def tag_html(label: str, value: float | str, foot: str = "", tone: str = "") -> str:
    if isinstance(value, (int, np.integer)) and not isinstance(value, bool):
        value = f"{int(value):,}"          # a count, not money
    price = (_price_parts(value) if isinstance(value, (float, np.floating)) else
             f'<span class="word{" long" if len(str(value)) > 12 else ""}">{esc(value)}</span>')
    foot_html = f'<div class="pw-tag-foot">{esc(foot)}</div>' if foot else '<div class="pw-tag-foot">&nbsp;</div>'
    return (f'<div class="pw-tag {tone}"><div class="pw-tag-label">{esc(label)}</div>'
            f'<div class="pw-tag-price">{price}</div>{foot_html}</div>')


def tags(items: list[tuple]) -> None:
    """items: (label, value, foot, tone)"""
    st.html('<div class="pw-tags">' + "".join(tag_html(*it) for it in items) + "</div>")


def sticker(text: str, tone: str = "") -> str:
    return f'<span class="pw-sticker {tone}">{esc(text)}</span>'


def receipt(store: str, when: str, rows: list[dict], total: float | None, footer: str = "", animate: bool = False) -> None:
    """rows: {name, price, flag(bool), note_html}"""
    body = []
    for r in rows:
        cls = "row flag" if r.get("flag") else "row"
        note = f'<div class="note">{r["note_html"]}</div>' if r.get("note_html") else ""
        body.append(f'<div class="{cls}"><span class="n">{esc(r["name"])}</span><span class="p">{esc(r["price"])}</span>'
                    f'{note}</div>')
    tot = (f'<div class="rule"></div><div class="row total"><span>TOTAL</span><span>${total:,.2f}</span></div>'
           if total is not None else "")
    foot = f'<div class="sub">{esc(footer)}</div>' if footer else ""
    st.html(f'<div class="pw-receipt-wrap {"pw-print" if animate else ""}"><div class="pw-receipt">'
            f'<div class="head">{esc(store.upper())}</div><div class="sub">{esc(when)}</div><div class="rule"></div>'
            + "".join(body) + tot + f'<div class="barcode"></div>{foot}</div></div>')


def rows(items: list[tuple]) -> None:
    """items: (badge_html, title, detail, value, tone)"""
    out = []
    for badge, title, detail, value, tone in items:
        out.append(f'<div class="pw-item"><div>{badge}</div><div><div class="t">{esc(title)}</div>'
                   f'<div class="d">{esc(detail)}</div></div><div class="v {tone}">{esc(value)}</div></div>')
    st.html('<div class="pw-list">' + "".join(out) + "</div>")


def note(text_html: str) -> None:
    st.html(f'<div class="pw-note">{text_html}</div>')


def evidence(lines: list[str]) -> None:
    st.html('<div class="pw-evidence">' + "<br>".join(esc(l) for l in lines) + "</div>")


def empty(message: str) -> None:
    st.html(f'<div class="pw-note">{esc(message)}</div>')


def timeline(stage: int, labels: list[tuple[str, str]]) -> None:
    """Three-step replay timeline. stage: 0, 1 or 2 (the current step)."""
    steps = []
    for i, (t, d) in enumerate(labels):
        cls = "done" if i < stage else "now" if i == stage else "locked"
        steps.append(f'<div class="pw-step {cls}"><div class="dot">{i + 1}</div><div class="t">{esc(t)}</div>'
                     f'<div class="d">{esc(d)}</div></div>')
    st.html('<div class="pw-timeline">' + "".join(steps) + "</div>")


def store_colors(stores: list[str]) -> dict[str, str]:
    return {s: STORE_COLORS[i % len(STORE_COLORS)] for i, s in enumerate(sorted(stores))}


@lru_cache(maxsize=1)
def _plotly_template() -> None:
    t = go.layout.Template()
    t.layout = go.Layout(
        font=dict(family="Atkinson Hyperlegible, Segoe UI, sans-serif", color=INK, size=14),
        title=dict(font=dict(family="Big Shoulders Display, Arial Narrow, sans-serif", size=24, color=INK)),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor=PAPER,
        colorway=STORE_COLORS,
        xaxis=dict(gridcolor="rgba(28,43,75,.08)", linecolor=INK, linewidth=2, ticks="outside", tickcolor=INK,
                   zeroline=False),
        yaxis=dict(gridcolor="rgba(28,43,75,.12)", linecolor=INK, linewidth=2, zeroline=False),
        legend=dict(bgcolor="rgba(0,0,0,0)", orientation="h", yanchor="bottom", y=1.02, x=0, traceorder="normal"),
        hoverlabel=dict(bgcolor=INK, font=dict(color="#fff", family="Atkinson Hyperlegible, sans-serif")),
        margin=dict(l=10, r=10, t=56, b=10),
    )
    pio.templates["pricewise"] = t
    pio.templates.default = "pricewise"


def chart(fig: go.Figure, height: int = 380) -> None:
    fig.update_layout(height=height, template="pricewise")
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
