"""Pure bill-splitting math: who owes what, in integer cents."""

from backend.models import Claim, Item, Person, PersonShare, ShareLine, Summary


def divide(amount: int, n: int, start: int = 0) -> list[int]:
    """Split exactly; the extra cents go to `extra` people beginning at index `start`."""
    base, extra = divmod(amount, n)
    return [base + (1 if (i - start) % n < extra else 0) for i in range(n)]


def compute_split(
    items: list[Item],
    people: list[Person],
    claims: list[Claim],
    tax_cents: int,
    tip_cents: int,
    overrides: dict[int, int] | None = None,
    receipt_total_cents: int | None = None,
) -> Summary:
    overrides = overrides or {}
    items_total = sum(item.price_cents for item in items)
    receipt_total = items_total + tax_cents + tip_cents if receipt_total_cents is None else receipt_total_cents
    if not people:
        return Summary(people=[], items_total_cents=items_total, receipt_total_cents=receipt_total,
                       computed_total_cents=0, matches_receipt=receipt_total == 0)

    person_ids = [p.id for p in people]
    claimers: dict[int, set[int]] = {}
    for claim in claims:
        if claim.person_id in person_ids:
            claimers.setdefault(claim.item_id, set()).add(claim.person_id)

    # Rotate who receives leftover cents so the same person isn't always rounded up.
    turn = 0

    def fair_divide(amount: int, n: int) -> list[int]:
        nonlocal turn
        shares = divide(amount, n, turn % n)
        turn += amount % n
        return shares

    lines: dict[int, list[ShareLine]] = {pid: [] for pid in person_ids}
    for item in items:
        payers = [pid for pid in person_ids if pid in claimers.get(item.id, set())]
        unclaimed = not payers
        if unclaimed:
            payers = person_ids
        for pid, share in zip(payers, fair_divide(item.price_cents, len(payers))):
            lines[pid].append(ShareLine(item_id=item.id, item_name=item.name, share_cents=share,
                                        split_ways=len(payers), unclaimed=unclaimed))

    taxes = fair_divide(tax_cents, len(people))
    tips = fair_divide(tip_cents, len(people))
    shares = []
    for person, tax, tip in zip(people, taxes, tips):
        computed = sum(line.share_cents for line in lines[person.id]) + tax + tip
        edited = person.id in overrides
        shares.append(PersonShare(person_id=person.id, name=person.name, lines=lines[person.id], tax_cents=tax,
                                  tip_cents=tip, computed_cents=computed,
                                  final_cents=overrides[person.id] if edited else computed, edited=edited))

    computed_total = sum(s.computed_cents for s in shares)
    return Summary(people=shares, items_total_cents=items_total, receipt_total_cents=receipt_total,
                   computed_total_cents=computed_total, matches_receipt=computed_total == receipt_total)
