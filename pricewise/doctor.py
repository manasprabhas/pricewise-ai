"""Checks that everything PriceWise needs is installed, before it starts.

    python -m pricewise.doctor          # prints a checklist; exit code 1 if something required is missing

Required: Python 3.10+, the packages in requirements.txt, a writable data folder.
Optional: Tesseract (paper-receipt photos), an OS credential store (only for optional API keys).
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

REQUIRED = [("streamlit", "streamlit", "1.40"), ("pandas", "pandas", "2.0"), ("plotly", "plotly", "5.0"),
            ("rapidfuzz", "rapidfuzz", "3.0"), ("pypdf", "pypdf", "4.0"), ("numpy", "numpy", "1.24")]
OPTIONAL = [("cv2", "opencv-python-headless", "photo clean-up for paper receipts"),
            ("pytesseract", "pytesseract", "reading paper-receipt photos"),
            ("keyring", "keyring", "keeping optional API keys in the OS credential store")]


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True


def _ver(dist: str) -> str:
    for d in (dist, dist.replace("-headless", "")):
        try:
            return metadata.version(d)
        except metadata.PackageNotFoundError:
            continue
    return "?"


def _at_least(v: str, minimum: str) -> bool:
    def parts(x):
        return tuple(int(p) for p in (x.split("+")[0].split(".") + ["0", "0"])[:3] if p.isdigit())
    try:
        return parts(v) >= parts(minimum)
    except ValueError:
        return True


def run() -> list[Check]:
    out = [Check("Python", sys.version_info >= (3, 10), f"{sys.version.split()[0]} (needs 3.10 or newer)")]
    for mod, dist, minimum in REQUIRED:
        try:
            importlib.import_module(mod)
            v = _ver(dist)
            out.append(Check(dist, _at_least(v, minimum), f"{v}" + ("" if _at_least(v, minimum) else
                                                                    f" (needs {minimum}+; run setup.bat again)")))
        except Exception as e:                      # noqa: BLE001
            out.append(Check(dist, False, f"missing ({e.__class__.__name__}); run setup.bat"))
    for mod, dist, why in OPTIONAL:
        try:
            importlib.import_module(mod)
            out.append(Check(dist, True, f"{_ver(dist)} · {why}", required=False))
        except Exception:                           # noqa: BLE001
            out.append(Check(dist, False, f"not installed · needed only for {why}", required=False))
    try:
        from . import scanner
        t = scanner.find_tesseract()
        out.append(Check("Tesseract OCR", bool(t), t or "not found · needed only for paper-receipt photos "
                                                         "(digital receipts work without it)", required=False))
    except Exception as e:                          # noqa: BLE001
        out.append(Check("Tesseract OCR", False, f"not checked ({e})", required=False))
    data = Path(os.environ.get("PRICEWISE_DB") or Path(__file__).resolve().parent.parent / "data" / "x").parent
    try:
        data.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=data):
            pass
        out.append(Check("Data folder", True, f"{data} is writable"))
    except OSError as e:
        out.append(Check("Data folder", False, f"{data} is not writable ({e})"))
    return out


def ok(checks: list[Check]) -> bool:
    return all(c.ok for c in checks if c.required)


def main() -> int:
    checks = run()
    for c in checks:
        mark = "OK  " if c.ok else ("FAIL" if c.required else "--  ")
        print(f"  [{mark}] {c.name:24s} {c.detail}")
    if ok(checks):
        print("\nEverything PriceWise needs is here.")
        return 0
    print("\nSomething required is missing. Run setup.bat (or: pip install -r requirements.txt), then try again.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
