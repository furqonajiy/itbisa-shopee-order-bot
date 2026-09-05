"""What each order contained — the record that lets an opname tell a write-off
from goods already packed for shipment."""
from datetime import datetime, timezone

from src import order_items


def _order(sn, items, status="READY_TO_SHIP"):
    return {"order_sn": sn, "order_status": status, "item_list": items}


def test_extract_prefers_model_sku_and_normalizes():
    # model_sku is the variation's seller SKU and is what the ledger keys on;
    # item_sku is the fallback for a listing with no variations. Both are
    # UPPER().strip() to the repo-wide SKU contract, or the report side cannot
    # match its own ledger.
    got = order_items.extract(_order("A", [
        {"model_sku": " itbisa-relay-12v-5pin-blue ", "item_sku": "IGNORED",
         "model_quantity_purchased": 100},
        {"model_sku": "", "item_sku": "itbisa-buzzer-active-5v",
         "model_quantity_purchased": 6},
    ]))
    assert got == [{"sku": "ITBISA-BUZZER-ACTIVE-5V", "qty": 6},
                   {"sku": "ITBISA-RELAY-12V-5PIN-BLUE", "qty": 100}]


def test_extract_sums_split_lines_and_drops_junk():
    # Shopee can return one variation on several lines (a split package). Two
    # lines of 50 are one shipment of 100 as far as the shelf is concerned.
    got = order_items.extract(_order("A", [
        {"model_sku": "ITBISA-X", "model_quantity_purchased": 50},
        {"model_sku": "ITBISA-X", "model_quantity_purchased": 50},
        {"model_sku": "", "model_quantity_purchased": 9},          # no SKU
        {"model_sku": "ITBISA-Y", "model_quantity_purchased": 0},  # zero qty
        {"model_sku": "ITBISA-Z", "model_quantity_purchased": "n/a"},
    ]))
    assert got == [{"sku": "ITBISA-X", "qty": 100}]


def test_record_captures_every_pending_order_not_just_new_ones():
    # Goods leave the shelf when the order is PICKED, not when its label finally
    # prints. An order can be seen, skipped because the label is not ready, and
    # processed on a later run — recording only successes would miss exactly the
    # orders a same-day opname trips over.
    now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    state = order_items.record([
        _order("SN1", [{"model_sku": "ITBISA-A", "model_quantity_purchased": 3}]),
        _order("SN2", [{"model_sku": "ITBISA-B", "model_quantity_purchased": 7}]),
    ], now=now)
    assert set(state) == {"SN1", "SN2"}
    assert state["SN1"]["first_seen"] == now.isoformat()


def test_record_upserts_and_keeps_first_seen():
    first = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    later = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
    s = order_items.record([_order("SN1", [
        {"model_sku": "ITBISA-A", "model_quantity_purchased": 3}])], now=first)
    s = order_items.record([_order("SN1", [
        {"model_sku": "ITBISA-A", "model_quantity_purchased": 3}],
        status="PROCESSED")], s, now=later)
    assert len(s) == 1
    # first_seen is when the goods started moving and must not drift forward.
    assert s["SN1"]["first_seen"] == first.isoformat()
    assert s["SN1"]["last_seen"] == later.isoformat()
    assert s["SN1"]["order_status"] == "PROCESSED"


def test_empty_item_list_is_flagged_not_dropped():
    # Shopee occasionally returns the detail before the lines are ready.
    # "not known yet" must be distinguishable from "nothing ordered", and a
    # later run must be able to fill it in.
    s = order_items.record([_order("SN1", [])])
    assert s["SN1"]["items"] == [] and s["SN1"]["pending_items"] is True
    s = order_items.record([_order("SN1", [
        {"model_sku": "ITBISA-A", "model_quantity_purchased": 4}])], s)
    assert s["SN1"]["items"] == [{"sku": "ITBISA-A", "qty": 4}]
    assert s["SN1"]["pending_items"] is False


def test_summarize_totals_the_in_flight_position():
    s = order_items.record([
        _order("SN1", [{"model_sku": "ITBISA-A", "model_quantity_purchased": 100}]),
        _order("SN2", [{"model_sku": "ITBISA-A", "model_quantity_purchased": 291},
                       {"model_sku": "ITBISA-B", "model_quantity_purchased": 6}]),
    ])
    assert order_items.summarize(s) == {"ITBISA-A": 391, "ITBISA-B": 6}


def test_load_survives_a_corrupt_file(tmp_path):
    # Bookkeeping must never block a label.
    p = tmp_path / "order_items.json"
    p.write_text("{not json", encoding="utf-8")
    assert order_items.load(p) == {}


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "order_items.json"
    s = order_items.record([_order("SN1", [
        {"model_sku": "ITBISA-A", "model_quantity_purchased": 3}])])
    order_items.save(s, p)
    assert order_items.load(p) == s


def test_record_tracking_attaches_resi_and_stamps_shipped_at():
    now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    s = order_items.record([_order("SN1", [
        {"model_sku": "ITBISA-A", "model_quantity_purchased": 3}])], now=now)
    s = order_items.record_tracking("SN1", "JX1234567890", s, now=now)
    assert s["SN1"]["tracking_number"] == "JX1234567890"
    assert s["SN1"]["shipped_at"] == now.isoformat()
    # The items must survive untouched — the resi is extra evidence, not a
    # replacement for what was in the parcel.
    assert s["SN1"]["items"] == [{"sku": "ITBISA-A", "qty": 3}]


def test_record_tracking_keeps_first_shipped_at():
    first = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    later = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
    s = order_items.record_tracking("SN1", "JX1", None, now=first)
    s = order_items.record_tracking("SN1", "JX1", s, now=later)
    # A re-run must not make the parcel look newer than it is.
    assert s["SN1"]["shipped_at"] == first.isoformat()


def test_blank_resi_is_ignored_not_stored():
    # An empty string would be indistinguishable from "shipped, number unknown".
    s = order_items.record_tracking("SN1", "", None)
    assert s == {}
    s = order_items.record_tracking("", "JX1", None)
    assert s == {}


def test_a_later_record_run_never_erases_the_resi():
    # `record` runs on EVERY pending order every run. Rebuilding the row from
    # scratch would erase a resi captured in an earlier run — losing exactly the
    # proof this file exists to keep.
    s = order_items.record([_order("SN1", [
        {"model_sku": "ITBISA-A", "model_quantity_purchased": 3}])])
    s = order_items.record_tracking("SN1", "JX9", s)
    s = order_items.record([_order("SN1", [
        {"model_sku": "ITBISA-A", "model_quantity_purchased": 3}],
        status="PROCESSED")], s)
    assert s["SN1"]["tracking_number"] == "JX9"
    assert "shipped_at" in s["SN1"]
    assert s["SN1"]["order_status"] == "PROCESSED"
