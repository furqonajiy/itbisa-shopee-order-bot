"""Unit tests for the (intentionally duplicated) balance dispatcher.

Covers the pure SKU-normalisation and the best-effort no-token path. The
actual GitHub workflow_dispatch HTTP call is not exercised (network glue).
"""

from src.balance_dispatcher import BalanceDispatcher, to_base_sku


def test_to_base_sku_strips_pack_prefix_and_uppercases():
    assert to_base_sku("20PCS-ITBISA-LED-5MM") == "ITBISA-LED-5MM"
    assert to_base_sku("20pcs-itbisa-led-5mm") == "ITBISA-LED-5MM"
    assert to_base_sku("ITBISA-LED-5MM") == "ITBISA-LED-5MM"


def test_to_base_sku_empty_inputs_return_none():
    assert to_base_sku(None) is None
    assert to_base_sku("") is None
    assert to_base_sku("   ") is None


def test_to_base_sku_strips_only_one_leading_prefix():
    assert to_base_sku("20PCS-30PCS-X") == "30PCS-X"


def test_record_dedupes_and_normalises():
    d = BalanceDispatcher()
    d.record("20PCS-ITBISA-LED-5MM")
    d.record("ITBISA-LED-5MM")  # same base after stripping -> one entry
    d.record("itbisa-cap-100uf")
    d.record(None)
    d.record("")
    assert sorted(d._skus) == ["ITBISA-CAP-100UF", "ITBISA-LED-5MM"]


def test_dispatch_all_without_token_is_best_effort(monkeypatch):
    monkeypatch.delenv("STOCK_DISPATCH_TOKEN", raising=False)
    d = BalanceDispatcher()
    d.record("20PCS-ITBISA-LED-5MM")
    d.record("ITBISA-CAP-100UF")
    result = d.dispatch_all()
    # No token => nothing dispatched, all reported failed, no exception raised,
    # no network call. Counts reflect SKUs; skus is sorted + deduped.
    assert result["requested"] == 2
    assert result["dispatched"] == 0
    assert result["failed"] == 2
    assert result["skus"] == ["ITBISA-CAP-100UF", "ITBISA-LED-5MM"]


def test_dispatch_all_empty_is_noop():
    d = BalanceDispatcher()
    result = d.dispatch_all()
    assert result == {"requested": 0, "dispatched": 0, "failed": 0, "skus": []}
