"""Optional Gemini connection (free tier). Off by default; nothing is sent unless you switch it on.

Uses the public REST endpoint with `requests`, so no extra package is needed.
Note: on Google's free tier, what you send may be used to improve Google's products.
"""
from __future__ import annotations

import base64
import json
import os

import requests

from . import db

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiError(RuntimeError):
    pass


def api_key() -> str:
    from . import credentials
    return credentials.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY", "").strip()


def enabled() -> bool:
    return db.get_setting("gemini_enabled") == "1" and bool(api_key())


def model() -> str:
    return db.get_setting("gemini_model") or "gemini-flash-latest"


def generate(parts: list[dict], json_mode: bool = False, timeout: float = 60) -> str:
    key = api_key()
    if not key:
        raise GeminiError("No Gemini API key saved. Add one in Settings.")
    body: dict = {"contents": [{"parts": parts}]}
    if json_mode:
        body["generationConfig"] = {"responseMimeType": "application/json", "temperature": 0}
    try:
        r = requests.post(ENDPOINT.format(model=model()), headers={"x-goog-api-key": key}, json=body,
                          timeout=timeout)
    except requests.RequestException as e:
        raise GeminiError(f"Could not reach Gemini: {e}") from e
    if r.status_code == 429:
        raise GeminiError("Gemini free-tier limit reached for now. Try again later, or use the offline scanner.")
    if r.status_code >= 400:
        raise GeminiError(f"Gemini returned {r.status_code}: {r.text[:300]}")
    try:
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, ValueError) as e:
        raise GeminiError("Gemini sent an empty answer. Try again.") from e


RECEIPT_PROMPT = """You read grocery receipts. Return ONLY JSON with this shape:
{"store": string|null, "date": "YYYY-MM-DD"|null,
 "items": [{"raw_text": string, "qty": number|null, "qty_unit": "each"|"lb"|"oz"|"kg",
            "unit_price": number|null, "line_total": number, "discount": number, "confidence": number}],
 "subtotal": number|null, "tax": number|null, "total": number|null}
Rules: copy each product line's text exactly as printed in raw_text. For items sold by weight, qty is the
weight and unit_price the price per weight unit. Put coupon or savings lines into the discount of the item
they apply to (positive number). Never invent values: use null when unreadable, and lower confidence (0-1)
for anything you are unsure about. Skip payment, change and loyalty-summary lines."""


def read_receipt(image_bytes: bytes, mime_type: str) -> dict:
    text = generate([
        {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(image_bytes).decode()}},
        {"text": RECEIPT_PROMPT},
    ], json_mode=True)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise GeminiError("Gemini's answer was not valid JSON. Try again or use the offline scanner.") from e
    data.setdefault("items", [])
    return data
