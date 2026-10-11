"""Local reader: image prep, JSON extraction, row cleanup, mapping, and replay dispatch. No model needed."""

import io
import json

import pytest
from PIL import Image

from backend import ocr, ocr_local
from backend.ocr import UnreadableReceipt
from backend.ocr_local import clean_rows, extract_json, map_local_response, prepare_image


def png_bytes(width: int, height: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(out, format="PNG")
    return out.getvalue()


def test_small_photo_is_upscaled_and_large_one_left_alone():
    small = Image.open(io.BytesIO(prepare_image(png_bytes(645, 360), "image/png")))
    assert small.width == ocr_local.TARGET_WIDTH and small.height == round(360 * ocr_local.TARGET_WIDTH / 645)
    big = Image.open(io.BytesIO(prepare_image(png_bytes(1920, 2560), "image/png")))
    assert (big.width, big.height) == (1920, 2560)


def test_non_image_is_unreadable():
    with pytest.raises(UnreadableReceipt):
        prepare_image(b"%PDF-1.4 not an image", "application/pdf")


def test_extract_json_from_reasoning_style_text():
    text = 'Okay, reading the receipt.\n{"merchant": null, "items": [], "subtotal": null, "tax": null, "tip": null, "total": 5}\nDone.'
    assert extract_json(text)["total"] == 5
    with pytest.raises(UnreadableReceipt):
        extract_json("no json here")


def test_clean_rows_drops_summary_labels_and_cuts_at_subtotal():
    rows = [("Organic Milk", 1, 699), ("Fresh Eggs", 1, 549), ("SUBTOTAL", 1, 1248),
            ("Ground", 1, 333), ("Bounty Beers", 1, 591), ("TAX", 1, 333), ("TOTAL", 1, 1581)]
    assert clean_rows(rows, 1248) == [("Organic Milk", 1, 699), ("Fresh Eggs", 1, 549)]
    # No subtotal printed: only the label rows go.
    assert clean_rows(rows, None) == [("Organic Milk", 1, 699), ("Fresh Eggs", 1, 549),
                                      ("Ground", 1, 333), ("Bounty Beers", 1, 591)]


def test_maps_restaurant_receipt_with_tip():
    # Shape of the real local-model output for the Pizza Chicago receipt (trimmed).
    data = {"merchant": "Pizza Chicago", "items": [
        {"name": "Sampler Platter", "quantity": 1, "price": 11.0},
        {"name": "Sampler Platter", "quantity": 1, "price": 11.0},
        {"name": "Mr Pibb", "quantity": 1, "price": 2.75}],
        "subtotal": 24.75, "tax": 2.17, "tip": 3.71, "total": 30.63}
    r = map_local_response(data)
    assert [(i.name, i.price_cents) for i in r.items] == [("Sampler Platter", 1100), ("Sampler Platter", 1100), ("Mr Pibb", 275)]
    assert (r.merchant, r.tax_cents, r.tip_cents, r.total_cents) == ("Pizza Chicago", 217, 371, 3063)


def test_missing_tax_comes_from_subtotal_gap_and_discount_stays_negative():
    data = {"merchant": None, "items": [
        {"name": "Nachos", "quantity": 1, "price": 12.0},
        {"name": "Coupon", "quantity": 1, "price": -2.0}],
        "subtotal": 10.0, "tax": None, "tip": None, "total": 10.8}
    r = map_local_response(data)
    assert r.items[1].price_cents == -200
    assert (r.tax_cents, r.tip_cents, r.total_cents) == (80, 0, 1080)


def test_no_items_is_unreadable():
    with pytest.raises(UnreadableReceipt):
        map_local_response({"items": [{"name": "TOTAL", "quantity": 1, "price": 5.0}], "subtotal": None,
                            "tax": None, "tip": None, "total": 5.0})


def test_read_receipt_local_replays_saved_fixture(tmp_path, monkeypatch):
    image = png_bytes(10, 10)
    fixture = tmp_path / f"local-{ocr.image_hash(image)}.json"
    fixture.write_text(json.dumps({"merchant": "Cafe", "items": [{"name": "Tea", "quantity": 1, "price": 2.5}],
                                   "subtotal": 2.5, "tax": 0.25, "tip": None, "total": 2.75}))
    monkeypatch.setattr(ocr, "FIXTURES_DIR", tmp_path)
    monkeypatch.setenv("OCR_MODE", "replay")
    r = ocr.read_receipt(image, "image/png", reader="local")
    assert r.merchant == "Cafe" and r.items[0].price_cents == 250 and r.total_cents == 275
    with pytest.raises(ocr.ReplayMissing):
        ocr.read_receipt(png_bytes(11, 11), "image/png", reader="local")


def test_absurd_model_values_are_ignored_not_crashed():
    data = {"merchant": None, "items": [
        {"name": "Soup", "quantity": True, "price": "$4.50"},          # bool qty -> 1; "$" tolerated
        {"name": "Huge", "quantity": 1, "price": 1e308},                # noise, dropped
        {"name": "NaN", "quantity": 1, "price": float("nan")},         # dropped
        {"name": "Inf", "quantity": 1, "price": "inf"},                # dropped
        {"name": "List", "quantity": 1, "price": [1, 2]},              # dropped
        {"name": "Big qty", "quantity": 10**12, "price": 1.0}],        # qty clamped to 1
        "subtotal": None, "tax": None, "tip": None, "total": None}
    r = map_local_response(data)
    assert [(i.name, i.quantity, i.price_cents) for i in r.items] == [("Soup", 1, 450), ("Big qty", 1, 100)]

