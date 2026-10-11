---
doc: checklist
status: approved
---

# Build Checklist

Build mode: learn (chosen by default on 2026-10-10; learner can switch to fast at any time)

## Slices

- [x] **1. Upload a real receipt and see its items**
  Becomes usable: Both servers run; in the Upload tab you pick a receipt photo, a spinner shows, and the bill opens with its name and items (unique ID, name, quantity, price) read by Google Document AI. The bill is saved to SQLite, and the Google response is saved so the same photo replays for free.
  Why now: Document AI is the biggest unknown (setup, billing, real entity names, whether a tip entity exists). Finding out first means every later slice works on real receipt data, not guesses. Bootstrapping both processes rides along here.
  PRD ref: `prd.md > The Core Journey` (steps 1-4), `prd.md > Receipt Upload and Reading`
  Spec ref: `spec.md > Where It Runs and How Someone Tries It`, `spec.md > Components` (Streamlit Screen, API Client, FastAPI Backend, Contracts, Receipt Reader, Storage), `spec.md > Data Model`, `spec.md > File Structure`, `spec.md > External Services and Dependencies`
  Build: Scaffold `bill-splitter/` per the file structure (requirements with pinned versions, `.env.example`, `.streamlit/config.toml` muted theme, root `.gitignore` additions for `data/`, key files, `fixtures/*.json` except one sample). Write `models.py` contracts, `ocr.py` (hash → replay or live call → save fixture; pure `map_expense_document()`; `UnreadableReceipt`; timing log), `storage.py` (`init_db`, `create_bill`, `get_bill_rows`), `main.py` with `POST /bills` and `GET /bills/{id}` (summary empty until slice 2) and per-request timing log, `api_client.py`, and the Upload tab + read-only bill view in `app.py`. Learner does the Cloud Console setup and fills `.env` locally. Record the real entity names and tip finding under Revisions.
  Verify (mechanical): Make one live upload through `POST /bills` with a real receipt and confirm items come back and a fixture file is written; re-run with `OCR_MODE=replay` and confirm the same items with no network call; `pytest tests/test_ocr_mapping.py` passes on the saved sample fixture; Streamlit starts with no errors and Streamlit's built-in `AppTest` renders the Upload tab.
  Learner check: Start both servers, open http://localhost:8501, upload a receipt photo, watch the spinner, and compare the item list against the paper receipt. Say what was misread, if anything.
  Commit: `Read a receipt photo into a saved bill`

- [x] **2. Claim and share items and see what everyone owes**
  Becomes usable: On an open bill you enter friends' names, pick a person from the dropdown, tick their items, and their name appears under each item. Ticking one item under two people shares it; the third pays nothing. The summary shows each person's items, tax and tip share, and total, with unclaimed items labeled and spread across everyone, all adding up to the receipt total.
  Why now: This is the unique kernel. It comes right after real data exists, so the "oh, that's cool" moment is reachable early and the early feedback can still shape the rest.
  PRD ref: `prd.md > People`, `prd.md > Claiming Items`, `prd.md > Split Summary`, `prd.md > The Core Journey` (steps 6-9)
  Spec ref: `spec.md > Components > Split Logic` and `> Contracts` (only these go to the subagent), `spec.md > Components > FastAPI Backend`, `spec.md > Components > Storage`, `spec.md > Data Model`
  Build: Dispatch a subagent with only `Split Logic` + `Contracts` to write `split.py` and `tests/test_split.py`; record whether it passed first try and what it missed. Then add `replace_people` and `set_claim` to storage, `PUT /people` and `PUT /claims`, wire `compute_split()` into every `BillDetail`, and add the names input, person dropdown, claim checkboxes with names underneath, summary blocks, and `st.toast` feedback to the bill view.
  Verify (mechanical): `pytest tests/test_split.py` passes all required cases including "always sums to items + tax + tip"; an API call sequence (3 names, one solo claim, one shared 2-of-3, one left unclaimed) returns a summary with `matches_receipt: true` and the third person paying 0 for the shared item.
  Learner check: Open a bill, add three names, claim one item for one person, share one item between two people, leave one unclaimed, and check that the summary reads right and adds up to the receipt total.
  Commit: `Claim and share items with a live per-person split`

- [x] **3. Read a receipt with the local model, and compare it with Google**
  Becomes usable: The Upload tab has a Reader choice: Google Document AI (default) or Local model (LM Studio). Picking Local reads the same photo with your Qwen model, free and offline, and opens a bill the same way. A script runs both readers on the same receipts and prints a comparison (items found, items in common, totals, seconds) for the README.
  Why now: You asked to try an LLM reader and keep both. Doing it right after claiming means the comparison can run on real bills while the editing and History slices are still open to change. The reader sits behind the same `read_receipt()` contract, so nothing downstream changes.
  PRD ref: `prd.md > Receipt Upload and Reading`, `prd.md > Added During the Build`
  Spec ref: `spec.md > Components > Local Reader`, `spec.md > Components > Receipt Reader`, `spec.md > External Services and Dependencies` (LM Studio)
  Build: Add `backend/ocr_local.py` (upscale, rules prompt, strict JSON schema, reasoning-field fallback, summary-row and subtotal-prefix cleanup, `LocalReaderUnavailable`); make `read_receipt()` dispatch on `reader` with per-reader fixture names; add `reader` to `POST /bills` (503 when LM Studio is down); add the Reader selectbox to the Upload tab; write `tests/test_ocr_local.py` on canned model outputs; write `scripts/compare_readers.py`. Add `Pillow`, `LMSTUDIO_URL`, `LMSTUDIO_MODEL`.
  Verify (mechanical): `pytest` passes including `test_ocr_local.py`; `POST /bills` with `reader=local` on the sample receipt returns the 6 items and `matches_receipt: true`; a second call replays from `fixtures/local-*.json`; with LM Studio stopped the API returns 503 with the plain message; `scripts/compare_readers.py` prints both readers' rows for the sample receipt.
  Learner check: On Upload, choose Local model, upload the grocery receipt, and compare the item list with the Google version of the same receipt. Then run the comparison script and say which reader you'd trust, and why.
  Commit: `Add a local-model receipt reader and a reader comparison`

- [x] **4. Fix misreads and adjust final amounts**
  Becomes usable: You can rename the bill, edit a row's quantity or price to fix an OCR mistake, edit the tax, tip, and receipt total (readers miss tax lines), and edit any person's final amount. The summary updates immediately, edited amounts are marked, and a warning shows when items + tax + tip don't match the receipt total.
  Why now: Real OCR will misread something; once claiming works, correcting the input and the output is what makes the split trustworthy. It depends on the summary from slice 2.
  PRD ref: `prd.md > Fixing Misreads`, `prd.md > Split Summary` (editable final amount), `prd.md > Screens and Layout` (bill name)
  Spec ref: `spec.md > Components > FastAPI Backend` (`PATCH /bills/{id}`, `PATCH /items`, `PUT /overrides`), `spec.md > Components > Storage`, `spec.md > Important Failure Modes` (totals disagree)
  Build: Add `update_bill` (name, tax, tip, receipt total), `update_item`, `set_override` to storage and the three endpoints; add the editable bill name, editable tax/tip/total fields under the items, a pencil edit for quantity/price per row, an edit control per person in the summary, the "edited" marker, and the mismatch warning.
  Verify (mechanical): API calls: change a price and confirm the summary amounts change; set tax on the Umami fixture (reader returned 0) and confirm `matches_receipt` flips to true; set an override and confirm `final_cents` changes, `edited: true`, and `matches_receipt` is unaffected; clear it with `null`. Re-run `pytest`.
  Learner check: Change a price on one row and watch the summary update; type in a tax amount and watch everyone's share change; set one person's final amount by hand and confirm it shows as edited.
  Commit: `Edit items, bill name, and final amounts`

- [ ] **5. Come back to past bills, and fail gracefully**
  Becomes usable: The History tab lists saved bills newest first (or an empty message pointing to Upload); clicking one reopens it with its names, claims, and summary, even after restarting both servers. A blurry or non-receipt photo shows "Upload a clear image of the receipt" with the uploader still there; a stopped backend or missing Google config shows a plain message instead of a crash.
  Why now: History and error states round out the full core journey and the demo script; they depend on everything above being saved and editable.
  PRD ref: `prd.md > History`, `prd.md > States and Boundaries`, `prd.md > The Core Journey` (step 11)
  Spec ref: `spec.md > Components > FastAPI Backend` (`GET /bills`), `spec.md > Components > Storage` (`list_bills`), `spec.md > Important Failure Modes`, `spec.md > Where It Runs and How Someone Tries It`
  Build: Add `list_bills` and `GET /bills`; the History tab and empty state; 422 for `UnreadableReceipt`, 500 "Receipt reader isn't configured. Check .env." for setup errors, and the backend-down message in `api_client.py`. Write `tests/test_api.py` (TestClient, replay mode) and `bill-splitter/README.md` setup + run.
  Verify (mechanical): `pytest` (all three test files) passes; restart both servers and confirm `GET /bills` still lists the bill with its claims; with the backend stopped, `AppTest` shows the backend-down message.
  Learner check: Restart both servers, open History, reopen your bill, and confirm the claims are all still there. Then upload a photo that isn't a receipt and see what happens.
  Commit: `Add History and friendly error states`

## Hands-on Checkpoints

- [x] Early usable behavior explored — after slice 2 (claiming and the live split on a real receipt), while the editing and History slices can still change. Learner feedback 2026-10-10: no way to edit tax and tip → added tax/tip/receipt-total editing to slice 4.
- [ ] Final kick-the-tires exploration and feedback completed

## Final Review

- [ ] Final review complete — feedback resolved and learner confirms ready to ship

## Code Tour and App Map

- [ ] Learning activity complete — guided route, focused alternative, prior practice connected, or brief recap
- [ ] Optional edit and transfer reflection addressed — offered/declined/already covered/not applicable as appropriate
- [ ] `devpost/app-map.html` generated from finished code, checked, and shown, including a project-grounded practice to reuse

Activity and evidence: [what actually happened; real document/test/code references; unfinished work if interrupted]
Route and stops: [actual paths and symbols; guided stops completed, or reference-only route]
Edit outcome: [tried/kept/reverted/declined/not applicable; verification if changed]
Reflection: [offered/answered/declined/already covered — personal answer belongs only in the ignored profile]
Activity mode: [live app and editor, explicit static fallback, focused alternative, prior practice, or recap]

## Revisions

- `compute_split` takes `receipt_total_cents` and `overrides` as `{person_id: cents}` — the subagent building `split.py` from only `Split Logic` + `Contracts` found rule 6 needs the receipt total but the signature never passed it, and the overrides type was unstated. It also chose: no 0-cent lines for non-payers, extra cents follow people-list order, with no people nothing is distributed. Tests passed first run (10/10).
- Leftover cents rotate across a bill's divisions instead of always going to the first person — research into how bill-split apps handle rounding showed the old rule piles extra cents on one person across many uneven items.
- The receipt reader treats `(5.00)` and `5.00-` as negative discount lines, and `tip_amount` is confirmed as the Expense Parser's tip field (`line_item/quantity` is only listed for processor v1.3.2, so quantity defaults to 1) — research into Google's field list and known receipt-OCR failure modes; discount rows were being read as positive charges.
- Line items are paired in reading order (each description with the next amount, by text position) instead of trusting Google's `line_item` groups — the first live response packed rows into wrong groups ("Milk 6.99 Eggs" / "5.49 Beef 14.80 Bread"), which lost items and shifted prices.
- A missing tax/tip is derived only from the printed subtotal (`net_amount`) → total gap, never as `total − items − tax` — the first live receipt had no tax entity, and the old fallback turned an $8.82 misread into "tip", making the mismatch warning impossible to trigger.
- Added slice 3 (local LM Studio reader + reader comparison) and renumbered the old 3–4 to 4–5 — the learner asked to try an LLM reader and keep both; a live probe of the local Qwen 3.5 9B showed it can read the sample receipt (6/6 items at 2× upscale) but needs code-side cleanup of junk rows, which is now part of the slice's design. Scope, PRD, and spec updated in the same pass.
- Tax, tip, and receipt total become editable in slice 4 — learner feedback at the slice-2 checkpoint; the reader comparison showed both readers miss a tax line sometimes, so without this a bill could never be made to match its receipt.
- With no people on a bill, the summary now reports items + tax + tip as the computed total so `matches_receipt` is meaningful before names are added — the API showed `matches: false` on every fresh bill, which was misleading.
- Known limit of the subtotal-gap rule: when a receipt has two tax lines and a reader returns only one, the remainder is labelled tip (Umami: $2.00 liquor tax shows as tip). Totals still agree; editing tax/tip in slice 4 is the fix.
- Comparison through the app's own readers (5 receipts, 2026-10-10): local model got every row on all 5 (Google: 3 of 5), at 30–55 s vs ~2 s live. Google reads replay instantly after the first run; the script runs Google in parallel with the local reads.
- The shares now always add up to the receipt total: any gap between the rows and the receipt is split equally as a visible "Receipt difference" line (warning kept), and a bill-level Discount box was added — learner decision at the slice-4 checkpoint: "whatever editing we do the total should be the same, and we can add a discount which subtracts." Property test extended: for random bills, discounts, and receipt totals, the shares sum to exactly what was paid.
