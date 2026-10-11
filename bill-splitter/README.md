# Bill Splitter

Snap a photo of a group receipt, claim items on behalf of each friend, and see what everyone
owes the person who paid. Shared items are split only among the people who had them; anything
nobody claims is split across everyone. The amounts always add up to the receipt total.

Proof of concept built for the Devpost *Build With AI: Basics* hackathon. Runs locally only.

## How it works

```
Streamlit screen  ──HTTP──>  FastAPI backend  ──>  SQLite (data/bills.db)
 (frontend/app.py)            (backend/main.py)      bills, items, people, claims
                                   │
                                   ├── backend/ocr.py        Google Document AI (Expense Parser)
                                   ├── backend/ocr_local.py  local vision model via LM Studio
                                   └── backend/split.py      pure split math, integer cents
```

Two receipt readers sit behind one function, `read_receipt()`. Every response is saved by
image hash under `backend/fixtures/` and replayed for free on the next upload of the same photo.
The split is recomputed from the database on every request, so it can't go stale.

## Setup

Requirements: Python 3.11+, and at least one reader:

- **Google Document AI** (default): a Google Cloud project with billing enabled, the Document AI
  API turned on, an **Expense Parser** processor, and a service-account key with the
  *Document AI API User* role. About $0.10 per receipt read.
- **Local model** (optional, free): [LM Studio](https://lmstudio.ai/) with a vision model such as
  `qwen/qwen3.5-9b`, served with `lms server start` (port 1234). About 30–50 s per receipt.

```bash
cd bill-splitter
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
cp .env.example .env                                    # then fill in the values locally
```

`.env` keys:

| Key | What it is |
|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` | path to the service-account key JSON (keep it outside git) |
| `DOCAI_PROJECT_ID`, `DOCAI_LOCATION`, `DOCAI_PROCESSOR_ID` | your Expense Parser processor |
| `OCR_MODE` | `live` (call the reader and save the response) or `replay` (saved responses only) |
| `LMSTUDIO_URL`, `LMSTUDIO_MODEL` | local reader server and model name |
| `API_URL` | where the screen finds the backend (default `http://localhost:8000`) |

## Run

Two terminals, both from `bill-splitter/` with the venv active:

```bash
uvicorn backend.main:app --port 8000      # backend; API docs at http://localhost:8000/docs
streamlit run frontend/app.py             # screen; opens http://localhost:8501
```

Then: **Upload** a receipt photo (JPEG/PNG), pick a reader, add your friends' names, choose a
person and tick what they had, and read **Who owes what**. Fix any misread row with the ✎
pencil; tax, tip, discount, and the receipt total are editable under the items. **History**
reopens past bills with their claims.

## Tests

```bash
python -m pytest -q
```

No network or database needed: the split rules (including a property test that the shares
always sum to what was paid), both readers' mapping code on saved responses, and every endpoint
through FastAPI's TestClient in replay mode.

## Comparing the readers

```bash
python scripts/compare_readers.py photo1.jpg photo2.png --rows
```

Runs both readers on each photo (Google reads in parallel with the local reads) and prints
items found, items in common, totals, and seconds. On five public receipts the local model read
every row on all five (Google: three of five) but took 30–55 s versus about 2 s.

## What's deliberately not here

One person claims on behalf of everyone from one screen. The shareable-link, multi-device
version is designed in `../devpost/spec.md` but not built. No accounts, no payments.
