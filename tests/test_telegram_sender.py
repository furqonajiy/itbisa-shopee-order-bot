"""Unit tests for caption formatting helpers (pure string logic)."""

from src.telegram_sender import _mono, _pick_sku, build_caption


def test_mono_wraps_in_code_span():
    assert _mono("ITBISA-X") == "`ITBISA-X`"
    assert _mono(123) == "`123`"


def test_mono_strips_backticks_so_span_cannot_break():
    assert _mono("a`b`c") == "`abc`"


def test_pick_sku_prefers_variant_then_parent_then_name():
    assert _pick_sku({"model_sku": "VAR", "item_sku": "PARENT", "item_name": "N"}) == "VAR"
    assert _pick_sku({"model_sku": "", "item_sku": "PARENT", "item_name": "N"}) == "PARENT"
    assert _pick_sku({"model_sku": "", "item_sku": "", "item_name": "Name"}) == "Name"


def test_build_caption_wraps_copyable_values():
    order = {
        "order_sn": "2606168NT8VTU9",
        "shipping_carrier": "SPX Hemat",
        "item_list": [
            {"model_quantity_purchased": 1, "model_sku": "ITBISA-BLUETOOTH-MODULE-HC05"},
        ],
    }
    caption = build_caption(order)
    assert "`2606168NT8VTU9`" in caption  # order number tap-to-copy
    assert "`SPX Hemat`" in caption  # courier tap-to-copy
    assert "• 1 x `ITBISA-BLUETOOTH-MODULE-HC05`" in caption  # SKU tap-to-copy
    assert "Barang:" in caption
