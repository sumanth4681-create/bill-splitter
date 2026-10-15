"""The friend's page (?share=token): no tabs, pick a name, tick items, see your total."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).parent.parent / "frontend" / "app.py"

FAKE_BILL = {
    "id": 1, "name": "Taco Place", "share_token": "tok", "items": [
        {"id": 1, "name": "Nachos", "quantity": 1, "price_cents": 1200},
        {"id": 2, "name": "Coke", "quantity": 1, "price_cents": 300}],
    "people": [{"id": 1, "name": "Sam"}, {"id": 2, "name": "Alex"}],
    "claims": [{"item_id": 1, "person_id": 2}],
    "summary": {"people": [
        {"person_id": 1, "name": "Sam", "final_cents": 150},
        {"person_id": 2, "name": "Alex", "final_cents": 1350}]},
}


def test_friend_page_shows_only_their_claims(monkeypatch):
    from frontend import api_client

    monkeypatch.setattr(api_client, "get_shared_bill", lambda token: FAKE_BILL)
    at = AppTest.from_file(str(APP))
    at.query_params["share"] = "tok"
    at.run(timeout=30)
    assert not at.exception and at.tabs == []            # a friend never sees Upload/History
    assert at.selectbox[0].value == "Sam" and [c.value for c in at.checkbox] == [False, False]
    at.selectbox[0].select("Alex").run(timeout=30)
    assert not at.exception and [c.value for c in at.checkbox] == [True, False]   # Alex already has Nachos
    assert at.metric[0].value == "$13.50"
