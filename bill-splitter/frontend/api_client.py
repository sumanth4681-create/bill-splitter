"""Thin wrappers around the backend endpoints."""

import os

import requests

API_URL = os.getenv("API_URL", "http://localhost:8000")
START_HINT = "Backend isn't running. Start it with `uvicorn backend.main:app --port 8000`."


class BackendUnavailable(Exception):
    pass


class ApiError(Exception):
    """The backend answered with an error; str(exc) is its message."""


READ_TIMEOUT = 180  # the local model can take a minute; everything else answers in milliseconds


def _request(method: str, path: str, timeout: int = 30, **kwargs) -> dict | list:
    try:
        resp = requests.request(method, f"{API_URL}{path}", timeout=timeout, **kwargs)
    except requests.ConnectionError as exc:
        raise BackendUnavailable(START_HINT) from exc
    except requests.Timeout as exc:
        raise ApiError("The backend is taking too long. If this was a receipt read, check History in a "
                       "moment: the bill may still appear.") from exc
    except requests.RequestException as exc:
        raise ApiError(f"Request failed: {type(exc).__name__}") from exc
    if not resp.ok:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        if isinstance(detail, list):  # FastAPI validation errors: [{loc, msg, ...}, ...]
            detail = "; ".join(f"{'.'.join(str(p) for p in e.get('loc', [])[1:]) or 'input'}: {e.get('msg', '')}"
                               for e in detail)
        raise ApiError(str(detail))
    return resp.json()


def create_bill(filename: str, data: bytes, mime_type: str, reader: str = "docai") -> dict:
    return _request("POST", "/bills", timeout=READ_TIMEOUT, files={"image": (filename, data, mime_type)},
                    data={"reader": reader})


def get_bill(bill_id: int) -> dict:
    return _request("GET", f"/bills/{bill_id}")


def list_bills() -> list:
    return _request("GET", "/bills")


def set_people(bill_id: int, names: list[str]) -> dict:
    return _request("PUT", f"/bills/{bill_id}/people", json={"names": names})


def set_claim(bill_id: int, item_id: int, person_id: int, claimed: bool) -> dict:
    return _request("PUT", f"/bills/{bill_id}/claims",
                    json={"item_id": item_id, "person_id": person_id, "claimed": claimed})


def update_bill(bill_id: int, **fields) -> dict:
    return _request("PATCH", f"/bills/{bill_id}", json=fields)


def update_item(bill_id: int, item_id: int, **fields) -> dict:
    return _request("PATCH", f"/bills/{bill_id}/items/{item_id}", json=fields)


def set_override(bill_id: int, person_id: int, amount_cents: int | None) -> dict:
    return _request("PUT", f"/bills/{bill_id}/overrides", json={"person_id": person_id, "amount_cents": amount_cents})
