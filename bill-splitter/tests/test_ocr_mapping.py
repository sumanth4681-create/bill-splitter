"""map_expense_document() on Document AI responses, no network."""

from pathlib import Path

import pytest
from google.cloud import documentai

from backend.ocr import UnreadableReceipt, map_expense_document

Entity = documentai.Document.Entity
SAMPLE = Path(__file__).parent.parent / "backend" / "fixtures" / "sample.json"


def money(kind: str, text: str, units: int, nanos: int = 0) -> Entity:
    return Entity(type_=kind, mention_text=text,
                  normalized_value={"money_value": {"currency_code": "USD", "units": units, "nanos": nanos}})


def line(desc: str, qty: str | None, amount_text: str, units: int, nanos: int = 0) -> Entity:
    props = [Entity(type_="line_item/description", mention_text=desc), money("line_item/amount", amount_text, units, nanos)]
    if qty:
        props.append(Entity(type_="line_item/quantity", mention_text=qty))
    return Entity(type_="line_item", mention_text=desc, properties=props)


def test_maps_items_tax_total():
    doc = documentai.Document(entities=[
        Entity(type_="supplier_name", mention_text="Taco  Place"),
        line("Nachos", "1", "$12.50", 12, 500_000_000),
        line("Coke", None, "2.99", 2, 990_000_000),
        line("Coke", None, "2.99", 2, 990_000_000),
        money("total_tax_amount", "1.85", 1, 850_000_000),
        money("total_amount", "20.33", 20, 330_000_000),
    ])
    r = map_expense_document(doc)
    assert r.merchant == "Taco Place"
    assert [(i.name, i.quantity, i.price_cents) for i in r.items] == [
        ("Nachos", 1, 1250), ("Coke", 1, 299), ("Coke", 1, 299)]
    assert (r.tax_cents, r.tip_cents, r.total_cents) == (185, 0, 2033)


def test_missing_tax_comes_from_subtotal_gap_not_from_items():
    # Items are misread (sum 1000) but the printed subtotal is 1200: the gap to the
    # total is tax, and the items mismatch must stay visible rather than become "tip".
    doc = documentai.Document(entities=[
        line("Soup", None, "10.00", 10),
        money("net_amount", "12.00", 12),
        money("total_amount", "13.00", 13),
    ])
    r = map_expense_document(doc)
    assert (r.tax_cents, r.tip_cents, r.total_cents) == (100, 0, 1300)
    assert sum(i.price_cents for i in r.items) + r.tax_cents + r.tip_cents != r.total_cents


def test_misgrouped_rows_are_paired_in_reading_order():
    # Shape of a real Expense Parser response: rows packed into the wrong line_item groups.
    def prop(kind: str, text: str, at: int) -> Entity:
        return Entity(type_=kind, mention_text=text, text_anchor={"text_segments": [{"start_index": at}]})
    doc = documentai.Document(entities=[
        Entity(type_="line_item", properties=[
            prop("line_item/description", "Milk", 0), prop("line_item/amount", "6.99", 10),
            prop("line_item/description", "Eggs", 20)]),
        Entity(type_="line_item", properties=[
            prop("line_item/amount", "5.49", 30), prop("line_item/description", "Beef", 40),
            prop("line_item/amount", "14.80", 50)]),
    ])
    r = map_expense_document(doc)
    assert [(i.name, i.price_cents) for i in r.items] == [("Milk", 699), ("Eggs", 549), ("Beef", 1480)]


def test_amount_from_text_when_not_normalized():
    doc = documentai.Document(entities=[Entity(type_="line_item", properties=[
        Entity(type_="line_item/description", mention_text="Fries"),
        Entity(type_="line_item/amount", mention_text="$4.25"),
    ])])
    r = map_expense_document(doc)
    assert r.items[0].price_cents == 425
    assert r.total_cents == 425 and r.tip_cents == 0


def test_no_line_items_is_unreadable():
    with pytest.raises(UnreadableReceipt):
        map_expense_document(documentai.Document(entities=[money("total_amount", "5.00", 5)]))


@pytest.mark.parametrize("printed, cents", [
    ("$5.00", 500), ("-5.00", -500), ("(5.00)", -500), ("5.00-", -500), ("$1,234.56", 123456)])
def test_discount_formats_become_negative(printed, cents):
    doc = documentai.Document(entities=[Entity(type_="line_item", properties=[
        Entity(type_="line_item/description", mention_text="Thing"),
        Entity(type_="line_item/amount", mention_text=printed),
    ])])
    assert map_expense_document(doc).items[0].price_cents == cents


def test_tip_amount_entity_is_used_when_present():
    doc = documentai.Document(entities=[
        line("Burger", None, "15.00", 15),
        money("tip_amount", "3.00", 3),
        money("total_amount", "18.00", 18),
    ])
    r = map_expense_document(doc)
    assert r.tip_cents == 300 and r.total_cents == 1800


def test_real_sample_fixture():
    # A real Document AI response for a grocery receipt photo (backend/fixtures/sample.json).
    doc = documentai.Document.from_json(SAMPLE.read_text(), ignore_unknown_fields=True)
    r = map_expense_document(doc)
    assert [(i.name, i.price_cents) for i in r.items] == [
        ("Organic Milk", 699), ("Fresh Eggs", 549), ("Ground Beef", 1480),
        ("Bread", 425), ("Apples", 610), ("Pasta", 395)]
    assert (r.tax_cents, r.tip_cents, r.total_cents) == (333, 0, 4491)
