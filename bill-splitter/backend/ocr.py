"""Receipt reader: Google Document AI Expense Parser, with save-and-replay.

read_receipt() hashes the image. A saved response for that hash is replayed for
free; otherwise Document AI is called and its response saved to fixtures/.
map_expense_document() is pure, so it can be tested on saved responses offline.
"""

import hashlib
import logging
import os
import re
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

from google.cloud import documentai

from backend.models import ParsedItem, ParsedReceipt

log = logging.getLogger("bill_splitter.ocr")

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class UnreadableReceipt(Exception):
    """No line items found: blurry photo or not a receipt."""


class ReplayMissing(Exception):
    """OCR_MODE=replay and no saved response exists for this image."""


class ReaderNotConfigured(Exception):
    """Document AI settings are missing or rejected."""


def image_hash(image_bytes: bytes) -> str:
    return hashlib.sha256(image_bytes).hexdigest()


def read_receipt(image_bytes: bytes, mime_type: str) -> ParsedReceipt:
    fixture = FIXTURES_DIR / f"{image_hash(image_bytes)}.json"
    if fixture.exists():
        log.info("OCR replay from %s", fixture.name)
        doc = documentai.Document.from_json(fixture.read_text(), ignore_unknown_fields=True)
    elif os.getenv("OCR_MODE", "live") == "replay":
        raise ReplayMissing("No saved response for this image (OCR_MODE=replay).")
    else:
        doc = _call_document_ai(image_bytes, mime_type)
        FIXTURES_DIR.mkdir(exist_ok=True)
        fixture.write_text(documentai.Document.to_json(doc))
    return map_expense_document(doc)


def _call_document_ai(image_bytes: bytes, mime_type: str) -> documentai.Document:
    project = os.getenv("DOCAI_PROJECT_ID")
    location = os.getenv("DOCAI_LOCATION", "us")
    processor = os.getenv("DOCAI_PROCESSOR_ID")
    if not project or not processor or not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        raise ReaderNotConfigured("Receipt reader isn't configured. Check .env.")

    client = documentai.DocumentProcessorServiceClient(
        client_options={"api_endpoint": f"{location}-documentai.googleapis.com"}
    )
    name = client.processor_path(project, location, processor)
    started = time.perf_counter()
    try:
        result = client.process_document(
            request={"name": name, "raw_document": {"content": image_bytes, "mime_type": mime_type}}
        )
    except Exception as exc:  # auth, billing, wrong processor id, network
        log.warning("Document AI call failed: %s: %s", type(exc).__name__, str(exc)[:300])
        raise ReaderNotConfigured("Receipt reader isn't configured. Check .env.") from exc
    log.info("Document AI call took %.0f ms", (time.perf_counter() - started) * 1000)
    return result.document


# ---------- pure mapping ----------

def _cents_from_text(text: str) -> int | None:
    text = (text or "").strip()
    # Receipts print discounts as -5.00, (5.00), or 5.00-
    negative = text.startswith("-") or text.endswith("-") or (text.startswith("(") and text.endswith(")"))
    cleaned = re.sub(r"[^\d.]", "", text)
    if not cleaned:
        return None
    try:
        cents = int((Decimal(cleaned) * 100).quantize(Decimal("1")))
    except InvalidOperation:
        return None
    return -cents if negative else cents


def _money_cents(entity) -> int | None:
    money = entity.normalized_value.money_value
    if money.units or money.nanos:
        return money.units * 100 + round(money.nanos / 10_000_000)
    return _cents_from_text(entity.mention_text)


def _quantity(entity) -> int:
    text = entity.normalized_value.text or entity.mention_text
    try:
        qty = int(Decimal(re.sub(r"[^\d.]", "", text)))
    except (InvalidOperation, ValueError):
        return 1
    return qty if qty > 0 else 1


def _position(entity) -> int:
    segments = entity.text_anchor.text_segments
    return segments[0].start_index if segments else 0


def _line_items(line_entities) -> list[ParsedItem]:
    """Pair each description with the next amount in reading order.

    Google sometimes packs several rows into one line_item (e.g. "Milk 6.99 Eggs" then
    "5.49 Beef 14.80 Bread"), so its grouping can't be trusted; the text order can.
    """
    props = [p for e in line_entities for p in e.properties]
    props.sort(key=_position)
    items: list[ParsedItem] = []
    name, qty = None, 1
    for prop in props:
        if prop.type_ == "line_item/description":
            name = " ".join(prop.mention_text.split()) or None
        elif prop.type_ == "line_item/quantity":
            qty = _quantity(prop)
        elif prop.type_ == "line_item/amount":
            price = _money_cents(prop)
            if price is None:
                continue
            items.append(ParsedItem(name=name or "Item", quantity=qty, price_cents=price))
            name, qty = None, 1
    return items


def map_expense_document(doc: documentai.Document) -> ParsedReceipt:
    by_type: dict[str, list] = {}
    for entity in doc.entities:
        by_type.setdefault(entity.type_, []).append(entity)

    def amount(kind: str) -> int | None:
        found = by_type.get(kind)
        return _money_cents(found[0]) if found else None

    items = _line_items(by_type.get("line_item", []))
    if not items:
        raise UnreadableReceipt("Upload a clear image of the receipt.")

    merchant = None
    if by_type.get("supplier_name"):
        merchant = " ".join(by_type["supplier_name"][0].mention_text.split()) or None

    items_total = sum(i.price_cents for i in items)
    subtotal = amount("net_amount")
    tax = amount("total_tax_amount")
    tip = amount("tip_amount")
    total = amount("total_amount")
    if total is None:
        total = (subtotal if subtotal is not None else items_total) + (tax or 0) + (tip or 0)

    # Fill a missing tax/tip only from the printed subtotal -> total gap. Never balance
    # against the items: that would hide a misread row from the mismatch warning.
    if subtotal is not None:
        extra = total - subtotal - (tax or 0) - (tip or 0)
        if extra > 0:
            if tax is None:
                tax = extra
            elif tip is None:
                tip = extra

    return ParsedReceipt(merchant=merchant, items=items, tax_cents=tax or 0, tip_cents=tip or 0,
                         total_cents=total)
