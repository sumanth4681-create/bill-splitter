"""FastAPI backend. Every bill response is a full BillDetail with a fresh summary."""

import logging
import time

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

load_dotenv()

from backend import ocr, ocr_local, split, storage  # noqa: E402  (after .env is loaded)
from backend.models import BillDetail, Claim, Item, Person, Summary  # noqa: E402

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
        receipt_total_cents=bill["receipt_total_cents"],
        items=[Item(id=r["id"], name=r["name"], quantity=r["quantity"], price_cents=r["price_cents"])
               for r in rows["items"]],
        people=[Person(id=r["id"], name=r["name"]) for r in rows["people"]],
        claims=[Claim(**r) for r in rows["claims"]],
        summary=_summary(rows),
    )


@app.post("/bills", response_model=BillDetail, status_code=201)
async def create_bill(image: UploadFile = File(...), reader: str = Form("docai")) -> BillDetail:
    if reader not in ocr.READERS:
        raise HTTPException(status_code=422, detail=f"reader must be one of {', '.join(ocr.READERS)}.")
    data = await image.read()
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


@app.get("/bills/{bill_id}", response_model=BillDetail)
def get_bill(bill_id: int) -> BillDetail:
    return _bill_detail(bill_id)


class PeopleIn(BaseModel):
    names: list[str]


class ClaimIn(BaseModel):
    item_id: int
    person_id: int
    claimed: bool


@app.put("/bills/{bill_id}/people", response_model=BillDetail)
def put_people(bill_id: int, body: PeopleIn) -> BillDetail:
    _bill_detail(bill_id)  # 404 if missing
    storage.replace_people(bill_id, body.names)
    return _bill_detail(bill_id)


@app.put("/bills/{bill_id}/claims", response_model=BillDetail)
def put_claim(bill_id: int, body: ClaimIn) -> BillDetail:
    detail = _bill_detail(bill_id)
    if body.item_id not in {i.id for i in detail.items} or body.person_id not in {p.id for p in detail.people}:
        raise HTTPException(status_code=404, detail="That item or person isn't on this bill.")
    storage.set_claim(body.item_id, body.person_id, body.claimed)
    return _bill_detail(bill_id)


class BillIn(BaseModel):
    name: str | None = None
    tax_cents: int | None = None
    tip_cents: int | None = None
    receipt_total_cents: int | None = None


class ItemIn(BaseModel):
    quantity: int | None = None
    price_cents: int | None = None


class OverrideIn(BaseModel):
    person_id: int
    amount_cents: int | None


@app.patch("/bills/{bill_id}", response_model=BillDetail)
def patch_bill(bill_id: int, body: BillIn) -> BillDetail:
    _bill_detail(bill_id)
    if body.name is not None and not body.name.strip():
        raise HTTPException(status_code=422, detail="Bill name can't be empty.")
    if any(v is not None and v < 0 for v in (body.tax_cents, body.tip_cents, body.receipt_total_cents)):
        raise HTTPException(status_code=422, detail="Amounts can't be negative.")
    storage.update_bill(bill_id, name=body.name.strip() if body.name else None, tax_cents=body.tax_cents,
                        tip_cents=body.tip_cents, receipt_total_cents=body.receipt_total_cents)
    return _bill_detail(bill_id)


@app.patch("/bills/{bill_id}/items/{item_id}", response_model=BillDetail)
def patch_item(bill_id: int, item_id: int, body: ItemIn) -> BillDetail:
    detail = _bill_detail(bill_id)
    if item_id not in {i.id for i in detail.items}:
        raise HTTPException(status_code=404, detail="That item isn't on this bill.")
    if body.quantity is not None and body.quantity < 1:
        raise HTTPException(status_code=422, detail="Quantity must be at least 1.")
    storage.update_item(item_id, quantity=body.quantity, price_cents=body.price_cents)
    return _bill_detail(bill_id)


@app.put("/bills/{bill_id}/overrides", response_model=BillDetail)
def put_override(bill_id: int, body: OverrideIn) -> BillDetail:
    detail = _bill_detail(bill_id)
    if body.person_id not in {p.id for p in detail.people}:
        raise HTTPException(status_code=404, detail="That person isn't on this bill.")
    if body.amount_cents is not None and body.amount_cents < 0:
        raise HTTPException(status_code=422, detail="Amounts can't be negative.")
    storage.set_override(body.person_id, body.amount_cents)
    return _bill_detail(bill_id)
