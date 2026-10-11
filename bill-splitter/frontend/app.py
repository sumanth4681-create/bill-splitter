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


def to_cents(dollars: float) -> int:
    return round(dollars * 100)


def _call(action, toast: str) -> None:
    try:
        action()
    except (api_client.BackendUnavailable, api_client.ApiError) as exc:
        st.session_state.flash_error = str(exc)
        return
    st.toast(toast)


def save_bill_name(bill_id: int) -> None:
    name = st.session_state[f"name_{bill_id}"]
    _call(lambda: api_client.update_bill(bill_id, name=name), "Renamed")


def save_amounts(bill_id: int) -> None:
    fields = {k: to_cents(st.session_state[f"{k}_{bill_id}"])
              for k in ("tax_cents", "tip_cents", "discount_cents", "receipt_total_cents")}
    _call(lambda: api_client.update_bill(bill_id, **fields), "Saved")


def save_item(bill_id: int, item_id: int) -> None:
    qty = int(st.session_state[f"qty_{item_id}"])
    price = to_cents(st.session_state[f"price_{item_id}"])
    _call(lambda: api_client.update_item(bill_id, item_id, quantity=qty, price_cents=price), "Row updated")


def save_override(bill_id: int, person_id: int, name: str) -> None:
    cents = to_cents(st.session_state[f"final_{person_id}"])
    _call(lambda: api_client.set_override(bill_id, person_id, cents), f"{name}'s amount set by hand")


def clear_override(bill_id: int, person_id: int, name: str) -> None:
    _call(lambda: api_client.set_override(bill_id, person_id, None), f"{name}'s amount recalculated")


def money_input(label: str, key: str, cents: int, on_change, args, **kwargs):
    """A dollars number box whose state is refreshed from the bill on every rerun."""
    st.session_state[key] = cents / 100
    return st.number_input(label, key=key, step=0.01, format="%.2f", on_change=on_change, args=args, **kwargs)


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
        diff = summary["difference_cents"]
        st.warning(
            f"Items + tax + tip − discount = {money(summary['receipt_total_cents'] - diff)}, receipt says "
            f"{money(summary['receipt_total_cents'])}. The {money(abs(diff))} difference is split equally "
            f"so the totals still add up to what was paid. Check for a misread row."
        )
    for share in summary["people"]:
        with st.container(border=True):
            head, total = st.columns([6, 2])
            head.markdown(f"**{share['name']}**")
            with total:
                money_input("Owes", f"final_{share['person_id']}", share["final_cents"], save_override,
                            (bill["id"], share["person_id"], share["name"]), label_visibility="collapsed")
            if share["edited"]:
                head.caption(f"Set by hand (calculated: {money(share['computed_cents'])})")
                head.button("Recalculate", key=f"reset_{share['person_id']}", on_click=clear_override,
                            args=(bill["id"], share["person_id"], share["name"]))
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
            for name, cents in (("Tax share", share["tax_cents"]), ("Tip share", share["tip_cents"]),
                                ("Discount share", -share["discount_cents"]),
                                ("Receipt difference", share["difference_cents"])):
                if cents:
                    label, amt = st.columns([6, 2])
                    label.caption(name)
                    with amt:
                        right(f"<small>{money(cents)}</small>")


def items_section(bill: dict, person: dict | None) -> None:
    st.markdown("**Items**")
    by_person = {p["id"]: p["name"] for p in bill["people"]}
    claims_by_item: dict[int, list[str]] = {}
    for c in bill["claims"]:
        claims_by_item.setdefault(c["item_id"], []).append(by_person.get(c["person_id"], "?"))

    for item in bill["items"]:
        tick, name_col, qty_col, price_col, edit_col = st.columns([1, 5, 1, 2, 1])
        if person is not None:
            key = f"claim_{item['id']}_{person['id']}"
            st.session_state[key] = person["id"] in {c["person_id"] for c in bill["claims"] if c["item_id"] == item["id"]}
            tick.checkbox("claim", key=key, label_visibility="collapsed", on_change=toggle_claim,
                          args=(bill["id"], item["id"], person["id"], person["name"], item["name"]))
        name_col.write(item["name"])
        qty_col.write(f"×{item['quantity']}")
        with price_col:
            right(money(item["price_cents"]))
        with edit_col.popover("✎", help="Fix a misread quantity or price"):
            st.session_state[f"qty_{item['id']}"] = item["quantity"]
            st.number_input("Quantity", key=f"qty_{item['id']}", min_value=1, step=1)
            st.session_state[f"price_{item['id']}"] = item["price_cents"] / 100
            st.number_input("Price (line total)", key=f"price_{item['id']}", step=0.01, format="%.2f")
            st.button("Save", key=f"save_{item['id']}", on_click=save_item, args=(bill["id"], item["id"]))
        claimers = claims_by_item.get(item["id"])
        if claimers:
            name_col.caption(", ".join(claimers))

    st.divider()
    st.caption("Tax, tip, discount, and the receipt total can be corrected here if the reader missed a line.")
    tax_col, tip_col, disc_col, total_col = st.columns(4)
    with tax_col:
        money_input("Tax", f"tax_cents_{bill['id']}", bill["tax_cents"], save_amounts, (bill["id"],), min_value=0.0)
    with tip_col:
        money_input("Tip", f"tip_cents_{bill['id']}", bill["tip_cents"], save_amounts, (bill["id"],), min_value=0.0)
    with disc_col:
        money_input("Discount", f"discount_cents_{bill['id']}", bill["discount_cents"], save_amounts, (bill["id"],),
                    min_value=0.0, help="Taken off the bill and split equally")
    with total_col:
        money_input("Receipt total", f"receipt_total_cents_{bill['id']}", bill["receipt_total_cents"], save_amounts,
                    (bill["id"],), min_value=0.0)


def show_bill(bill: dict) -> None:
    st.session_state[f"name_{bill['id']}"] = bill["name"]
    st.text_input("Bill name", key=f"name_{bill['id']}", on_change=save_bill_name, args=(bill["id"],))
    st.caption(f"Receipt total {money(bill['receipt_total_cents'])}")
    flash = st.session_state.pop("flash_error", None)
    if flash:
        st.error(flash)
    person = people_section(bill)
    summary_section(bill)
    items_section(bill, person)


# ---------- tabs ----------

READERS = {"Google Document AI (about 2 seconds)": "docai", "Local model via LM Studio (about half a minute)": "local"}


def upload_tab() -> None:
    photo = st.file_uploader("Receipt photo", type=["jpg", "jpeg", "png", "webp", "pdf"])
    reader = READERS[st.selectbox("Reader", list(READERS), key="reader_choice")]
    if photo is not None and st.button("Read receipt", type="primary"):
        wait = "Reading the receipt…" if reader == "docai" else "Reading with the local model… this takes a while"
        with st.spinner(wait):
            try:
                bill = api_client.create_bill(photo.name, photo.getvalue(), photo.type or "image/jpeg", reader)
            except (api_client.BackendUnavailable, api_client.ApiError) as exc:
                show_error(exc)
                return
        st.session_state.bill_id = bill["id"]
        st.toast("Receipt read")


def open_bill(bill_id: int) -> None:
    st.session_state.bill_id = bill_id


def history_tab() -> None:
    try:
        bills = api_client.list_bills()
    except (api_client.BackendUnavailable, api_client.ApiError) as exc:
        show_error(exc)
        return
    if not bills:
        st.info("No bills yet. Upload a receipt to get started.")
        return
    st.caption("Newest first. Click a bill to reopen it with its names and claims.")
    for b in bills:
        day = b["created_at"][:10]
        st.button(f"{b['name']}  ·  {day}", key=f"open_{b['id']}", on_click=open_bill, args=(b["id"],),
                  use_container_width=True)


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
