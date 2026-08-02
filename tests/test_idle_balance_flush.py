"""Idle runs must still drain the pending stock-balance queue.

`_do_run` returns early when there are no new orders. It used to return BEFORE
`_run_throttled_balance`, so a SKU deferred by the throttle (or left over from
a failed dispatch) could only be flushed by the arrival of an unrelated new
order — stranding it indefinitely in a quiet period and breaking the
"withholding never drops a SKU" guarantee.

These drive the real `_do_run` with the Shopee/Telegram/state boundaries stubbed
so no network is touched.
"""

import src.main as main


class _Recorder:
    """Captures what the run did, standing in for every external boundary."""

    def __init__(self):
        self.summaries = []
        self.saved_state = None
        self.throttle_saved = None
        self.dispatched = []
        self.has_work = []


def _install(monkeypatch, rec, *, throttle_state, dispatch_ok=True):
    monkeypatch.setattr(main.state_manager, "load", lambda: {})
    monkeypatch.setattr(main.state_manager, "save", lambda s: setattr(rec, "saved_state", s))
    monkeypatch.setattr(main.state_manager, "now_iso", lambda: "2026-08-01T00:00:00+00:00")

    # No new orders -> the idle path.
    monkeypatch.setattr(main.shopee_client, "get_pending_orders", lambda: [])

    monkeypatch.setattr(main.telegram_sender, "send_summary", lambda s: rec.summaries.append(s))
    monkeypatch.setattr(main, "_emit_has_work", lambda v: rec.has_work.append(v))

    monkeypatch.setattr(main.balance_throttle, "load", lambda: dict(throttle_state))
    monkeypatch.setattr(
        main.balance_throttle, "save", lambda s: setattr(rec, "throttle_saved", s)
    )

    class _FakeDispatcher:
        def __init__(self):
            self._skus = []

        def record(self, sku):
            if sku:
                self._skus.append(sku)

        def collected(self):
            return sorted(set(self._skus))

        def dispatch_all(self):
            rec.dispatched.append(sorted(set(self._skus)))
            n = len(set(self._skus))
            return {
                "requested": n,
                "dispatched": n if dispatch_ok else 0,
                "failed": 0 if dispatch_ok else n,
                "skus": sorted(set(self._skus)),
            }

    monkeypatch.setattr(main.balance_dispatcher, "BalanceDispatcher", _FakeDispatcher)


def test_idle_run_flushes_pending_when_window_open(monkeypatch):
    rec = _Recorder()
    _install(
        monkeypatch,
        rec,
        throttle_state={"last_dispatch_at": None, "pending_skus": ["ITBISA-A", "ITBISA-B"]},
    )

    main._do_run()

    # The whole queue went out in one dispatch, without needing a new order.
    assert rec.dispatched == [["ITBISA-A", "ITBISA-B"]]
    # Success resets the window and empties the queue.
    assert rec.throttle_saved["pending_skus"] == []
    assert rec.throttle_saved["last_dispatch_at"] == "2026-08-01T00:00:00+00:00"


def test_idle_run_keeps_pending_when_window_closed(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(main.balance_throttle, "window_open", lambda s: False)
    _install(
        monkeypatch,
        rec,
        throttle_state={"last_dispatch_at": "2026-08-01T00:00:00+00:00",
                        "pending_skus": ["ITBISA-A"]},
    )

    main._do_run()

    assert rec.dispatched == []                                  # nothing sent
    assert rec.throttle_saved["pending_skus"] == ["ITBISA-A"]    # still queued
    assert "ITBISA-A" not in "".join(rec.summaries)              # no SKU spam
    assert "menunggu" in rec.summaries[0]                        # but it IS reported


def test_idle_run_requeues_a_failed_dispatch(monkeypatch):
    rec = _Recorder()
    _install(
        monkeypatch,
        rec,
        throttle_state={"last_dispatch_at": None, "pending_skus": ["ITBISA-A"]},
        dispatch_ok=False,
    )

    main._do_run()

    assert rec.dispatched == [["ITBISA-A"]]                    # it was attempted
    assert rec.throttle_saved["pending_skus"] == ["ITBISA-A"]  # and stays queued
    assert rec.throttle_saved["last_dispatch_at"] is None      # window NOT reset


def test_idle_run_with_nothing_pending_keeps_the_one_line_heartbeat(monkeypatch):
    rec = _Recorder()
    _install(monkeypatch, rec, throttle_state={"last_dispatch_at": None, "pending_skus": []})

    main._do_run()

    assert rec.dispatched == []
    assert len(rec.summaries) == 1
    # A genuinely idle run must not grow a stock-balance line.
    assert "Stock Balance" not in rec.summaries[0]


def test_precheck_still_emits_has_work_false_after_draining(monkeypatch):
    rec = _Recorder()
    _install(
        monkeypatch,
        rec,
        throttle_state={"last_dispatch_at": None, "pending_skus": ["ITBISA-A"]},
    )

    main._do_run(precheck=True)

    # Draining the queue must not make the workflow think there is label work:
    # that would install poppler and run the full processor for nothing.
    assert rec.has_work == [False]
    assert rec.dispatched == [["ITBISA-A"]]
