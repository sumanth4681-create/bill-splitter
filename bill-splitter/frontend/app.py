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


def right(text: str) -> None:
    st.markdown(f"<div style='text-align:right'>{text}</div>", unsafe_allow_html=True)


def show_error(exc: Exception) -> None:
    st.error(str(exc))


# ---------- actions (each calls the API, then Streamlit reruns the page) ----------

def save_names(bill_id: int) -> None:
    raw = st.session_state.get("names_text", "")
    names = [n for chunk in raw.splitlines() for n in chunk.split(",")]
    try:
        api_client.set_people(bill_id, names)
    except (api_client.BackendUnavailable, api_client.ApiError) as exc:
        st.session_state.flash_error = str(exc)
        return
    st.toast("Names saved")


def toggle_claim(bill_id: int, item_id: int, person_id: int, person_name: str, item_name: str) -> None:
    claimed = st.session_state[f"claim_{item_id}_{person_id}"]
    try:
        api_client.set_claim(bill_id, item_id, person_id, claimed)
    except (api_client.BackendUnavailable, api_client.ApiError) as exc:
        st.session_state.flash_error = str(exc)
        return
    st.toast(f"{'Claimed' if claimed else 'Unclaimed'} {item_name} for {person_name}")


# ---------- bill view ----------

def people_section(bill: dict) -> dict | None:
    """Names input + person dropdown. Returns the selected person, or None."""
    people = bill["people"]
    if "names_text" not in st.session_state or st.session_state.get("names_for_bill") != bill["id"]:
        st.session_state.names_text = "\n".join(p["name"] for p in people)
        st.session_state.names_for_bill = bill["id"]
    st.text_area("Friends' names (one per line, or comma-separated)", key="names_text", height=90)
    st.button("Save names", on_click=save_names, args=(bill["id"],))

    if not people:
        st.info("Add names to start claiming.")
        return None
    names = [p["name"] for p in people]
    chosen = st.selectbox("Claiming for", names, key=f"person_for_{bill['id']}")
    return next(p for p in people if p["name"] == chosen)


def summary_section(bill: dict) -> None:
    summary = bill["summary"]
    if not summary["people"]:
        return
    st.markdown("**Who owes what**")
    if not summary["matches_receipt"]:
        st.warning(
            f"Items + tax + tip = {money(summary['computed_total_cents'])}, receipt says "
            f"{money(summary['receipt_total_cents'])}. Check for a misread row."
        )
    for share in summary["people"]:
        with st.container(border=True):
            head, total = st.columns([6, 2])
            head.markdown(f"**{share['name']}**")
            with total:
                right(f"**{money(share['final_cents'])}**" + (" ✎" if share["edited"] else ""))
            for line in share["lines"]:
                note = ""
                if line["unclaimed"]:
                    note = f" · unclaimed, split {line['split_ways']} ways"
                elif line["split_ways"] > 1:
                    note = f" · shared {line['split_ways']} ways"
                label, amt = st.columns([6, 2])
                label.caption(f"{line['item_name']}{note}")
                with amt:
                    right(f"<small>{money(line['share_cents'])}</small>")
            for name, cents in (("Tax", share["tax_cents"]), ("Tip", share["tip_cents"])):
                if cents:
                    label, amt = st.columns([6, 2])
                    label.caption(f"{name} share")
                    with amt:
                        right(f"<small>{money(cents)}</small>")


def items_section(bill: dict, person: dict | None) -> None:
    st.markdown("**Items**")
    by_person = {p["id"]: p["name"] for p in bill["people"]}
    claims_by_item: dict[int, list[str]] = {}
    for c in bill["claims"]:
        claims_by_item.setdefault(c["item_id"], []).append(by_person.get(c["person_id"], "?"))

    for item in bill["items"]:
        tick, name_col, qty_col, price_col = st.columns([1, 5, 1, 2])
        if person is not None:
            key = f"claim_{item['id']}_{person['id']}"
            st.session_state[key] = person["id"] in {c["person_id"] for c in bill["claims"] if c["item_id"] == item["id"]}
            tick.checkbox("claim", key=key, label_visibility="collapsed", on_change=toggle_claim,
                          args=(bill["id"], item["id"], person["id"], person["name"], item["name"]))
        name_col.write(item["name"])
        qty_col.write(f"×{item['quantity']}")
        with price_col:
            right(money(item["price_cents"]))
        claimers = claims_by_item.get(item["id"])
        if claimers:
            name_col.caption(", ".join(claimers))

    st.divider()
    for label, cents in (("Tax", bill["tax_cents"]), ("Tip", bill["tip_cents"])):
        label_col, price_col = st.columns([7, 2])
        label_col.write(label)
        with price_col:
            right(money(cents))


def show_bill(bill: dict) -> None:
    st.subheader(bill["name"])
    st.caption(f"Receipt total {money(bill['receipt_total_cents'])}")
    flash = st.session_state.pop("flash_error", None)
    if flash:
        st.error(flash)
    person = people_section(bill)
    summary_section(bill)
    items_section(bill, person)


# ---------- tabs ----------

def upload_tab() -> None:
    photo = st.file_uploader("Receipt photo", type=["jpg", "jpeg", "png", "webp", "pdf"])
    if photo is not None and st.button("Read receipt", type="primary"):
        with st.spinner("Reading the receipt…"):
            try:
                bill = api_client.create_bill(photo.name, photo.getvalue(), photo.type or "image/jpeg")
            except (api_client.BackendUnavailable, api_client.ApiError) as exc:
                show_error(exc)
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
        show_error(exc)
    else:
        st.divider()
        show_bill(current)
