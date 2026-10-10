"""Streamlit screen: Upload and History tabs, plus the bill view. No math, no storage."""

import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

from frontend import api_client  # noqa: E402

st.set_page_config(page_title="Bill Splitter", page_icon="🧾", layout="centered")


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


def show_bill(bill: dict) -> None:
    st.subheader(bill["name"])
    st.caption(f"Receipt total {money(bill['receipt_total_cents'])}")

    st.markdown("**Items**")
    for item in bill["items"]:
        name_col, qty_col, price_col = st.columns([6, 1, 2])
        name_col.write(item["name"])
        qty_col.write(f"×{item['quantity']}")
        price_col.markdown(f"<div style='text-align:right'>{money(item['price_cents'])}</div>",
                           unsafe_allow_html=True)
    st.divider()
    for label, cents in (("Tax", bill["tax_cents"]), ("Tip", bill["tip_cents"])):
        label_col, price_col = st.columns([7, 2])
        label_col.write(label)
        price_col.markdown(f"<div style='text-align:right'>{money(cents)}</div>", unsafe_allow_html=True)


def upload_tab() -> None:
    photo = st.file_uploader("Receipt photo", type=["jpg", "jpeg", "png", "webp", "pdf"])
    if photo is not None and st.button("Read receipt", type="primary"):
        with st.spinner("Reading the receipt…"):
            try:
                bill = api_client.create_bill(photo.name, photo.getvalue(), photo.type or "image/jpeg")
            except (api_client.BackendUnavailable, api_client.ApiError) as exc:
                st.error(str(exc))
                return
        st.session_state.bill_id = bill["id"]
        st.toast("Receipt read")


def history_tab() -> None:
    st.caption("Past bills will be listed here.")


st.title("Bill Splitter")
upload, history = st.tabs(["Upload", "History"])
with upload:
    upload_tab()
with history:
    history_tab()

if "bill_id" in st.session_state:
    try:
        current = api_client.get_bill(st.session_state.bill_id)
    except (api_client.BackendUnavailable, api_client.ApiError) as exc:
        st.error(str(exc))
    else:
        st.divider()
        show_bill(current)
