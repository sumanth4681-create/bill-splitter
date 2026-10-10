"""Contracts shared by every module. All money is integer cents."""

from pydantic import BaseModel


class Item(BaseModel):
    id: int
    name: str
    quantity: int
    price_cents: int  # line total, as printed on the receipt row


class Person(BaseModel):
    id: int
    name: str


class Claim(BaseModel):
    item_id: int
    person_id: int


class ParsedItem(BaseModel):
    name: str
    quantity: int = 1
    price_cents: int


class ParsedReceipt(BaseModel):
    merchant: str | None = None
    items: list[ParsedItem]
    tax_cents: int = 0
    tip_cents: int = 0
    total_cents: int


class ShareLine(BaseModel):
    item_id: int
    item_name: str
    share_cents: int
    split_ways: int
    unclaimed: bool


class PersonShare(BaseModel):
    person_id: int
    name: str
    lines: list[ShareLine]
    tax_cents: int
    tip_cents: int
    computed_cents: int
    final_cents: int
    edited: bool


class Summary(BaseModel):
    people: list[PersonShare]
    items_total_cents: int
    receipt_total_cents: int
    computed_total_cents: int
    matches_receipt: bool


class BillListItem(BaseModel):
    id: int
    name: str
    created_at: str


class BillDetail(BaseModel):
    id: int
    name: str
    created_at: str
    tax_cents: int
    tip_cents: int
    receipt_total_cents: int
    items: list[Item]
    people: list[Person]
    claims: list[Claim]
    summary: Summary
