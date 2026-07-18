"""Heartbeat summary: waiting-on-Shopee skips are reported separately from
real failures, with per-order reason lines — so an allocation-lag run no
longer reads as 'N gagal' and the operator never digs through Actions logs
to see which orders are pending."""

from src.telegram_sender import build_summary, _SUMMARY_DETAIL_MAX


def test_no_orders():
    assert build_summary("11:00", 0) == "✅ Shopee - 11:00 - Tidak ada pesanan baru"


def test_all_sent():
    assert build_summary("12:00", 3) == "✅ Shopee - 12:00 - 3 label terkirim"


def test_waiting_only_lists_orders_with_reason():
    waiting = [
        ("2607181CM2DKY3", "menunggu alokasi Shopee"),
        ("26071813VC607H", "belum siap (LOGISTICS_NOT_START)"),
    ]
    text = build_summary("14:59", 0, waiting)
    lines = text.split("\n")
    assert lines[0] == "⚠️ Shopee - 14:59 - 0 terkirim, 2 menunggu Shopee (akan dicoba lagi)"
    assert "⏳ 2607181CM2DKY3 — menunggu alokasi Shopee" in lines
    assert "⏳ 26071813VC607H — belum siap (LOGISTICS_NOT_START)" in lines
    assert "gagal" not in text  # waiting is not failure


def test_mixed_waiting_and_failed():
    text = build_summary(
        "13:00", 2,
        waiting=[("SN1", "label belum siap")],
        failed=[("SN2", "kirim Telegram gagal")],
    )
    lines = text.split("\n")
    assert lines[0] == "⚠️ Shopee - 13:00 - 2 terkirim, 1 menunggu Shopee, 1 gagal (akan dicoba lagi)"
    assert "⏳ SN1 — label belum siap" in lines
    assert "❌ SN2 — kirim Telegram gagal" in lines


def test_failed_only_omits_waiting_segment():
    text = build_summary("13:00", 1, failed=[("SN9", "atur pengiriman gagal")])
    assert text.split("\n")[0] == "⚠️ Shopee - 13:00 - 1 terkirim, 1 gagal (akan dicoba lagi)"
    assert "menunggu" not in text


def test_detail_lines_capped_with_overflow():
    waiting = [(f"SN{i:02d}", "menunggu alokasi Shopee") for i in range(15)]
    text = build_summary("10:00", 0, waiting)
    lines = text.split("\n")
    detail = [l for l in lines if l.startswith("⏳")]
    assert len(detail) == _SUMMARY_DETAIL_MAX
    assert "...dan 5 lainnya" in lines
