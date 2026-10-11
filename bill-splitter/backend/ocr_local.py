"""Local receipt reader: a vision model served by LM Studio (OpenAI-compatible API).

The model proposes rows; code verifies them. Summary labels are dropped and, when a
printed subtotal exists, only the prefix of rows that adds up to it is kept, because the
model ignores "stop at SUBTOTAL" in practice. Small photos are upscaled first: at 645 px
the model misread $6.99 as $6.59, at 1290 px it got every row.
"""

import base64
import io
import json
import logging
import os
import re
import time
from decimal import Decimal, InvalidOperation

import requests
from PIL import Image, UnidentifiedImageError

from backend.models import ParsedItem, ParsedReceipt
from backend.ocr import UnreadableReceipt

log = logging.getLogger("bill_splitter.ocr_local")

MIN_WIDTH = 1200
TARGET_WIDTH = 1400

SYSTEM_PROMPT = """You extract structured data from a photo of a shopping or restaurant receipt.
Rules:
1. "items" are ONLY purchased products or services. Never list SUBTOTAL, TAX, TIP, GRATUITY, TOTAL, BALANCE, CHANGE, CASH, CARD, payment, date, store or address lines as items.
2. Read items from top to bottom and stop at the first SUBTOTAL or TOTAL line.
3. "price" is the amount printed on that item's row (the line total). Copy the digits exactly as printed; do not round, correct, or calculate.
4. "quantity" is the printed quantity for that row, or 1 if none is printed.
5. A discount or coupon row is an item with a negative price.
6. "subtotal", "tax", "tip", "total" are the amounts printed next to those labels. Use null if a label is not printed. Never put one label's amount in another field.
7. If you cannot read a value, use null rather than guessing."""

SCHEMA = {
    "type": "object",
    "properties": {
        "merchant": {"type": ["string", "null"]},
        "items": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "quantity": {"type": "integer"}, "price": {"type": ["number", "null"]}},
            "required": ["name", "quantity", "price"]}},
        "subtotal": {"type": ["number", "null"]},
        "tax": {"type": ["number", "null"]},
        "tip": {"type": ["number", "null"]},
        "total": {"type": ["number", "null"]},
    },
    "required": ["merchant", "items", "subtotal", "tax", "tip", "total"],
}

SUMMARY_LABEL = re.compile(
    r"^(sub\s?total|total|tax|gst|hst|vat|tip|gratuity|balance|change|cash|card|tender|visa|master|amount|items?|number of)\b",
    re.I,
)


class LocalReaderUnavailable(Exception):
    """LM Studio isn't reachable or answered with an error."""


# ---------- image prep ----------

def prepare_image(image_bytes: bytes, mime_type: str) -> bytes:
    """Return PNG bytes, upscaled to TARGET_WIDTH when the photo is narrower than MIN_WIDTH."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
    except UnidentifiedImageError as exc:
        raise UnreadableReceipt("The local reader needs a photo (JPEG or PNG).") from exc
    img = img.convert("RGB")
    if img.width < MIN_WIDTH:
        scale = TARGET_WIDTH / img.width
        img = img.resize((TARGET_WIDTH, round(img.height * scale)), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


# ---------- model call ----------

def call_model(image_bytes: bytes, mime_type: str) -> dict:
    """Send the receipt to LM Studio and return the model's JSON as a dict."""
    url = os.getenv("LMSTUDIO_URL", "http://localhost:1234/v1").rstrip("/")
    model = os.getenv("LMSTUDIO_MODEL", "qwen/qwen3.5-9b")
    png = prepare_image(image_bytes, mime_type)
    data_url = "data:image/png;base64," + base64.b64encode(png).decode()
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 4000,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": "Extract this receipt as JSON. /no_think"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ],
        "response_format": {"type": "json_schema", "json_schema": {"name": "receipt", "strict": True, "schema": SCHEMA}},
    }
    started = time.perf_counter()
    try:
        resp = requests.post(f"{url}/chat/completions", json=body, timeout=600)
    except requests.ConnectionError as exc:
        raise LocalReaderUnavailable("Local model isn't running. Start LM Studio's server (`lms server start`).") from exc
    if not resp.ok:
        raise LocalReaderUnavailable(f"Local model returned HTTP {resp.status_code}.")
    log.info("Local model call took %.0f ms", (time.perf_counter() - started) * 1000)
    message = resp.json()["choices"][0]["message"]
    # LM Studio sometimes puts the whole answer in reasoning_content and leaves content empty.
    text = (message.get("content") or "").strip() or (message.get("reasoning_content") or "")
    return extract_json(text)


def extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise UnreadableReceipt("Upload a clear image of the receipt.")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise UnreadableReceipt("Upload a clear image of the receipt.") from exc


# ---------- pure mapping ----------

def _cents(value) -> int | None:
    if value is None:
        return None
    try:
        return int((Decimal(str(value)) * 100).quantize(Decimal("1")))
    except InvalidOperation:
        return None


def clean_rows(rows: list[tuple[str, int, int]], subtotal_cents: int | None) -> list[tuple[str, int, int]]:
    """Drop summary-label rows; with a printed subtotal, keep only the prefix that sums to it."""
    kept = [(n, q, p) for n, q, p in rows if not SUMMARY_LABEL.match(n.strip())]
    if subtotal_cents is not None:
        running = 0
        for index, (_, _, price) in enumerate(kept):
            running += price
            if running == subtotal_cents:
                return kept[: index + 1]
    return kept


def map_local_response(data: dict) -> ParsedReceipt:
    rows = []
    for raw in data.get("items") or []:
        price = _cents(raw.get("price"))
        if price is None:
            continue
        name = " ".join(str(raw.get("name") or "").split()) or "Item"
        qty = raw.get("quantity") or 1
        rows.append((name, qty if isinstance(qty, int) and qty > 0 else 1, price))

    subtotal = _cents(data.get("subtotal"))
    rows = clean_rows(rows, subtotal)
    if not rows:
        raise UnreadableReceipt("Upload a clear image of the receipt.")
    items = [ParsedItem(name=n, quantity=q, price_cents=p) for n, q, p in rows]

    tax = _cents(data.get("tax"))
    tip = _cents(data.get("tip"))
    total = _cents(data.get("total"))
    items_total = sum(i.price_cents for i in items)
    if total is None:
        total = (subtotal if subtotal is not None else items_total) + (tax or 0) + (tip or 0)
    # Same rule as the Document AI mapper: fill a missing tax/tip only from the printed
    # subtotal -> total gap, never from the items, so a misread row stays visible.
    if subtotal is not None:
        extra = total - subtotal - (tax or 0) - (tip or 0)
        if extra > 0:
            if tax is None:
                tax = extra
            elif tip is None:
                tip = extra

    merchant = " ".join(str(data.get("merchant") or "").split()) or None
    return ParsedReceipt(merchant=merchant, items=items, tax_cents=tax or 0, tip_cents=tip or 0, total_cents=total)
