"""compute_split() rules from the Split Logic spec."""

import random

from backend.models import Claim, Item, Person
from backend.split import compute_split


def item(id: int, price: int, name: str = "Thing") -> Item:
    return Item(id=id, name=name, quantity=1, price_cents=price)


def people(n: int) -> list[Person]:
    return [Person(id=i + 1, name=f"P{i + 1}") for i in range(n)]


def by_id(summary, pid: int):
    return next(s for s in summary.people if s.person_id == pid)


def test_solo_claim():
    s = compute_split([item(1, 1250)], people(2), [Claim(item_id=1, person_id=2)], 0, 0, {})
    assert by_id(s, 2).computed_cents == 1250
    assert by_id(s, 1).computed_cents == 0
    assert by_id(s, 2).lines[0].split_ways == 1
    assert by_id(s, 2).lines[0].unclaimed is False


def test_shared_between_two_of_three():
    claims = [Claim(item_id=1, person_id=1), Claim(item_id=1, person_id=3)]
    s = compute_split([item(1, 900)], people(3), claims, 0, 0, {})
    assert [p.computed_cents for p in s.people] == [450, 0, 450]
    assert by_id(s, 2).lines == []
    assert by_id(s, 1).lines[0].split_ways == 2


def test_unclaimed_spread_across_three():
    s = compute_split([item(1, 600)], people(3), [], 0, 0, {})
    for p in s.people:
        assert p.computed_cents == 200
        assert p.lines[0].unclaimed is True
        assert p.lines[0].split_ways == 3


def test_ten_dollars_three_ways():
    s = compute_split([item(1, 1000)], people(3), [], 0, 0, {})
    assert [p.computed_cents for p in s.people] == [334, 333, 333]
    assert s.matches_receipt


def test_tax_and_tip_split():
    s = compute_split([], people(3), [], 100, 200, {})
    assert [p.tax_cents for p in s.people] == [34, 33, 33]
    assert [p.tip_cents for p in s.people] == [66, 67, 67]  # leftover cents rotate past P1
    assert [p.computed_cents for p in s.people] == [100, 100, 100]
    assert s.computed_total_cents == 300


def test_override_sets_final_but_not_match():
    s = compute_split([item(1, 1000)], people(2), [], 0, 0, {1: 700})
    p1, p2 = s.people
    assert (p1.final_cents, p1.computed_cents, p1.edited) == (700, 500, True)
    assert (p2.final_cents, p2.edited) == (500, False)
    assert s.matches_receipt is True
    assert s.computed_total_cents == 1000


def test_no_people():
    s = compute_split([item(1, 500)], [], [], 50, 0, {}, receipt_total_cents=550)
    assert s.people == []
    assert s.items_total_cents == 500
    assert s.computed_total_cents == 550 and s.matches_receipt  # receipt check still works before names exist


def test_receipt_difference_is_shared_so_totals_always_match_what_was_paid():
    # Milk misread as 8.99 (really 6.99): rows say 10.00 + 1.00 tax, receipt says 9.00.
    s = compute_split([item(1, 899), item(2, 101)], people(2), [], 100, 0, {}, receipt_total_cents=900)
    assert s.difference_cents == -200 and not s.matches_receipt
    assert [p.difference_cents for p in s.people] == [-100, -100]
    assert sum(p.computed_cents for p in s.people) == 900 == s.computed_total_cents


def test_discount_comes_off_everyone_equally_and_rounds_exactly():
    s = compute_split([item(1, 1000)], people(3), [], 0, 0, {}, receipt_total_cents=700, discount_cents=300)
    assert [p.discount_cents for p in s.people] == [100, 100, 100]
    assert sum(p.computed_cents for p in s.people) == 700 and s.matches_receipt
    s = compute_split([item(1, 1000)], people(3), [], 0, 0, {}, receipt_total_cents=899, discount_cents=101)
    assert sum(p.discount_cents for p in s.people) == 101
    assert sum(p.computed_cents for p in s.people) == 899


def test_same_name_items_kept_separate():
    items = [item(1, 300, "Taco"), item(2, 500, "Taco")]
    claims = [Claim(item_id=1, person_id=1), Claim(item_id=2, person_id=2)]
    s = compute_split(items, people(2), claims, 0, 0, {})
    assert [(l.item_id, l.share_cents) for l in by_id(s, 1).lines] == [(1, 300)]
    assert [(l.item_id, l.share_cents) for l in by_id(s, 2).lines] == [(2, 500)]


def test_mismatched_receipt_total_is_flagged():
    s = compute_split([item(1, 1000)], people(2), [], 0, 0, {}, receipt_total_cents=1100)
    assert s.matches_receipt is False


def test_sum_always_equals_items_tax_tip():
    rng = random.Random(1234)
    for _ in range(500):
        n_people = rng.randint(1, 8)
        ppl = people(n_people)
        items = [item(i, rng.randint(0, 20000)) for i in range(1, rng.randint(0, 12) + 1)]
        claims = [Claim(item_id=it.id, person_id=p.id)
                  for it in items for p in ppl if rng.random() < 0.3]
        tax, tip = rng.randint(0, 3000), rng.randint(0, 5000)
        overrides = {p.id: rng.randint(0, 10000) for p in ppl if rng.random() < 0.2}
        s = compute_split(items, ppl, claims, tax, tip, overrides)
        expected = sum(it.price_cents for it in items) + tax + tip
        assert sum(p.computed_cents for p in s.people) == expected
        assert s.computed_total_cents == expected
        assert s.matches_receipt
        # With a discount and a receipt total that disagrees with the rows, the shares must
        # still add up to exactly what was paid.
        discount = rng.randint(0, 2000)
        paid = max(expected - discount + rng.randint(-500, 500), 0)
        s = compute_split(items, ppl, claims, tax, tip, {}, receipt_total_cents=paid, discount_cents=discount)
        assert sum(p.computed_cents for p in s.people) == paid == s.computed_total_cents
        assert sum(p.discount_cents for p in s.people) == discount
        assert s.matches_receipt == (paid == expected - discount)
        for it in items:
            assert sum(l.share_cents for p in s.people for l in p.lines if l.item_id == it.id) == it.price_cents


def test_leftover_cents_rotate_instead_of_piling_on_one_person():
    # Ten 1-cent unclaimed items among 3: nobody should end up more than 1 cent apart.
    s = compute_split([item(i, 1) for i in range(1, 11)], people(3), [], 0, 0, {})
    totals = [p.computed_cents for p in s.people]
    assert sum(totals) == 10
    assert max(totals) - min(totals) <= 1


def test_discount_line_reduces_what_claimers_pay():
    # A -$3.00 discount on Nachos, claimed by the same two people who shared Nachos.
    items = [item(1, 1200, "Nachos"), item(2, -300, "Nachos discount"), item(3, 600, "Soda")]
    claims = [Claim(item_id=1, person_id=1), Claim(item_id=1, person_id=2),
              Claim(item_id=2, person_id=1), Claim(item_id=2, person_id=2),
              Claim(item_id=3, person_id=3)]
    s = compute_split(items, people(3), claims, 0, 0, {})
    assert [p.computed_cents for p in s.people] == [450, 450, 600]
    assert s.matches_receipt


def test_duplicate_person_ids_count_once():
    twice = [Person(id=1, name="a"), Person(id=1, name="b"), Person(id=2, name="c")]
    s = compute_split([item(1, 100)], twice, [Claim(item_id=1, person_id=1)], 0, 0, {})
    assert [p.person_id for p in s.people] == [1, 2]
    assert sum(p.computed_cents for p in s.people) == 100 and s.matches_receipt

