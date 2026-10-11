"""Endpoints end to end with FastAPI's TestClient: replay-mode OCR, a scratch database, no network."""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from google.cloud import documentai

from backend import ocr

SAMPLE = Path(__file__).parent.parent / "backend" / "fixtures" / "sample.json"


def png(width: int) -> bytes:
    """A real (blank) PNG; different widths give different bytes, hence different hashes."""
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (width, 10), "white").save(out, format="PNG")
    return out.getvalue()


RECEIPT = png(10)   # its hash points at the saved sample response
BLANK = png(11)     # its saved response has no line items
UNSEEN = png(12)    # nothing saved for it


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BILLS_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("OCR_MODE", "replay")
    monkeypatch.setattr(ocr, "FIXTURES_DIR", tmp_path)
    shutil.copy(SAMPLE, tmp_path / f"{ocr.image_hash(RECEIPT)}.json")
    # A "receipt" whose saved response has no line items at all.
    empty = documentai.Document(entities=[documentai.Document.Entity(type_="total_amount", mention_text="5.00")])
    (tmp_path / f"{ocr.image_hash(BLANK)}.json").write_text(documentai.Document.to_json(empty))

    from backend import main
    main.storage.init_db()
    return TestClient(main.app)


def upload(client, data=RECEIPT, reader="docai"):
    return client.post("/bills", files={"image": ("r.png", data, "image/png")}, data={"reader": reader})


def test_upload_lists_and_reopens_with_claims(client):
    assert client.get("/bills").json() == []
    bill = upload(client).json()
    assert bill["items"][0]["name"] == "Organic Milk" and bill["receipt_total_cents"] == 4491

    bid = bill["id"]
    people = client.put(f"/bills/{bid}/people", json={"names": ["Sam", "Alex", "Priya"]}).json()["people"]
    sam, alex = people[0]["id"], people[1]["id"]
    beef = next(i["id"] for i in bill["items"] if i["name"] == "Ground Beef")
    client.put(f"/bills/{bid}/claims", json={"item_id": beef, "person_id": sam, "claimed": True})
    detail = client.put(f"/bills/{bid}/claims", json={"item_id": beef, "person_id": alex, "claimed": True}).json()

    shares = {p["name"]: p for p in detail["summary"]["people"]}
    assert shares["Priya"]["lines"] and all(l["item_name"] != "Ground Beef" for l in shares["Priya"]["lines"])
    assert sum(p["computed_cents"] for p in shares.values()) == 4491
    assert detail["summary"]["matches_receipt"]

    listed = client.get("/bills").json()
    assert [b["id"] for b in listed] == [bid] and listed[0]["name"] == bill["name"]
    reopened = client.get(f"/bills/{bid}").json()
    assert len(reopened["claims"]) == 2 and [p["name"] for p in reopened["people"]] == ["Sam", "Alex", "Priya"]


def test_history_is_newest_first(client):
    first = upload(client).json()["id"]
    second = upload(client).json()["id"]
    assert [b["id"] for b in client.get("/bills").json()] == [second, first]


def test_edits_keep_totals_honest(client):
    bid = upload(client).json()["id"]
    client.put(f"/bills/{bid}/people", json={"names": ["Sam", "Alex"]})
    milk = client.get(f"/bills/{bid}").json()["items"][0]["id"]
    s = client.patch(f"/bills/{bid}/items/{milk}", json={"price_cents": 899}).json()["summary"]
    assert not s["matches_receipt"] and s["difference_cents"] == -200
    assert sum(p["computed_cents"] for p in s["people"]) == 4491
    s = client.patch(f"/bills/{bid}", json={"tax_cents": 133}).json()["summary"]
    assert s["matches_receipt"]  # 2.00 less tax balances the 2.00 price increase
    p = client.put(f"/bills/{bid}/overrides", json={"person_id": s["people"][0]["person_id"], "amount_cents": 1}).json()
    assert p["summary"]["people"][0]["edited"] and p["summary"]["people"][0]["final_cents"] == 1


def test_error_responses(client):
    assert upload(client, data=BLANK).status_code == 422             # saved response had no line items
    assert "clear image" in upload(client, data=BLANK).json()["detail"]
    assert upload(client, data=UNSEEN).status_code == 422            # replay mode, nothing saved
    assert upload(client, data=b"not an image at all").status_code == 422
    assert client.post("/bills", files={"image": ("r.gif", RECEIPT, "image/gif")}).status_code == 415
    big = RECEIPT + b"\0" * (10 * 1024 * 1024)
    assert client.post("/bills", files={"image": ("r.png", big, "image/png")}).status_code == 413
    assert upload(client, reader="gemini").status_code == 422
    assert client.get("/bills/999").status_code == 404
    bid = upload(client).json()["id"]
    assert client.put(f"/bills/{bid}/claims", json={"item_id": 1, "person_id": 999, "claimed": True}).status_code == 404
    assert client.patch(f"/bills/{bid}", json={"name": " "}).status_code == 422
    assert client.patch(f"/bills/{bid}", json={"discount_cents": -1}).status_code == 422


def test_bounds_reject_overflow_coercion_and_oversized_input(client):
    bid = upload(client).json()["id"]
    client.put(f"/bills/{bid}/people", json={"names": ["Sam"]})
    item = client.get(f"/bills/{bid}").json()["items"][0]["id"]
    sam = client.get(f"/bills/{bid}").json()["people"][0]["id"]
    assert client.patch(f"/bills/{bid}", json={"tax_cents": 2**63}).status_code == 422       # was a 500
    assert client.patch(f"/bills/{bid}/items/{item}", json={"price_cents": 2**70}).status_code == 422
    assert client.put(f"/bills/{bid}/overrides", json={"person_id": sam, "amount_cents": 2**63}).status_code == 422
    assert client.patch(f"/bills/{bid}", json={"tax_cents": True}).status_code == 422          # strict ints
    assert client.patch(f"/bills/{bid}", json={"tax_cents": "5"}).status_code == 422
    assert client.patch(f"/bills/{bid}", json={"name": "x" * 100_000}).status_code == 422
    assert client.put(f"/bills/{bid}/people", json={"names": ["p"] * 10_000}).status_code == 422
    assert client.put(f"/bills/{bid}/people", json={"names": ["x" * 500]}).status_code == 422
    assert client.get(f"/bills/{2**63}").status_code == 422
    assert client.patch(f"/bills/{bid}/items/{item}", json={"price_cents": -300}).status_code == 200  # discount row
    assert client.patch(f"/bills/{bid}/items/{item}", json={"price_cents": -(10**12)}).status_code == 422
    assert client.get(f"/bills/{bid}").json()["summary"]["matches_receipt"] is not None  # still healthy


def test_cross_site_browser_post_is_refused_but_local_tools_work(client):
    assert upload(client).status_code == 201                                   # no Origin: curl, requests, Streamlit
    ok = client.post("/bills", files={"image": ("r.png", RECEIPT, "image/png")}, data={"reader": "docai"},
                     headers={"Origin": "http://localhost:8000"})               # the API's own /docs page
    assert ok.status_code == 201
    evil = client.post("/bills", files={"image": ("r.png", RECEIPT, "image/png")}, data={"reader": "docai"},
                       headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403

