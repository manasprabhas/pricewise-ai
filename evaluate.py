"""Measure receipt-reading accuracy on receipts YOU labelled, independent of the app's own tests.

Usage:
    python evaluate.py eval/sample                 # the 3 fictional samples that ship with PriceWise
    python evaluate.py eval/real                   # your own labelled receipts (see eval/README.md)
    python evaluate.py eval/real --engine gemini   # photos read with Gemini instead of Tesseract
    python evaluate.py eval/real --pipeline plain  # photos read by plain Tesseract, without the OpenCV clean-up
    python evaluate.py --blind-spots               # the cases built to fool an evaluator, and what catches each
    python evaluate.py --benchmark                 # price-rule benchmark on the active PriceWise data

Every receipt file (photo .png/.jpg, .pdf, .eml, .html, .txt) needs a label file next to it with the same name
and .json, written by a person from the paper or email, NOT copied from PriceWise's output:

    {"store": "Walmart", "date": "2026-11-07", "subtotal": 26.78, "total": 26.78,
     "lines": [{"text": "GV WHL MLK GAL", "line_total": 3.48, "qty": 1, "discount": 0, "size": "1 gal"},
               {"text": "FAIRLIFE LF WHOLE 52OZ", "line_total": 4.29, "variant": "lactose free"}, ...]}

Only "text" and "line_total" are required. Every other field you label is scored separately and reported with its
denominator ("Quantity 41/43": 43 labelled lines carried a quantity, 41 were read right).

Results are printed and saved to <folder>/results.csv.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

if "--benchmark" not in sys.argv:
    os.environ.setdefault("PRICEWISE_DB", str(Path(tempfile.mkdtemp()) / "eval.db"))   # never touches your data

import pandas as pd  # noqa: E402

from pricewise import db, evidence  # noqa: E402

score = evidence.score            # kept importable for the tests and older scripts
read = evidence.read_file


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="?")
    ap.add_argument("--engine", default="offline", choices=["offline", "gemini"])
    ap.add_argument("--pipeline", default="opencv", choices=["opencv", "plain"])
    ap.add_argument("--blind-spots", action="store_true")
    ap.add_argument("--benchmark", action="store_true")
    args = ap.parse_args()
    db.init_db()
    pd.set_option("display.width", 200)
    pd.set_option("display.max_colwidth", 60)
    if args.blind_spots:
        print(evidence.run_blind_spots().drop(columns=["what happens"]).to_string(index=False))
        return 0
    if args.benchmark:
        b = evidence.benchmark()
        print(f"{b['n']} receipt lines from {b.get('start')} on, each predicted only from earlier observations\n")
        print(b["rules"].to_string(index=False))
        return 0
    if not args.folder:
        ap.print_help()
        return 1
    folder = Path(args.folder)
    r = evidence.evaluate_folder(folder, args.engine, args.pipeline)
    df = r["receipts"]
    if df.empty:
        print("No labelled receipts found.")
        return 1
    cols = ["file", "kind", "lines_labelled", "lines_read", "lines_correct", "recall", "precision", "store_ok",
            "date_ok", "total_ok", "reconciles", "seconds"]
    print(df[cols].to_string(index=False, formatters={"recall": "{:.0%}".format, "precision": "{:.0%}".format}))
    print("\nField by field (correct / labelled):")
    for _, f in r["fields"].iterrows():
        acc = "—" if f["labelled"] == 0 else f"{f['accuracy']:.1%}"
        print(f"  {f['field']:24s} {f['correct']:>4}/{f['labelled']:<4} {acc}")
    for name in ("wrong", "missing", "extra"):
        if not r[name].empty:
            print(f"\n{name.title()}:")
            print(r[name].head(20).to_string(index=False))
    print("\nManifest:", json.dumps(r["manifest"], indent=1))
    df.to_csv(folder / "results.csv", index=False)
    (folder / "manifest.json").write_text(json.dumps(r["manifest"], indent=1))
    print(f"\nSaved {folder / 'results.csv'} and manifest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
