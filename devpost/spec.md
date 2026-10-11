---
doc: spec
status: approved
---

# Bill Splitter — Technical Spec

## How This Works, In Plain Language
Bill Splitter has four modules behind one screen:

- **The screen (Streamlit).** What you see and click: the Upload and History tabs, plus the bill view. It never does math or stores anything. It only asks the backend and shows what comes back.
- **The backend (FastAPI).** A small local server with clear endpoints, such as "create a bill from this image," "claim this item for this person," and "give me the bill with its summary." Every endpoint has a fixed input and return shape. That contract is the line between modules.
- **The receipt reader (`ocr.py`).** Sends the photo to **Google Document AI's Expense Parser**, which returns line items, tax, tip, and total. It converts that into our item format and saves Google's response to a file, so the same receipt can be replayed later at no cost.
- **The split math (`split.py`).** A pure function: items, people, and claims go in, and each person's share comes out. It has no network, database, or UI, so it's the easiest module to test and the one we hand to a subagent.
- **The storage (`storage.py` + SQLite).** One file, `bills.db`, holds bills, items, people, and claims, so History survives restarts.

Why this shape: the backend is the foundation the later shareable-link version needs, since friends' phones would talk to the same API. Each module can be built and tested on its own, so when something breaks, you know which module it is.

## The Core Journey Through the System
PRD ref: `prd.md > The Core Journey`.

1. **Upload.** In the Upload tab, the payer picks a photo. Streamlit sends it to `POST /bills` and shows a spinner.
2. **Read.** FastAPI hands the image bytes to `ocr.read_receipt()`. It hashes the image. If a saved response exists, it replays it; otherwise it calls Document AI and saves the response. It returns `ParsedReceipt` (items, tax, tip, total, merchant name). *This is the slow step: seconds, network-bound.*
3. **Store.** `storage.create_bill()` writes the bill and its items to SQLite. Each item gets a unique integer ID. The API returns the full `BillDetail`.
4. **Show.** Streamlit renders the bill view: name, names input, summary, and items.
5. **Fix a misread.** Editing a row's quantity or price sends `PATCH /bills/{id}/items/{item_id}`. The API saves it and returns the updated `BillDetail`, including a freshly computed summary.
6. **Add names.** `PUT /bills/{id}/people` saves the list, and the dropdown fills from the returned people.
7. **Claim.** With a person selected, ticking an item sends `PUT /bills/{id}/claims` (`{item_id, person_id, claimed: true}`). The API stores it, and the response shows the name under the item and an updated summary.
8. **Summary.** On every `BillDetail` response, the API loads items, people, claims, and overrides from SQLite and calls `split.compute_split()`. The summary is always computed, never stored, so it can't go stale.
9. **Edit a final amount.** `PUT /bills/{id}/overrides` stores a per-person amount. The summary marks it as edited.
10. **History.** The History tab calls `GET /bills` for the list, and clicking a bill calls `GET /bills/{id}`. The view is the same as step 4.

Steps 5–9 are all local: Streamlit → localhost → SQLite → `split.py`. They should feel instant. Streamlit re-runs the page on each click, which is expected.

## Stack
| Piece | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | One language across all modules |
| Screen | [Streamlit](https://docs.streamlit.io/) | Learner's choice; fast to build interactive pages in Python |
| Backend | [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/) | Learner's choice (Option A): clean, typed endpoint contracts, ready for multi-user later. Accepted tradeoff: two processes to run |
| Contracts | [Pydantic](https://docs.pydantic.dev/) (comes with FastAPI) | Request/response shapes are the module contracts |
| Receipt reading | [Google Document AI, Expense Parser](https://cloud.google.com/document-ai/docs/processors-list#processor_expense-parser) via [`google-cloud-documentai`](https://cloud.google.com/python/docs/reference/documentai/latest) | Learner's choice: real receipt OCR with structured line items. Accepted tradeoff: about $0.10 per receipt, billing required, more setup than Gemini |
| Storage | SQLite (Python's built-in `sqlite3`) | Learner's choice: relational data (claims link items to people) in one file |
| Local receipt reading | [LM Studio](https://lmstudio.ai/) local server (OpenAI-compatible, `localhost:1234`) running `qwen/qwen3.5-9b` (vision) | Learner's addition: free, offline second reader to compare against Document AI. Accepted tradeoff: ~20 s per receipt, needs LM Studio running |
| HTTP from Streamlit | [`requests`](https://requests.readthedocs.io/) | Simple calls to the local API |
| Image upload to FastAPI | `python-multipart` | Required for FastAPI file uploads |
| Tests | [pytest](https://docs.pytest.org/) | Module tests against varied inputs |

**Verify early in the build (not yet checked):** the exact Expense Parser entity names for line items, tax, and **tip** (a tip entity may not exist; see `External Services and Dependencies`), the current `google-cloud-documentai` version, and whether new-account trial credits apply.

## Where It Runs and How Someone Tries It
**Local only** (learner's choice). Nothing is deployed.

Requirements: Python 3.11+, a Google Cloud project with billing on, an Expense Parser processor, and a service-account key file saved outside the repo or gitignored.

Setup (from `bill-splitter/`):
```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env   # fill in values locally, never paste them into chat
```

`.env` keys: `GOOGLE_APPLICATION_CREDENTIALS` (path to the key file), `DOCAI_PROJECT_ID`, `DOCAI_LOCATION` (e.g. `us`), `DOCAI_PROCESSOR_ID`, `OCR_MODE` (`live` or `replay`), `API_URL` (default `http://localhost:8000`), `LMSTUDIO_URL` (default `http://localhost:1234/v1`), `LMSTUDIO_MODEL` (default `qwen/qwen3.5-9b`).

Run (two terminals, from `bill-splitter/`):
```bash
uvicorn backend.main:app --reload --port 8000
streamlit run frontend/app.py          # opens http://localhost:8501
```

**For the demo video:** upload a real receipt (live call), add three names, claim one item solo, share one item between two people, leave one unclaimed, show that the summary adds up to the receipt total, edit one final amount, then open the bill from History.
**Submission:** the short demo video plus a public GitHub repo. The repo must not contain `.env`, the key file, or `bills.db`.

## Look and Feel
From `prd.md > Look and Feel`: **interactive, not vibrant.**
- **Palette:** muted and calm, set in `.streamlit/config.toml`. Light base, background `#F6F6F3`, secondary background `#ECECE7`, text `#2A2A28`, primary accent muted sage `#5B7F75`. No bright reds or blues except error messages.
- **Typography:** Streamlit's default sans-serif. Item prices are right-aligned so the numbers line up.
- **Interactivity:** every action gives immediate visible feedback. The name appears under the item and the summary updates on the same rerun. Use `st.toast` for small confirmations like "Saved" or "Claimed for Sam." The spinner shows while reading.
- **Density:** comfortable, one column, with items as clean rows (not a dense spreadsheet).
- **Copy tone:** plain and friendly, for example "Upload a clear image of the receipt."
- **Stack limit:** Streamlit allows theme colors and fonts but little custom layout. We stick to its built-in widgets and avoid hacking CSS.

## Components

### Streamlit Screen (`frontend/app.py`)
Two tabs: **Upload** and **History**. Both open the same **bill view**, top to bottom:
1. Bill name (editable text)
2. Names input (comma-separated or one per line) and a **person dropdown** (`st.selectbox`)
3. Summary: one block per person with name, item lines, tax/tip share, and total (with an edit control). Unclaimed items are labeled. A warning shows if the computed total ≠ the receipt total.
4. Items: one row per item with name, quantity, price, and a pencil to edit quantity/price, a checkbox for "claimed by the selected person," and the claimers' names listed underneath.

Holds only the current bill ID and the selected person in `st.session_state`. All data comes from the API.
PRD ref: `prd.md > Screens and Layout`, `prd.md > Claiming Items`, `prd.md > Split Summary`, `prd.md > People`.

### API Client (`frontend/api_client.py`)
Thin functions that wrap each endpoint (`create_bill(image)`, `get_bill(id)`, `set_claim(...)`, …). If the backend isn't reachable, it raises a clear error that the screen turns into "Backend isn't running. Start it with `uvicorn …`."

### FastAPI Backend (`backend/main.py`)
| Method + path | Input | Returns | PRD ref |
|---|---|---|---|
| `POST /bills` | multipart `image` | `BillDetail` (201), or 422 `{"detail": "Upload a clear image of the receipt."}` | Receipt Upload and Reading |
| `GET /bills` | — | `list[BillListItem]` (id, name, created_at), newest first | History |
| `GET /bills/{id}` | — | `BillDetail` | History |
| `PATCH /bills/{id}` | `{name?, tax_cents?, tip_cents?, receipt_total_cents?}` | `BillDetail` | Screens and Layout (bill name), Fixing Misreads (tax/tip/total) |
| `PATCH /bills/{id}/items/{item_id}` | `{quantity?, price_cents?}` | `BillDetail` | Fixing Misreads |
| `PUT /bills/{id}/people` | `{names: [str]}` | `BillDetail` | People |
| `PUT /bills/{id}/claims` | `{item_id, person_id, claimed: bool}` | `BillDetail` | Claiming Items |
| `PUT /bills/{id}/overrides` | `{person_id, amount_cents: int \| null}` | `BillDetail` | Split Summary |

Every mutating endpoint returns the full `BillDetail`, so the screen re-renders from one response, which is simple to reason about. Each request logs how long it took (see `Decisions and Open Issues`).

### Contracts (`backend/models.py`)
Pydantic models shared by all modules:
- `Item {id, name, quantity, price_cents}`. `price_cents` is the **line total** (what the receipt shows for that row).
- `Person {id, name}`
- `Claim {item_id, person_id}`
- `ParsedReceipt {merchant, items: [ParsedItem], tax_cents, tip_cents, total_cents}` (from OCR, before IDs exist)
- `PersonShare {person_id, name, lines: [{item_id, item_name, share_cents, split_ways, unclaimed: bool}], tax_cents, tip_cents, computed_cents, final_cents, edited: bool}`
- `Summary {people: [PersonShare], items_total_cents, receipt_total_cents, computed_total_cents, matches_receipt: bool}`
- `BillDetail {id, name, created_at, items, people, claims, summary}`

All money is **integer cents** to avoid floating-point errors.

### Receipt Reader (`backend/ocr.py`)
`read_receipt(image_bytes: bytes, mime_type: str, reader: str = "docai") -> ParsedReceipt`

Two readers behind one function. `reader="docai"` is below; `reader="local"` delegates to `backend/ocr_local.py` (see **Local Reader**). Both save-and-replay under `fixtures/{reader}-{hash}.json` and return the same `ParsedReceipt`, so nothing downstream knows which one ran.
1. `key = sha256(image_bytes)`. If `fixtures/{key}.json` exists, load it (replay). If not and `OCR_MODE=replay`, raise `ReplayMissing`.
2. Otherwise, call Document AI `process_document` with `RawDocument(content, mime_type)` on the Expense processor, then save the response JSON to `fixtures/{key}.json`.
3. `map_expense_document(doc) -> ParsedReceipt` is a **separate pure function**, so it can be tested on saved fixtures without the network. Each `line_item` entity becomes one `ParsedItem` (description, quantity defaulting to 1, amount → cents). `total_tax_amount` becomes tax, `total_amount` becomes total, and `supplier_name` becomes merchant.
4. No line items found → raise `UnreadableReceipt`. The API turns that into the 422 "Upload a clear image" message.
PRD ref: `prd.md > Receipt Upload and Reading`.

### Local Reader (`backend/ocr_local.py`)
`read_receipt_local(image_bytes, mime_type) -> ParsedReceipt`, built from the 2026-10-10 prompt evaluation:
1. **Upscale** images narrower than ~1200 px (Pillow) — the model misread $6.99 as $6.59 at 645 px and got 6/6 at 1290 px.
2. POST to `{LMSTUDIO_URL}/chat/completions` with a rules system prompt (items are purchased products only; copy printed digits; null when unreadable; `/no_think`), the image as a data URL, `temperature=0`, and a strict JSON schema `{merchant, items:[{name, quantity, price}], subtotal, tax, tip, total}`.
3. Read the JSON from `content`, falling back to `reasoning_content` (LM Studio sometimes puts the answer there), and parse it with a regex for the outermost `{...}`.
4. **Code-side cleanup**, because the model ignores "stop at SUBTOTAL": drop rows whose name is a summary label (subtotal/tax/tip/total/balance/change/cash/card); if a printed subtotal exists, keep the longest prefix of rows whose sum equals it, dropping anything after.
5. Map to `ParsedReceipt` (cents via `Decimal`), raising `UnreadableReceipt` when no items remain and `LocalReaderUnavailable` when LM Studio isn't reachable (API → 503 "Local model isn't running. Start LM Studio's server.").

`scripts/compare_readers.py <image>...` runs both readers on each image and prints a table: items found, items in common, subtotal/tax/total per reader, seconds. Results go in the README as the comparison evidence.

### Split Logic (`backend/split.py`)
`compute_split(items, people, claims, tax_cents, tip_cents, overrides: dict[person_id, cents] | None, receipt_total_cents) -> Summary` is **pure**: no I/O, deterministic.

Rules (from `prd.md > Claiming Items`, `prd.md > Split Summary`):
1. **Claimed item:** its `price_cents` is divided equally among the people who claimed it. Unclaimers pay nothing for it.
2. **Unclaimed item:** divided equally among **all** people, with `unclaimed: true` on the line.
3. **Tax and tip:** each divided equally among all people.
4. **Rounding:** to divide `amount` among `n` people, each gets `amount // n`, and `amount % n` people get 1 extra cent. Who gets the extra cent rotates across the bill's divisions (items, then tax, then tip), so no one is always rounded up. The sum always equals the original amount exactly.
5. `computed_cents` = sum of the person's lines + tax share + tip share. `final_cents` = the override if one is set (`edited: true`), otherwise `computed_cents`.
6. `matches_receipt` = (sum of `computed_cents` == `receipt_total_cents`). Overrides don't affect this check.
7. **No people:** returns `people: []`. The screen shows "Add names to start claiming."

Required tests (`tests/test_split.py`): solo claim; a shared item between 2 of 3 (third pays 0); an unclaimed item spread across 3; $10.00 split 3 ways (334/333/333); tax and tip split; an override sets `final_cents` but leaves `matches_receipt` intact; no people; same-name items with different IDs kept separate; and **for any input, the sum of computed amounts equals items + tax + tip.**

**Subagent experiment:** this module is built by a subagent given *only* this section plus the `Contracts` section, not the conversation or the other docs. See `Decisions and Open Issues`.

### Storage (`backend/storage.py`)
Functions: `init_db()`, `create_bill(parsed, image_hash) -> id`, `list_bills()`, `get_bill_rows(id)`, `update_bill(id, name?, tax_cents?, tip_cents?, receipt_total_cents?)`, `update_item`, `replace_people(id, names)`, `set_claim(item_id, person_id, claimed)`, `set_override(person_id, amount_cents|None)`. Uses `sqlite3` with foreign keys on, writing to `data/bills.db`.
PRD ref: `prd.md > History`, `prd.md > States and Boundaries` (persistence).

### Future: Shareable Link (designed, not built)
From `scope.md > Later`. **Nothing here gets implemented in the POC.**
- Add `bills.share_token` (random string). `GET /share/{token}` returns the same `BillDetail`.
- When the payer adds a person to the bill, that person is sent a link. A friend opens the link on their phone, picks their own name from `people` (no accounts), and only claims for themselves. The same `PUT /claims` is used, scoped to their `person_id`.
- Approval rounds: add `bills.version` (incremented on every change) and an `approvals(person_id, version)` table. Each person accepts the current version, and the split is "final" when every person has accepted the latest version. Any change bumps the version, which triggers another round.
- Because the screen already only talks to the API, this is backend additions plus a mobile-friendly claim page. `split.py` doesn't change.

## Data Model
SQLite, `data/bills.db`:

```
bills   (id INTEGER PK, name TEXT, created_at TEXT, image_hash TEXT,
         tax_cents INT, tip_cents INT, receipt_total_cents INT)
items   (id INTEGER PK, bill_id → bills.id, position INT,
         name TEXT, quantity INT, price_cents INT)
people  (id INTEGER PK, bill_id → bills.id, position INT, name TEXT,
         override_cents INT NULL)
claims  (item_id → items.id, person_id → people.id, PRIMARY KEY(item_id, person_id))
```

| Data | Lives in | Updated by | When you leave and come back |
|---|---|---|---|
| Bill, items (incl. fixed misreads) | SQLite | `POST /bills`, `PATCH` endpoints | Reloaded from History |
| People, overrides | SQLite `people` | `PUT /people`, `PUT /overrides` | Reloaded |
| Claims | SQLite `claims` | `PUT /claims` | Reloaded |
| Summary | **Not stored**. Computed per request | — | Recomputed, always current |
| Google's raw response | `backend/fixtures/{hash}.json` | First live read | Replayed for the same image |
| Current bill, selected person | Streamlit `session_state` | Clicks | Lost on browser refresh (reopen from History) |

`replace_people` keeps existing people (and their claims) whose names are unchanged. It removes people who were deleted, along with their claims.

## File Structure
```
learn-ai-basics-main/
├── bill-splitter/                # the app (folder already exists)
│   ├── backend/
│   │   ├── main.py               # FastAPI app + endpoints, request timing log
│   │   ├── models.py             # Pydantic contracts shared by all modules
│   │   ├── ocr.py                # read_receipt() dispatcher; Document AI call, save/replay, map_expense_document()
│   │   ├── ocr_local.py          # LM Studio (local Qwen) reader: upscale, prompt, JSON cleanup
│   │   ├── split.py              # pure split math (subagent experiment)
│   │   ├── storage.py            # SQLite functions
│   │   └── fixtures/             # saved Document AI responses (gitignored, except one sample)
│   ├── frontend/
│   │   ├── app.py                # Streamlit: Upload, History, bill view
│   │   └── api_client.py         # calls to the backend
│   ├── scripts/
│   │   └── compare_readers.py    # Document AI vs local model on the same receipts
│   ├── tests/
│   │   ├── test_split.py         # split rules + "always sums to total"
│   │   ├── test_ocr_mapping.py   # map_expense_document() on a saved fixture
│   │   ├── test_ocr_local.py     # local-reader JSON cleanup + mapping (no model needed)
│   │   └── test_api.py           # endpoints with FastAPI TestClient, OCR in replay mode
│   ├── .streamlit/config.toml    # muted theme
│   ├── data/                     # bills.db lives here (gitignored)
│   ├── requirements.txt
│   ├── .env.example              # keys with no values
│   └── README.md                 # setup + run
├── devpost/                      # planning docs
└── .gitignore                    # adds .env, key files, data/, fixtures/*.json (except sample)
```

## External Services and Dependencies

### Google Document AI — Expense Parser
- **Docs:** [Expense Parser](https://cloud.google.com/document-ai/docs/processors-list#processor_expense-parser) · [Python client](https://cloud.google.com/python/docs/reference/documentai/latest) · [Send a processing request](https://cloud.google.com/document-ai/docs/send-request) · [Pricing](https://cloud.google.com/document-ai/pricing)
- **Setup (learner, in the Cloud Console):** create a project, turn on billing, enable the Document AI API, create an **Expense Parser** processor (note its ID and location), create a service account with the Document AI API User role, and download its key JSON to a gitignored path.
- **Call:** `DocumentProcessorServiceClient(client_options={"api_endpoint": f"{location}-documentai.googleapis.com"}).process_document(request={"name": processor_path, "raw_document": {"content": image_bytes, "mime_type": "image/jpeg"}})`. Auth comes from `GOOGLE_APPLICATION_CREDENTIALS`.
- **Response used:** `document.entities`, where `type_` is `line_item` (with properties like `line_item/description`, `line_item/quantity`, `line_item/amount`), `total_tax_amount`, `total_amount`, and `supplier_name`. **Verify on the first real response:** exact names, and whether a tip entity exists. If there's no tip entity, use `tip = total − (items + tax)` when positive, otherwise 0. Record what we find.
- **Cost:** about $0.10 per receipt (≤10 pages). No free tier was found. Save-and-replay keeps repeat tests free.
- **Speed:** unverified. Expect a few seconds per receipt, and we'll measure it.

### LM Studio (local model)
- Learner's machine runs LM Studio with `qwen/qwen3.5-9b` (vision, 9B, 262k context). Start the server with `lms server start` (port 1234). No key, no cost.
- **Comparison on 4 public receipts (Wikimedia Commons, 2026-10-10), Document AI vs local Qwen 3.5 9B (upscaled to ≥1400 px, rules prompt, temperature 0, one run each):**
  | Receipt | Document AI | Local Qwen |
  |---|---|---|
  | Umami Tapas (scan, 10 items, GST + liquor tax) | 1.5 s · 8/10 names, 10/10 prices · tax missed | 46.6 s · 10/10 · tax 6.23 (missed 2.00 liquor tax) |
  | Pizza Chicago (phone photo, 13 items, gratuity) | 2.3 s · 3/13 names, 10/13 prices, tax off 1¢ | 53.0 s · 13/13 · tax, tip, total all correct |
  | Tesco 1994 (faint, quantity prefixes, £) | 1.5 s · 5/10 names, 8/10 prices | 32.4 s · 10/10 |
  | Save Mart (1 item) | 1.7 s · 1/1 | 29.2 s · 1/1 |
  Local read items better on 3/4; Google is 20–35× faster; both missed a tax line once. Gaps in this evidence: only one messy phone photo, no discount rows, no qty×price rows, single run per receipt.
- Measured 2026-10-10 on one 645×360 receipt photo: ~17–24 s per read (52 s cold); 5/6 items at native size, 6/6 at 2× upscale; junk rows (summary labels, garbled footer text) in every run regardless of prompt; the detailed rules prompt did not beat a bare prompt on accuracy. Thinking mode added time without improving results.

### Python packages
`fastapi`, `uvicorn`, `python-multipart`, `pydantic`, `google-cloud-documentai`, `streamlit`, `requests`, `python-dotenv`, `pytest`, `httpx` (for FastAPI's TestClient), `Pillow` (upscaling for the local reader). Pin versions in `requirements.txt` at install time.

## Important Failure Modes
- **Blurry photo or not a receipt** (no line items) → a 422 from the API, and Streamlit shows "Upload a clear image of the receipt" with the uploader still available. (`prd.md > States and Boundaries`)
- **Google setup missing or wrong** (no key, wrong processor ID, billing off) → the API returns a 500 with a plain message, such as "Receipt reader isn't configured. Check .env." It shows on screen, and keys are never echoed. `OCR_MODE=replay` lets everything else keep working on saved receipts.
- **Backend not running** → Streamlit shows "Backend isn't running. Start it with `uvicorn backend.main:app --port 8000`."
- **OCR misread makes totals disagree** → the summary shows a warning, "Items + tax + tip = $X, receipt says $Y. Check for a misread row," which points to `Fixing Misreads`.

## What Was Simplified and Why
- **One person claims for everyone (dropdown)** instead of a shared link: proves the split logic without multi-device sync. The link design is above, not built.
- **Summary computed on every request** instead of stored or cached: always correct and trivially fast at this size.
- **Equal tax/tip split** instead of proportional: the learner's rule, and simpler math. Proportional is a later enhancement.
- **Local SQLite + local run** instead of hosted: no deploy, and nothing to secure in the cloud.
- **Saved-response replay** for development: same real data, no repeat cost. Labeled as saved data. The demo uses a live call.
- **No accounts/login:** a single payer on their own laptop.

## Decisions and Open Issues

**Learner decisions**
- The payer (uploader) adds the people to the bill and uses the dropdown to select who bought what. In the future version, adding a person sends them a link.
- Streamlit screen + FastAPI backend as two processes (Option A), for clean contracts and readiness for multi-user. Accepted cost: two servers.
- Google **Document AI Expense Parser** for real OCR. Accepted: about $0.10 per receipt and billing setup.
- Save-and-replay of Document AI responses during development.
- SQLite for storage.
- Run locally and record. No deployment.
- Build `split.py` via a subagent with only its contract (learning goal: orchestration and lean context).
- Add a second, local-model receipt reader (LM Studio / Qwen 3.5 9B) alongside Document AI and compare them on real receipts (added 2026-10-10 after a live test of the local model).

**Implementation details derived from those (agent)**
- Money stored as integer cents. The rounding rule gives extra cents to the first people in name order.
- Every mutating endpoint returns the full `BillDetail`.
- The payer gets no special treatment. If they add their own name, they're just another person with a share. If not, the friends split everything. No "payer" flag in the data.
- The bill name defaults to the receipt's merchant, otherwise "Bill – {date}", and is editable (PRD assumption carried forward).

**The learner's open question: how do the parts interact, and how fast is it?**
- *Interaction:* answered by `The Core Journey Through the System` and the diagram in `spec.html`.
- *Speed:* the investigation runs during the build. `main.py` logs the duration of every request, and `ocr.py` logs the Document AI call time separately. Evidence to collect: Document AI time for 2–3 real receipts, and the time for a claim click (API + split). Expected: seconds for OCR, milliseconds for everything else. Record the actual numbers in the learner profile's Learning Moments.

**The subagent experiment**
- In the build, `split.py` and `tests/test_split.py` are written by a subagent that gets only `Components > Split Logic` and `Components > Contracts`. Evidence: does it pass the required tests on the first try, and what did it get wrong or have to ask about? That shows whether the contract was enough context.

**Open**
- **Tip entity in Document AI:** verify on the first live response (fallback rule above).
- **Trial credits and pricing:** confirm when creating the Cloud project.
