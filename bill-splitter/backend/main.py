"""FastAPI backend. Every bill response is a full BillDetail with a fresh summary."""

import logging
import sqlite3
import time
from typing import Annotated

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Path, Request, UploadFile
from pydantic import BaseModel, Field, StrictInt

load_dotenv()

from backend import ocr, ocr_local, split, storage  # noqa: E402  (after .env is loaded)
from backend.models import BillDetail, BillListItem, Claim, Item, Person, Summary  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("bill_splitter.api")

app = FastAPI(title="Bill Splitter API")
storage.init_db()


@app.middleware("http")
async def time_requests(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    log.info("%s %s -> %s in %.1f ms", request.method, request.url.path,
             response.status_code, (time.perf_counter() - started) * 1000)
    return response


# Request bounds. Strict ints reject true/"5"/5.0; the caps keep values inside SQLite's 64-bit
# integers and inside anything a receipt could plausibly say ($10M).
MAX_CENTS = 1_000_000_000
MAX_ID = 2**62
Money = Annotated[StrictInt, Field(ge=0, le=MAX_CENTS)]
SignedMoney = Annotated[StrictInt, Field(ge=-MAX_CENTS, le=MAX_CENTS)]  # item rows: discounts are negative
Quantity = Annotated[StrictInt, Field(ge=1, le=10_000)]
Id = Annotated[StrictInt, Field(ge=1, le=MAX_ID)]
PathId = Annotated[int, Path(ge=1, le=MAX_ID)]
BillName = Annotated[str, Field(max_length=200)]
PersonName = Annotated[str, Field(max_length=80)]


def _summary(rows: dict) -> Summary:
    bill = rows["bill"]
    return split.compute_split(
        items=[Item(id=r["id"], name=r["name"], quantity=r["quantity"], price_cents=r["price_cents"])
               for r in rows["items"]],
        people=[Person(id=r["id"], name=r["name"]) for r in rows["people"]],
        claims=[Claim(**r) for r in rows["claims"]],
        tax_cents=bill["tax_cents"],
        tip_cents=bill["tip_cents"],
        overrides={r["id"]: r["override_cents"] for r in rows["people"] if r["override_cents"] is not None},
        receipt_total_cents=bill["receipt_total_cents"],
        discount_cents=bill["discount_cents"],
    )


def _bill_detail(bill_id: int) -> BillDetail:
    rows = storage.get_bill_rows(bill_id)
    if rows is None:
        raise HTTPException(status_code=404, detail="Bill not found.")
    bill = rows["bill"]
    return BillDetail(
        id=bill["id"],
        name=bill["name"],
        created_at=bill["created_at"],
        tax_cents=bill["tax_cents"],
        tip_cents=bill["tip_cents"],
        discount_cents=bill["discount_cents"],
        receipt_total_cents=bill["receipt_total_cents"],
        items=[Item(id=r["id"], name=r["name"], quantity=r["quantity"], price_cents=r["price_cents"])
               for r in rows["items"]],
        people=[Person(id=r["id"], name=r["name"]) for r in rows["people"]],
        claims=[Claim(**r) for r in rows["claims"]],
        summary=_summary(rows),
    )


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}


LOCAL_ORIGINS = {"http://localhost:8000", "http://127.0.0.1:8000"}


@app.post("/bills", response_model=BillDetail, status_code=201)
def create_bill(request: Request, image: UploadFile = File(...), reader: str = Form("docai")) -> BillDetail:
    # Plain `def`: the reader takes 2-60 s, and a sync endpoint runs in a worker thread so
    # other requests (claims, History) keep responding meanwhile.
    origin = request.headers.get("origin")
    if origin and origin not in LOCAL_ORIGINS:
        # Browsers always send Origin on cross-site POSTs; a malicious page must not trigger paid reads.
        raise HTTPException(status_code=403, detail="Uploads are only accepted from this machine.")
    if reader not in ocr.READERS:
        raise HTTPException(status_code=422, detail=f"reader must be one of {', '.join(ocr.READERS)}.")
    mime = image.content_type or ""
    if mime not in ALLOWED_TYPES:
        raise HTTPException(status_code=415, detail="Upload a JPEG, PNG, WebP, or PDF.")
    data = image.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="That file is too big (limit 10 MB).")
    if not ocr.looks_like_receipt_file(data, mime):  # checked before any paid call
        raise HTTPException(status_code=422, detail="Upload a clear image of the receipt.")
    try:
        parsed = ocr.read_receipt(data, image.content_type or "image/jpeg", reader=reader)
    except ocr_local.LocalReaderUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ocr.UnreadableReceipt:
        raise HTTPException(status_code=422, detail="Upload a clear image of the receipt.")
    except ocr.ReplayMissing as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ocr.ReaderNotConfigured as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    bill_id = storage.create_bill(parsed, ocr.image_hash(data))
    return _bill_detail(bill_id)


@app.get("/bills", response_model=list[BillListItem])
def list_bills() -> list[BillListItem]:
    return [BillListItem(**row) for row in storage.list_bills()]


@app.get("/bills/{bill_id}", response_model=BillDetail)
def get_bill(bill_id: PathId) -> BillDetail:
    return _bill_detail(bill_id)


class PeopleIn(BaseModel):
    names: list[PersonName] = Field(max_length=50)


class ClaimIn(BaseModel):
    item_id: Id
    person_id: Id
    claimed: bool


@app.put("/bills/{bill_id}/people", response_model=BillDetail)
def put_people(bill_id: PathId, body: PeopleIn) -> BillDetail:
    _bill_detail(bill_id)  # 404 if missing
    storage.replace_people(bill_id, body.names)
    return _bill_detail(bill_id)


@app.put("/bills/{bill_id}/claims", response_model=BillDetail)
def put_claim(bill_id: PathId, body: ClaimIn) -> BillDetail:
    detail = _bill_detail(bill_id)
    if body.item_id not in {i.id for i in detail.items} or body.person_id not in {p.id for p in detail.people}:
        raise HTTPException(status_code=404, detail="That item or person isn't on this bill.")
    try:
        storage.set_claim(body.item_id, body.person_id, body.claimed)
    except sqlite3.IntegrityError:  # the person or item was removed by a concurrent edit
        raise HTTPException(status_code=409, detail="That person or item was just removed. Reload the bill.")
    return _bill_detail(bill_id)


class BillIn(BaseModel):
    name: BillName | None = None
    tax_cents: Money | None = None
    tip_cents: Money | None = None
    discount_cents: Money | None = None
    receipt_total_cents: Money | None = None


class ItemIn(BaseModel):
    quantity: Quantity | None = None
    price_cents: SignedMoney | None = None


class OverrideIn(BaseModel):
    person_id: Id
    amount_cents: Money | None


@app.patch("/bills/{bill_id}", response_model=BillDetail)
def patch_bill(bill_id: PathId, body: BillIn) -> BillDetail:
    _bill_detail(bill_id)
    if body.name is not None and not body.name.strip():
        raise HTTPException(status_code=422, detail="Bill name can't be empty.")
    storage.update_bill(bill_id, name=body.name.strip() if body.name else None, tax_cents=body.tax_cents,
                        tip_cents=body.tip_cents, discount_cents=body.discount_cents,
                        receipt_total_cents=body.receipt_total_cents)
    return _bill_detail(bill_id)


@app.patch("/bills/{bill_id}/items/{item_id}", response_model=BillDetail)
def patch_item(bill_id: PathId, item_id: PathId, body: ItemIn) -> BillDetail:
    detail = _bill_detail(bill_id)
    if item_id not in {i.id for i in detail.items}:
        raise HTTPException(status_code=404, detail="That item isn't on this bill.")
    storage.update_item(item_id, quantity=body.quantity, price_cents=body.price_cents)
    return _bill_detail(bill_id)


@app.put("/bills/{bill_id}/overrides", response_model=BillDetail)
def put_override(bill_id: PathId, body: OverrideIn) -> BillDetail:
    detail = _bill_detail(bill_id)
    if body.person_id not in {p.id for p in detail.people}:
        raise HTTPException(status_code=404, detail="That person isn't on this bill.")
    storage.set_override(body.person_id, body.amount_cents)
    return _bill_detail(bill_id)
