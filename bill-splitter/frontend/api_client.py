"""Thin wrappers around the backend endpoints."""

import os

import requests

API_URL = os.getenv("API_URL", "http://localhost:8000")
START_HINT = "Backend isn't running. Start it with `uvicorn backend.main:app --port 8000`."


class BackendUnavailable(Exception):
    pass


class ApiError(Exception):
    """The backend answered with an error; str(exc) is its message."""


def _request(method: str, path: str, **kwargs) -> dict | list:
    try:
        resp = requests.request(method, f"{API_URL}{path}", timeout=60, **kwargs)
    except requests.ConnectionError as exc:
        raise BackendUnavailable(START_HINT) from exc
    if not resp.ok:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        raise ApiError(detail)
    return resp.json()


def create_bill(filename: str, data: bytes, mime_type: str) -> dict:
    return _request("POST", "/bills", files={"image": (filename, data, mime_type)})


def get_bill(bill_id: int) -> dict:
    return _request("GET", f"/bills/{bill_id}")
