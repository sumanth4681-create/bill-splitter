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
MAX_WIDTH = 2400          # anything wider is downscaled: smaller request, no memory spike
MAX_CENTS = 1_000_000_000  # a model "price" beyond this is noise, not a receipt row
MAX_QUANTITY = 10_000

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

# A whole row that is just a summary label, optionally followed by punctuation, a percentage,
# or an amount: "TOTAL", "Sub-Total:", "Tax 8%". Not "Total Wine 750ml" or "Cash register toy".
SUMMARY_LABEL = re.compile(
    r"^(sub[\s-]?total|total|tax|gst|hst|vat|tip|gratuity|balance(\s+due)?|change(\s+due)?|cash|card|credit|debit|"
    r"tender|visa|master\s?card|amex|amount(\s+due)?|items?|number\s+of\s+items|rounding)"
    r"\s*[:.\-]?\s*[\d.,%$-]*\s*$",
    re.I,
)
MAX_JSON_CHARS = 200_000
UNNAMED = "(unnamed row)"


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
    if img.width < MIN_WIDTH or img.width > MAX_WIDTH:
        target = TARGET_WIDTH if img.width < MIN_WIDTH else MAX_WIDTH
        img = img.resize((target, round(img.height * target / img.width)), Image.LANCZOS)
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
    """The first JSON object in the model's text. Trailing chatter is ignored; anything
    malformed, absurdly nested, or absurdly long counts as unreadable, never as a crash."""
    text = (text or "")[:MAX_JSON_CHARS]
    start = text.find("{")
    if start < 0:
        raise UnreadableReceipt("Upload a clear image of the receipt.")
    try:
        data, _ = json.JSONDecoder().raw_decode(text, start)
    except (ValueError, RecursionError) as exc:  # JSONDecodeError is a ValueError; huge ints raise ValueError too
        raise UnreadableReceipt("Upload a clear image of the receipt.") from exc
    if not isinstance(data, dict):
        raise UnreadableReceipt("Upload a clear image of the receipt.")
    return data


# ---------- pure mapping ----------

_MONEY_TEXT = re.compile(r"^[-+]?\$?\s*\d[\d,]*(\.\d+)?$|^[-+]?\$?\s*\d+,\d{1,2}$")


def _cents(value) -> int | None:
    """Integer cents from a number or a printed-looking string; None for anything else."""
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        if isinstance(value, str):
            text = value.strip()
            if not _MONEY_TEXT.match(text):
                return None
            text = text.replace("$", "").replace(" ", "")
            if "," in text and "." not in text and re.search(r",\d{1,2}$", text):
                text = text.replace(",", ".")  # "12,99" is a decimal comma, not thousands
            amount = Decimal(text.replace(",", ""))
        else:
            amount = Decimal(str(value))
        if not amount.is_finite():
            return None
        cents = int((amount * 100).quantize(Decimal("1")))
    except (InvalidOperation, ValueError, OverflowError):  # NaN/inf, 5000-digit ints, absurd exponents
        return None
    return cents if abs(cents) <= MAX_CENTS else None


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
    if not isinstance(data, dict):
        raise UnreadableReceipt("Upload a clear image of the receipt.")
    raw_items = data.get("items")
    rows = []
    for raw in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(raw, dict):
            continue
        price = _cents(raw.get("price"))
        if price is None:
            continue
        name = " ".join(str(raw.get("name") or "").split())[:200] or UNNAMED
        qty = raw.get("quantity")
        if isinstance(qty, bool) or not isinstance(qty, int) or not 1 <= qty <= MAX_QUANTITY:
            qty = 1
        rows.append((name, qty, price))

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
