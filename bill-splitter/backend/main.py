"""FastAPI backend. Every bill response is a full BillDetail with a fresh summary."""

import logging
import time

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile

load_dotenv()

from backend import ocr, storage  # noqa: E402  (after .env is loaded)
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
    # Placeholder until split.py exists: totals only, no per-person shares.
    bill = rows["bill"]
    items_total = sum(r["price_cents"] for r in rows["items"])
    return Summary(
        people=[],
        items_total_cents=items_total,
        receipt_total_cents=bill["receipt_total_cents"],
        computed_total_cents=items_total + bill["tax_cents"] + bill["tip_cents"],
        matches_receipt=items_total + bill["tax_cents"] + bill["tip_cents"] == bill["receipt_total_cents"],
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
async def create_bill(image: UploadFile = File(...)) -> BillDetail:
    data = await image.read()
    try:
        parsed = ocr.read_receipt(data, image.content_type or "image/jpeg")
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
