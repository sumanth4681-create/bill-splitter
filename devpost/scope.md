---
doc: scope
status: approved
---

# Bill Splitter (OCR)

Snap a photo of a group receipt, let each person claim what they had, and let the app do the math.

## The Unique Kernel
**Item-level claiming instead of equal splits.** Each receipt item (with a unique ID, so duplicate names don't collide) can belong to one person or be shared by only the people who had it. If two people share something, the third doesn't pay for it. Anything nobody claims is split equally across the group by default (amounts can be edited) and called out clearly.

## Who It's For
The friend who paid the bill after a group outing. Today the group has to "sit together" afterward and work out who bought what. Everyone already knows what they had; the app just needs to let them say so.

## The Core Loop
Photo of the receipt → Google OCR turns it into items, prices, and quantities → people claim their items (names appear under each item) → the app calculates each person's itemized share of what they owe the payer.

## Inspiration & Identity
None volunteered yet. `3-prd` can fill this in.

## Why This Matters to the Learner
"Making group bill splitting easier: everyone chooses what they bought, and the app handles the calculations." It's also a chance to experiment, and to practice agent orchestration by designing the multi-user architecture up front while only building the single-user slice.

## What "Working" Looks Like
On a single screen, one person acts as several people. They scan a real receipt, see the items, switch between people to claim items (names get added under each item and stored in a list), share some items among a subset, and see each person's itemized total. Then they re-check the split logic with different inputs.

The "oh, that's cool" beat: a shared item split between two people while the third pays nothing, plus unclaimed items spread across everyone, all adding up to the receipt total.

## The POC Boundary
- Real OCR on a receipt photo. Learner prefers **Google OCR** because real receipts are messy (details confirmed in `4-spec`)
- Editable amounts, so a misread price or the leftover split can be corrected by hand
- A single screen where one person enters the group's names and claims on behalf of each person
- Items with unique IDs; one owner or shared among selected people
- Everything left unclaimed is split equally across the group by default, or the amounts can be edited; shown in the summary
- Per-person itemized summary of what each owes the payer
- Split logic tested against varied inputs

## Later
- Shareable group link, with friends joining from their own devices. **The architecture gets designed now but is not implemented or tested.**
- The accept → correct → recalculate → approval rounds → publish-final-split flow, since it depends on multiple users
- Claiming by quantity for uneven splits (e.g., 3 units split 2-and-1). Even splits are already covered by subset sharing

## Explicitly Cut
- In-app payments. The app shows who owes what; money moves outside it.
