---
doc: prd
status: approved
---

# Bill Splitter — Product Requirements

A small app for the friend who paid after a group outing. They upload a photo of the receipt, the items are read automatically, they claim items on behalf of each friend, and they see what everyone owes them.
Source: `scope.md > The Unique Kernel`, `scope.md > Who It's For`.

## The Core Journey
Source: `scope.md > The Core Loop`.

1. The payer opens Bill Splitter and goes to **Upload**.
2. They upload a photo of the receipt (taken on their phone).
3. A spinning "reading / summarizing" indicator shows while Google OCR reads the receipt.
4. The bill opens, with the receipt's items listed top to bottom. Each item has a unique ID, name, quantity, and price.
5. If anything was misread, the payer fixes the quantity or price on that row.
6. Above the items, the payer enters the friends' names. Those names fill a **person dropdown**.
7. The payer picks a person from the dropdown and selects that person's items. The person's name appears under each item they selected. To share an item, they select it under each person who had it.
8. They repeat for each friend.
9. The **summary** shows each person's name, their items, and the amount they owe the payer. Shared items are divided among the people who selected them, while unclaimed items, tax, and tip are split equally by default.
10. If needed, the payer edits individual amounts in the summary at the end.
11. The bill is saved and appears in **History**. Clicking it later reopens the bill with its items and who bought what.

Success: every person's amount adds up to the receipt total, and the split matches who claimed what.

## Screens and Layout
Two places in the app:

- **Upload** — an image input and a **Reader** choice (Google Document AI, the default, or the local model). After upload: the loading spinner, then the bill opens.
- **History** — a list of past bills, each shown by bill name. Clicking one opens that bill.

**The bill view** (opened from Upload or History), top to bottom:
1. Bill name
2. Friends' names input, plus the person dropdown
3. Summary per person (name, items, amount). *Placement: "at the top or below the names". Assumed here to sit directly below the names; adjust in review.*
4. Items list (item, quantity, price, select control, with the claimers' names shown under each item)

## Look and Feel
- **Interactive, not vibrant.** A calm, muted palette with no loud colors.
- "Interactive" means clear, immediate feedback: selecting an item instantly shows the name under it and updates the summary.
- No references given. Avoid generic bright "AI app" styling.

## Features and Behavior

### Receipt Upload and Reading
Source: `scope.md > The POC Boundary` (real OCR).
- Accepts a receipt photo and sends it to the chosen reader: Google OCR (default) or the local LM Studio model.
- Shows a spinner while it's reading.
- Produces a list of items, each with a unique ID (so two "Coke" lines stay separate), a name, a quantity, and a price. Tax and tip are captured separately from the items when present.
  - [ ] Uploading a real receipt photo shows the spinner, then a list of items matching the receipt.
  - [ ] Two items with the same name appear as two separate rows that can be claimed independently.
  - [ ] The same photo read by the local model opens a bill with the same item rows (differences, if any, are visible in the mismatch warning and fixable by editing).

### Fixing Misreads
- Quantity and price on each row can be edited to correct OCR errors. This is not claiming part of an item.
- Tax, tip, and the receipt total can also be edited, because readers sometimes miss a tax line (learner feedback at the slice-2 checkpoint, 2026-10-10).
  - [ ] Editing a price updates the summary amounts immediately.
  - [ ] Editing tax or tip updates every person's share and clears the mismatch warning when the numbers now agree.

### People
- Friends' names are entered above the items when the bill is open.
- The entered names populate the person dropdown.
  - [ ] Adding three names makes all three appear in the dropdown.

### Claiming Items
Source: `scope.md > The Unique Kernel`.
- With a person chosen in the dropdown, selecting an item claims it for that person, and their name appears under the item.
- Selecting the same item under two or more people makes it shared, divided equally among only those people.
- Deselecting removes the claim.
  - [ ] Select "Nachos" as A, then as B: both names show under Nachos, and C pays nothing for it.
  - [ ] Deselecting removes the name and updates the summary.

### Split Summary
Source: `scope.md > What "Working" Looks Like`.
- One block per person: name, their items (with their share of shared items), and the total they owe the payer.
- Unclaimed items are split equally across everyone and clearly labeled as unclaimed in the summary.
- Tax and tip are split equally by default. A bill-level discount (entered by hand) comes off everyone equally.
- **The amounts always add up to the receipt total**, because that is what the payer actually paid. If the rows don't add up to it (a misread), the difference is split equally as a visible "Receipt difference" line and a warning asks the payer to check for a misread row. (Learner decision at the slice-4 checkpoint, 2026-10-10.)
- Each person's final amount can be edited at the end.
  - [ ] The sum of all people's amounts equals the receipt total (before any manual edits).
  - [ ] An unclaimed item appears in the summary labeled as unclaimed and spread across all people.
  - [ ] Editing a person's final amount shows the new amount.

### History
- Each processed bill is saved and listed by name in History.
- Opening one shows its items, names, claims, and summary.
  - [ ] After processing a bill, it appears in History, and reopening it shows the same claims.

## States and Boundaries
- **First use / empty History**: no bills yet. History shows an empty message pointing to Upload. *(Assumption.)*
- **Reading**: the spinner shows while OCR runs.
- **Unreadable image** (blurry, or not a receipt): a message asks the payer to "upload a clear image" and lets them upload again.
- **No names yet**: the dropdown is empty, so items can't be claimed until a name is added. *(Assumption.)*
- **Nothing claimed**: every item is unclaimed and split equally across everyone.
- **Persistence**: bills, names, and claims stay saved and reappear in History.

## Product Decisions
- Name: **Bill Splitter**.
- One person acts as everyone, using a person dropdown, so the multi-user link is not needed for the demo (from scope).
- Editing quantity and price is only for fixing misreads; claiming part of an item stays deferred.
- Unclaimed items, tax, and tip are split **equally by default**, with the option to **edit amounts at the end**.
- Friends' names are entered on the bill itself, above the items.
- Unreadable image → "upload a clear image" and retry.
- Look: interactive, not vibrant.

## What We're Building
Upload with spinner → Google OCR → editable item list with unique IDs → names and person dropdown → claim/share items → per-person summary (unclaimed items, tax, and tip split equally; final amounts editable) → saved in History.

## Deferred From the POC
- **Shareable group link and multiple devices.** The architecture gets designed in spec but is not built (scope).
- **Accept / correct / recalculate / approval rounds / publish final split.** These need multiple users.
- **Claiming by quantity** (e.g., 3 beers split 2-and-1). Even splits are already covered by sharing.

## Possible Later Enhancements
- Proportional tax and tip based on what each person ordered.
- Sending each friend their summary.

## Non-Goals
- In-app payments. The app shows who owes what; money moves outside it.
- User accounts or login. There is only one payer using the app.

## Added During the Build
- **Local-model reader (2026-10-10):** a second reader backed by the learner's LM Studio server, with a comparison script that scores both readers on the same receipts (rows matched, totals, time). Evidence so far: on one receipt the local model matched 6/6 items once the photo was enlarged, but took ~20 s vs ~1.6 s and added junk rows that code must filter.

## Open Questions
- **Bill name:** who sets it? *Assumption: defaults to the restaurant name or upload date, and can be edited.* Can wait for spec.
- **Summary placement:** above or below the items? *Assumption: below the names, above the items.* Confirm in review.
- **Rounding:** when an amount doesn't divide evenly (e.g., $10 split three ways), who gets the extra cent? Can be decided in spec.
